
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

**Status note (code review, 11 Sep 2026): the trade-off above is broader in
practice than this write-up describes.** `_LoadOutsideCallCollector`
(`edges.py`) only excludes the outermost node of a call's `.func` chain from
the "referenced outside a call" set — for an attribute-chain call like
`pkg.sink.run()`, `generic_visit` still walks into the inner `Attribute`/`Name`
nodes (`sink`, `pkg`) and incorrectly adds them too. So `target_symbol="sink"`
(any intermediate module/attribute qualifier of *any* attribute-chain call
anywhere in the repo, not just names passed by value) also gets the escape.
This still only pushes toward more `unknown`, never toward a false
`not_reachable`, so it does not violate G1 — but it is a materially wider
trade-off than "a common name referenced outside a call position." Uncaught by
L5's corpus because none of the `not_reachable`-labeled fixtures (09/10/11/12/20)
use attribute-chain calls. Not fixed in this loop; tracked as a fast-follow
(walk the full `.func` attribute chain into `_call_func_ids`, add a direct unit
test for `collect_load_referenced_names`, add a fixture exercising this case).

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

## 5. Phase 1 carryover closeout (11 Sep 2026)

Three items were left open at the end of the L5 session (`agent_docs/L5_HANDOFF.md`).
All three are closed here, before any Phase 2 work began.

### 5.1 Revert-attribution check (D2/D4/D6)

Each of D1's, D3's, and D5's `src/` fix was reverted independently (fix logic
removed in place, test files untouched), bare `pytest` run, the result recorded,
then the fix restored and 110/110 reconfirmed before moving to the next one.

**D4 (pins D3): clean, as expected.** Reverting D3's Step 5 (bare-name-referenced-
elsewhere check) sent exactly `test_l5_fixture17_bare_name_argument_yields_unknown_
not_not_reachable` and `test_l5_fixture19_monkeypatch_reference_yields_unknown_not_
not_reachable` red. No masking.

**D6 (pins D5): the committed pytest test was never masked — but the corpus fixture
it's named after was, confirming the original concern one level up.**
`test_l5_fixture16_module_attribute_not_found_falls_back_to_unresolved` calls
`build_edge_index` directly and asserts on `resolution_rule`; it never passes
through `compute_reachability`'s Step 5 at all, so D3's bare-name mechanism was
structurally incapable of masking it. Reverting D5 alone sent this test red, exactly
as the code should behave. The predicted masking (L5_HANDOFF.md item 1) was real,
but at the *corpus-measurement* level, not the unit-test level: re-running
`scripts/measure_l5.py` with D5 reverted still reports fixture 16 as `unknown`
(not a G1 violation) because `pkg/lazy.py`'s `__getattr__` body contains a bare
`return target_func` that D3's Step 5 independently catches — so the adversarial
corpus's coverage of the confidently-wrong-`MODULE_ATTRIBUTE` bug class was gone at
the measurement-gate level even though the direct unit test was fine. Closed per
item 5.2 below (fixture 16b).

**D2 (pins D1): NOT clean — an unpredicted instance of the exact same masking
class.** `test_l5_fixture13_...` and `test_l5_fixture18_...` went red cleanly on
revert. `test_l5_fixture14_registry_dict_dispatch_yields_unknown_not_not_reachable`
did **not** — it stayed green with D1 (Step 4, the nameless-dynamic-dispatch check)
fully reverted. Root cause: its inline fixture builds `HANDLERS = {"go":
target_func}` after `from sink import target_func` — `target_func` is a bare `Name`
Load inside the dict literal, which D3's Step 5 independently catches, regardless of
D1. `scripts/measure_l5.py` confirms the same thing for the real corpus fixture 14
(`tests/fixtures/l5/14_registry_dict_dispatch`): reverting D1 alone still reports it
`unknown`, not a G1 violation — masked the same way fixture 16 was, just never
flagged as an open item because the original plan assumed 13/14/17/18/19 were one
undifferentiated "named vs. nameless" split rather than checking each test's actual
attribution. Fixed the same way as D6: rewrote the pytest test
(`test_l5_fixture14b_registry_dict_dispatch_pins_dynamic_dispatch_not_d3`) against a
no-bare-name variant and added an assertion on `result.path[-1].resolution_rule ==
ResolutionRule.DYNAMIC_DISPATCH`, not just `verdict == UNKNOWN`, so the test cannot
pass via D3's mechanism even by coincidence. Re-verified: red with D1 reverted,
green with D1 restored.

### 5.2 Fixture 16b / 14b — no-bare-name corpus variants

Added `tests/fixtures/l5/16b_pep562_no_bare_name/` (for D5, per the original plan)
and, per 5.1's finding, `tests/fixtures/l5/14b_registry_dict_no_bare_name/` (for
D1) — same `label.json`/`RATIONALE.md` shape as their numbered counterparts, but
the target symbol's name never appears as a bare `ast.Name`/`ast.Attribute` Load
anywhere: `16b`'s `__getattr__` fetches the function via
`getattr(importlib.import_module("pkg.sink"), "vulnerable")`, and `14b`'s registry
is built via the same `getattr(importlib.import_module(...), "vulnerable")` pattern
instead of a bare imported name in a dict literal. In both, `"vulnerable"` appears
only as a string literal, so D3's collector has nothing to catch, and the
MODULE_ATTRIBUTE-existence check (D5) / nameless-dynamic-dispatch check (D1)
become the *only* route to a non-`not_reachable` verdict.

Verified directly (not just by inspection): with each fix reverted, `scripts/
measure_l5.py` correctly flips the `*b` variant to `not_reachable` (14b) or a
confidently-wrong `not_reachable` via a misdirected edge (16b — the fabricated edge
points at `pkg.lazy:vulnerable`, which doesn't match the query's
`pkg.sink:vulnerable`, so the BFS reports no path and the bug presents as a false
`not_reachable`, same shape as the original fixture-16 bug) — both correctly
classified as `unknown`/`pass` with the fix present. `test_l5_fixture16b_module_
attribute_not_found_falls_back_to_unresolved` (rewritten D6) and
`test_l5_fixture14b_registry_dict_dispatch_pins_dynamic_dispatch_not_d3` (rewritten
D2) cover the unit level; the two new corpus fixtures cover the measurement level.
The original fixtures 14 and 16 are left in the corpus unchanged (still pass, still
useful as a record of the masking coincidence) — `14b`/`16b` are additive, not
replacements for the numbered IDs.

### 5.3 Escape-set scaling check (blocking) and the D3 narrowing fix

Measured `collect_load_referenced_names` / `_LoadOutsideCallCollector` against
`starlette` (venv-installed, pure-Python, 35 modules / 6,890 lines — a real
mid-sized third-party package already in this environment, not a new dependency):

| | before narrowing | after narrowing |
|---|---|---|
| total resolvable symbols (all node kinds) | 659 | 659 |
| escape set size | 833 | 428 |
| escape/total_symbols ratio | 1.26 | 0.65 |
| distinct callable (func/method/class) simple-names | 365 | 365 |
| of those, masked (name also in escape set) | 159 (43.6%) | 101 (27.7%) |

**Judgment: the pre-narrowing ratio was unambiguously too high to trust outside the
corpus.** An escape set *larger than the package's entire symbol count*, and 43.6%
of distinct callable names masked, would mean that for nearly half the named
symbols in a real codebase, `compute_reachability` could never return a confident
`not_reachable` verdict anywhere in that repo — not because the symbol is genuinely
ambiguous, but because the collector was catching every bare Load anywhere (return
values, comparisons, loop targets, and — the code-review-documented gap —
intermediate attribute-chain segments like the `sink`/`pkg` in `pkg.sink.run()`),
not just genuinely value-bound references.

**Fix (`src/reachability/index/edges.py`, `_LoadOutsideCallCollector`):** narrowed
to only record a `Name`/`Attribute` as escaped when it is itself the value-bound
expression at an assignment (`Assign`/`AnnAssign`/`AugAssign`) or a call argument
(positional or keyword) — including a direct element of a `list`/`tuple`/`set`/
`dict` literal at one of those positions (so "stored in a container" stays
covered) — never a sub-expression reached by descending further into an attribute
chain or a call's receiver. Implemented via `visit_Assign`/`visit_AnnAssign`/
`visit_AugAssign`/`visit_Call` each calling a `_record_value_bound` helper on the
relevant expression, instead of the previous blanket `visit_Name`/`visit_Attribute`
override that fired on every Load anywhere.

**Conservative-direction check, same rule as every Stage D fix:** narrowing must
never convert an existing fixture's `unknown` into a `not_reachable`. Re-ran
`scripts/measure_l5.py` after the change — all gates still pass (G1–G5), all 22
fixtures (20 original + 14b + 16b) unchanged in verdict. In particular, fixture 16
(original) still resolves `unknown` via `resolution_rule=unresolved_attribute`: its
`return target_func` is no longer caught by the narrowed collector (a bare `return`
is neither an assignment nor a call argument), but D5's own fallback already
produces an `UNRESOLVED_ATTRIBUTE` (`?:vulnerable`) edge, which the pre-existing
Step 3 named-`?:`-bridge (documented before L5, see CLAUDE.md) still catches
independently — so removing the over-broad part of D3's coverage didn't remove any
fixture's actual protection, it just stopped being redundant-for-the-wrong-reason in
that one case. Fixtures 17/19 (genuine argument-position references) and the new
14b/16b (assignment-position references) are all still caught, since "argument
position" and "assignment position" are exactly what the narrowed collector keeps.
Added `test_load_outside_call_collector_excludes_attribute_chain_segments` and
`test_load_outside_call_collector_still_catches_value_bound_references` in
`tests/test_index_edges.py` as direct unit coverage of the narrowing itself. Bare
`pytest`: 112 passed (110 + 2).

This closes the BLOCKING item from `agent_docs/L5_HANDOFF.md`: the collector is now
narrowed to value-bound name references before any real codebase gets indexed.
The remaining 0.65 ratio / 27.7% masked-name figure is the accepted D3 trade-off
already documented above (a common name referenced anywhere outside a call
position suppresses `not_reachable` for that name repo-wide) — now measuring the
intended trade-off rather than an inflated one.

## 6. Phase 2 U1/U2 — open items carried forward (11 Sep 2026)

Both units (`agent_docs/PHASE2_TRIAGE_AGENT.md` §3) shipped with 0 critical review
findings and are committed (`f5647ca`, `74985d7`), but each left one class of
accepted, documented gap open rather than closing it inside the unit's own gate.
Recorded here so a later phase doesn't have to rediscover them from commit
messages.

### U1 — an existing-but-empty `repo_root` still produces a confidently-wrong-looking result

`build_repo_index`'s precondition check (`src/reachability/triage/index_adapter.py:29-30`)
raises `IndexBuildError` for a `repo_root` that doesn't exist or isn't a directory,
closing the gap where `pathlib.rglob` silently returns nothing on a missing path. It
does **not** close the adjacent case: a `repo_root` that exists but is empty (or is a
directory one level off — a typo'd subpath) still produces a structurally valid,
"successful" empty `RepoIndex` (zero modules, zero entrypoints). Fed into
`compute_reachability`, this yields a confident `NOT_REACHABLE` ("no entrypoints
detected in repository", `src/reachability/index/reachability.py:115-122`) that is
indistinguishable from "this repo genuinely has no triageable code" — the same class
of confidently-wrong-verdict problem D1/D3/D5 (§4 above) exist to eliminate at the
AST-index layer, now reappearing one layer up. Not closed in U1; worth addressing
before U5 exposes `build_repo_index` to a live HTTP path, where a caller's typo'd
acquisition result could otherwise present as a clean "not reachable" instead of a
visible error.

### U2 — three residual gaps (documented in code), plus one forward-looking near-miss

`acquire_source` (`src/reachability/triage/acquisition.py`) documents three accepted
gaps directly in its own docstring, not just here — repeated in this log for
discoverability alongside U1's open item above:

1. **pip failure-string ambiguity** (`acquisition.py:46-55`): pip emits identical
   stderr text ("Could not find a version that satisfies the requirement" / "No
   matching distribution found") for a package+version with no wheel and for one
   that doesn't exist at all (e.g. a typo). Both are classified as `"no wheel
   available for X==Y"` — still fail-loud either way, never a silent failure or a
   build fallback, but the message may misname a plain typo as a missing-wheel
   condition.
2. **Unverified core assumption** (`acquisition.py:57-65`): that
   `--only-binary=:all:` never invokes the target package's own PEP 517 build
   backend even when no wheel exists. This rests on documented pip behavior, not an
   empirical observation in this sandbox — no genuinely sdist-only package could be
   found in this environment's package index after 8 probe attempts during U2's
   planning. The "no wheel available" test exercises `acquire_source`'s own
   response to a *mocked* pip failure, not a live observation of pip refusing to
   build for the target package.
3. **No zip-bomb/decompression-size guard** on wheel extraction (recorded in U2's
   plan under Out of Scope, not in the docstring): `zipfile.ZipFile(...).extractall(...)`
   has no cap on total uncompressed size or file count. Path traversal itself is
   not a gap — stdlib `zipfile` already sanitizes `..`/absolute-path components
   before joining onto the extraction root (verified directly against the
   installed CPython 3.13 `zipfile` source during both U2's critique and review
   passes, independently). A small-compressed/huge-uncompressed malicious wheel is
   a real, separate risk in exactly the class this unit's wheel-only rationale
   exists to guard against, left open pending Docker/sandboxing (still project-wide
   NOT BUILT per `CLAUDE.md`).

**Forward-looking near-miss, not yet a defect:** `acquire_source`'s docstring states
it "creates only a `download` and an `extracted` subdirectory" beneath `workdir`,
but the code also silently `mkdir`s `workdir` itself (`acquisition.py:72`) if it
doesn't already exist, rather than treating a missing `workdir` as a precondition
failure the way U1's `build_repo_index` does for `repo_root`. Harmless today (every
current caller is a test using pytest's own `tmp_path`, which always exists), but
worth resolving explicitly if U5's job-lifecycle wiring ever needs a "workdir must
already exist" contract.

### U3/U4 — `_on_raw_tool_result` is test-only instrumentation, not a production hook

`run_triage_loop`'s `_on_raw_tool_result` parameter (`src/reachability/triage/agent_loop.py`)
exists purely so `tests/test_triage_agent_loop_l5.py` can observe the real,
structured tool-call return values (e.g. the actual `list[CallEdge]` a
`find_callers` dispatch produced) for gate (ii)'s path-provenance check, without
resorting to a test-side recomputation that would prove nothing about the loop's
real behavior. It is underscore-prefixed, not part of the public contract, and is
never called by any production caller. Recorded here so U5's FastAPI job-lifecycle
wiring (or any later unit) doesn't rediscover — the hard way — that it was never
meant to become load-bearing production surface: do not wire it into U5's job
lifecycle or any other production caller.

## 7. Phase 2 U5 — job lifecycle wiring (12 Sep 2026)

`main.py`'s `POST /v1/triage` now dispatches `run_triage_job`
(`src/reachability/triage/job_runner.py`, new) via `BackgroundTasks`, chaining
U2 (`acquire_source`) → U1 (`build_repo_index`) → U3/U4 (`run_triage_loop`)
end to end and mutating `TRIAGE_DB[triage_id]` through
`QUEUED → RUNNING → COMPLETED/FAILED`. Six decisions, mirrored from
`job_runner.py`'s own module docstring:

1. **target_module/target_symbol source**: `TriageRequest` gained
   `target_module: str = Field(min_length=1)` (required) and
   `target_symbol: str | None = None`, independent of the existing
   package/version/repo_url exclusivity validator. A real caller (e.g.
   triaging a CVE advisory) always names a specific vulnerable symbol; this
   unit never derives a target automatically from the package.
2. **Production "LLM" client**: `DeterministicPolicyStubLLMClient`
   (`stub_llm.py`), instantiated fresh per job. This is **not** a real LLM —
   it is a deterministic placeholder policy used until a later unit adds live
   LLM integration, still NOT BUILT per project `CLAUDE.md`.
3. **Tool-call budget**: `DEFAULT_TOOL_CALL_BUDGET = 30`, a fixed
   module-level constant in `job_runner.py`, not exposed on `TriageRequest`
   in this unit.
4. **Workdir lifecycle**: `run_triage_job` opens one
   `tempfile.TemporaryDirectory(prefix="reachability-triage-",
   ignore_cleanup_errors=True)` per job, scoped to the whole chain via a
   `with` block. `ignore_cleanup_errors=True` is required, not optional: it
   makes a `shutil.rmtree` cleanup failure during `__exit__` be suppressed by
   `tempfile` itself rather than raised, so it can never masquerade as a
   chain failure. The chain itself uses an explicit `try`/`except`/`else`
   structure (not a bare `try` wrapping the whole `with` block):
   `record["finding"]`/`record["status"] = "completed"` are only set inside
   the `else` clause, which Python guarantees runs only when the `try`
   block's own body raised nothing, so a successful run can never be
   miscategorized as a failure (or vice versa) by an unanticipated exception
   source between the chain's success and the status assignment.
5. **Empty `RepoIndex` → FAILED**: `build_repo_index` succeeding with zero
   modules (`len(repo_index.report.modules) == 0`) is treated as a `FAILED`
   job via a new `EmptyRepoIndexError(RuntimeError)`, raised inside the same
   `try` block and caught by the same `except Exception` clause as any other
   chain failure. Rationale: an empty index is far more likely a silent
   acquisition/extraction defect (per §6's U1 open item above) than a
   genuinely empty package, and letting it proceed would present that defect
   as an indistinguishable, confident-looking `COMPLETED`/`NOT_REACHABLE` or
   `COMPLETED`/`UNKNOWN` — exactly the "confident wrong answer" failure class
   this project exists to avoid. **Accepted known limitation**: a genuinely
   pure-native/C-extension wheel with zero `.py` modules will also FAIL under
   this rule, since `EmptyRepoIndexError` cannot distinguish
   "acquisition/extraction defect" from "package genuinely has no Python
   source" — both present identically as a zero-module `RepoIndex`. (User
   sign-off, this conversation.)
6. **Error message detail**: the stored `error` string is
   `f"{type(exc).__name__}: {exc}"`, passed through the existing
   `sandbox_untrusted_text` (`sandbox.py`) — built for a different threat
   model (prompt-injection redaction of untrusted tool-result text fed back
   to an LLM context), reused here for a new one (HTTP response leakage) —
   then truncated to 2000 characters. Its coverage for this new purpose was
   checked, not assumed: `tests/test_triage_job_runner.py`'s
   `test_sanitize_error_against_real_captured_exceptions` captures real
   exceptions from U1/U2's actual failure paths (a real network call against
   a nonexistent package; a real `subprocess.TimeoutExpired` instance raised
   via a monkeypatched `subprocess.run`, which is the sub-case that actually
   embeds an absolute workdir path via `acquisition.py`'s real `--dest`
   argument; and a real `IndexBuildError` from `build_repo_index` against a
   real nonexistent path) and confirms `_sanitize_error`'s redaction holds
   for each — all three pass.

**Pydantic dataclass-serialization verification (step 7)**: checked
empirically before committing to an approach, not guessed. Pydantic v2
natively serializes a `TriageFinding` — including a populated
`path: list[CallEdge]` whose `CallEdge.file` is a real `pathlib.Path`, and
`ToolCallRecord.arguments: dict[str, object]` — through both direct
`model_dump_json()` and a real FastAPI `response_model=TriageOut` round trip
via `TestClient`, with `Path` values serializing to plain JSON strings and no
extra `model_config` needed. Verified directly against the real L5 fixture
`tests/fixtures/l5/02_transitive_three_hop` (a genuine `REACHABLE` verdict
with a 3-edge path) in
`tests/test_triage_job_lifecycle.py::test_full_lifecycle_completed_via_monkeypatched_chain`.
**Landed on the native path — no `dataclasses.asdict()`/custom
`dict_factory` fallback was needed**, and `TriageOut.finding`'s type is
`TriageFinding | None`, not `dict[str, object] | None`.

**Discovered side effect, not a redesign**: making `target_module` required
with no default (decision 1) breaks any pre-existing `TriageRequest(...)`
construction or `POST /v1/triage` body that didn't already carry it. This
was already anticipated for `tests/test_triage_acquisition.py`'s four direct
`TriageRequest(...)` call sites; fixed the same way (`target_module=
"placeholder"`) here. It was **not** anticipated for `tests/test_main.py`'s
existing `POST /v1/triage` body-shape/response-shape tests, which also
needed `target_module` added and, for the two tests asserting an exact
response key set, updated to include the new `finding`/`error` keys. A
second, related and also-undiscussed consequence: since `BackgroundTasks`
runs synchronously (from the caller's perspective) under `TestClient`, those
same `test_main.py` tests that POST a real `package`/`version` pair (e.g.
`{"package": "requests", "version": "2.31.0"}`) now trigger a real,
synchronous `acquire_source` call against real PyPI as a side effect of
exercising unrelated request/response-shape assertions — none of them are
marked `@pytest.mark.network`, unlike this project's convention for
deliberate real-network tests. This does not break correctness (the
assertions those tests make are unaffected by the job's real outcome, and
this project's own `pytest.ini` convention already documents that a plain
`pytest` run executes network-marked tests too, i.e. real network
dependence during a bare run is already an accepted norm here), but it is a
newly-introduced, unmarked real-network dependency in previously-fast,
network-independent tests, worth a deliberate look (e.g. monkeypatching
`job_runner`'s collaborators from `test_main.py`, or marking those tests
`@pytest.mark.network`) in a follow-up rather than silently accepted here.
