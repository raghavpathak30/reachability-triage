# Demo: talking points and timing

`scripts/demo.sh` runs a narrated demo of the compose stack. The human records it;
the script only prints and waits. Needs Docker with Compose v2 and no API key.

```
bash scripts/demo.sh                 # paced, ~2-3 minutes
DEMO_PACE=0 bash scripts/demo.sh     # no pauses, for a quick check
```

It exits non-zero on a failed job, a poll timeout, or a verdict other than the one
each case expects, and it always tears the stack down (`docker compose down -v`).

## Say this first, and again at the end: this is STUB mode

- The demo runs in **stub mode** (`TRIAGE_LLM_MODE=stub`): the LLM client is a
  deterministic policy stub (`DeterministicPolicyStubLLMClient`), **not a real
  model**. Do not describe any verdict in it as coming from a model.
- What is real in both modes: the static index and call-graph analysis
  (`build_repo_index`, `compute_reachability`, tool dispatch). The verdict comes
  from that analysis, not from model prose.
- The script prints `llm_mode` next to every verdict it shows. Point at it.

## Timing map (default pace, approximate)

| time | segment | what to say |
|---|---|---|
| 0:00 | intro | the question: is the vulnerable function reachable from the package's entrypoints? Stub mode, what is real |
| 0:40 | stack up | Postgres, migrations, API, worker, web under compose; offline fixture wheels, no network. Allow 20-60 s for the image build and start |
| 1:10 | case 1, reachable | fixture 02: entrypoint to sink in three hops. The verdict carries the call path as evidence |
| 1:35 | case 2, not_reachable | fixture 09: the module is never imported. The reason is "no call path"; this is the noise a version scanner cannot filter |
| 2:00 | case 3, unknown | fixture 13: `getattr` with no literal name. The analysis cannot rule the function out, so it says `unknown` |
| 2:30 | why unknown | a wrong confident `not_reachable` is the worst outcome; every LLM failure also degrades to `unknown` |
| 2:45 | caveats | stub mode; local only; real-model injection resistance unclaimed |

## Caveats to state honestly

- Stub mode only. The real-model path exists (`TRIAGE_LLM_MODE=groq`) but is not
  what this demo runs.
- Real-model injection resistance is **unclaimed**: in the latest real-model run
  (Phase 9, `agent_docs/PHASE9_RESULTS.md`) 1 of 9 adversarial runs finished, with no
  manipulation, and 6 ended in `budget_exceeded`. Budget exhaustion is not counted as
  resistance (`DECISIONS.md` §13, §17).
- The three packages are fixture repos packed as offline wheels
  (`scripts/build_fixture_wheels.py`), chosen from `tests/fixtures/l5/` (02, 09, 13).
  Fixture 13's label allows `unknown` or `reachable`; the stub gives `unknown`.
- Local only: no authentication, no deployment, no hosted endpoint.
- The worker healthcheck proves database connectivity only; job-processing
  liveness is proven by `scripts/smoke_compose.sh`.

## UI walkthrough (browser)

`scripts/demo.sh` is the non-browser equivalent of the same three cases. For the
browser, use the same offline fixture wheels (they are not on PyPI):

```
python3 scripts/build_fixture_wheels.py --out <DIR>
FIXTURE_WHEELS_DIR=<DIR> docker compose -f docker-compose.yml -f docker-compose.smoke.yml up -d --build --wait
```

Open `http://127.0.0.1:8080` (`WEB_PORT` changes it). In the form, enter Version `1.0`,
Target module `pkg.sink`, Target symbol `vulnerable`, and one Package per case:

| Package | Expected on the job page (stub mode) |
|---|---|
| `reach-fx-02` | Reachable, a 3-edge call path |
| `reach-fx-09` | No path found, with the reason "no call path found from any entrypoint to ..." (`src/reachability/index/reachability.py:234`) |
| `reach-fx-13` | Unknown; the last path edge is a low-confidence `dynamic_dispatch` edge |

Every job shows the LLM mode "stub (deterministic policy, not a real model)". Fixture
13's label allows `unknown` or `reachable` (`tests/fixtures/l5/13_getattr_dynamic_dispatch/label.json`);
the demo and smoke scripts check for exactly `unknown`, so treat `reachable` as a
mismatch with the expected stub result.

While a job is queued or running, the heading, the Package row (`package==version`) and the
Target row show what you submitted, taken from the `target` field of
`GET /v1/triage/{id}` (`web/src/routes/JobPage.tsx:126-141`, `DECISIONS.md` §21). Before
the first response arrives they read "Checking".

Tear down with `docker compose -f docker-compose.yml -f docker-compose.smoke.yml down -v`.

`agent_docs/screenshots/demo.gif` - to be recorded. The file does not exist yet.
