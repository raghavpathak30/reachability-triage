# reachability-triage v1.0.0 — final push plan (2026-11-16, after council)

Research: `reports/Python CVE reachability tool gap.md`. Council record: `agent_docs/council/v1-final-push-plan-2026-11-16.md`.

## Where the code disagrees with your summary
- Latest tag is **v0.4.0**, not v0.3.5. Bare `pytest` = **871 passed, 3 deselected**, not ~216.
- The app scan is **partly built**. A1a (lockfile inventory + pinned-OSV matcher, CLI) is merged. A1b is waiting at its [GATE]. Wheel fetch, target symbols, call graph and evidence levels exist only as a spec.
- Your summary expects `scan` to say `not_reachable`. **DECISIONS §26 forbids that for scans.** The output is `reachable` + a call path, or `unknown` + an evidence level.
- **No Gemini anywhere in the repo.** Only `GROQ_API_KEY` is referenced.
- The `eval-real-gate` environment and the GROQ secret are **confirmed missing** on GitHub.
- No `pyproject.toml`, no `LICENSE`, no `CITATION.cff`. The PyPI name `reachability-triage` is free.

## A. Positioning
**The gap, bluntly.**
- **Paid tools own it.** Python function-level reachability is a solved *paid* category: Endor, Snyk (GA), Socket/Coana (Enterprise), Checkmarx, Mend, Semgrep (per-CVE rules).
- **No free, adopted tool does it.**
  - OSV-Scanner's Python work is an import-level prototype and has stalled.
  - pip-audit and `uv audit` only match versions.
  - GitHub withdrew its Python beta; its new reachability preview is JS/TS only.
  - dep-scan's documented verdict is per package.
- **Features are not ownable.** About ten tiny 2026 repos already claim the same feature list, so "first" and "features" are gone.
- **Real threat:** Astral lists reachability on the `uv audit` roadmap.
- **What is unowned is data and proof.**
  - 2 of 14,236 live PyPI advisories carry machine-readable symbol data.
  - 0 of 6,873 GitHub-reviewed pip advisories list vulnerable functions.
  - No function-level Python reachability benchmark exists. VEX-Bench has 20 Python cases, labelled per project.
- **The gap is small as a product and moderate as a data/research contribution.**
- Because the scan never says `not_reachable`, the tool re-orders findings; it does not suppress them. It cannot sell "what you don't need to patch".

**Pitch:** "A free, offline scanner for Python apps that shows the exact call path to every vulnerable dependency function it can prove your code calls, and says why for every one it can't. It never marks a CVE safe."

**Headline README claim** — write it only after dogfood, with real counts, never a percentage:
> "In N real apps, it found K CVEs your code provably calls. Each comes with a call path, checked by hand against the function the fix changed. Everything else is `unknown`, with the reason."

**True today:** "Matches your lockfile offline against a pinned, hash-checked OSV PyPI snapshot."

**Strategy:** be the dataset that `uv audit` and OSV would need. Upstream high-confidence symbols to `pypa/advisory-database` `ecosystem_specific.imports`.

## B. Done = v1.0.0 (each item verifiable)
1. **Install.** `uvx reachability-triage scan ./app` works from PyPI in a clean `python:3.13-slim` container (transcript committed). A CI job installs the built wheel and scans an offline miniature fixture.
2. **Inputs.** `uv.lock` (including a workspace member), `poetry.lock` and a fully pinned `requirements.txt`, each covered by G5 tests.
   - A bare `pyproject.toml` is refused, with a hint to lock it. There is no resolver (D2).
   - A missing lockfile is a loud result, not an empty pass.
3. **Advisories.** `db update` downloads the OSV PyPI zip from a fixed HTTPS host with size and member caps, writes it atomically, and records date + sha256. `--snapshot-sha256` pins it. Every report prints the date and hash.
4. **Pipeline:**
   - wheel fetch with the security core: allowlisted hosts, hash checked before extraction, archive caps, unparseable file → Gap;
   - combined index;
   - targets: wheel diff plus code-form names;
   - app-only start points;
   - four evidence levels plus the closed reason registry;
   - malicious packages listed first;
   - a failing dependency becomes a Gap, never a crashed scan.
5. **No LLM.** The scan path imports no LLM code; a test checks that `groq` and `langgraph` are absent from `sys.modules`.
6. **Output and controls:**
   - ranked text output with control characters stripped;
   - `--format json` with `schema_version`;
   - exit codes: 0 = scan done, 1 = findings at or above `--fail-on`, 2 = tool error;
   - flags: `--offline`, `--max-deps`, `--timeout` (deterministic truncation order), `--verbose` (per-phase time and memory);
   - a stale-cache warning.
7. **No `not_reachable`** in any scan output (test + grep). Stated as a design rule, never as an accuracy figure.
8. **Eval.** The §4 miniature lane is a required CI job (`scan-eval`). The real-wheel lane has been run and recorded (G1–G8).
9. **Dogfood** on 4–5 real apps at pinned commits, including one ML/numpy-heavy app and one with no lockfile.
   - The rules are pre-registered in DECISIONS before any run.
   - Every `path_proven` is hand-checked against `true_symbols`.
   - The match set is compared with osv-scanner.
   - Results are reported as counts.
10. **Dataset v0.1:**
    - 25 library advisories mapped to true symbols, with provenance (fix commit, hunk, confidence, rater);
    - the sample seed committed before labelling;
    - labels frozen by commit hash before the tool is ever run on those advisories;
    - app cases: the §4 fixtures plus the verified dogfood findings;
    - CC-BY-4.0, a datasheet, CITATION.cff, and a Zenodo DOI separate from the tool.
11. **One product.** The service (FastAPI, Postgres, LangGraph, web) gets a final tag `v0.4-service-final` and moves to an archived branch. Main ships a CLI-only package whose only core dependency is `packaging`, upper-pinned because `inventory.py:19` uses a private API. The semver promise covers JSON `schema_version` and CLI flags only.
12. **README:**
    - a one-screen quickstart;
    - a 10 s GIF (vhs tape committed);
    - a results table with methodology;
    - limitations;
    - a dated comparison table;
    - every claim backed by file:line or a results file.
13. **Housekeeping.** LICENSE (Apache-2.0), SECURITY.md, CHANGELOG, a green bare `pytest` (count reported), and the CLAUDE.md NOT BUILT list rewritten for the CLI.

**Cut to 1.0.1 or later:**
- SARIF + the GitHub Action, with fork-PR hardening (`pull_request` only, least permissions, SHA-pinned actions);
- Pipfile.lock / pylock.toml;
- A3 (`/v1/scans`), A7 (and the L3 exception), A8 (LLM ranking), A10, OpenVEX;
- frontend redesign, auth;
- the real-model injection claim (stays UNCLAIMED);
- the eval-real environment and secret, the 10 s SIGALRM, an authenticated healthcheck;
- the arXiv paper (week 2–3; needs a second rater, so recruit one now);
- 50+ labels.

## C. Day plan — 10 working days. 7 is not realistic.
**Why not 7.** S1 alone folds three spec units, and A1a alone took a research day plus a 48-commit day. The measured pace is about one medium unit per evening.

**Calendar.**
- Day 1 is Tue Nov 17 and Day 10 is Fri Nov 27; Sunday off.
- Tag v1.0.0 on Day 9.
- **Launch Tue Dec 1**, not Thu Nov 26 (Thanksgiving) or Fri Nov 27.

**Labelling** runs alongside the build at about 1.25 h/day on Days 1–8. 25 labels × 25 min ≈ 10 h.

| Day | Units (scope → gate, hours) | Merge/tag | Biggest risk → fallback |
|---|---|---|---|
| 1 Tue 17 | **Spike** (2.5 h, below). **A1b**: approve the existing gate *minus the Pipfile/pylock steps*; uv workspaces + `ValueError` fix only (1.5 h). Commit the dataset sample seed. | merge A1b | Spike finds fewer than 5 paths → pivot to C (§C′) |
| 2 Wed 18 | **S1** plan → critique → gate; implement starts (4.5 h) | — | — |
| 3 Thu 19 | **S1** finishes. Gate: E1 `path_proven` → true symbol; G1; crafted-wheel tests; real-wheel run on flaskr + FastAPI recorded (4.5 h) | merge, v0.5.0 | Not merged by end of Day 3 → dogfood drops to 3 apps and no performance work. The security core is never cut. |
| 4 Fri 20 | **S2a** closure + evidence levels 2–4 + reason registry + budgets. Gate: E2, E4, E4w, T1–T7, T9 (4.5 h) | merge | Namespace/RECORD edge cases → Gap + `closure_complete=false` |
| 5 Sat 21 | **S2b** code-form `named` targets + high/medium confidence. A target cap only, no ≤1 s/query goal. Gate: the 5 R§4.3 diffs; vanna's "ask" not named; 30/30 stub eval unchanged (4.5 h) | merge | Django-style bundled fixes inflate targets → cap + `targets_truncated` |
| Sun 22 | off | | |
| 6 Mon 23 | **S3** §4 eval corpus + required `scan-eval` CI; real-wheel lane (4 h) | merge, v0.6.0 | A G3 failure: the phase does not close until you choose fix or amendment (spec rule) |
| 7 Tue 24 | **S4** product shell: pyproject + console script, `db update` with caps, JSON schema, exit codes, `--offline/--max-deps/--timeout/--verbose`, service archived + tag, `packaging` upper pin, LICENSE, built-wheel CI. Publish 1.0.0rc1 (5 h) | merge, rc1 | CLI import drags in fastapi/sqlalchemy → the archive removes it; a test enforces this |
| 8 Wed 25 | **Dogfood** (pre-registered): 4–5 apps incl. one ML app + one with no lockfile. Hand-check every `path_proven`. **S5** fixes crashers only (5 h) | merge fixes | A big app over budget → record SCAN_BUDGET as-is, never tune to the results |
| 9 Thu 26 | Labels frozen → compute target-extraction metrics. Datasheet, CITATION.cff, Zenodo. **S6 docs**: README, GIF, results, limitations, comparison; claims audit. Tag **v1.0.0**, trusted-publish to PyPI, fresh-container check (5 h) | **v1.0.0** | An unbacked claim → delete the claim; don't move the tag |
| 10 Fri 27 | Buffer for the S1 overrun. Then the blog draft, post texts, and a pypa/advisory-database PR with 5–10 high-confidence `imports` entries | — | — |
| Tue Dec 1 | Launch (D) | | |

**C′. If the spike fails** (fewer than 5 paths), the plan changes to:
- Days 2–6: S1 + S2a only (the scanner reports evidence levels; `path_proven` stays rare).
- Days 3–8: labels raised to 50, plus the app-case set.
- Day 9: release dataset v1.0 + scanner 0.9.
- The headline becomes the dataset and the "why static Python reachability can't say unreachable" finding.

**Day 1 spike** (measurement, not a /loop unit; any Claude session or by hand, 3 h time-box):
1. Pre-register in `.agent/spike.md` before running anything: the app list, the counting rule, and the threshold (≥5 → plan; 1–4 → plan, dataset-led headline; 0 → C′).
2. Pick 6–8 public apps with a committed pinned lockfile and record each commit SHA. Candidates: full-stack-fastapi-template, healthchecks, paperless-ngx, saleor, netbox, mealie, superset, pretix, open-webui. Keep the first that qualify.
3. Run `scripts/scan_inventory.py` on each against the reference snapshot. Record matched non-malicious advisory groups per app.
4. For each matched *library* advisory that names a function or has a fix commit:
   - get the true symbol from the fix commit;
   - hand-trace whether app code reaches it through **explicit calls only** (what S1 can see);
   - separately count pairs reachable only via a property or framework call (what A7 would add).
5. Paste the counts into `.agent/spike.md`. The S1 gate cites them in DECISIONS §29.

## D. Launch (Tue Dec 1, ~8:30 am US Eastern = evening IST)
- **Show HN.** "Show HN: Reachability-triage – which CVEs in my Python app does my code actually call?" Stay in the thread 3–4 h; the first comment gives the method, the limits and the dogfood table.
- **Blog post** (own site + dev.to): "I tried to build govulncheck for Python. Here's why it can never say 'not reachable'". Uses the measured Flask `request.form` and psycopg-from-a-URL cases plus the dataset.
- **r/Python**, using the Showcase template:
  - What My Project Does;
  - Target Audience;
  - Comparison.
- **r/netsec:** the blog post only, as a finding, not an ad.
- **r/devops / r/devsecops:** at 1.0.1, when the Action lands.
- **Newsletters:**
  - tl;dr sec;
  - PyCoder's Weekly;
  - Python Weekly;
  - Latio Tech (reviews reachability tools).
- **OSV/PyPA:**
  - the advisory-database PR with symbols;
  - a substantive data comment on osv-scanner#2225;
  - a discuss.python.org Security thread.
- **Trust builders:**
  - fully local; no account, key, LLM or telemetry;
  - a pinned snapshot hash;
  - a reproducible dogfood script with app commits;
  - open labels with provenance;
  - published negative results;
  - a one-dependency install;
  - trusted-publishing provenance.
- **Put-offs:**
  - "90% noise reduction" claims;
  - percentages built on tiny counts;
  - an LLM anywhere near the verdict;
  - code upload;
  - an AI-slop README;
  - an unexplained wall of `unknown`. Lead with the evidence levels.
- **Unrealistic part of the goal:** "widely starred and cited in a week". Stars depend on one HN roll; citations take months and need the paper. Realistic: a verified, installable tool plus a DOI'd dataset that the uv/OSV folks have a reason to look at.

## E. Day 1 /loop prompts
1. **A1b** (already planned and critiqued, so no new prompt). At its gate, reply:
   > Approved with a scope cut: drop the Pipfile.lock and pylock.toml steps (plan steps for them, their fixtures, and their G5 rows). Keep the uv workspace roots, `--member`, `app_root`, the precedence change limited to the three A1a formats, the ValueError fix, the real-app check (64 entries) and DECISIONS §28 (state the two formats moved to 1.0.1).
2. **S1** (start after the spike and the A1b merge): paste the prompt below.

---

Branch: `app-scan-s1-thin-slice` from main after the A1b merge. Move the old `.agent/` out of the repo to `~/agent-archive/agent-app-scan-a1b-<timestamp>` and `mkdir .agent`. Copy `backlog.md` from the archive into the new `.agent/` (read-only reference; do not work the NITs).

# Goal

Build the thinnest end-to-end app scan on real dependency trees, first, before
anything is deepened: inventory → OSV match → wheel fetch → combined index →
changed-symbol targets → call-path query from the app's own start points →
JSON + text output. This is the riskiest part of v1.0 (`agent_docs/PHASE_APP_SCAN.md`
A2 + a minimal A4 + a minimal A5), and it must show what real apps produce
before evidence levels, the eval corpus or packaging are built.

The only verdicts this slice can print are `reachable` /
`reachable_only_from_tests` with `evidence_level: path_proven` and a path, or
`unknown` with a reason. Never `not_reachable` (DECISIONS §26, D3).

# Context

- A1a/A1b are built: `src/reachability/scan/inventory.py` (`build_inventory`,
  statuses, `app_root` for uv workspaces) and `src/reachability/scan/advisories.py`
  (snapshot loader, groups, matcher, fixed-in). CLI today:
  `scripts/scan_inventory.py <dir> --snapshot <zip>`.
- Spec: `agent_docs/PHASE_APP_SCAN.md` §2.3 (A, S, owners, dist_of), §2.4
  (changed set, stable key `(module, qualname, ordinal)`, version-only filter,
  test-module exclusion), §2.8 budgets, §3 A2/A4/A5, §4 E1 row (frozen).
- Measured facts: `agent_docs/research/APP_SCAN_RESEARCH.md` §4.3 (5 wheel diffs,
  recall 5/5, `@lineno` phantom pairs), §5.2-5.3 (combined index method: app
  top-level packages + every wheel's top-level packages copied into one root,
  then unmodified `build_repo_index`; flaskr 0.81 s build, 0.23-0.26 s/query;
  FastAPI 13.2 s build, 3.9-4.9 s/query; library entrypoints must be excluded,
  901 polluting start points), §5.6.
- Engine: `compute_reachability` (`src/reachability/index/reachability.py`)
  can return `NOT_REACHABLE`. The scan must map it to `unknown` with reason
  `NO_PATH_FOUND`; it must never pass it through.
- Real apps (same as the research, read-only, cloned outside the repo if
  missing): Flask tutorial = `pallets/flask` `examples/tutorial` @ `49943b5`
  (unpinned `flask`; use a pinned `requirements.txt` generated for measurement
  only and kept under `/tmp/app-scan-research/`, never committed);
  FastAPI template = `fastapi/full-stack-fastapi-template` @ `aabb901`
  (`uv.lock`, workspace member `backend`, 64 runtime entries, `dkimpy` sdist-only).
- Snapshot: `~/osv-snapshots/osv-pypi-2026-11-12.zip` (sha256 `c7ea4e15…08c`).
  Never re-download.

# Plan change to record first (DECISIONS §29, before any code)

"v1.0 is a CLI-only package" (council record
`agent_docs/council/v1-final-push-plan-2026-11-16.md`):
- A3 (`/v1/scans`, tables, worker), A7, A8 and A10 are deferred past 1.0.
- SARIF, the GitHub Action and the Pipfile.lock/pylock.toml parsers go to 1.0.1.
- The FastAPI/Postgres/LangGraph/web service is archived at release, in S4.
- Caches live on disk, not Postgres.
- Wheels are fetched through the PyPI JSON API with stdlib `urllib`, not a
  `pip` subprocess, because `uvx` environments have no pip.
- Units S1 (this one), S2a, S2b and S3 replace A2/A4/A5/A6, in that order.
- §4's frozen eval rules are unchanged.
- Record the Day 1 spike result (`.agent/spike.md`, pasted) and the threshold
  it was judged against.

Raghav approves this section at the GATE.

# Do

0. **Pre-register before any graded code** (`tests/fixtures/app_scan/eval/prereg/`):
   E1 miniature expectations from §4 (already frozen), and the real-app
   measurement plan: the two apps, commits, lockfile, snapshot hash, and the
   exact list of numbers to record (step 8). No pass threshold on real-app
   counts other than G1 and "the run finishes or ends in `SCAN_BUDGET`".

1. **Fetch** (`src/reachability/scan/fetch.py`). For each `pinned`,
   marker-true inventory row: PyPI JSON API `/<name>/<version>/json`; pick a
   wheel in this order: the lock's own wheel URL+hash if present (uv.lock,
   pylock), else `py3-none-any`, else a wheel whose tags match `MARKER_ENV`
   (cp313, manylinux, x86_64), else `no_wheel`.
   **Security core (council 2026-11-16, non-negotiable):**
   - Download only over HTTPS from `pypi.org` (JSON) and
     `files.pythonhosted.org` (files). A lock URL on any other host →
     `no_wheel` with the host named. A PR controls the lockfile.
   - Verify sha256 **before** opening the archive: the lock hash when
     present, else PyPI's digest. A mismatch → `no_wheel`, never extracted.
   - Archive rules per A2: absolute, `..`, drive, backslash and symlink or
     hardlink entries → reject the wheel. Caps: 100 MB download, 200 MB
     extracted (streaming byte counter, not declared sizes), 20,000 members,
     5 MB per `.py` file. Only `.py` files and `*.dist-info/RECORD` are
     written.
   - Every extracted `.py` file is pre-parsed with `ast.parse` on the scan
     side. `SyntaxError`, `RecursionError`, `MemoryError` or `ValueError` →
     the file is left out of the combined root and recorded as
     `Gap{unparsed_file}`. The index is unchanged; `build.py:20` catches only
     `SyntaxError`.
   Network only in this module, behind an injectable fetcher so tests use
   local wheels. Disk cache keyed by `(name, version, sha256)` under a cache
   dir argument; writes are atomic (temp dir + rename).

1b. **Per-dependency isolation.** Any exception while fetching, extracting or
   diffing one dist becomes a `Gap` on that dist (with the exception class),
   and the scan continues. `closure_complete` goes false. A tool-level failure
   (no snapshot, unreadable root) is the only thing that fails the scan.
   The CLI exits 0 when the scan completes and 2 on a tool error; the
   findings exit policy is S4's.

2. **Combined index** (`src/reachability/scan/combined.py`). App modules (from
   `app_root`) plus each fetched wheel's top-level packages copied into one
   temp root, then `build_repo_index` unchanged. A same-path collision → `Gap`.

3. **Start points.** Entrypoints whose module is an app module only (§2.3 S).
   Library `__main__`, tests, fixtures, routes, CLI are never start points.
   App tests keep `is_test=True`.

4. **Targets, minimal** (`src/reachability/scan/targets.py`). `changed(adv)`
   between the installed wheel and the first fixed version above it (§2.4: the
   stable key, version-only filter, test modules excluded, removed symbols
   count). Confidence `medium` for all (no `named` yet). Cap 20 per advisory,
   `targets_truncated`. No fixed version → `unknown`, `NO_FIX_VERSION`; no
   wheel for the fix → `NO_WHEEL_FOR_FIX`; empty diff → `NO_CANDIDATE`.

5. **Query.** For each target in the installed index, `compute_reachability`
   from S. `REACHABLE`/`REACHABLE_ONLY_FROM_TESTS` → `path_proven` with the
   path; the shortest path wins. Every other engine result → `unknown` with
   the engine reason mapped per §2.5 (`NOT_REACHABLE` → `NO_PATH_FOUND`).

6. **Output** (`src/reachability/cli.py`, `python -m reachability.cli scan
   <dir> --snapshot <zip> [--format text|json] [--cache-dir D]`). JSON with
   `schema_version: "0"`, snapshot `{date, sha256}`, inventory, malicious,
   findings `{advisory_id, aliases, dist, version, verdict, evidence_level,
   reason_code, path|null, targets}`, gaps, timings. Any advisory text or
   package-supplied string written to the terminal has C0/C1 control
   characters (including ESC) stripped. Text: lead line,
   malicious, `path_proven` findings with the path one edge per line
   (`file:line caller -> callee`), then a count of `unknown` by reason.
   Output via `sys.stdout.write`, no `print(`.

7. **Budgets** (§2.8): 600 s wall clock and 200 advisories → remaining get
   `SCAN_BUDGET`; the scan still completes and says so.

8. **Real-wheel measurement** (the loop runs it; network for PyPI only; not a
   committed fixture). On both apps record: inventory size, advisories
   matched, wheels fetched / `no_wheel`, combined modules and edges, build
   time, per-query time (median, max), peak RSS, findings by verdict and
   reason, and every `path_proven` finding in full. Paste the output in
   `.agent/progress.md` and the numbers in DECISIONS §29. Do not tune
   anything to change these numbers.

9. **Fixtures**: miniature E1 (stand-in wheels for langchain-openai 1.1.13
   and 1.1.14 with the real module/symbol layout of
   `langchain_openai.chat_models.base:_url_to_size`, app calls it directly),
   a no-fixed-version case, a native-only empty diff, crafted wheels each
   rejected with a named reason (zip-slip, absolute path, symlink, hardlink,
   a bomb over the extracted cap, too many members, a hash mismatch, a
   non-allowlisted URL host), a deeply nested `.py` (RecursionError → Gap,
   scan continues), a dist whose fetcher raises (Gap, scan continues), an
   advisory summary containing `\x1b[2J` (stripped in text output), a
   same-path collision.

# Invariants

- No changes under `src/reachability/index/`, `src/reachability/triage/`,
  `src/reachability/agent/`, `main.py`, `alembic/`, `web/`,
  `docker-compose*.yml`, `CLAUDE.md`, `requirements.txt`. No new dependency
  (stdlib `urllib`, `hashlib`, `zipfile`, `tempfile` only; `packaging` is
  already pinned).
- No network in tests (the existing socket-blocking fixture covers
  `tests/scan/`).
- Never build, install, import or execute a scanned app or wheel.
- No `not_reachable` in any scan output: a test runs every fixture and
  asserts it, and `grep -rn "NOT_REACHABLE" src/reachability/scan/` only hits
  the mapping line.
- A1a/A1b G5 fixtures and the matcher table pass unchanged.

# Verification

```
source .venv/bin/activate
pytest                                     # report the count vs the A1b baseline
pytest tests/scan -q
python scripts/run_eval_suite.py           # 30/30, verdicts unchanged
git diff --stat main -- src/reachability/index src/reachability/triage src/reachability/agent main.py alembic web docker-compose.yml docker-compose.smoke.yml CLAUDE.md requirements.txt   # must be empty
python -m reachability.cli scan tests/fixtures/app_scan/eval/E1 --snapshot <E1 mini snapshot> --format json   # path_proven, final callee = _url_to_size (G3)
python -m reachability.cli scan /tmp/app-scan-research/flaskr --snapshot ~/osv-snapshots/osv-pypi-2026-11-12.zip
python -m reachability.cli scan /tmp/app-scan-research/fastapi-template --snapshot ~/osv-snapshots/osv-pypi-2026-11-12.zip
grep -rn "print(" src/reachability/scan/ src/reachability/cli.py   # must be empty
```

# Out of scope

Import closure and evidence levels 2-4 (S2a), `named` targets, confidence
`high`, the D3-set cache and per-query speed (S2b), the §4 eval corpus and CI
job (S3), pyproject/console script/`db update`/SARIF/exit-code policy (S4),
any API, table or UI.

Do NOT run scripts/smoke_compose.sh, any real-model run, git push, git tag, or a merge to main.
