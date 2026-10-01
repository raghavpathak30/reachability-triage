# reachability-triage

A local, async service that triages dependency vulnerabilities by checking whether
the vulnerable code is actually reachable from the package's own entrypoints —
instead of reporting every advisory that matches a version range.

**Status:** runs locally under Docker Compose. No authentication, no deployment,
no hosted endpoint. Read [Limitations](#limitations) before trusting a verdict.

## Why

A scanner that matches versions tells you a package you depend on has a CVE. It
doesn't tell you whether any code path ever calls the vulnerable function. Most of
what a version-matching scanner reports is noise, and triaging that noise by hand
is where security teams lose their week. This service does that triage for one
named symbol at a time: it says whether the symbol is reachable, and attaches the
evidence — the call path, or the reason none was found — never a bare true/false.

## Architecture

```
POST /v1/triage ──► triage_jobs (Postgres) ◄── polling worker
                                                   │
   acquire source (wheel-only pip download) ───────┤
   static AST index + call graph (build_repo_index)│   ← real, deterministic
   triage loop (LangGraph StateGraph)              │
     LLM client ⇄ tools: search_symbol / find_callers / resolve_import
     tool results pass through the sanitize node (sandbox_untrusted_text)
   verdict = compute_reachability(...)  ───────────┘   ← sole source of the verdict
```

- The **static index** (`src/reachability/index/`) is real in every mode: it parses
  the package, builds a symbol table and call graph, detects entrypoints, and
  computes one of four verdicts — `reachable`, `reachable_only_from_tests`,
  `not_reachable`, `unknown` — with a call path or a reason.
- The **triage loop** (`src/reachability/triage/langgraph_loop.py`) lets an LLM client
  drive the index's query tools under a hard tool-call budget. Untrusted repo text
  crosses into the LLM's context only through `sandbox_untrusted_text`. The final
  verdict comes from `compute_reachability`, not from the model's prose.
- **`unknown` is the safe default.** Dynamic dispatch (`getattr` with no literal
  name, `eval`/`exec`, callbacks passed by reference) resolves to `unknown`, and
  every LLM failure class (timeout, rate limit, refusal, malformed or truncated
  response, budget exhaustion) degrades to `unknown` with a reason. A wrong
  confident `not_reachable` is the failure this project is built to avoid.

### LLM modes

The LLM client is chosen per job by `TRIAGE_LLM_MODE`
(`src/reachability/triage/job_runner.py`, `resolve_llm_mode`):

| value | client | needs a key |
|---|---|---|
| `stub` | `DeterministicPolicyStubLLMClient` — a deterministic policy, **not a real model** | no |
| `groq` | `GroqLLMClient` — the real model (Groq, `openai/gpt-oss-20b`) | `GROQ_API_KEY` |
| unset | same as `groq` (the code default) | `GROQ_API_KEY` |

`docker compose` sets `stub` by default so it works with no key. Any other value
fails the job, and the worker refuses to start. Every job record and
`GET /v1/triage/{id}` response carries `llm_mode` (`stub` or `groq`) so a stub verdict
can never be mistaken for a real-model verdict.

## Quickstart

Requires Docker with Compose v2. No API key needed.

```
git clone git@github.com:raghavpathak30/reachability-triage.git
cd reachability-triage
cp .env.example .env
docker compose up -d --wait
curl -s -X POST "http://127.0.0.1:${API_PORT:-8000}/v1/triage" -H 'Content-Type: application/json' -d '{"package":"six","version":"1.16.0","target_module":"six","target_symbol":"raise_from"}'
docker compose down -v
```

The `six` example finishes `not_reachable` in stub mode, with reason `no entrypoints detected in repository`: the `six` wheel has no entrypoints of its own (no `__main__` block, console script or route), so nothing inside it reaches `raise_from`. See [Limitations](#limitations).

`cp .env.example .env` is optional. The POST returns HTTP 202 and a job `id`; fetch
the result with:

```
curl -s "http://127.0.0.1:${API_PORT:-8000}/v1/triage/<id>"
```

The port defaults to 8000 (bound to 127.0.0.1 only); set `API_PORT` if it is taken.
In the compose stack the worker runs on the stub LLM, so any real PyPI package can
be triaged by `package` + `version` (wheel-only: the worker downloads a wheel with
`pip download --only-binary=:all:`), but the result is a **stub** verdict — check
`llm_mode` in the response. The smoke test and the demo do not touch PyPI: they feed
the worker offline wheels built from three fixture repos (`scripts/build_fixture_wheels.py`
and `docker-compose.smoke.yml`).

Verify a checkout end to end (commit first: it tests the committed HEAD in a fresh
clone, then tears everything down):

```
bash scripts/smoke_compose.sh
```

The worker healthcheck proves database connectivity only; job-processing liveness is proven by scripts/smoke_compose.sh.

`bash scripts/demo.sh` runs a narrated three-verdict demo on the same stack (see
`agent_docs/DEMO.md`).

To use the real model instead, set `TRIAGE_LLM_MODE=groq` and `GROQ_API_KEY` in
`.env`. See the results below before relying on it.

## Results

Stub-lane and real-model results are different things. They are kept apart on
purpose, and the first says nothing about the second.

### Stub eval (deterministic policy client, not a real model)

`python scripts/run_eval_suite.py` runs the full triage loop over 30 fixtures with
`DeterministicPolicyStubLLMClient`. Last recorded run: commit `4e46d56` (local run; result file
`results/eval_4e46d56de19600867f5b8031b0a7e64954626b41.json`, gitignored, reproduce with the
command above), 30 of 30 fixtures pass,
G1/G2/G3/G5/G6 PASS, overall PASS (G4 is reported-only: `unknown_count=0`). This is
the required `eval` CI job. It gates a stub loop's verdicts against hand-labelled
fixtures; it does not gate prompts or a real model. The compose smoke test
(`compose-smoke` CI job) additionally checks three fixture verdicts over HTTP.

### Real-model runs (Groq `openai/gpt-oss-20b`)

**Real-model injection resistance is unclaimed.** The real-model injection suite
(`scripts/run_injection_suite_real.py`, `agent_docs/PHASE5_INJECTION_REAL_MODEL.md`)
has never had a run in which the model completed an investigation on an adversarial
fixture. Latest run (`DECISIONS.md` §14): **0 of 3 completed** — two ended in
`budget_exceeded`, one in `llm_malformed_response`. Budget exhaustion is not counted
as resistance (`DECISIONS.md` §13).

- **Rate limit (TPM), fixed.** Earlier runs died on the account's 8000
  tokens-per-minute cap. Phase 6b added an opt-in wait-on-rate-limit and an
  `llm_request_too_large` cause (`DECISIONS.md` §14); 14 real 429s were waited out and
  no run terminated on a rate limit.
- **Budget, open.** With the rate limit out of the way, the model still does not reach
  a final answer within the tool-call budget on the adversarial fixtures. Whether
  clean fixtures also exhaust it is unmeasured.
- **Malformed response, open.** On one fixture the model answered with text instead
  of a tool call; the raw reply was not recorded.

The production default for an unset `TRIAGE_LLM_MODE` is still the Groq client
(`src/reachability/triage/job_runner.py`, `resolve_llm_mode`), which was swapped in
under Phase 5 with this gate not met. Every failure class degrades to `unknown`, but
the model's behaviour under injection is not established.

## Limitations

- Local only: no authentication, no TLS, no hosted deployment, no per-user quotas.
- Source acquisition is wheel-only `package` + `version`. `repo_url` is rejected as
  unsupported. A package with no `.py` modules (pure native) fails the job.
- Python only; direct imports and calls, transitively. Dynamic dispatch resolves to
  `unknown`, and that check is corpus-wide, so it over-flags rather than under-flags.
- Reachability is measured from entrypoints detected in the acquired package
  itself (`__main__` blocks, console scripts, route/CLI decorators, tests). A
  library wheel with none (for example `six`) yields `not_reachable` with the
  reason `no entrypoints detected in repository` — that is a statement about the
  package's own entrypoints, not about how your application calls it.
- A failed job is not automatically re-run (only a single LLM API call is retried).
- The worker has no mid-execution heartbeat; a hung single worker is not recovered.
- Real-model injection resistance is unclaimed (see Results).

## Repo

- `main.py` — the API
- `src/reachability/index/` — AST index (see `agent_docs/PHASE1_AST_INDEX.md`). L1
  (module discovery + import map), L2 (symbol table), L3 (call edge extraction), L4
  (entrypoint detection, BFS, reachability verdicts, query layer), and L5 (fixture
  corpus + measurement) are built.
- `src/reachability/triage/` — the triage agent (see `agent_docs/PHASE2_TRIAGE_AGENT.md`).
  Source acquisition (`acquire_source`), the index-pipeline adapter
  (`build_repo_index`), the triage loop (`langgraph_loop.py`, a LangGraph
  `StateGraph` — `DECISIONS.md` §10) over `search_symbol`/`find_callers`/
  `resolve_import` with a hard tool-call budget and a sanitizing boundary
  (`sandbox_untrusted_text`). `worker.py` (the polling worker) and `reaper.py` (the
  stale-job reaper) own job execution (`agent_docs/PHASE3_PERSISTENCE.md`,
  `DECISIONS.md` §8); `job_runner.py` runs one job and picks the LLM client from
  `TRIAGE_LLM_MODE`. `groq_llm.py`'s `GroqLLMClient` is a second implementation of
  the same `StubLLMClient` Protocol, with a typed error taxonomy that degrades every
  failure class to `Verdict.UNKNOWN` and a Postgres-backed response cache
  (`DECISIONS.md` §12). `DeterministicPolicyStubLLMClient` (`stub_llm.py`) drives
  the CI-blocking eval gate's stub lane and the compose default.
- `src/reachability/agent/` — the eval harness (`agent_docs/PHASE2_TRIAGE_AGENT.md`
  U6, `agent_docs/PHASE4_EVAL_PROTOCOL.md`). `prompt_registry.py` + `prompts/v1/*.md`
  are file-based prompt-versioning scaffolding, not consumed by the agent loop —
  `GroqLLMClient`'s system prompt is a hardcoded constant; only `PROMPT_VERSION` is
  recorded, as per-job metadata.
- `src/reachability/db/` — Postgres persistence (`agent_docs/PHASE3_PERSISTENCE.md`,
  `DECISIONS.md` §8). Not built: a distributed broker/queue (the worker polls one
  Postgres table), job-level capped retries-with-backoff, a worker heartbeat.
- `Dockerfile`, `docker-compose.yml`, `docker-compose.smoke.yml`, `.env.example` —
  the local stack (`db`, one-shot `migrate`, `api`, `worker`).
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

## Eval harness

`scripts/run_eval_suite.py` drives 30 fixtures — the reused `tests/fixtures/l5/`
corpus (22) plus 8 new fixtures in `tests/fixtures/l5_phase4/`
(`agent_docs/PHASE4_EVAL_HARNESS.md`) — through the full triage agent loop
instead of a direct index call — see `agent_docs/PHASE4_EVAL_PROTOCOL.md`
for the six pre-registered gates (G1/G2/G3/G5/G6 hard — G2's floor is 6 of 7 —
G4 reported-only; `agent_docs/U6_EVAL_PROTOCOL.md` describes the historical
22-fixture run only). It is a required CI check (the `eval` job in
`.github/workflows/tests.yml`). Run it locally with:

```
python scripts/run_eval_suite.py
```

Results are written to `results/eval_<git-sha>.json` (gitignored — `results/` holds
run-specific output that may contain org identifiers; see `DECISIONS.md` §11, §15).

**Dual-lane eval (Phase 5 §12).** The command above runs the default,
CI-blocking **stub lane**. A separate `--lane=real` **real lane** runs the same
30 fixtures against the real `GroqLLMClient`; only G1 (zero false
`not_reachable`) is hard on that lane, the rest are reported-only, and it only
ever runs via the `eval-real` GitHub Actions job (`workflow_dispatch`-only, never on
a normal push/PR), never CI-blocking:

```
python scripts/run_eval_suite.py --lane=real
python scripts/diff_eval_lanes.py   # per-fixture stub-vs-real disagreement report
```
