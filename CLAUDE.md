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
content-hash caching, DB-stored prompt versioning, the eval harness.
These are in DECISIONS.md as intent. Check the code before claiming any
of them work — in a README, docstring, commit message or comment.

AST index (`agent_docs/PHASE1_AST_INDEX.md`): L1 (module discovery + import
map), L2 (symbol table — functions, classes, methods, nested functions,
module-level callable aliases, decorator/base resolution), and L3 (call
edge extraction — every `ast.Call` resolved to a `CallEdge` with
`caller_id`/`callee_id`/`confidence`/`resolution_rule`, per the table in
section 3) are built, all in `src/reachability/index/`. Not built: L4
(entrypoint detection, BFS, query layer), L5 (fixture corpus +
measurement), and `.reachability/index.json` serialization. No
reachability verdicts exist yet — nothing computes
`reachable`/`not_reachable`/`unknown`; L3 produces edges only, no
traversal or entrypoint concept.

L3-specific gaps, not deferred to a named unit, documented in
`.agent/plan.md`'s Out of scope section: calls inside a `lambda` body, a
call sitting directly in a class body outside any method, and
`cls.method()`/`super().method()` (treated as unresolved, not MRO) are
never extracted as edges; `self.method()` MRO resolution is
declaration-order DFS over bases, not true C3 linearization.

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

