
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

---

## 4. L5 — what the adversarial corpus caught (11 Sep 2026)

L1-L4 shipped with 104/104 tests green, which only proved the code matched its own
assumptions. L5 built a 20-fixture adversarial corpus with hand-derived labels
(`tests/fixtures/l5/`, `agent_docs/L5_PROTOCOL.md`) and measured the real engine
against it (`scripts/measure_l5.py`). The first run was red: 6 of 20 fixtures
produced a false `not_reachable` (G1 violation) — the corpus did its job. This
section records what was over-confident and what changed.

### A4 — L3-freeze wording corrected (Stage A)

`CLAUDE.md`'s L3-specific-gaps paragraph originally read as a blanket freeze. It
was reworded, before any fixture existed, to: "L3 is frozen against new
resolution capability, not against fixes that reduce confidence." A freeze that
blocks fixing a confidently-wrong resolution inverts this project's own severity
ordering (a confident wrong answer is worse than an unresolved one) — the two
`edges.py` fixes below (D5, and D1/D3's use of data `edges.py` already exposes)
rely on this reading. Recorded here so a later reader does not mistake this for a
silently loosened policy.

### D1 — nameless dynamic dispatch and eval/exec (`src/reachability/index/reachability.py`)

**Bug found:** `compute_reachability`'s Step 3 (any-confidence BFS, previously
lines 149-165, now the block preceding the new Step 4) fell straight through to a
confident `NOT_REACHABLE` even when the reachable set contained an opaque call —
`getattr(obj, name)()` with no literal name, or `eval`/`exec` of a string this
engine never parses as code. Neither carries a name to bridge on the way a named
`?:name` unresolved call already does.

**Fix:** before concluding `NOT_REACHABLE`, `compute_reachability` now checks
whether the any-confidence reachable set contains `"?:<dynamic>"`
(`ResolutionRule.DYNAMIC_DISPATCH`, from `edges.py:347-349`) or
`"ext:builtins.eval"` / `"ext:builtins.exec"` (`ResolutionRule.BUILTIN`, from
`edges.py:182-183`), and returns `UNKNOWN` instead. Fixes L5 fixtures 13 and 18.

**Trade-off, and it is a big one — read this before assuming G1's pass means the
gap is small:** this check is **repo-wide, not scoped to the query target**. It
cannot be, because neither marker carries a name to filter on (unlike the
already-accepted named-`?:`-bridge trade-off in the pre-L5 CLAUDE.md text).
Concretely: once a repo has *one* reachable opaque call anywhere on a path from
an entrypoint, **no symbol in that repo can ever again receive a confident
`not_reachable` verdict from this engine** — not just the symbol near the opaque
call. Scoping this more tightly would require tracking which candidate call
sites could plausibly resolve to the queried symbol specifically (literal/value
tracking), which is a materially larger analysis capability, out of scope for
this loop. This is a deliberate, accepted trade-off in the same direction as
every other L5 fix (never let a false `not_reachable` through), but it is
**broader** than D3's version below — do not conflate the two.

**Correction to the L5 plan's own prediction — fixture 14 was NOT fixed by D3 as
planned, it was fixed by D1:** the plan assumed getattr-call dispatch (fixture
13) and registry-dict dispatch (fixture 14) were "distinct mechanisms." They are
not. `edges.py:346-349` treats `isinstance(func, (ast.Call, ast.Subscript))`
identically — a call through `HANDLERS[key]()` (a `Subscript`) produces the exact
same nameless `"?:<dynamic>"` marker as `getattr(...)()` (a `Call`), with zero
name information preserved in either case. D1's fix therefore closes fixture 14
as a side effect, before D3 was even written. Verified directly: after D1 alone
(before D3 existed), `scripts/measure_l5.py` showed fixture 14 as `unknown`
already, `resolution_rule=dynamic_dispatch`. D3 was still required for fixtures
17 and 19, which go through a different, genuine gap (see below).

### D3 — name referenced outside a call position (`src/reachability/index/edges.py`, `reachability.py`)

**Bug found:** L3's edge extraction (`build_module_call_edges`) only ever
inspects `call.func`; it never inspects `call.args` or `call.keywords`. A
function handed to a framework by reference (`app.on_event("startup",
target_func)`, L5 fixture 17) or substituted via `monkeypatch.setattr(mod,
"name", target_func)` (L5 fixture 19) produces **no call edge naming it at
all** — not even an unresolved `?:name` one — so the engine had no signal
whatsoever that the symbol was reachable through anything.

**Fix:** new `collect_load_referenced_names(report)` in `edges.py` walks every
module's AST once and records every `ast.Name`/`ast.Attribute` identifier loaded
somewhere that is *not* the `.func` of the `Call` it belongs to. `
compute_reachability` gained a **required** `report: DiscoveryReport` parameter
and computes this set **unconditionally, as its own first action** — not as a
caller-supplied value. An earlier draft of this fix made the set an optional
parameter defaulting to empty; that was rejected during planning specifically
because a caller (including a future real one) could satisfy the signature with
`frozenset()` and silently disable the check, making a measurement harness green
without protecting the actual query path. Making `report` required and deriving
the set internally means no caller decision can skip it. Fixes L5 fixtures 17
and 19 (and, redundantly with D1, fixture 14).

**Trade-off:** also repo-wide, not per-target — a common name (`run`, `load`,
`get`, `safe`) referenced anywhere outside a call position anywhere in the repo
will suppress `not_reachable` for that name repo-wide. Unlike D1, this is at
least filtered by name, so it is the **same size** of trade-off as the
already-accepted `UNRESOLVED_ATTRIBUTE` / named-`?:`-bridge behavior documented
in CLAUDE.md before L5 — not a new class of consequence.

**Deliberately not implemented — Phase 2 backlog:** a `ResolutionRule
.CALLBACK_REFERENCE` edge type that would trace *which* call eventually invokes
a by-reference callback (rather than a blunt name-level escape) was considered
and rejected for this loop. It is new analysis capability, not a bug fix to
existing logic, and the task spec explicitly scoped it out. If a future phase
wants tighter precision on the callback-reference class of gap (D3, and the
repo-wide side of D1), this is the starting point.

### D5 — module-attribute existence check (`src/reachability/index/edges.py`, `_resolve_attribute_callee`)

**Bug found, most severe of the three:** for a module-level attribute access
(`lazy.target_func()`) resolving through a plain module-alias import,
`_resolve_attribute_callee`'s `MODULE_ATTRIBUTE` branch (previously
`edges.py:279-287`) constructed a node id from the attribute name **without
checking it actually exists** in the target module's own symbol table. A module
with a PEP 562 `__getattr__` that synthesizes an attribute at access time (L5
fixture 16: `pkg/lazy.py` has no `target_func` written anywhere in its own
source, only a `__getattr__` that fetches the real one from `pkg/sink.py` on
demand) produced a confidently **wrong** `HIGH`-confidence edge to a symbol that
was never defined in the module the edge claimed to point at. This is a worse
bug than a dead end: not merely unresolved, but confidently resolved to the
wrong place.

**Note on measurement order:** by the time D5 was written, D3 had already
flipped fixture 16's verdict to `UNKNOWN` — but via a coincidence specific to
this one fixture (`pkg/lazy.py`'s `__getattr__` body contains a bare `return
target_func`, which D3's collector picks up as a Load-outside-call-position
reference), not because the underlying `MODULE_ATTRIBUTE` bug was fixed. A
hypothetical variant where the target is fetched without ever binding it to a
bare name (e.g. via `getattr(importlib.import_module(...), name)` inside
`__getattr__`) would still trigger the original bug, undetected by D3. D5 was
implemented anyway, on the merits, not to move a gate that had already turned
green — this is the actual root-cause fix. Verified: `resolution_rule` for
fixture 16 changed from `module_attribute` (pre-fix) to `unresolved_attribute`
(post-fix) even though the verdict was `unknown` both before and after D5, for
different underlying reasons.

**Fix:** before returning a confident `MODULE_ATTRIBUTE` resolution for a
single-attribute access on a first-party module alias, check the attribute name
against `module_qualname_indices[alias.resolved_absolute]`; if absent, fall
through to the existing `UNRESOLVED_ATTRIBUTE` fallback instead of fabricating a
resolution. Scoped deliberately to the single-attribute, first-party case only
(matching what fixture 16 and the two pre-existing `MODULE_ATTRIBUTE` tests in
`tests/test_index_edges.py` exercise) — the multi-attribute chain branch and the
external-package branch were left untouched, since neither has a target symbol
table this engine can check against. Committed alone (no other `src/` file in
that commit), per the plan, since it reopens the L3 freeze A4 reworded above.

### G4 — no sign-off needed

Post-fix, the `unknown` count among the 13 `decidable: true` fixtures is 0 (see
`results/l5_<sha>.json` after Stage D). No sign-off paragraph is required this
round.

### Result

All five gates pass on the post-fix run: G1 = 0 false `not_reachable`, G2 = 5/5
(exceeds the 4/5 floor), G3 = 2/2, G4 = 0 (reported), G5 = 0 crashes. Bare
`pytest` from the venv: 110 passed (104 baseline + 6 regression tests: 2 for D1,
2 for D3, 1 incidental-but-pinned for D1's fixture-14 side effect, 1 for D5).
