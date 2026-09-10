
It's a service that answers one question: a security advisory says package X version Y is vulnerable — is that flaw actually reachable from your code, or is it noise you can ignore? You point it at a Python repo or a package you're considering adopting, and it returns REACHABLE, NOT_REACHABLE, or UNKNOWN, with file-and-line evidence backing the verdict.

The engineering underneath: an agent loop against the raw LLM API that investigates using tools you built — search_symbol, find_callers, resolve_import over an AST index of the repo — instead of being handed a precomputed call graph. Around it sits a FastAPI + Postgres backend with a background worker, an eval suite gating every prompt change in CI, and an injection-resistance layer, since advisory text and repo source are both attacker-controlled input flowing into an agent with tool access.

# Architectural Decisions (20 Aug 2026)

## 1. Triage State Machine

### States
- `QUEUED`: Request accepted, assigned an ID, and waiting for worker execution.
- `RUNNING`: Analysis is actively executing.
- `COMPLETED`: Analysis ran to completion without unhandled crashes. Reachability findings (`REACHABLE`, `NOT_REACHABLE`, `UNKNOWN`) are recorded as payload attributes inside this state, not separate top-level states.

  **Status note (L4, shipped):** the AST-index layer's actual verdict set is four
  values, not three — `agent_docs/PHASE1_AST_INDEX.md` §3.4 and
  `src/reachability/index/reachability_models.py::Verdict` also distinguish
  `REACHABLE_ONLY_FROM_TESTS` (a confident path exists, but only via test-function
  entrypoints, not production code) from plain `REACHABLE`. This document's three-value
  list above was written before that distinction was introduced; not rewritten here to
  preserve the original rationale, but the FastAPI layer's `COMPLETED` payload should
  expect four possible finding values, not three, whenever it starts consuming L4's
  output.
- `FAILED`: Analysis aborted due to a crash, hard error, or worker failure.

### State Transitions
         ┌────────────────┐
         │     QUEUED     │
         └───────┬──┬─────┘
                 │  │
Worker picks up  │  │ Rejected before execution (e.g. queue full, unresolvable target)
                 │  │
                 ▼  ▼
         ┌────────────────┐
         │    RUNNING     │──────┐
         └───────┬────────┘      │
                 │               │ Crash / Worker unhandled error
Analysis finishes│               │
                 ▼               ▼
         ┌───────────┐    ┌───────────┐
         │ COMPLETED │    │  FAILED   │
         └───────────┘    └───────────┘


- **Legal Transitions:**
  - `QUEUED` → `RUNNING`: Worker begins execution.
  - `QUEUED` → `FAILED`: Job rejected or aborted before execution starts (e.g., worker pre-check failure or queue capacity exceeded).
  - `RUNNING` → `COMPLETED`: Analysis finishes and records a reachability verdict.
  - `RUNNING` → `FAILED`: Analysis process encounters an unhandled exception or crash.

- **Impossible Transitions:**
  - `QUEUED` → `COMPLETED`: A triage cannot produce results without running.
  - `COMPLETED` → any state: `COMPLETED` is an immutable terminal state.
  - `FAILED` → any state: `FAILED` is an immutable terminal state. Retries must be submitted as new triage jobs.

- **Deferred Decision (Stuck in `RUNNING`):**
  - If a worker dies mid-analysis, a job could theoretically remain stuck in `RUNNING`. Handling dead-worker recovery and stale job timeouts is **deferred to Phase 4 (Worker Pool)**.

---

## 2. Identifier Format: UUIDv4

### Decision
Use **UUIDv4** via Python's standard library `uuid.uuid4()`.

### Rationale
- **No auto-increment integers:**
  - **Information leakage:** Sequential IDs reveal total system volume, velocity, and submission rates in public-facing URL paths (`/v1/triage/{id}`).
  - **Insecure Direct Object Reference (IDOR):** Sequential integers make trivial enumeration and result scraping possible.
- **Immediate Generation (Pre-allocation):**
  - The API handler generates the `UUIDv4` in-memory before persisting the record or dispatching background tasks. This allows the endpoint to return `202 Accepted` and a `Location: /v1/triage/{id}` header immediately without waiting for a database round-trip or sequence generation.
- **Why UUIDv4 over ULID:**
  - `uuid` is built into Python stdlib (zero extra dependencies).
  - While ULIDs offer lexicographical time-ordering to minimize B-tree index fragmentation in relational databases, the scale of this project does not justify an external library dependency.

---

## 3. Request Shape: The "Exactly-One-Of" Target

### Decision
`TriageRequest` must accept either a package spec (`package` and `version`) OR a repository URL (`repo_url`), but **never both and never neither**.

### Validation Rules
- `{"package": "foo", "version": "1.0.0"}` → **Valid**
- `{"repo_url": "https://github.com/org/repo"}` → **Valid**
- `{"package": "foo", "version": "1.0.0", "repo_url": "https://github.com/org/repo"}` → **Invalid (422)**
- `{}` → **Invalid (422)**
- `{"package": "foo"}` (missing `version`) → **Invalid (422)**

Enforced via Pydantic model-level validation (`@model_validator(mode="after")`).
### Validation Mechanism & Status Code
- Mutual exclusivity (`package` + `version` vs `repo_url`) is enforced via a Pydantic `@model_validator(mode="after")`. Raising a standard `ValueError` inside the model allows FastAPI's built-in `RequestValidationError` handler to catch and translate it into a `422 Unprocessable Entity` before handler execution. Enforcing this in the endpoint handler with an explicit `HTTPException(status_code=400)` was rejected to keep business schema rules unified inside the model layer.

### JSON Parse Errors (FastAPI Default Behavior)
- FastAPI routes malformed JSON strings directly through Starlette's `RequestValidationError` (error type: `json_invalid`), returning `422 Unprocessable Entity` by default, not `400`. We preserve this framework behavior: all request body contract failures (syntactic or semantic) produce `422`.

### Security Note & Deferral: SSRF Protection on `repo_url`
- Accepting a user-supplied `repo_url` introduces Server-Side Request Forgery (SSRF) risks (e.g., target pointing to cloud metadata endpoints like `http://169.254.169.254/`). Full URL scheme validation, private IP range blocking, and host allowlisting are **deferred to Phase 5**, when worker git cloning is implemented.

### Error Envelope Contract
- In the error envelope, `code` and `message` are strictly required strings; `details` is an optional list that defaults to empty when no granular field breakdown is available.
