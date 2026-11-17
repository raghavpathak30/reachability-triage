# S1 real-app measurement plan (pre-registered before any S1 code)

Measurement only. Nothing is tuned to change these numbers. Nothing from the apps is committed.

## Snapshot
`~/osv-snapshots/osv-pypi-2026-11-12.zip`, sha256 `c7ea4e15b83912960fc5cbd42c6003dd630e422ec4dfbfc4ed19445573e1b08c`. Never re-downloaded.

## Apps
| App | Commit | Lockfile | Scan directory and flags |
|---|---|---|---|
| Flask tutorial (flaskr) | `pallets/flask` `examples/tutorial` @ `49943b5` | unpinned `flask`; a pinned `requirements.txt` is generated for measurement only, kept under `/tmp/app-scan-research/`, never committed | `/tmp/app-scan-research/flaskr` |
| FastAPI template | `fastapi/full-stack-fastapi-template` @ `aabb901` | `uv.lock`, workspace member `backend` (package name `app`), 64 runtime entries, `dkimpy` sdist-only | `/tmp/app-scan-research/fastapi-template`, no `--member` (manifest selects the single member) |
| open-webui | `open-webui/open-webui` @ `b0e6098e940942281d9c83f3529ea117287a3a22` | `uv.lock` | `/tmp/spike/open-webui`, no `--member` (same directory and flags as the Day 1 spike, `agent_docs/spike-2026-11-17.md`) |

## Numbers recorded per app
1. inventory size (pinned rows)
2. advisories matched
3. wheels fetched, and `no_wheel` with the reason (for example download cap)
4. combined modules and combined edges
5. combined index build time
6. per-query time, median and max
7. peak RSS
8. findings by verdict and by reason code
9. every `path_proven` finding in full
10. any dist cut by the 150-dist cap or the fetch budget, by name
11. open-webui only: cold-cache and warm-cache wall time, reported separately
12. open-webui only: confirmation that pypdf, pyjwt and anyio are inside the 150-dist limit (if any is cut, report it, do not tune)

## open-webui additional checks
- Which of the 5 pre-registered pairs (`s1_openwebui_expected.json`) came out `path_proven`, and the engine reason for each that did not.
- For every `path_proven`, whether the final callee is that advisory's own true symbol (spike file) or a function changed by a different advisory in the same diff. pyjwt reported separately.
- If open-webui exceeds the fetch budget, SCAN_BUDGET or memory, that is recorded as a result.

## Thresholds
Only two:
- G1: zero `not_reachable` anywhere in any output, checked on the `verdict` field with `jq`.
- Each run finishes or ends in `SCAN_BUDGET` (analysis budget) with a stated cut.

No threshold on counts of findings, `path_proven`, or time.
