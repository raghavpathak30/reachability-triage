# Project: reachability-triage

## STABLE — do not edit mid-session (cache-critical)
Edits here do nothing until `/clear`, `/compact`, or restart.

- Stack as SHIPPED: Python, FastAPI, uvicorn, pydantic. Single `main.py`.
- Package manager: pip + venv (`requirements.txt`) — always this, never switch
- Test command: none yet — `pytest` once tests exist
- Lint/typecheck command: none configured
- Run command: `uvicorn main:app --reload`
- Build command: none (no Docker files in the repo yet)
- Do not touch: `.agent/`, `.venv/`, `__pycache__/`
- Success criteria for any change: the app starts clean, no debug prints

## NOT BUILT — do not describe these as existing
PostgreSQL, SQLAlchemy, Docker, async job submission,
retries/backoff, idempotency, per-user quotas, cost accounting,
content-hash caching, DB-stored prompt versioning, the eval harness,
live LLM API integration.
These are in DECISIONS.md as intent. Check the code before claiming any
of them work — in a README, docstring, commit message or comment.

Phase 2 U3/U4 (`src/reachability/triage/agent_loop.py`,
`src/reachability/triage/sandbox.py`, `src/reachability/triage/stub_llm.py`)
are built: a stub-LLM tool-calling loop over `search_symbol`/`find_callers`/
`resolve_import`, a hard tool-call budget, and a `sandbox_untrusted_text`
injection-resistance boundary live from its first commit. The LLM in that loop
is a deterministic stub (`stub_llm.py`), never a live API call — do not
describe this as a working agent against a real model. FastAPI job-lifecycle
wiring (U5) and the eval harness (U6) are not built.

AST index (`agent_docs/PHASE1_AST_INDEX.md`): L1 (module discovery + import
map), L2 (symbol table — functions, classes, methods, nested functions,
module-level callable aliases, decorator/base resolution), L3 (call
edge extraction — every `ast.Call` resolved to a `CallEdge` with
`caller_id`/`callee_id`/`confidence`/`resolution_rule`, per the table in
section 3), and L4 (entrypoint detection — `if __name__ == "__main__"`,
`[project.scripts]`, route/celery/CLI decorators, `test_*`/pytest-fixture
functions — plus BFS-based reachability verdicts and the
`search_symbol`/`find_callers`/`resolve_import` query layer), and L5
(a 20-fixture adversarial measurement corpus + `scripts/measure_l5.py`,
see DECISIONS.md §4) are built, all in `src/reachability/index/` (L5's
corpus lives in `tests/fixtures/l5/`). Not built: `.reachability/index.json`
serialization.
`compute_reachability` returns one of four verdicts —
`reachable`/`reachable_only_from_tests`/`not_reachable`/`unknown` — each
with a `path: list[CallEdge] | None` or a `reason: str`, never a bare
true/false.

L3-specific gaps, not deferred to a named unit, documented in
`.agent/plan.md`'s Out of scope section: calls inside a `lambda` body, a
call sitting directly in a class body outside any method, and
`cls.method()`/`super().method()` (treated as unresolved, not MRO) are
never extracted as edges; `self.method()` MRO resolution is
declaration-order DFS over bases, not true C3 linearization.

L4-specific gaps, documented in `.agent/plan.md`'s Out of scope section:
Django URLconf and argparse `set_defaults(func=...)` dispatch-target
detection are not implemented (spec names both; deliberate deviation, not
an oversight — see plan for rationale); `setup.py`/`[tool.poetry.scripts]`
console-script parsing is not implemented (only PEP 621
`[project.scripts]`/`[project.gui-scripts]` via stdlib `tomllib`).

A *named* unresolved call (e.g. `obj.load()`) bridges to `unknown` for
**any** query whose `target_symbol` matches that name, regardless of
`target_module` — a known false-positive-toward-`unknown` gap, since an
unresolved callee carries no module information to check. This is an
accepted consequence of resolving toward `unknown` over a wrong confident
answer, not something L4 attempts to fix (would require L3 to record
receiver-module provenance). L3 is frozen against new resolution
capability, not against fixes that reduce confidence.

L5 (DECISIONS.md §4) found and fixed the more severe, nameless version of
this same class of gap: `getattr(obj, name)()` with no literal name, or
`eval`/`exec` of a string, previously fell through to a confident
`NOT_REACHABLE` — the worst class of bug this project defines.
`compute_reachability` now degrades to `unknown` whenever such a call is
reachable anywhere in the repo (D1), and does the same for any symbol
referenced by name outside a call position anywhere in the repo — e.g. a
function passed by reference to a framework callback or
`monkeypatch.setattr` (D3). Both checks are corpus-wide, not scoped to the
query target — see DECISIONS.md §4 for why that's an accepted trade-off,
and for a code-review-found gap (documented, not yet fixed) where an
intermediate attribute-chain segment of an unrelated call can also trigger
D3's escape unintentionally.

## Handoff protocol
- Agents communicate through files in `.agent/`. Read the previous phase's
  file before doing anything.
- Never invent a plan. Read `.agent/plan.md`.
- Agents do not message each other. The main thread carries every handoff.

## Conventions
- Reachability verdicts must carry their evidence — the call path, or the
  reason none was found. A bare true/false is not an acceptable output.
- New dependencies go in the plan first, then `requirements.txt`. Pin them.

## POINTERS — read on demand, do not inline
- Decisions and rationale: `DECISIONS.md`
- Diagrams: `agent_docs/`

