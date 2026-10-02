# Project: reachability-triage

## STABLE — do not edit mid-session (cache-critical)
Edits here do nothing until `/clear`, `/compact`, or restart.

- Stack as SHIPPED: Python, FastAPI, uvicorn, pydantic. Single `main.py`.
- Package manager: pip + venv (`requirements.txt`) — always this, never switch
- Test command: `pytest` (bare, full run)
- Lint/typecheck command: none configured
- Run command: `uvicorn main:app --reload`
- Build command: `docker compose build`
- Do not touch: `.agent/`, `.venv/`, `__pycache__/`
- Success criteria for any change: the app starts clean, no debug prints

## NOT BUILT — do not describe these as existing
A distributed task broker/queue, capped retries-with-backoff **at
the job-lifecycle level** (a failed/errored triage job is not automatically
re-run — Phase 5 U2's retry-with-backoff is scoped to a single
`GroqLLMClient` API call, not the job as a whole, see the Phase 5
paragraph below), per-user quotas, cost accounting **as a system**
(budgets, alerts, cross-job aggregation — Phase 5 persists a per-job
token/cost/model/prompt-version record, which is a narrower thing, see
below), DB-stored prompt versioning, CI-gated prompt changes.
These are in DECISIONS.md as intent. Check the code before claiming any
of them work — in a README, docstring, commit message or comment.

Live LLM API integration and content-hash caching, previously listed
here, are now built — see the Phase 5 paragraph below and
`DECISIONS.md` §12.

Phase 2 U3/U4 (originally `src/reachability/triage/agent_loop.py`,
`src/reachability/triage/sandbox.py`, `src/reachability/triage/stub_llm.py`)
built a stub-LLM tool-calling loop over `search_symbol`/`find_callers`/
`resolve_import`, a hard tool-call budget, and a `sandbox_untrusted_text`
injection-resistance boundary live from its first commit. **Superseded by
Phase LangGraph's Unit 6 cutover** (see the Phase LangGraph paragraph
below): the hand-rolled loop module itself is deleted, and its
tool-schema/dispatch/target-matching logic now lives in
`src/reachability/triage/tool_dispatch.py`, imported by
`src/reachability/triage/langgraph_loop.py`. The guarantees these two
units established are still true today, just implemented differently: the
hard tool-call budget and the `sandbox_untrusted_text` injection-resistance
boundary both still hold. The LLM in that loop
is a deterministic stub (`stub_llm.py`), never a live API call — do not
describe this as a working agent against a real model.

Phase 2 U5 (`src/reachability/triage/job_runner.py`) is built: `POST
/v1/triage` dispatches `run_triage_job` via FastAPI's `BackgroundTasks`,
chaining `acquire_source` → `build_repo_index` → `run_triage_loop_langgraph` and
mutating `TRIAGE_DB[triage_id]` through `QUEUED → RUNNING →
COMPLETED/FAILED`; `GET /v1/triage/{triage_id}` returns the widened record
including `finding`/`error`. This is real, working end-to-end HTTP wiring —
not a stub — but it is explicitly an **in-process** runner
(`asyncio`/`BackgroundTasks`), never a distributed broker/queue, and
`TRIAGE_DB` is still an in-memory dict with no persistence across a process
restart.

Phase 2 U6 (`src/reachability/agent/eval_harness.py`) is built: `run_eval_suite()`
runs the full triage agent loop (`run_triage_loop_langgraph`, not a direct
`compute_reachability` call) over 30 fixtures across two directories — the
original 22 in `tests/fixtures/l5/` (reused) plus 8 new fixtures added by Phase 4
in `tests/fixtures/l5_phase4/` — gated by six pre-registered gates
(`agent_docs/PHASE4_EVAL_PROTOCOL.md`, which supersedes
`agent_docs/U6_EVAL_PROTOCOL.md`'s historical 22-fixture numbers): G1/G2/G3/G5/G6
hard (G2's floor is now 6 of 7, up from U6's original 4 of 5), G4 reported-only.
`src/reachability/agent/prompt_registry.py` + `prompts/v1/*.md` are file-based
prompt-versioning scaffolding (`PROMPT_VERSION = "v1"`, `load_prompt()`) — genuinely
inert today, not read by `stub_llm.py` or `langgraph_loop.py` (a static test in
`tests/test_eval_harness.py` guards this), since the agent loop is still a
deterministic stub with no prompt-reading code path. Do not describe the eval
harness as CI-gating prompt changes against a real model — it gates a stub loop's
verdicts against fixture labels, file-based, not a live model.

Phase 4 (`agent_docs/PHASE4_EVAL_HARNESS.md`, `agent_docs/PHASE4_EVAL_PROTOCOL.md`)
is built: the eval corpus grew from 22 to 30 fixtures (8 new adversarial fixtures,
`21`-`28`, in a new sibling directory `tests/fixtures/l5_phase4/`, each probing a
D1/D3 trade-off or a `DECISIONS.md` §5.3 AST-shape gap that no existing L5
fixture exercised), and `run_eval_suite()` (`scripts/run_eval_suite.py`) is now
wired into CI as a new, required `eval` job in
`.github/workflows/tests.yml`, parallel to `test`/`concurrency`, with no
`services: postgres:` block since the eval harness never touches
`DATABASE_URL`. `tests/fixtures/l5/`, `scripts/measure_l5.py`, and
`agent_docs/L5_PROTOCOL.md` are untouched by this phase — the sibling-directory
design keeps `measure_l5.py`'s own dynamically-computed G2 pool mechanically
unaffected. Whether the `eval` job actually blocks a merge depends on this
repo's branch-protection required-status-checks list, a server-side GitHub
setting this file does not control.

Phase LangGraph (`src/reachability/triage/langgraph_loop.py`) is built: as
of this phase's Unit 6 cutover, the triage loop runs on a LangGraph
`StateGraph`, not the hand-rolled Python loop Phase 2 U3/U4 originally
shipped. This is an explicit, goal-driven architecture change, not a bug
fix or a gap closure — `.agent/investigation.md`'s original analysis
recommended **deferring** a LangGraph migration, and that recommendation
was knowingly overridden; see `agent_docs/PHASE_LANGGRAPH.md` §0 for the
full rationale for why. `src/reachability/triage/tool_dispatch.py` now
holds the shared tool-schema/dispatch/target-matching surface (`TOOL_SCHEMAS`,
`AgentLoopError`, `_is_well_formed_tool_call`, `_dispatch_tool`,
`_confirmed_id_matches_target`) both the old and new loops used; the old
loop module itself is gone, not deprecated. There is no backend-selection
flag left — `job_runner.py`, `eval_harness.py`, and `src/reachability/agent/__init__.py`
all call `run_triage_loop_langgraph` unconditionally. Unit 5's parallel-run
parity harness proved the two loops agreed byte-for-byte on verdict/path/
reason across all 30 eval fixtures before the old loop was deleted, and
this cutover's own post-deletion `scripts/run_eval_suite.py` re-run
confirmed the same 30/30 result against the sole remaining implementation.

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

Phase 5 (`src/reachability/triage/groq_llm.py`, `llm_errors.py`,
`llm_config.py`, `llm_cache.py`) is built: `job_runner.py` now
constructs a real `GroqLLMClient` (Groq, model pinned in `llm_config.py`)
whenever `TRIAGE_LLM_MODE` is unset or `groq` (Phase 7 made this
mode-selected, no longer unconditional — see the Phase 7 paragraph below;
`job_runner.py:110-130`) — this is a **second
implementation of the unchanged `StubLLMClient` Protocol**
(`stub_llm.py:95-98`, byte-for-byte untouched), not a widened interface.
`DeterministicPolicyStubLLMClient` still exists and still drives the
CI-blocking eval gate's stub lane; with `TRIAGE_LLM_MODE` unset,
production jobs do not construct it (with `stub`, they do). A typed error taxonomy (`llm_errors.py`: timeout,
rate-limited, transport, refusal, truncated, malformed-response) degrades
every failure class to `Verdict.UNKNOWN` with a preserved reason string,
never a wrong confident verdict — timeout/rate-limited/transport retry up
to 3 times with backoff at the single-API-call level only (not the job
level, see NOT BUILT above); refusal/malformed/truncated never retry.
`triage_jobs` gained `token_count`/`total_cost`/`model_string`/
`prompt_version` columns and a new, Postgres-backed
`llm_response_cache` table (content-hash-keyed on prompt version + model +
the literal system-prompt/tool-schema text + conversation context — this
last part was a post-review fix, see DECISIONS.md §12(e); the original
key omitted the literal prompt text and could have gone stale across a
future in-code prompt edit without bumping `PROMPT_VERSION`), bypassable
via `TRIAGE_LLM_CACHE_DISABLED`. The production client swap
(`job_runner.py`) was made citing a real-model injection-resistance gate
(`agent_docs/PHASE5_INJECTION_REAL_MODEL.md`) that was **not met**: zero
observed injection wins, but no real-model adversarial run ever completed
an investigation (0 of 3 reproduced in Phase 8 — two `budget_exceeded`, one
`llm_malformed_response`; the clean 30-fixture baseline finished 16 of 30,
with 14 of 30 ending in `llm_malformed_response` final answers,
`DECISIONS.md` §16; Phase 9 U1 now accepts the exact `functions.` tool-name
prefix, `groq_llm.py:82,493-496,537`), and budget exhaustion is not
counted as resistance (§13). **Real-model injection resistance is
unclaimed.** A new, non-blocking
`eval-real` CI job (`.github/workflows/tests.yml`) runs the same
30-fixture eval corpus against the real client — `workflow_dispatch`-only,
never on push/PR, behind a required-reviewers GitHub Environment not yet
created as of this merge (repo-admin action needed, along with adding the
`GROQ_API_KEY` secret) — with only G1 (zero false `not_reachable`) hard;
the existing, unchanged `eval` job remains the sole CI-blocking gate. See
DECISIONS.md §12 for the full unit-by-unit rationale and what this phase
does and does not close.

Phase 7 (`Dockerfile`, `docker-compose.yml`, `docker-compose.smoke.yml`,
`scripts/smoke_compose.sh`, `scripts/demo.sh`, `agent_docs/DEMO.md`) is
built: a local Docker Compose stack (`db`, one-shot `migrate`, `api`,
`worker`) that runs with no Groq key, because compose sets
`TRIAGE_LLM_MODE=stub` (`DeterministicPolicyStubLLMClient`, a deterministic
policy, **not a real model**). The code default when the variable is unset
is still `GroqLLMClient`; any other value fails the job, and the worker at
startup (`job_runner.py:110`, `worker.py:246`). The static index and
call-graph analysis are real in both modes. `triage_jobs.llm_mode`
(migration 0004) records which mode produced each verdict and
`GET /v1/triage/{id}` returns it. `scripts/smoke_compose.sh` (CI job
`compose-smoke`) runs three offline fixture wheels (02/09/13) through the
stack and checks verdicts against `label.json` allowed sets. This is local
only: no auth, no deployment. Still NOT BUILT: job-level retries, quotas,
cost accounting as a system, a worker heartbeat (the worker healthcheck
proves database connectivity only). See `DECISIONS.md` §15.

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
query target — see DECISIONS.md §4 for why that's an accepted trade-off.
A code-review-found gap once raised here — an intermediate attribute-chain
segment of an unrelated call (e.g. the `probe` in `toolbox.probe.execute()`)
leaking into D3's escape set — is closed: DECISIONS.md §5.3's narrowing of
`_LoadOutsideCallCollector` already excludes any Name/Attribute that is not
itself a value-bound expression at an assignment or call-argument position,
which excludes exactly this shape; Phase 4's fixture 21
(`agent_docs/PHASE4_EVAL_HARNESS.md`,
`tests/fixtures/l5_phase4/21_attribute_chain_segment_escape/`) empirically
confirms this AST shape now resolves `not_reachable`, not `unknown`.

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

