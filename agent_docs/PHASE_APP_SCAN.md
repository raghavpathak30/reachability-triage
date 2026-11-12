# Phase App Scan: which advisories in my dependencies can my app reach?

Status: **spec only, nothing built.** Every component below is *proposed*.
Research and measurements are in `agent_docs/research/APP_SCAN_RESEARCH.md`,
cited here as "R §n". Where R's design sections differ from this spec, this
spec wins.

Baseline: `main` at `2a540d5` (tag v0.3.5). Bare `pytest` gives 461 passed,
3 deselected. The 3 are deselected by `addopts -m "not real_llm"` in
`pytest.ini`.

Revision 2 (2026-11-12) applies the plan critique (`.agent/critique.md`) and
Raghav's decisions D1–D5. The mapping from each critique item to a change is
§9. No target tag is proposed yet.

## 0. Goal and non-goals

**Goal.** A user points the service at a Python application on the same
machine. The service would:
1. read the app's lockfile;
2. list the exact installed dependency versions;
3. find every advisory in a pinned OSV snapshot that applies to them;
4. return **one finding per advisory**, each with:
   - a **verdict**: one of the existing four values;
   - a separate **evidence level** (§2.3) saying what was actually shown;
   - a **reason code** (§2.5);
   - a **plain-English sentence**.

Malicious-package advisories are listed separately, first, with no verdict
(D5).

**What success looks like.** Measured on two real apps, no dependency module
can soundly be called `not_reachable`. Config-driven imports defeat every
import-graph rule we tried. For example, the FastAPI template never imports
psycopg; it builds the string `"postgresql+psycopg://"`
(`backend/app/core/config.py:39`), and SQLAlchemy loads the driver with
`__import__` (R §0.4). An earlier "87 of 2,241 eligible" figure is
**withdrawn**. The corrected result is **0 eligible on both apps**. So this
phase **never issues `not_reachable`** (D3). Its value is:
- `reachable`, with a call path from the app into the dependency
  (`path_proven`);
- `unknown`, with an evidence level that says how far the analysis got:
  `module_imported_by_app`, `package_installed_not_imported`, or
  `no_target_symbol`.

**Non-goals (do not build, do not describe as built):**
- **No `not_reachable` from any scan, in any unit of this phase** (D3).
- No change to `/v1/triage`, its request or response shape, or `triage_jobs`.
- No archive upload or git URL in A1–A9. Archive upload is a later unit, A10
  (D1). `repo_url` stays NOT BUILT.
- No dependency resolution. No resolver runs, no sdist is built, and no
  package or app code is imported or executed (D2, R §2.2, R §10).
- No live OSV query and no fetch of advisory data by the worker. Fix-commit
  fetching only in A8 (D4).
- No framework-aware analysis beyond the bounded resolution unit A7: no Django
  URLconf, signals, `Depends`, or template-text filter calls.
- No severity scoring of our own. The record's severity is shown as given.
- No web UI work; the UI redesign waits for this phase.
- No real-model run by the agent. Real-model runs are Raghav's only.
- No claim of exploitability or non-exploitability anywhere.

## 1. Architecture (proposed)

```
POST /v1/scans {path}
      │  api: validate relative path; insert scans row (queued)
      ▼
worker claim (scans; SKIP LOCKED; same claim/finalize/reaper contract as triage_jobs, DECISIONS §8)
      │
      ├─ 1. locate     /scan-inputs/<path> (ro bind mount; resolve(strict) + is_relative_to; symlinks skipped and recorded)
      ├─ 2. inventory  lockfile → [(dist, version, status, wheel url+hash?)]                  scan/inventory.py   (A1a/A1b)
      ├─ 3. match      pinned OSV snapshot → malicious list + advisory groups per (dist, version) scan/advisories.py  (A1a)
      ├─ 4. fetch      wheels for every pinned, marker-true dist; sha256-verified; wheel-only    scan/fetch.py       (A2)
      ├─ 5. closure    app import closure C(A) over app + all fetched wheels (L1 tables)        scan/closure.py     (A2)
      ├─ 6. targets    named-in-text ∪ changed-in-diff (installed ↔ first fixed above it)       scan/targets.py     (A4)
      ├─ 7. paths      linked per-dist L1–L3 indexes; compute_reachability from app start points scan/dist_index.py  (A5)
      ├─ 8. evidence   level + reason + sentence per advisory (§2.3, §2.5)                       scan/evidence.py
      └─ 9. finalize   scans + scan_findings rows, snapshot {date, sha256} on the result
```

New package `src/reachability/scan/`. `src/reachability/index/` stays
unchanged except in two places:
- A5: a no-new-capability helper that computes the D3 name-escape set once per
  index;
- A7: the bounded resolution changes. The L3 exception is pending: it is
  decided at the start of A7, in its own `DECISIONS.md` section, and is not
  part of §26 (§6).

The LangGraph triage loop is not on the scan path (§2.7).

## 2. Data model and API (proposed)

### 2.1 HTTP

| Call | Success | Errors |
|---|---|---|
| `POST /v1/scans` body `{path: str}` (1–512 chars, relative, no `..`, no leading `/`) | 202 `ScanOut`, `Location: /v1/scans/{id}` | 422 envelope (existing) |
| `GET /v1/scans/{id}` | 200 `ScanOut` | 404 envelope |

```
ScanOut = {
  id, status: queued|running|completed|failed, error|null, llm_mode|null,
  snapshot: {date, sha256},                      # always present once running (D4)
  target_env: "cp313-manylinux_x86_64",
  malicious: [MaliciousEntry],                   # shown first; verdict-independent (D5)
  inventory: {source_file|null, hint|null,       # hint per D2 when no lockfile / unpinned lines
              closure_complete: bool, closure_gaps: [Gap],
              packages: [{name, version|null, status, advisories_any_version?: int}]},
  summary: {lead: str|null,                       # unpinned lead line (§2.6); first line of the summary when set
            unpinned: {packages: N, advisories_any_version: M},
            verdicts: {reachable, reachable_only_from_tests, not_reachable: 0, unknown},
            evidence: {path_proven, module_imported_by_app, package_installed_not_imported, no_target_symbol},
            reasons: {CODE: n}},
  findings: [Finding]
}
MaliciousEntry = {advisory_id, aliases[], dist, version, summary, signal: mal_prefix|cwe_506}
Finding = {advisory_id, aliases[], dist, version, severity_as_given|null,
           verdict, evidence_level, reason_code|null, sentence,
           evidence: PathEvidence | ImportEvidence | NotImportedEvidence | NoTargetEvidence,
           targets: [Target]}
Target = {node_id, file, lineno, confidence: high|medium, named: bool, changed: changed|added|removed|null}
Gap = {kind: no_wheel|unparsed_file|skipped_symlink|unresolved_relative_import|budget, detail}
```

`not_reachable` stays in the verdict enum, because the verdict set is
unchanged. The summary reports it, and in this phase it is always 0. A test
asserts that no code path in `scan/` can produce it.

### 2.2 Tables (one migration, `0005_create_scans`)

- `scans`: `id` uuid pk, `status`, `path`, `inventory_json`, `malicious_json`,
  `snapshot_date`, `snapshot_sha256`, `target_env`, `llm_mode`, `error`,
  `worker_id`, `attempt_count`, `created_at`, `updated_at`.
- `scan_findings`: `id`, `scan_id` FK (on delete cascade), `advisory_id`,
  `aliases` json, `dist`, `version`, `verdict`, `evidence_level`,
  `reason_code`, `sentence`, `evidence_json`, `targets_json`.
- `dist_index_cache` (A5): pk (`dist`, `version`, `wheel_sha256`,
  `index_schema_version`), `blob` bytea.
- `advisory_targets_cache` (A4): pk (`advisory_id`, `installed_version`,
  `fixed_version`, `method_version`), `named` json, `changed` json.

### 2.3 Evidence levels (separate from the verdict)

**Terms.** All of these are computed from the existing index data structures.
- **A**: app modules. The modules `discover_modules` finds under the scan root.
  The scan-side walker skips symlinked files and records them as `Gap`s.
- **S**: app start points. `build_entrypoint_index` output restricted to
  modules in A. Library `__main__`, tests, fixtures, routes and CLI are never
  start points (R §5.3: 901 polluting start points measured). App tests keep
  `is_test=True`.
- **owners(d)**: every dotted prefix of `d` that is a discovered module.
  Importing `a.b.c` executes `a` and `a.b`.
- **C(A)**: the app import closure. The smallest module set containing A and
  closed under the following, for each module `m` in the set:
  - every `ImportAlias` in `import_tables[m]` with
    `type_checking_only == False` adds `owners(resolved_absolute or
    target_module)`. If `target_symbol` is set, it also adds
    `owners(target + "." + target_symbol)`, so `from pkg import sub` counts
    `pkg.sub` when that is a module;
  - every `StarImport` adds `owners(resolved_absolute)`;
  - conditional imports (`try/except ImportError`) count;
  - an import whose target resolves to `None` (a relative import escaping
    the root) is a `Gap` (`unresolved_relative_import`);
  - imports inside function bodies must count. A2 checks whether L1 records
    them. If it does not, a scan-side collector adds them, with no L1 change
    and a test either way.
- **dist_of(module)**: the distribution that owns the module's file, per the
  wheel's `RECORD`. A namespace-package directory with no `__init__.py` is
  owned by no distribution. Importing `ns.x` loads the owner of `ns/x`.
- **T(adv)**: target symbols with confidence `high` or `medium` (§2.4) that
  exist in the **installed** version's symbol table.

**Levels.** Evaluated in this order; the first one that holds wins.

| # | Level | Holds when | Verdict | Evidence the API returns |
|---|---|---|---|---|
| 1 | `path_proven` | `compute_reachability` over the linked index (A5) with start points S returns `REACHABLE` or `REACHABLE_ONLY_FROM_TESTS` for some t ∈ T(adv). That means a path of high- and medium-confidence edges only (the existing engine rule). | `reachable` or `reachable_only_from_tests` (copied from the engine) | `PathEvidence = {start_point, path: CallEdge[], target: Target}`. The target with the highest confidence wins; ties go to the shortest path |
| 2 | `package_installed_not_imported` | The dist's wheel was indexed and **no** module with `dist_of = dist` is in C(A). | `unknown` | `NotImportedEvidence = {closure_complete, closure_gaps, depended_on_by: [dist], dist_entry_points: [group:name]}`. The sentence always says a host, plugin or config string can still load it |
| 3 | `module_imported_by_app` | T(adv) is non-empty, some module of the dist is in C(A), and level 1 did not hold. | `unknown` | `ImportEvidence = {import_chain: [{module, file, lineno}], target_modules_in_closure: [module], engine_reason_code, partial_path: CallEdge[]\|null}` |
| 4 | `no_target_symbol` | T(adv) is empty, and either some module of the dist is in C(A) or the dist could not be indexed (`NO_WHEEL`). | `unknown` | `NoTargetEvidence = {why: reason_code, named_count, changed_count, import_chain\|null}` |

The levels are total and mutually exclusive: every matched, non-malicious
advisory on a pinned dist gets exactly one. Unpinned dists get no findings at
all. They appear in the inventory with `status: unpinned` and
`advisories_any_version` (D2).

**Summary rule for unpinned packages.** Unpinned packages stay finding-free.
But when N ≥ 1, the scan summary **leads** with exactly this line, before the
malicious list and the verdict counts:

> "N packages are not pinned; they have M known advisories (any version). Pin
> them to check."

The terms are:
- **N** = the number of inventory rows with `status: unpinned`.
- **M** = the number of distinct non-withdrawn, non-malicious advisory groups
  in the snapshot whose `affected[].package.name` (PEP 503-normalised) equals
  any of those packages, regardless of version.

It is returned as `summary.lead`, with `summary.unpinned = {packages: N,
advisories_any_version: M}`. When N = 0, `summary.lead` is `null`.

### 2.4 Target confidence

- **changed(adv)**: symbols that differ between the **installed** version's
  wheel and the **first fixed version above it**. That is the smallest `fixed`
  event greater than the installed version. When the advisory has several
  release lines, this picks the right backport (critique B5).
  - The comparison key is `(module, qualname, ordinal among same-qualname
    defs)`, not `node_id`. `node_id`s carry `@lineno` suffixes, which produced
    15 phantom add/remove pairs in R §4.3.
  - Symbols changed, added in fixed, or removed in fixed all count. A removed
    symbol exists in the installed version, so it can be a target.
  - Test modules (any dotted segment `tests`/`test`/`testing` or `test_*`) are
    excluded.
  - A **version-only module change** is dropped. Definition: the module's
    non-def, non-import top-level statements differ *only* in `Assign` or
    `AnnAssign` nodes whose single target is one of `__version__`, `VERSION`,
    `version`, `version_info`, `__version_info__`, and whose value is a
    constant or a tuple of constants. Any other difference keeps the module
    (critique M9).
  - Non-`.py` file changes (templates, `.so`, data) are recorded as a flag
    `non_py_changes: [path]`. They never create targets.
- **named(adv)**: installed-version symbols whose name appears in the
  advisory's `summary` or `details` **in code form**:
  - the text is first passed through `sandbox_untrusted_text`;
  - code form means inside backticks, immediately followed by `(`, or part of
    a dotted chain of at least two identifiers;
  - a token matches a symbol if it equals the symbol's final qualname segment,
    its `Class.method` qualname, or its full `module.qualname`;
  - plain prose words never match, e.g. the “ask” in vanna's advisory
    (GHSA-7735-w2jp-gvg6). The regex heuristic in R §4.2 (precision 0.78) is
    why code form is required.
- **high** = named ∩ changed. **medium** = (named ∪ changed) minus high.
- **none** = both sets are empty. The finding falls back to the module-level
  check, which yields level 2 or level 4.
- Up to 20 targets are queried per advisory: every `high`, then `medium` in
  stub-rank order. When targets are truncated, `targets_truncated: true` is
  set.
- **Model rule (A8):** the model may only **rank** symbols in named ∪ changed.
  Any id outside that set is dropped and logged. The model never adds a symbol
  and never sets a verdict or level.

### 2.5 Reason-code registry (closed)

`src/reachability/scan/reasons.py` would hold one `Enum` holding exactly
these codes. A test asserts three things: every finding's code is in the
enum; the code is allowed for that finding's level; every engine
`termination_cause` for an engine `UNKNOWN` (DECISIONS §24) maps to exactly
one code.

| Code | Allowed level | Sentence (template) |
|---|---|---|
| *(none)* | `path_proven` | high: "Your code calls `{target}`, which the advisory names and the fix changed: `{path}`." medium, named only: "Your code calls `{target}`, which the advisory names (the fix did not change it): `{path}`." medium, changed only: "Your code calls `{target}`, which the fix changed; the advisory does not name it, so it may not be the vulnerable function: `{path}`." |
| `NOT_IMPORTED_BY_APP_CODE` | `package_installed_not_imported` | "Your code never imports `{dist}`. It can still be loaded by the server that runs your app, a plugin, or a setting that names it as a string (for example a database URL), so we cannot say it is unreachable." |
| `NO_PATH_FOUND` | `module_imported_by_app` | "Your app imports `{module}`, but we found no call path to `{target}`. Frameworks often call such code for you (for example when you read `request.form`), which we cannot see." |
| `LOW_CONFIDENCE_PATH` | `module_imported_by_app` | "A path to `{target}` exists only through calls we could not resolve." |
| `OPAQUE_CALL` | `module_imported_by_app` | "Code on the way to `{target}` makes a call whose target is computed at runtime." |
| `NAME_ESCAPE` | `module_imported_by_app` | "`{target}` is passed around by name, so it may be called indirectly." |
| `NO_APP_ENTRYPOINTS` | `module_imported_by_app` | "We found no start points in your app (no `__main__`, routes, CLI or console scripts)." |
| `NO_FIX_VERSION` | `no_target_symbol` | "The advisory lists no fixed version, so we cannot tell which function it is about." |
| `NO_WHEEL_FOR_FIX` | `no_target_symbol` | "The fixed version has no wheel to compare against." |
| `NATIVE_ONLY_CHANGE` | `no_target_symbol` | "The fix changed only compiled or non-Python files." |
| `DEPENDENCY_PIN_FIX` | `no_target_symbol` | "The fix only changed a dependency requirement, not code." |
| `NO_CANDIDATE` | `no_target_symbol` | "Neither the advisory text nor the fix points to a Python function." |
| `NO_WHEEL` | `no_target_symbol` | "`{dist}=={version}` has no wheel; we never build source packages." |
| `TARGET_NOT_RESOLVED` | `no_target_symbol`, `package_installed_not_imported` | "Matched to your version; function-level analysis is not available yet." (A2–A3 only; removed in A4) |
| `SCAN_BUDGET` | `module_imported_by_app`, `no_target_symbol` | "The scan hit its time or size budget before finishing this advisory." |

`not_reachable` has no code because it is never issued. The engine reasons map
from `termination_cause.py` constants (`LOW_CONFIDENCE_PATH`, `OPAQUE_CALL`,
`NAME_ESCAPE`, `NO_ENTRYPOINTS` → `NO_APP_ENTRYPOINTS`).

### 2.6 Inventory statuses and the D2 hint

`pinned`, `unpinned`, `unsupported` (a VCS, URL or editable line, `-e`, `-c`,
or an unknown option), `marker_skipped` (its marker is false for
`target_env`; listed, no findings), and `no_wheel` (found at fetch time).
- An `--index-url` or `--extra-index-url` in a requirements file marks every
  package in that file `unsupported`, because the package universe differs.
- A dist listed at two versions that both survive marker evaluation produces
  findings per (dist, version).
- If no lockfile is found, or any line is unpinned, `inventory.hint` is set:

> "We need exact versions and never run a resolver. Create a lockfile where you
> run the app, then scan again: `uv lock` (uv), `poetry lock` (Poetry), or
> `pip freeze > requirements.txt` run inside the environment you deploy."

Each unpinned line is listed with its line number. `pip lock` (experimental
since pip 25.1) is not suggested until it is stable (R §2.1).

### 2.7 What the LangGraph agent does here

Nothing. Targets come from §2.4 and the queries are deterministic. The only
model use is A8 ranking, behind `TRIAGE_LLM_MODE`, with a deterministic stub in
CI. Scans do not call `run_triage_loop_langgraph`.

### 2.8 Budgets (constants, each with a test)

| Budget | Value | When exceeded |
|---|---|---|
| Scan wall clock | 600 s | remaining advisories get `SCAN_BUDGET`; scan completes |
| Advisories per scan | 200 | the rest get `SCAN_BUDGET` |
| Targets per advisory | 20 | `targets_truncated: true` |
| Distributions fetched per scan | 150 | the rest are `Gap{kind: budget}`; `closure_complete: false` |
| Scan input | 20,000 files / 200 MB / depth 32 | scan `failed`, error names the limit |
| Wheel download | 120 s each (as today) | `no_wheel` with the pip error |
| Model calls (A8) | ≤ 1 per advisory | stub order |

### 2.9 Snapshot (D4)

- **Refresh** is operator-run and never done by the worker. It would be a new
  script, `scripts/refresh_osv_snapshot.sh`:
  ```
  d=$(date -u +%F)
  curl -sSf -o "osv-pypi-$d.zip" https://osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip
  sha256sum "osv-pypi-$d.zip" | cut -d' ' -f1 > "osv-pypi-$d.zip.sha256"
  ```
- **Configuration:** `OSV_SNAPSHOT_PATH` and `OSV_SNAPSHOT_SHA256`. The worker
  verifies the sha256 at startup and refuses to start on a mismatch, in the
  same fail-fast style as the `TRIAGE_LLM_MODE` check.
- **Date** comes from the filename, `osv-pypi-YYYY-MM-DD.zip`. A filename that
  does not match is a startup error.
- **Every `ScanOut`** carries `snapshot: {date, sha256}`. The summary sentence
  says "advisories as of {date}".
- **Current reference snapshot:** 2026-11-12, sha256
  `c7ea4e15b83912960fc5cbd42c6003dd630e422ec4dfbfc4ed19445573e1b08c`,
  37,790,549 bytes (R §3.5).

### 2.10 Malicious packages (D5)

A matched record is classified as malicious, and moved to `ScanOut.malicious`,
by structured fields only. There is **no keyword or summary-text matching.**
- **Primary signal (`mal_prefix`):** the record's OSV id, or any of its
  `aliases`, starts with `MAL-`. That is the OpenSSF malicious-packages
  database prefix.
- **Secondary signal (`cwe_506`):** otherwise,
  `database_specific.cwe_ids` contains `CWE-506` (embedded malicious code).

Measured on the pinned snapshot (sha256 `c7ea4e15…08c`, 26,863 records):

| Signal | Records | Non-withdrawn |
|---|---|---|
| id starts `MAL-` | 12,020 | 11,995 |
| id not `MAL-`, an alias starts `MAL-` | 42 | 42 |
| neither of the above, CWE-506 | 8 | 8 |

Command: `cd /tmp/app-scan-research/osv && sha256sum PyPI-all.zip && python3 -I -`
with a script that reads every member of the zip and classifies each record by
the first matching rule above (R §3.3).

These entries have no verdict and no evidence level. The UI and summary show
them first, after the unpinned lead line when there is one. Malware records
carrying neither signal (e.g. `PYSEC-2025-7`) are missed and fall through to a
normal finding. Dropping the earlier "Malicious code in" summary rule loses
**1** advisory group that has the summary but no `MAL-` alias (R §3.3). That
is a known, documented gap; the normal finding never claims safety.

## 3. Units

Each unit is one `/loop` cycle with its own [GATE]. Branch per unit:
`app-scan-<unit>-<slug>`. Smallest shippable first.

Every gate includes:
- a full bare `pytest`, with the count reported against the previous unit;
- the app starts clean, with no debug prints;
- `git diff --stat main -- src/reachability/index` is empty unless the unit
  names an index change (A5 helper, A7);
- `scripts/run_eval_suite.py` 30/30 verdicts unchanged in any unit that
  touches `src/reachability/index`.

### A1a: Inventory (pinned requirements, uv.lock, poetry.lock) and OSV matcher. Useful on its own.

- `scan/inventory.py`:
  - `requirements.txt`: `name==version` lines; `-r` includes within the scan
    root only; environment markers evaluated for `target_env`; `--hash`
    recorded.
  - `uv.lock`: runtime closure of the root member, extras followed.
  - `poetry.lock`: all packages, with the `groups` field recorded.
  - Statuses and the hint as in §2.6.
- `scan/advisories.py`:
  - loads the snapshot (sha256 checked) and excludes withdrawn records;
  - groups aliases with union-find;
  - an installed version matches if it is in `versions` ∪ the OSV-range
    evaluation, where `introduced: "0"` means −∞ and `last_affected` is
    inclusive;
  - honours `last_known_affected_version_range`;
  - an installed version that is not valid PEP 440 gets inventory status
    `unsupported`, never a match or non-match guess. An unparseable *range
    bound* in a record makes that advisory match conservatively (it is
    reported), with a note in the finding;
  - malicious classification per §2.10.
- `scripts/scan_inventory.py <dir> --snapshot <zip>` prints:
  - the malicious entries first;
  - then dist / version / status / advisory ids / fixed-in;
  - then the hint.
- New pinned dependency `packaging` (Apache-2.0 OR BSD-2-Clause), added to the
  plan first.
- Fixtures:
  - `tests/fixtures/app_scan/advisories/`: the subset of real OSV records
    the tests need, copied from the reference snapshot, plus a `NOTICE` with
    CC-BY-4.0 attribution;
  - lockfile miniatures in the three formats.
- **Gate** (the original A1 items, applied to A1a):
  - inventory exact match (dist, version, status) on every lockfile fixture
    (G5);
  - a matcher table of at least 12 rows from real records: pre-release vs
    `introduced: "0"`, `last_affected`, empty `versions`, a non-PEP 440
    string → `unsupported`, a withdrawn record excluded, an alias group
    collapsed, a `MAL-*` alias → malicious;
  - an unpinned line yields `unpinned` plus the hint, never a guessed version;
  - no network in tests (socket-blocking fixture).

### A1b: Pipfile.lock and pylock.toml

- `Pipfile.lock`: `default` only by default; no graph, so the whole set is
  fetched.
- `pylock.toml`: per PEP 751. A `packages` entry without `version` is
  `unpinned`. `packages.dependencies` is ignored, as the PEP requires.
- **Gate:** G5 on new fixtures in both formats; the format precedence test
  (uv.lock > pylock.toml > poetry.lock > Pipfile.lock > requirements.txt) with
  several files present.

### A2: Fetch and import-closure evidence (CLI)

- `scan/fetch.py`: wheel-only download of every `pinned`, marker-true dist,
  sha256 against the lock's hash when present. `no_wheel` → `Gap`.
- Archive safety on every extracted wheel: names that are absolute, contain
  `..`, a drive or a backslash, or are symlink entries → reject the wheel
  (`no_wheel` with reason).
- `scan/closure.py`: C(A) per §2.3, `RECORD`-based `dist_of`, namespace
  handling, `Gap`s.
- Levels available in A2: `package_installed_not_imported` and
  `no_target_symbol` (reason `TARGET_NOT_RESOLVED`, or `NO_WHEEL`).
- **Gate:**
  - closure-semantics tests: `from pkg import submodule`, star import,
    conditional import, a `TYPE_CHECKING`-only import (excluded),
    function-level import (included), unresolved relative import → `Gap`,
    namespace package split across two dists;
  - evidence-level fixtures E2 and E4-wheel (§4);
  - trap fixtures T1–T7 (§4) produce `package_installed_not_imported` or
    `no_target_symbol` with `closure_complete` set as labelled, and never
    `not_reachable`.

### A3: Scan resource (API, table, worker, mount)

- Migration `0005`, `POST`/`GET /v1/scans`, the worker claims scans in the
  existing poll loop, and the reaper covers `scans`.
- Compose:
  - the worker gets `${SCAN_INPUTS_DIR:-./scan-inputs}:/scan-inputs:ro`;
  - the snapshot is mounted as a read-only file;
  - `OSV_SNAPSHOT_PATH` and `OSV_SNAPSHOT_SHA256` are set;
  - the API mounts neither.
- Path rules (R §1.1 (b)).
- The OpenAPI snapshot is regenerated. The `/v1/triage` part of the schema
  diff must be empty.
- **Gate:**
  - claim/finalize/reaper tests mirroring `triage_jobs`;
  - path-escape tests (`..`, absolute, a symlink to outside, a symlink inside →
    skipped plus a `Gap`);
  - snapshot mismatch → the worker refuses to start;
  - `/v1/triage` OpenAPI unchanged;
  - human-run item (F4 precedent): `scripts/smoke_compose.sh` is run by
    Raghav, not the loop.

### A4: Targets

- `scan/targets.py` per §2.4: installed ↔ first-fixed-above diff on the stable
  key, the version-only filter, `non_py_changes`, `NATIVE_ONLY_CHANGE`,
  `DEPENDENCY_PIN_FIX` (only `Requires-Dist` differs), `named` extraction,
  confidence, cache.
- Adds level `module_imported_by_app` with reason `NO_PATH_FOUND` before A5
  exists. In A4 every imported dist with targets gets this level.
  `TARGET_NOT_RESOLVED` is removed.
- **Gate:**
  - the 5 diffs from R §4.3 reproduced on real wheels (real-wheel lane, §4):
    the labelled true symbol is in `changed`; Django `module django` and
    Starlette `module starlette` are filtered as version-only; jinja2 shows
    no phantom pairs;
  - unit tests for `named` on the R §4.2 sample texts: vanna's “ask” is not
    named, and langchain-openai's `` `_url_to_size()` `` is named.

### A5: Linked call graph (`path_proven`)

- `scan/dist_index.py`: per-dist L1–L3 with the wheel as its own root,
  cached. A link step re-resolves `ext:<module>.<name>` callees across the
  cached indexes. Same-file ownership collision → `Gap`.
- An `index/` helper computes the D3 set once per index. Behaviour is
  identical; the existing D3 tests stay unchanged.
- **Gate:**
  - **parity**: the linked edge set equals the single-root combined build (the
    R §5.2 method) on the two measured apps, at the commits and locks in R §5.2;
  - E1 is `path_proven` with a path ending at the labelled true symbol;
  - per-query time on the FastAPI template is at most 1.0 s (measured baseline
    3.9–4.9 s, R §5.3).

### A6: Eval corpus and CI gate

- The fixtures and labels frozen in §4, `scripts/run_scan_eval.py`, and the
  CI job `scan-eval` (miniature lane).
- **Gate:** the §4 rules applied verbatim on **both** lanes and reported.
  The real-wheel lane is the phase gate.

### A7: Resolution of unresolved app edges (measurable target)

- **Metric U.** For an app, U = (edges whose caller module is in A and whose
  callee id starts with `?:`) / (edges whose caller module is in A). It is
  computed on the combined index built per R §5.2 at the pinned commits and
  locks.
- **Corrected baselines:** Flask tutorial **104/177 = 58.8%**, FastAPI
  template **468/765 = 61.2%** `[measured]`. Earlier drafts said 55% for the
  Flask tutorial; that was a miscount.
- **Scope**, exactly three shapes:
  1. **Property and descriptor reads.** An attribute *load* `recv.attr`
     (not in call position) where `recv`'s class is statically known and
     `attr` resolves through the class's MRO to a function decorated
     `@property`, `@functools.cached_property`, or the dependency's own
     `cached_property`. Statically known means one of:
     - `recv` is `self` or `cls` in a method of that class;
     - `recv` is a class name;
     - `recv` is a module-level name whose annotation, or whose direct
       `Class(...)` assignment, names the class in its defining module.
     This adds a new edge to the getter, with a new resolution rule
     `property_access` at confidence `medium`.
  2. **`self.method()` through the class.** The existing `self_mro` rule,
     extended to methods inherited from a dependency class once A5 has linked
     the indexes. Measured: 0 app-side `self.` receivers are unresolved, so
     the benefit is on the dependency side, which is reported separately.
  3. **Module-attribute calls.** `imported_module.attr()` that today ends `?:`
     because `attr` is re-exported in the module's `__init__` (e.g.
     `jwt.decode`); and `Imported.attr()` where `Imported` is a class reached
     through a from-import and `attr` is a classmethod or staticmethod defined
     on it or its bases.
- Out of scope: `local_var.method()` (49–71% of unresolved app edges, R §5.3),
  which needs type inference.
- **Gate:**
  - U before and after, reported on both measured apps, plus the
    dependency-side U;
  - **FastAPI U ≤ 51.2%** (at least a 10-point drop; the in-scope shapes cover
    up to 185 of its 468 unresolved edges, R §5.3 table);
  - Flask tutorial U ≤ 58.8% (no increase; only 9 of its 104 are in scope);
  - a miniature fixture that copies the real declaration of flask's `request`
    and werkzeug's `Request.form` produces a `property_access` edge to `form`.
    If flask's real `request` declaration is not statically typed this way
    (**ASSUMPTION**: unverified), the unit reports that and the fixture uses
    the closest shape that is;
  - `scripts/run_eval_suite.py`: the **30 stub-eval verdicts unchanged**;
  - **no existing test's verdict moves from `unknown` to `not_reachable`** on
    the L5 and Phase 4 corpora. More resolved edges can remove a
    low-confidence bridge, so this is checked explicitly;
  - full `pytest` green.

### A8 (optional): Ranking and fix-commit narrowing

- A deterministic stub ranker: high first, then named, then changed, then
  lexical order.
- Optional `GroqLLMClient` ranker behind `TRIAGE_LLM_MODE=groq`. The advisory
  text goes through `sandbox_untrusted_text`, and ids outside named ∪ changed
  are dropped (§2.4).
- Fix-commit narrowing via `https://github.com/<o>/<r>/commit/<sha>.patch`,
  off by default (`SCAN_FIX_COMMIT_FETCH=0`). It can only reorder within
  named ∪ changed; it adds nothing.
- **Gate:**
  - property test: permuting any ranking changes no verdict or evidence level,
    only the order of targets and the displayed path;
  - real-model ranking run by Raghav only.

### A9: Docs

- README section, `agent_docs/DEMO.md` scan walkthrough, `DECISIONS.md` §26
  (final text from §8), and the CLAUDE.md NOT BUILT changes (§7).
- **Gate:** every doc claim has a file:line.

### A10 (later, not in this phase's first units): Archive upload (D1)

- `POST /v1/scans` multipart with a zip or tar.gz, written to a scratch root
  that then follows the A3 directory path.
- Rejected before extraction:
  - any entry that is absolute, contains `..`, has a drive or backslash, or is
    a symlink or hardlink (zip `external_attr` S_IFLNK; tar `issym`/`islnk`);
  - device files.
- tar is always extracted with `filter="data"`.
- Limits: 50 MB compressed, 200 MB extracted (a streaming byte counter, not
  the declared sizes), 20,000 files, 255-byte names, depth 32. nginx
  `client_max_body_size` is aligned.
- Only `.py`, `.toml`, `.lock`, `.txt`, `.cfg`, `.json` files are written.
- **Gate:** a crafted-archive test set (zip-slip, absolute path, symlink, a
  bomb measured at 49 KB → 50 MB per R §1.1, too many files, a deep tree), each
  rejected with a named error; nothing written outside the scratch root.

## 4. Eval protocol (pre-registered; frozen at spec approval)

These rules may not be amended after any A6 run starts. Changes before that
need Raghav's approval, recorded as a dated amendment. Advisory records come
from snapshot sha256 `c7ea4e15b83912960fc5cbd42c6003dd630e422ec4dfbfc4ed19445573e1b08c`.

**Two lanes.**
- **Real-wheel lane:** the phase gate. It is run by the loop locally, uses
  pip wheel-only with sha256 pins, and has network access for wheels only.
- **Miniature lane:** CI. No network; stand-in wheels with the same
  module/symbol layout for the touched modules, plus every feature the trap
  fixture depends on (dynamic import, entry points, a URL string).
- A row passes the phase only if it passes on the real-wheel lane. A
  difference between the lanes is reported, and the miniature is fixed to
  match.

**Label schema** (`label.json`):
```
{"advisories": {"<id>": {"verdict": [..], "evidence_level": [..], "reason_code": [..],
                          "true_symbols": ["module:qualname", ..]?}},
 "inventory":  {"<dist>": {"version": "..."|null, "status": "..."}},
 "closure_complete": true|false,
 "malicious": ["<id>", ..],
 "summary_lead": "<exact expected line>"|null}
```
Synthetic advisories, used where no real advisory exists for a trap dist, live
in `tests/fixtures/app_scan/advisories/synthetic/`. Their ids start
`SYNTH-`. They are counted separately and never reported as real coverage.

**Evidence-level fixtures** (one per level, plus contrasts):

| # | Advisory | Installed | App | verdict | evidence_level | reason_code | true_symbols |
|---|---|---|---|---|---|---|---|
| E1 | GHSA-r7w7-9xr2-qq2r | langchain-openai 1.1.13 | imports `langchain_openai.chat_models.base` and calls `_url_to_size(url)` directly *(synthetic call shape, real advisory)* | reachable | path_proven | – | `langchain_openai.chat_models.base:_url_to_size` |
| E1r | same | same | calls `ChatOpenAI(...).get_num_tokens_from_messages(...)` (realistic) | reachable, unknown | path_proven, module_imported_by_app | –, NO_PATH_FOUND, LOW_CONFIDENCE_PATH | same |
| E2 | same | same | lock has langchain-openai, app never imports it | unknown | package_installed_not_imported | NOT_IMPORTED_BY_APP_CODE | – |
| E3 | GHSA-gmhf-gg8w-jw42 | signxml 4.0.3 | imports `signxml` and uses only `XMLSigner` | unknown | module_imported_by_app | NO_PATH_FOUND, LOW_CONFIDENCE_PATH, OPAQUE_CALL, NAME_ESCAPE | `signxml.verifier:XMLVerifier.verify` |
| E3r | same | same | calls `XMLVerifier().verify(...)` | reachable, unknown | path_proven, module_imported_by_app | any allowed for the level | same |
| E4 | GHSA-7735-w2jp-gvg6 (vanna, no fixed version) | vanna 0.5.5 | imports `vanna` and calls `ask` | unknown | no_target_symbol | NO_FIX_VERSION | – |
| E4w | SYNTH-0001 on dkimpy | dkimpy 1.1.8 (sdist only, R §2.1) | imports `dkim` | unknown | no_target_symbol | NO_WHEEL | – |
| E5 | GHSA-qg2p-9jwr-mmqf | Django 4.2.13 | calls `django.utils.html.urlize` | reachable, unknown | path_proven, module_imported_by_app | any allowed | `django.utils.html:Urlizer.trim_punctuation`, `django.utils.html:urlize` |
| E6 | GHSA-wqp7-x3pw-xc5r | Starlette 1.0.1 | mounts `StaticFiles(...)` (framework calls it) | unknown | module_imported_by_app | NO_PATH_FOUND | – |
| M1 | a `MAL-*` record from the snapshot (picked by the A1a plan, recorded before any run) | that version | lock lists it | – (in `malicious`) | – | – | – |

**Trap fixtures** (measured failure classes; none may ever produce
`not_reachable`, and each checks that the evidence wording does not overclaim):

| # | Trap | Setup | Expected |
|---|---|---|---|
| T1 | host loads deps (R §5.5) | FastAPI-style app, uvicorn in the lock, SYNTH-0002 on httptools | package_installed_not_imported; evidence `depended_on_by` contains `uvicorn`; `dist_entry_points` of uvicorn listed |
| T2 | stdlib loader | app uses `zoneinfo.ZoneInfo`, tzdata in the lock, SYNTH-0003 on tzdata | package_installed_not_imported |
| T3 | URL-string driver (`config.py:39`) | SQLAlchemy `create_engine("postgresql+psycopg://…")`, psycopg in the lock, SYNTH-0004 on psycopg | package_installed_not_imported |
| T4 | settings-string import | Django `INSTALLED_APPS = ["rest_framework"]`, SYNTH-0005 on djangorestframework | package_installed_not_imported |
| T5 | unseen code | a lock dist that is sdist-only (dkimpy) and is a dependency of an imported dist | `closure_complete: false`, `Gap{no_wheel}`; E4w level for its own advisory |
| T6 | unparseable app file | one app module with a SyntaxError | `closure_complete: false`, `Gap{unparsed_file}` |
| T7 | symlinked app file | an app file that is a symlink | skipped, `Gap{skipped_symlink}`, `closure_complete: false` |
| T8 | property read (R §5.3) | Flask view reads `request.form`, GHSA-q34m-jh98-gwm2 on Werkzeug 3.0.5 | verdict {unknown, reachable}; evidence {module_imported_by_app, path_proven}; reachable only with a path ending in `true_symbols` = werkzeug form-parsing functions changed in 3.0.5→3.0.6 (listed by the A4 plan from the diff, before any A6 run) |
| T9 | inventory | `requirements.txt` with one unpinned line, one `-e` line, `--hash` lines, and a marker false for `target_env` | statuses `unpinned`, `unsupported`, `pinned`, `marker_skipped`; hint present; no findings for the unpinned dist |
| T10 | unpinned summary lead | `requirements.txt` with `jinja2` and `werkzeug` unpinned (no `==`) plus one pinned dist; reference snapshot | no findings for either unpinned dist. `summary.unpinned.packages` = 2. `summary.lead` = "2 packages are not pinned; they have M known advisories (any version). Pin them to check.", where M is the count of distinct non-withdrawn, non-malicious advisory groups for jinja2 ∪ werkzeug in the reference snapshot. The A1a plan computes M with a recorded command and writes it into `label.json` before any run. `summary.lead` is the first line of the CLI summary output |

**Decision rules:**
- **G1 (hard):** zero `not_reachable` in any finding on any fixture, on either
  lane. This is a code invariant as well as an eval rule.
- **G2 (hard):** every row's verdict, evidence level and reason code are each
  in the row's allowed set. A row without a listed reason set allows any code
  the registry permits for the level.
- **G3 (hard):** E1 is `path_proven`, and its path's final callee is in
  `true_symbols`. **Reported, not gating:** E1r, E3r, E5 and T8. For each, say
  whether it is `path_proven`, and if so whether the path ends in
  `true_symbols`. Any `path_proven` row whose final callee is *not* in
  `true_symbols` is a **G3 failure**: a reachable claim about the wrong
  function.
- **G4 (reported):** distribution of evidence levels and reason codes.
- **G5 (hard):** inventory exact match on every fixture.
- **G6 (hard):** zero network access in the miniature lane.
- **G7 (hard):** malicious rows are in `malicious`, and nowhere in `findings`.
- **G8 (hard):** `summary.lead` equals the label's `summary_lead` exactly (T10,
  T9, and `null` on every fully pinned fixture).
- **If G3 fails:** the phase does not close. Raghav decides between fixing and
  re-running (a new dated run, both runs reported) and amending E1.
- Budget exhaustion never counts as a pass for any row (DECISIONS §13).

**Amendment record:** G3 changed from R §9's "≥ 4 of 5 direct import-resolved
calls" to the rule above (critique M1/M3), before any run, on 2026-11-12.

## 5. Decisions (recorded 2026-11-12, Raghav)

| # | Decision |
|---|---|
| D1 | **Read-only folder mount** for A1–A9. Archive upload is the later unit A10, with zip-slip, symlink, size and file-count limits. No git URL. |
| D2 | **A lockfile or fully pinned requirements file is required.** Unpinned → status `unpinned`, never a guessed version. A user-facing hint tells the user to generate a lockfile locally (§2.6). |
| D3 | **Never issue `not_reachable` in this phase.** |
| D4 | **Pinned OSV snapshot only** for A1–A7. GitHub fix-commit fetch only in A8 (off by default). The refresh command is documented (§2.9). The snapshot date and sha256 are in every scan result. |
| D5 | **Malicious-package advisories are a separate category, shown first, verdict-independent** (§2.10). Primary signal: the OSV id or an alias starts `MAL-` (OpenSSF malicious-packages). Secondary: CWE-506. No keyword matching (amended 2026-11-12). |
| D2a | **Unpinned packages stay finding-free, but the summary leads with** "N packages are not pinned; they have M known advisories (any version). Pin them to check." (§2.3, T10, G8; amended 2026-11-12). |

## 6. Risks

- **The output is mostly `unknown`.** That is by design (D3). The evidence
  level and sentence carry the information, and the README must lead with
  `path_proven` findings and the levels, not with counts.
- **A wrong `reachable`.** A `path_proven` finding to a medium,
  changed-only target may point at a function that is not the vulnerable one.
  Measured: Django 4.2.14 bundles fixes for several advisories into one diff
  (R §4.3). This is contained by the sentence wording (§2.5), by showing the
  confidence, and by G3 treating a path to a non-true symbol as a failure.
- **L3 freeze (CLAUDE.md, L5 paragraph).** A7 adds new resolution capability,
  which CLAUDE.md forbids today. The exception is pending: it is decided at the
  start of A7, in its own `DECISIONS.md` section, and is not part of §26 (§8).
  A7 may only start after that section exists. More resolved edges can also remove
  a low-confidence bridge that today forces `unknown` in `/v1/triage`. A7's
  gate checks that no corpus verdict moves to `not_reachable`.
- **Config-driven and host loading** (psycopg via URL, uvicorn → httptools,
  INSTALLED_APPS). Handled by D3. T1–T4 check the wording.
- **Snapshot staleness.** Every result carries the date (D4).
- **The `named` heuristic** can miss names written in prose. A miss only lowers
  confidence; it never creates a target, because code form is required.
- **The malicious classifier** uses structured fields only (`MAL-` prefix,
  then CWE-506). It misses unsignalled records (§2.10), including 1 group found
  only by the dropped summary-text rule; these fall through to normal findings.
- **Cost.** 13.2 s to index the FastAPI combined tree; 3.9–4.9 s per query
  before the D3 cache (R §5.3). A5's gate sets ≤ 1.0 s.
- **Licence.** CC-BY-4.0 attribution for the committed advisory subset.
  Chainguard records are non-commercial, but are believed absent from the PyPI
  export (**ASSUMPTION**: not checked record by record).

## 7. NOT BUILT lines this phase would change (at A9)

**Added** (true after the phase):
- `not_reachable` from an app scan. Scans never issue it. Evidence levels are
  carried on `unknown` instead.
- App scan input by archive upload (until A10) or git URL.
- Dependency resolution for unpinned requirements.
- Framework-aware edges beyond A7's three shapes: Django URLconf, signals,
  `Depends`, template-text filter calls, `local_var.method()` type inference.
- Live OSV queries; automatic snapshot refresh.
- Fix-commit fetching on by default.
- Severity scoring of our own.

**Changed (pending, at A7/A9):** the L3 "frozen against new resolution
capability" statement changes only if the A7 exception is granted, which is
decided at the start of A7 in its own section, not in §26.

**Removed:** nothing. `repo_url` acquisition stays NOT BUILT.

## 8. `DECISIONS.md` §26 (applied as `DECISIONS.md` §26 on 12 Nov 2026 with changes: the L3 exception is not granted yet, the eval rules include G8, and the title drops "(draft)")

The quoted text below is the original draft, kept for history. Its "L3 exception (scoped)" paragraph was NOT applied: `DECISIONS.md` §26 says the L3 exception is not granted yet and is decided at the start of A7. It also had G1-G7 where §26 has G1-G8.

> ## 26. App scan: evidence levels instead of `not_reachable` (draft, 2026-11-12)
>
> **Decision.** `/v1/scans` takes a relative path under a read-only mount,
> requires a lockfile or fully pinned requirements, matches a pinned OSV PyPI
> snapshot (date and sha256 in every result), and returns one finding per
> advisory. Each finding carries one of the four existing verdicts plus an
> evidence level: `path_proven`, `module_imported_by_app`,
> `package_installed_not_imported` or `no_target_symbol`. Malicious-package
> records are listed separately and first, with no verdict. `/v1/triage` is
> unchanged.
>
> **Scans never issue `not_reachable`.** Two measured results:
> - A call graph over the Flask tutorial plus its wheels answered
>   `not_reachable` for werkzeug's multipart parser, which `request.form` runs
>   at runtime.
> - An import-graph rule looked sound until the FastAPI template showed
>   psycopg loaded from a URL string (`backend/app/core/config.py:39`, via
>   SQLAlchemy's `__import__`). With dynamic imports treated corpus-wide (the
>   D1 precedent), 0 dependency modules were eligible on either app.
>
> The earlier "87 of 2,241" figure is withdrawn (`APP_SCAN_RESEARCH.md` §0.4).
>
> **Target confidence:** high = named in the advisory text (code form) and
> changed between the installed version and the first fixed version above it.
> Medium = one of the two. The model may only rank within that set.
>
> **L3 exception (scoped):** the L3 freeze is lifted for exactly three shapes:
> - property/descriptor reads on a statically known class (new rule
>   `property_access`, medium);
> - `self.method()` into linked dependency classes;
> - module-attribute calls through `__init__` re-exports, and class
>   classmethod/staticmethod calls via from-imports.
>
> The exception holds only while the 30 stub-eval verdicts are unchanged and no
> corpus verdict moves to `not_reachable`. Everything else in L3 stays frozen.
>
> **Frozen eval rules:** `PHASE_APP_SCAN.md` §4 (G1/G2/G3/G5/G6/G7 hard, G4
> reported).

## 9. Critique resolution (`.agent/critique.md`)

| Item | Resolution |
|---|---|
| B1 taint direction | Moot: no `not_reachable` (D3). Config-driven loading appears as wording on `package_installed_not_imported`, checked by T3/T4 |
| B2 eligible set wrong | 87 figure withdrawn in R §0.4, §4.3, §5.5 and here; corrected result is 0 on both apps (psycopg URL string, `config.py:39`) |
| B3 incomplete closure | `closure_complete` and `closure_gaps` on every result; T5–T7 |
| B4 R4 modules only | R4 dropped; `non_py_changes` flag; targets only |
| B5 wrong diff pair | installed ↔ first fixed above installed (§2.4) |
| B6 roots incomplete | No `not_reachable`, so roots only shape evidence; host/plugin wording on level 2; T1 |
| B7 closure semantics | Defined in §2.3, with tests in A2's gate |
| M1 G3 candidate set | G3 pinned to `true_symbols`; a path to another symbol is a failure |
| M2 miniatures | Real-wheel lane is the phase gate; miniatures must copy trap features |
| M3 G3 rows and amendment | Only E1 is hard; realistic rows reported; amendment recorded in §4 |
| M4 traps and circular gate | T1–T9 added; A2's gate uses named fixtures, not G1 "so far" |
| M5 label keys | Label schema includes inventory and reason sets |
| M6 markers and multi-version | §2.6 statuses; per-(dist, version) findings |
| M7 reason drift | Closed registry, §2.5 |
| M8 unit size and checkability | A1 split into A1a/A1b; fetch and closure (A2) separate from API (A3); A5 timing threshold; smoke is a human-run item |
| M9 version filter | Exact definition in §2.4 |
| M10 namespace packages | `RECORD`-based `dist_of`; namespace dirs owned by no dist (§2.3) |
