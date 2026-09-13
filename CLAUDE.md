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
Docker, a distributed task broker/queue, capped retries-with-backoff,
per-user quotas, cost accounting, content-hash caching, DB-stored prompt
versioning, CI-gated prompt changes, live LLM API integration.
These are in DECISIONS.md as intent. Check the code before claiming any
of them work — in a README, docstring, commit message or comment.

Phase 2 U3/U4 (`src/reachability/triage/agent_loop.py`,
`src/reachability/triage/sandbox.py`, `src/reachability/triage/stub_llm.py`)
are built: a stub-LLM tool-calling loop over `search_symbol`/`find_callers`/
`resolve_import`, a hard tool-call budget, and a `sandbox_untrusted_text`
injection-resistance boundary live from its first commit. The LLM in that loop
is a deterministic stub (`stub_llm.py`), never a live API call — do not
describe this as a working agent against a real model.

Phase 2 U5 (`src/reachability/triage/job_runner.py`) is built: `POST
/v1/triage` dispatches `run_triage_job` via FastAPI's `BackgroundTasks`,
chaining `acquire_source` → `build_repo_index` → `run_triage_loop` and
mutating `TRIAGE_DB[triage_id]` through `QUEUED → RUNNING →
COMPLETED/FAILED`; `GET /v1/triage/{triage_id}` returns the widened record
including `finding`/`error`. This is real, working end-to-end HTTP wiring —
not a stub — but it is explicitly an **in-process** runner
(`asyncio`/`BackgroundTasks`), never a distributed broker/queue, and
`TRIAGE_DB` is still an in-memory dict with no persistence across a process
restart.

Phase 2 U6 (`src/reachability/agent/eval_harness.py`) is built: `run_eval_suite()`
runs U3/U4's full agent loop (`run_triage_loop`, not a direct
`compute_reachability` call) over all 22 fixtures in `tests/fixtures/l5/`
(reused, no new corpus), gated by six pre-registered gates
(`agent_docs/U6_EVAL_PROTOCOL.md`): G1/G2/G3/G5/G6 hard, G4 reported-only.
`src/reachability/agent/prompt_registry.py` + `prompts/v1/*.md` are file-based
prompt-versioning scaffolding (`PROMPT_VERSION = "v1"`, `load_prompt()`) — genuinely
inert today, not read by `stub_llm.py` or `agent_loop.py` (a static test in
`tests/test_eval_harness.py` guards this), since the agent loop is still a
deterministic stub with no prompt-reading code path. Do not describe the eval
harness as CI-gating prompt changes against a real model — it gates a stub loop's
verdicts, file-based, not wired into any CI pipeline.

Phase 3 (`src/reachability/db/` — `models.py`'s `TriageJob`/`Base`,
`session.py`'s lazily-configured `DATABASE_URL`-backed engine/sessionmaker,
`repository.py`'s `create_job`/`get_job`; `src/reachability/triage/worker.py`;
`src/reachability/triage/reaper.py`) is built: `main.py`'s in-memory
`TRIAGE_DB` dict and `BackgroundTasks` dispatch are gone entirely, replaced
by a Postgres-backed `triage_jobs` table (Alembic-migrated from its first
commit) and an independent polling worker (`python -m
reachability.triage.worker`) owning three explicit, independently-committing
transaction boundaries: claim (`SELECT ... FOR UPDATE SKIP LOCKED`,
demonstrated safe under real concurrent load by a dedicated stress test —
8 threads against 20 rows, 5 iterations), execute (`run_triage_job`, no
lock held), and finalize (guarded by a `worker_id`/`attempt_count` check so
a late, reaped-but-not-actually-dead worker's result can never clobber a
reclaiming worker's). A stale-job reaper (`reap_stale_jobs`) resets a
`running` job whose `updated_at` is older than
`TRIAGE_STALE_JOB_TIMEOUT_SECONDS` (default 300s) back to `queued`,
superseding DECISIONS.md §1's earlier "deferred to Phase 4" note for
dead-worker recovery — see DECISIONS.md §8. **Three accepted limitations,
stated plainly, not silently absorbed:** (1) no mid-execution heartbeat —
`updated_at` only moves at claim and finalize, so a single real job
legitimately running longer than the timeout gets reaped and reclaimed
while still alive (bounded duplicate work, not corruption, since the
finalize guard prevents a stale result from ever being applied); (2) this
design does not recover a genuinely *hung* (not crashed) worker when
exactly one worker process is running — recovery needs either a process
supervisor restarting a crash, or a second, independently-running worker
process; (3) an orphaned `tempfile.TemporaryDirectory` on a hard
SIGKILL/OOM crash is not cleaned up, a pre-existing possibility made
mechanically more frequent by this phase's designed crash-and-reclaim path.
A fourth risk — a malformed stored `target` or other failure outside
`run_triage_job`'s own guard crashing the whole worker process (review
finding, `.agent/review.md`) — is guarded, not merely documented:
`run_worker_once` finalizes such a job as `failed` instead of leaving it
`running`, and `main()`'s loop itself never dies from a residual exception
(e.g. inside `finalize_job` or `reap_stale_jobs`) — it logs and keeps
polling. The one narrow remaining gap: a failure raised by `finalize_job`
itself (rather than by building the request or running the job) is not
retried as a second finalize call, since that could fail for the same
reason — that row is left `running` for the reaper's timeout to recover,
same as any other crash-during-execute case.

AST index (`agent_docs/PHASE1_AST_INDEX.md`): L1 (module discovery + import
map), L2 (symbol table — functions, classes, methods, nested functions,
module-level callable aliases, decorator/base resolution), L3 (call
edge extraction — every `ast.Call` resolved to a `CallEdge` with
`caller_id`/`callee_id`/`confidence`/`resolution_rule`, per the table in
section 3), and L4 (entrypoint detection — `if __name__ == "__main__"`,
`[project.scripts]`, route/celery/CLI decorators, `test_*`/pytest-fixture
functions — plus BFS-based reachability verdicts and the
`search_symbol`/`find_callers`/`resolve_import` query layer), and L5
(a 22-fixture adversarial measurement corpus + `scripts/measure_l5.py`,
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

