# reachability-triage

An async service that triages dependency vulnerabilities by checking whether the
vulnerable code is actually reachable from your codebase — instead of reporting
every advisory that matches a version range.

**Status:** in development. Not usable yet.

## Why

A scanner that matches versions tells you a package you depend on has a CVE. It
doesn't tell you whether your code ever calls the vulnerable function. Most of
what a version-matching scanner reports is noise, and triaging that noise by hand
is where security teams lose their week. This service does that triage.

## Scope

Fixed on 18 Aug 2026. Not revisited.

- Python only.
- Direct imports and direct calls, transitively, across modules.
- Dynamic dispatch (`getattr`, `eval`/`exec`, module-level `__getattr__`,
  callback-by-reference) is modeled, not ignored: where it cannot be resolved to a
  confident call target, the verdict is `unknown`, never a false `not_reachable`.

Anything outside this line is out of scope, permanently. The analyzer is not the
point of the project; the agent, the evals and the injection work are.

## Stack

Python · FastAPI · Pydantic · pytest · PostgreSQL · SQLAlchemy · Alembic ·
Docker · Docker Compose · GitHub Actions · AWS EC2

## Repo

- `main.py` — the API
- `src/reachability/index/` — AST index (see `agent_docs/PHASE1_AST_INDEX.md`). L1
  (module discovery + import map), L2 (symbol table), L3 (call edge extraction), L4
  (entrypoint detection, BFS, reachability verdicts, query layer), and L5 (fixture
  corpus + measurement) are built.
- `src/reachability/triage/` — the triage agent (see `agent_docs/PHASE2_TRIAGE_AGENT.md`).
  Source acquisition (`acquire_source`), the index-pipeline adapter
  (`build_repo_index`), the stub-LLM agent loop (`run_triage_loop`) over
  `search_symbol`/`find_callers`/`resolve_import` with a hard tool-call budget and
  an injection-resistance boundary (`sandbox_untrusted_text`) live from its first
  commit, are built. `worker.py` (the polling worker) and `reaper.py` (the
  stale-job reaper) — see `agent_docs/PHASE3_PERSISTENCE.md` and
  `DECISIONS.md` §8 — now own job execution, replacing the earlier
  in-process `job_runner.py`/`BackgroundTasks` job lifecycle entirely (that
  code is removed, not kept alongside). Real LLM integration is not built.
- `src/reachability/agent/` — the eval harness (see `agent_docs/PHASE2_TRIAGE_AGENT.md`
  U6). `run_eval_suite()` runs the full agent loop (not a direct
  `compute_reachability` call) over 30 fixtures across two directories — the
  reused 22-fixture `tests/fixtures/l5/` corpus plus 8 new fixtures in
  `tests/fixtures/l5_phase4/` (`agent_docs/PHASE4_EVAL_HARNESS.md`) — gated by six
  pre-registered gates (`agent_docs/U6_EVAL_PROTOCOL.md`). `prompt_registry.py` +
  `prompts/v1/*.md` are file-based prompt-versioning scaffolding, not yet consumed
  by the (still-stub) agent loop.
- `src/reachability/db/` — Postgres persistence (see `agent_docs/PHASE3_PERSISTENCE.md`
  and `DECISIONS.md` §8). `POST`/`GET /v1/triage` (`main.py`) now read/write a real,
  Alembic-migrated `triage_jobs` table; `python -m reachability.triage.worker`
  is an independent process that claims a queued job (`SELECT ... FOR
  UPDATE SKIP LOCKED`, demonstrated safe under real concurrent load),
  executes it, and finalizes it across three separately-committing
  transactions; `reaper.py` reclaims a job whose worker crashed
  mid-execution. Not built: a distributed broker/queue (the worker polls
  one Postgres table directly), capped retries-with-backoff, and a
  mid-execution worker heartbeat.
- `DECISIONS.md` — dated design decisions and their reasoning

## L5 measurement

`tests/fixtures/l5/` is a frozen, adversarial 22-fixture corpus with hand-derived
ground-truth labels (see `agent_docs/L5_PROTOCOL.md` for the pre-registered gates).
Run it with:

```
make measure-l5
```

or directly:

```
python scripts/measure_l5.py
```

It builds the real index for each fixture, checks the resulting verdict against
its label, and exits nonzero if a hard gate (G1/G2/G3/G5) fails. Results are
written to `results/l5_<git-sha>.json`.

## U6 eval harness

`scripts/run_eval_suite.py` drives 30 fixtures — the reused `tests/fixtures/l5/`
corpus (22) plus 8 new fixtures in `tests/fixtures/l5_phase4/`
(`agent_docs/PHASE4_EVAL_HARNESS.md`) — through the full agent loop
(`run_triage_loop`, stub LLM) instead of a direct index call — see
`agent_docs/PHASE4_EVAL_PROTOCOL.md` for the six pre-registered gates gating the
30-fixture corpus (G1/G2/G3/G5/G6 hard — G2's floor is 6 of 7 — G4
reported-only; `agent_docs/U6_EVAL_PROTOCOL.md` describes the historical
22-fixture run only).
It is now a required CI check (the `eval` job in
`.github/workflows/tests.yml`), not just a local/manual command. Run it
locally with:

```
python scripts/run_eval_suite.py
```

Results are written to `results/eval_<git-sha>.json`.
