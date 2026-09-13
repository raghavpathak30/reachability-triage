# Phase 3 — Job Persistence

Drop this in `agent_docs/PHASE3_PERSISTENCE.md`. It is the spec the `/loop` planner reads.

---

## 0. What Phase 3 decides

Phase 2 U5 wired `POST /v1/triage` → `run_triage_job` end-to-end, but `TRIAGE_DB`
(`main.py:26`) is a plain `dict[uuid.UUID, dict]`, and dispatch is FastAPI
`BackgroundTasks` running inside the same process and the same request's ASGI
lifecycle. A process restart loses every job. There is exactly one worker
(implicitly, the request-handling process itself), so nothing has ever been
asked what happens when a worker dies mid-job.

Phase 3 answers one question:

> Can a triage job survive the process that queued it — persisted in Postgres,
> claimed by an independent polling worker, and recovered automatically if that
> worker dies mid-job — without ever corrupting a job's recorded state or
> silently double-reporting a result?

It does **not** build a distributed broker, retries-with-backoff, live LLM
integration, per-user quotas, cost accounting, or SSRF hardening on
`repo_url`. Those stay exactly where CLAUDE.md's NOT BUILT section and
DECISIONS.md's deferrals already put them — see §5/§6. It **does** pick up
one item DECISIONS.md §1 previously deferred to "Phase 4 (Worker Pool)":
dead-worker recovery and stale-job timeouts. See §4 for why that deferral is
being superseded now rather than honored.

**The dangerous errors Phase 3 must not introduce, in order:**

1. A job silently corrupted or double-processed after a worker crash — the
   exact failure class this phase exists to prevent. A stale, slow-but-not-
   actually-dead worker finishing late must never clobber a result a second
   worker already recorded for the same job.
2. A stale-job reaper that reclaims a job still being legitimately worked on.
   Reclaiming too aggressively causes two workers to run the same job
   concurrently, which is exactly how (1) happens if the finalize path isn't
   guarded against it.
3. A migration that silently drops or corrupts data — a destructive schema
   change checked in without its `downgrade()` path ever having been run and
   verified.
4. `SELECT ... FOR UPDATE SKIP LOCKED` claiming the same row twice under
   concurrent workers. This defeats the entire reason for moving off
   `BackgroundTasks`, and must be demonstrated safe under real concurrent
   load, not assumed from reading the Postgres docs.
5. A DB-unavailable environment causing DB-dependent tests to silently skip
   rather than fail loud. CLAUDE.md already requires "a collection error is
   a failure" and a full bare `pytest` run as the source of truth for test
   counts; a `postgres`-gated skip would quietly reintroduce exactly the kind
   of undercount that rule exists to prevent.

---

## 1. Storage / data model

Postgres, via SQLAlchemy 2.x declarative models, migrated with Alembic —
both move from CLAUDE.md's NOT BUILT list to BUILT (see §5). One table,
`triage_jobs`, replacing `TRIAGE_DB` entirely (not alongside it — see U2).

```python
# src/reachability/db/models.py
import uuid
from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TriageJob(Base):
    __tablename__ = "triage_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # App-generated (uuid.uuid4()) before insert, per DECISIONS.md §2 -- the
    # pre-allocation rationale (202 + Location header before any DB round
    # trip) still holds; Postgres never generates this column's value.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    target: Mapped[dict] = mapped_column(JSONB, nullable=False)
    finding: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped["datetime"] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped["datetime"] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped["datetime | None"] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reaped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_triage_jobs_status",
        ),
    )
```

**Loose-dict record → column mapping** (current shape documented in
`main.py:24-26`'s comment and job_runner.py's read/write sites):

| Dict key | Column | Notes |
|---|---|---|
| `id` | `id` | Same UUID, same pre-allocation timing |
| `status` | `status` | Plain `String`, not a Postgres native `ENUM` type — see rationale below |
| `target` | `target` | Already a JSON-serializable dict (`main.py:149-162`); stored as-is |
| `finding` | `finding` | `TriageFinding` dataclass → JSON via `pydantic.TypeAdapter(TriageFinding).dump_python(finding, mode="json")` before insert, and `.validate_python(row.finding)` to reconstruct on read. Same mechanism DECISIONS.md §7 already verified works for `CallEdge.file: Path` → JSON string round-tripping through `TriageOut` |
| `error` | `error` | Unchanged: `_sanitize_error`'s output string |
| *(new)* | `created_at`, `updated_at`, `claimed_at`, `worker_id`, `attempt_count`, `reaped_count` | Did not exist on the dict record. Needed for the claim/finalize/reap lifecycle (§3) |

**Why `status` is a plain `String` with a `CHECK` constraint, not a Postgres
native `ENUM` type:** `job_runner.py`'s own docstring already commits to
plain string literals over `main.TriageStatus` "so no runtime import of
`main` is needed at all" (`job_runner.py:65-70`). A native Postgres `ENUM`
requires an `ALTER TYPE ... ADD VALUE` migration (non-transactional pre-PG12,
still awkward after) for every future status value; a `CHECK` constraint is
a plain column-constraint migration like any other. Same values enforced,
cheaper to evolve.

**Migration strategy:** `alembic init alembic` at the repo root (`alembic.ini`,
`alembic/env.py`, `alembic/versions/`). `alembic/env.py` reads `DATABASE_URL`
from the environment (same variable the app and worker use — see §3, U1) and
sets `target_metadata = Base.metadata` so `alembic revision --autogenerate`
stays usable for later columns. The **first** revision is written by hand
(not autogenerated against an empty DB, to keep the initial migration
readable and intentional) and creates `triage_jobs` with every column and the
`CHECK` constraint above, plus an index on `(status, created_at)` for the
claim query's `ORDER BY`. It is committed from the start — "versioned from
the start," per the non-negotiable scope, not bolted on after the model
exists unmigrated.

---

## 2. Build order

Six units. U1 bundles Postgres test infrastructure with the schema/migration,
for the same reason Phase 2's U3/U4 were bundled: you cannot gate a migration
against a database that doesn't reproducibly exist yet, in CI or locally.
Every later unit's tests depend on U1's fixture existing first. Do not start
U2 before U1's gate passes; do not start U5 (the concurrency stress test)
before U3's claim/finalize logic exists; do not start U6 (CI wiring) before
U1-U5 all pass locally against the ephemeral cluster, since U6 is the point
where the "does this even run outside one developer's machine" question gets
answered for real.

---

## 3. Units

### U1 — Postgres test infrastructure + schema/migrations (one unit, two gates)

An ephemeral-cluster pytest fixture and the first Alembic migration are
built together because neither is independently testable without the other.
The fixture (`tests/conftest.py`, session-scoped): `initdb` into a
`tmp_path_factory`-provided temp dir, `pg_ctl start -o "-p <port> -c
listen_addresses=127.0.0.1 -c unix_socket_directories="` (TCP, per the
verified environment constraint — the scratchpad/tmp path is too long for a
Unix socket), a free port chosen by binding a throwaway `socket.socket()` to
port 0 and reading it back, connect via
`postgresql+psycopg://postgres@127.0.0.1:<port>/postgres`, run `alembic
upgrade head` programmatically (`alembic.command.upgrade`) against it once,
and `pg_ctl stop -m fast` at session teardown. A function-scoped fixture
truncates `triage_jobs` between tests (`TRUNCATE triage_jobs`) rather than
tearing down/rebuilding the schema per test.

If `DATABASE_URL` is already set in the environment when the fixture starts
(the CI-services-block case, see §5/U6's tradeoff), the fixture uses that
connection string as-is and skips `initdb`/`pg_ctl` entirely — one fixture,
two backing environments, no CI-only special-casing of the tests themselves.

**Gate (i) — schema round-trip.** Against the ephemeral cluster, `alembic
upgrade head` creates `triage_jobs` with exactly the columns and `CHECK`
constraint in §1. A direct insert/select through the SQLAlchemy model
round-trips every column's type correctly, including a populated `JSONB`
`target` and `finding`. `alembic downgrade base` followed by `alembic upgrade
head` again succeeds without error — the downgrade path is exercised at
least once, not just written and assumed.

**Gate (ii) — fixture reusability.** The ephemeral-cluster fixture starts
and stops cleanly across two independent test *files* in the same `pytest`
run (proving it is not accidentally single-use or leaking a port/process),
and a second, unrelated `pytest` invocation immediately afterward also
starts cleanly (proving `pg_ctl stop` actually released the port and any
lockfiles).

### U2 — Repository layer + FastAPI wiring (TRIAGE_DB removed, not shadowed)

`src/reachability/db/repository.py` provides `create_job(session, target:
dict) -> uuid.UUID`, `get_job(session, job_id: uuid.UUID) -> dict | None`
(returns the same loose-dict shape `TriageOut` already expects, with
`finding` deserialized via the `TypeAdapter` from §1), plus the
serialize/deserialize helpers. `src/reachability/db/session.py` exposes a
lazily-configured engine/sessionmaker read from `DATABASE_URL` at first use
(not import time, so tests can point it at the ephemeral cluster before any
connection is opened) and a test-only `reset_engine_for_tests()` hook.

`main.py`'s `TRIAGE_DB` dict (`main.py:26`) is deleted, not kept alongside
the DB as a cache — a second source of truth for job state is exactly the
kind of thing that causes (1) in §0's dangerous-errors list. `POST
/v1/triage` (`main.py:138-171`) no longer takes a `BackgroundTasks`
parameter or calls `background_tasks.add_task`; it opens a session, calls
`create_job`, commits, and returns `202` with the row's initial `queued`
state — dispatch is now exclusively the polling worker's job (U3). `GET
/v1/triage/{triage_id}` (`main.py:174-191`) calls `get_job` instead of
`TRIAGE_DB.get`.

`tests/test_main.py` and `tests/test_triage_job_lifecycle.py` (both import
`TRIAGE_DB` directly today, per `.agent/exploration.md`) drop that import
and their `clear_triage_db()` autouse fixture in favor of the U1
function-scoped DB-truncation fixture.

**Gate:** `POST /v1/triage` followed immediately by `GET
/v1/triage/{id}` (no worker running) returns `status: "queued"` — proving
persistence without any execution having happened, which `TRIAGE_DB`-backed
tests could never distinguish from "the background task just hasn't run
yet." Restarting the SQLAlchemy engine/session (simulating a process
restart, without touching the DB) and re-running `GET
/v1/triage/{id}` still returns the same record — proving survival across a
"process restart" in the one sense this phase can test it (a fresh
connection, not a fresh `initdb`).

### U3 — Polling worker: claim, execute, finalize (transaction boundaries are the design)

`src/reachability/triage/job_runner.py`'s `run_triage_job` is refactored
from `run_triage_job(triage_db, triage_id, request, budget)` to
`run_triage_job(record: dict, request, budget)`, mutating `record` in place
exactly as before — only the two-level `triage_db[triage_id]` indirection is
removed, since the worker (not a shared dict) now owns the record's
lifetime. This is a signature change only; the try/except/else body,
`EmptyRepoIndexError`, and `_sanitize_error` are untouched.

`src/reachability/triage/worker.py` is new and owns exactly three
transaction boundaries:

1. **Claim (one transaction, one commit):**
   ```sql
   WITH next_job AS (
     SELECT id FROM triage_jobs
     WHERE status = 'queued'
     ORDER BY created_at
     FOR UPDATE SKIP LOCKED
     LIMIT 1
   )
   UPDATE triage_jobs
   SET status = 'running', worker_id = :worker_id,
       claimed_at = now(), updated_at = now(),
       attempt_count = attempt_count + 1
   FROM next_job
   WHERE triage_jobs.id = next_job.id
   RETURNING triage_jobs.id, triage_jobs.target, triage_jobs.attempt_count;
   ```
   Committed immediately after this single statement. **If the worker
   crashes before this commits, the row was never claimed** — Postgres rolls
   the transaction back, the row is still `queued`, exactly as if nothing
   happened. No cleanup needed.

2. **Execute (no open transaction, no lock held):** the claimed row's
   `target` dict is turned back into a `TriageRequest`, and
   `run_triage_job(record, request, budget)` runs against an in-memory
   `record` dict built from the claimed row — the acquire/index/agent-loop
   chain (potentially minutes, over the network) never holds a database
   transaction or lock. **If the worker crashes here, the row is stuck
   `running` with a stale `updated_at`** — this is exactly what U4's reaper
   exists to detect and recover from.

3. **Finalize (one transaction, one commit, guarded):**
   ```sql
   UPDATE triage_jobs
   SET status = :final_status, finding = :finding_json, error = :error,
       updated_at = now()
   WHERE id = :id AND worker_id = :worker_id AND attempt_count = :claimed_attempt_count
   RETURNING id;
   ```
   The `worker_id`/`attempt_count` guard is the concrete anti-corruption
   mechanism: if this worker was reaped and the job reclaimed by another
   worker while this one was still (slowly, not dead) executing, `attempt_count`
   has already moved on and this `UPDATE` affects zero rows — a logged no-op,
   never an overwrite of a newer result. **This is what makes finalize
   idempotent under a late, non-dead worker**, distinct from what the reaper
   handles (a genuinely dead one).

`run_worker_once(session_factory, worker_id: str, budget: int) -> bool`
wraps steps 1-3 and returns whether a job was claimed (`False` on an empty
queue), so both the CLI loop and tests can drive exactly one unit of work
deterministically. `python -m reachability.triage.worker` runs `while True:
reap_stale_jobs(...); if not run_worker_once(...): sleep(poll_interval)` —
the reaper runs inline in the same process/loop as the claim loop, not as a
separate deployable, since nothing in this phase's scope needs more than one
moving part here (see §4).

**Gate:** a single claimed job runs to `completed` with `finding` populated,
using the same monkeypatched-`acquire_source` pattern
`tests/test_triage_job_lifecycle.py` already uses. Simulating "worker was
reaped mid-execution" (manually setting a claimed job's `attempt_count` to
`claimed_attempt_count + 1` before calling finalize) causes finalize to
affect zero rows and leaves the row's already-updated (by the reclaiming
worker) state untouched — never overwritten by the stale finalize call.

### U4 — Stale-job reaper (supersedes DECISIONS.md §1's Phase-4 deferral)

`src/reachability/triage/reaper.py`'s `reap_stale_jobs(session, timeout_seconds:
int) -> int` resets any `running` job whose `updated_at` is older than
`timeout_seconds` back to `queued`, clearing `worker_id`/`claimed_at` and
incrementing `reaped_count`:

```sql
WITH stale AS (
  SELECT id FROM triage_jobs
  WHERE status = 'running'
    AND updated_at < now() - make_interval(secs => :timeout_seconds)
  FOR UPDATE SKIP LOCKED
)
UPDATE triage_jobs
SET status = 'queued', worker_id = NULL, claimed_at = NULL,
    reaped_count = reaped_count + 1, updated_at = now()
FROM stale
WHERE triage_jobs.id = stale.id
RETURNING triage_jobs.id;
```

`FOR UPDATE SKIP LOCKED` in the reaper's own subquery, not just the worker's
claim query, matters for the same reason: it stops the reaper from blocking
on (or racing) a row a live worker's finalize `UPDATE` (U3, step 3) happens
to be touching at that exact instant.

**Timeout policy — concrete, not hand-waved, signed off by the user:**
`timeout_seconds` defaults to `300` (5 minutes), read from
`TRIAGE_STALE_JOB_TIMEOUT_SECONDS` (env var, overridable, not hardcoded so
ops can retune without a code change). 300 is derived from this repo's own
slowest *currently observed* real job path:
`tests/test_triage_job_lifecycle.py::test_resolvable_package_reaches_completed`
already polls for up to 180 seconds against a real PyPI download+index+agent-
loop chain. 300s gives roughly 1.7x headroom above that observed figure — a
fixed constant, not derived from a job-duration history table (that would be
new capability, a job-history/metrics feature out of scope here).

**Observability requirement added at sign-off:** every row `reap_stale_jobs`
resets emits a structured `logging.warning` carrying `job_id`,
`attempt_count`, and `elapsed_seconds` (time since that row's pre-reap
`updated_at`) — captured in the same `RETURNING` clause as the reset, before
`updated_at` is overwritten. This exists so a future retune of the 300s
constant is based on observed reap-time data, not the single 180s data point
this default itself was derived from.

**Accepted limitation, stated plainly:** `updated_at` is only bumped at claim
time and at finalize time (U3) — there is no mid-execution heartbeat. A
single real job whose acquire/index/agent-loop chain legitimately runs
longer than `timeout_seconds` will be reaped and reclaimed while still
genuinely alive, causing two workers to run the same job concurrently until
the original one eventually (uselessly) tries to finalize and no-ops against
U3's `attempt_count` guard. This is bounded, not unbounded, waste — the
guard prevents corruption, not duplicate work — and it is why the timeout is
generous (5 minutes) relative to every currently-observed job duration
rather than tight. Adding a mid-execution heartbeat would require a hook
into `run_triage_loop` (`agent_loop.py`), which DECISIONS.md's U3/U4
addendum already documents `_on_raw_tool_result` as explicitly *not* meant
to become production surface for — out of scope for this phase, noted here
so it isn't silently rediscovered later as an unexplained gap.

**Second accepted limitation: this design does not recover a genuinely
*hung* (not crashed) worker when exactly one worker process is running.**
`run_worker_once`'s loop is `while True: reap_stale_jobs(...);
run_worker_once(...)` — sequential and synchronous in one process. If that
one process is stuck inside `run_triage_job` rather than dead, its own loop
never returns to call `reap_stale_jobs` again, so nothing ever reaps the job
it's holding. Recovery in this scenario requires either a process supervisor
that restarts a crashed process (the crash case — a fresh process instance
reaps the abandoned job at its own next loop iteration, already covered
above) or a *second*, independently-running worker process whose own loop
keeps calling `reap_stale_jobs` regardless of what the first is doing. This
is materially different from the "legitimately slow, eventually finishes"
case above — a truly hung worker at N=1 has no path back to `queued` at all.
In practice, `acquire_source`'s pip-download step already bounds itself at
120s and `run_triage_loop` is a bounded 30-call deterministic stub, so most
hang vectors are already time-boxed, but `build_repo_index`'s AST parse over
an arbitrary extracted wheel has no stated timeout and remains a real (if
narrow) hang vector under this design. Deploying ≥2 worker processes is the
practical mitigation; this phase does not build a process supervisor or
enforce a minimum worker count.

**Third accepted limitation: an orphaned `tempfile.TemporaryDirectory` on a
hard crash (SIGKILL/OOM) is not cleaned up.** `job_runner.py`'s per-job
workdir uses `ignore_cleanup_errors=True`, which only suppresses exceptions
during a normally-executed `__exit__` — on a hard kill, `__exit__` never
runs at all and the directory is leaked on disk under `/tmp`. This was
already possible before Phase 3 (a crash was the only way to lose a job);
this phase's whole point is making worker crash-and-reclaim an expected,
designed recovery path, which mechanically increases how often this leak
occurs over a deployment's lifetime. No sweep/cleanup job is built for this
in Phase 3 — noted as an accepted limitation, not silently absorbed.

**Gate:** a `running` job with `updated_at` set to `now() - (timeout_seconds
+ 1)` is reset to `queued` with `reaped_count` incremented by exactly 1 and
`worker_id`/`claimed_at` cleared. A `running` job with `updated_at` inside
the threshold is untouched by the same call. Running the reaper twice in a
row against the same already-reaped job increments `reaped_count` only if
it has gone back through a full claim→stale cycle in between — not twice
for one staleness event.

### U5 — Concurrent-claim safety (stress test, not a unit test)

A dedicated test spins up `N` (8) worker threads against `M` (20)
pre-inserted `queued` rows in the U1 ephemeral cluster, each thread looping
`claim_next_queued_job` (the claim half of U3's `run_worker_once`) until it
returns `None`, recording every job `id` it personally claimed. Each thread
gets its **own** `Session` from a single shared `sessionmaker` bound to one
`Engine` sized with `pool_size >= N` — the `Engine` is thread-safe to share,
a `Session` is not, so no two threads ever touch the same `Session` object
(SQLAlchemy's own documented threading contract).

This is the one gate in this phase that cannot be satisfied by a
single-threaded unit test with mocked locking — it is the actual claim
mechanism, load-bearing dangerous-error #4 in §0, undemonstrated by anything
in U1-U4's own tests (which all exercise the claim query from one session at
a time).

**Gate:** across all `N` threads' recorded claims, the union covers all `M`
job ids exactly once each — no id missing (every queued job eventually
claimed), no id present in more than one thread's list (no double-claim).
Re-run the test multiple times in the same `pytest` session (a `for _ in
range(5)` outer loop reseeding `M` fresh rows each iteration) — a single
lucky pass under low contention would not be convincing on its own.

**The sanity check that proves this gate is not vacuous must remove the
entire locking clause, not just `SKIP LOCKED`.** Bare `FOR UPDATE` (no
`SKIP LOCKED`) already prevents double-claims in Postgres — a second
transaction blocks on the locked row, and once the first commits, Postgres
re-evaluates the CTE's `WHERE status = 'queued'` predicate against the row's
new value and filters it out, so claims serialize instead of double-claiming.
`SKIP LOCKED` is a throughput optimization over that already-correct
blocking behavior, not the source of the correctness guarantee — deleting
only those two words and leaving `FOR UPDATE` will not make this test fail,
and would falsely appear to validate dangerous-error #4 while proving
nothing. The clause must be removed in its entirety (no `FOR UPDATE` at all)
to actually exercise the failure mode this gate exists to catch.

### U6 — Dependencies, CI wiring, docs reconciliation

`requirements.txt` gains three pinned dependencies (listed in
`.agent/plan.md` before this unit starts, per CLAUDE.md's "new dependencies
go in the plan first" convention): `sqlalchemy`, `psycopg[binary]`,
`alembic`. `.github/workflows/tests.yml`, per sign-off, splits into two
required jobs, both backed by a `services: postgres:` block (the
self-managed `initdb`/`pg_ctl` ephemeral-cluster path is local-dev-only,
never used in CI): a `test` job running the full suite minus the
concurrency stress test, and a separate `concurrency` job running only
`tests/test_worker_concurrency.py`, in parallel with `test` — U5's stress
test is a required CI check, not a manual-only one. `CLAUDE.md`'s NOT BUILT
list and DECISIONS.md §1's "deferred to Phase 4" note are updated to
reflect §4/§5 below, following the same docs-sync pattern Phase 2 used
after U3/U4 and after U5 shipped.

**Gate:** a full bare `pytest` run from a clean clone (after `pip install -r
requirements.txt`) passes with the DB-dependent tests actually executing
(not skipped) — locally via the U1 ephemeral-cluster fixture, and in CI via
the `services: postgres:` block in both the `test` and `concurrency` jobs.
Test count is at or above the 177-test regression floor — **177 is a number
verified twice this session by running bare `pytest` against current `main`
HEAD ("177 passed", zero collection errors), not an estimate; see
`.agent/plan.md`'s "Success criteria (whole phase)" section for the
verification record.**

---

## 4. Disposition of backlog items

**Dead-worker recovery and stale-job timeouts — DECISIONS.md §1's "deferred
to Phase 4 (Worker Pool)" is superseded here, explicitly, not silently
contradicted.** That deferral was written when Phase 2 U5 shipped an
in-process `BackgroundTasks` runner with no independent worker concept at
all — there was no "worker pool" for a dead-worker scenario to apply to yet.
Phase 3 introduces the first independent worker process this project has
ever had, and per the current `/loop` scope handed to the planner for this
phase, stale-job recovery is explicitly **in Phase 3's remit now**, via U4's
reaper. This is a scope correction driven by the user's own instructions for
this loop, not a discovery that the original deferral was wrong — recorded
here, and DECISIONS.md gets a status note (U6, §3) rather than a silent
edit, following this project's own convention of marking superseded
decisions instead of rewriting history.

**Retries-with-backoff — still deferred, not the same thing as the reaper.**
U4's reaper resets a stale job to `queued` exactly once per staleness event
(tracked via `reaped_count`, not a smart retry budget); there is no backoff,
no max-retry cap, and no distinction between "reaped because the worker died"
and "reaped because the job is pathologically slow and will time out
forever." A job that keeps getting reaped and reclaimed and re-timing-out
will loop indefinitely under this phase's design. A retry cap (e.g. "give up
and mark `failed` after `reaped_count` exceeds N") is new policy, not
free from U4's implementation, and stays out of scope here — noted so a
later phase doesn't assume the reaper already solved this.

**Idempotency of `run_triage_job`'s own side effects — unchanged, still
sound.** `job_runner.py`'s existing per-job `tempfile.TemporaryDirectory`
scoping (decision 4 in its own docstring) already means a re-run (whether
from a fresh claim after a genuine crash, or a duplicate run under the
reaped-but-not-actually-dead scenario in U4) never contaminates another
job's workdir. Phase 3 does not change this; it is what made building U3's
claim/execute/finalize split safe to do without also auditing every acquire/
index/agent-loop side effect for cross-job leakage.

**SSRF hardening on `repo_url` — unchanged, still Phase 5.** Not touched by
persistence at all; `acquire_source` still fails fast on a `repo_url`
request exactly as U2 (Phase 2) left it.

---

## 5. NOT-BUILT reconciliation

| Item | Disposition | Reason |
|---|---|---|
| PostgreSQL | **Built** | §1 — `triage_jobs` table, real Postgres, no longer deferred |
| SQLAlchemy | **Built** | §1 — declarative models + Alembic-managed schema |
| Alembic / migrations | **Built** | §1, U1 — versioned from the first commit, `downgrade` exercised |
| Dead-worker recovery / stale-job timeouts | **Built** | U4 — supersedes DECISIONS.md §1's Phase-4 deferral, see §4 |
| Idempotent worker claim under concurrency | **Built** | U3/U5 — `SELECT ... FOR UPDATE SKIP LOCKED`, demonstrated under concurrent load |
| A distributed task broker/queue | Continue deferring | U3's worker polls one Postgres table directly — no RabbitMQ/Celery/SQS. Still the "no external queue/broker" scope this phase was given |
| Retries/backoff (smart, capped) | Continue deferring | See §4 — the reaper resets state, it does not implement a retry policy |
| Docker | Continue deferring | No deployment/sandboxing need addressed by this phase; unrelated to persistence |
| Live LLM API integration | Continue deferring | `DeterministicPolicyStubLLMClient` unchanged by this phase |
| Per-user quotas | Continue deferring | No auth/user model exists yet |
| Cost accounting | Continue deferring | U3's tool-call budget (Phase 2) remains a safety cap, not a ledger; unrelated to this phase |
| Multi-tenancy | Continue deferring | Single `triage_jobs` table, no tenant/org column, no auth |
| Replication / HA (Postgres) | Continue deferring | One ephemeral or dev-provided Postgres instance; no standby, no failover |
| Connection-pool tuning | Continue deferring | `pool_size` set only large enough for U5's concurrency test and normal worker/app usage; no load-tested tuning |
| `repo_url` acquisition / SSRF hardening | Continue deferring | DECISIONS.md §3 — still named Phase 5 |
| Content-hash caching | Continue deferring | Needs a content-addressed store; unrelated to job persistence |
| DB-stored prompt versioning | Continue deferring | Phase 2 U6's file-based versioning is unaffected |

---

## 6. What is explicitly not in Phase 3

A distributed task broker/queue (RabbitMQ, Celery, SQS, or similar) · smart
retries/backoff with a capped retry policy · Docker · live LLM API
integration · per-user quotas · a cost-accounting ledger · multi-tenancy ·
Postgres replication/HA/failover · connection-pool performance tuning beyond
what U5's concurrency test needs · a mid-execution worker heartbeat (see
U4's accepted limitation) · `repo_url` acquisition / SSRF hardening ·
content-hash caching · DB-stored prompt versioning · any change to
`agent_loop.py`, `sandbox.py`, `stub_llm.py`, or `index_adapter.py`'s own
logic (only `job_runner.py`'s outer signature changes, per U3) · a
job-duration-history table to auto-derive the reaper timeout (see U4 — the
300s default is a fixed constant, explicitly not this).

If a `/loop` iteration proposes any of these, the critic rejects the plan.
