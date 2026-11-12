# Phase App Scan: which advisories in my dependencies can my app reach?

Status: **spec only, nothing built.** Every component below is *proposed*.
Research and measurements are in `agent_docs/research/APP_SCAN_RESEARCH.md`
(cited here as "R §n"). Baseline: `main` at `2a540d5` (tag v0.3.5), pytest
461 passed, 3 deselected.
Target tag on completion: none proposed until the units are approved.

> **Critique status (2026-11-12): REVISE, not yet applied.** The plan-mode
> critique (`.agent/critique.md`, gitignored) found 7 BLOCKERs, all in the
> `not_reachable` rule R1–R4 (§2.3), plus 10 MAJORs in the eval (§4). Verified
> so far: the FastAPI template loads psycopg only through a
> `postgresql+psycopg://` URL string (`backend/app/core/config.py:39`), and
> SQLAlchemy imports dialects with `__import__(modulepath)`
> (`sqlalchemy/util/langhelpers.py:351`). So R2's lock-graph taint direction
> is unsound, and the "87 of 2,241 eligible" figure (§0, §8) includes code
> that is loaded at runtime. **Until this is revised, §2.3 R1–R4, §2.4 step 3,
> A3, A4 and §4 must not be implemented as written.** Agreed revision
> direction:
> - drop module-level R4;
> - allow `not_reachable` at distribution scope only, and only when:
>   - every locked dist was fetched and parsed cleanly, with no skipped files;
>   - there is no non-literal import or stdlib string-loader anywhere in the
>     closure (corpus-wide, D1 precedent);
>   - the roots include every entry point, every dist's `__main__` and
>     `sitecustomize`;
>   - the dist has no module in the closure and has at least one Python module.
>
> Under that rule both measured apps have **0** eligible dists, because both
> contain import-reachable dynamic imports. Further changes:
> - advisory-level `reachable` would require a high-confidence target;
>   otherwise `unknown` `REACHES_FIX_CANDIDATE`, with the path shown;
> - diff the installed version against the first fixed version above it;
> - G3 is pinned to a labelled true symbol;
> - the phase gate runs on real wheels, with trap fixtures (uvicorn→httptools,
>   zoneinfo→tzdata, URL→psycopg, INSTALLED_APPS, NO_WHEEL ancestor,
>   SyntaxError, symlink);
> - a closed reason-code registry.

## 0. Goal and non-goals

**Goal.** A user points the service at a Python application on the same
machine. The service would:
1. read the app's lockfile;
2. list the exact installed dependency versions;
3. find every advisory in a pinned OSV snapshot that applies to them;
4. work out where in each dependency the fix landed;
5. return **one verdict per advisory, with evidence**.

`reachable` would always carry a call path. `not_reachable` would always carry
the import-closure argument that justifies it. `unknown` would always carry a
reason code and a plain-English sentence.

**What success looks like, stated honestly up front.** Measured on two real
apps (R §5.5), a confident `not_reachable` is possible for **0 of 131**
dependency modules (Flask tutorial) and **87 of 2,241 (3.9%)** (FastAPI
template) under rules that survive the measured failure cases. This phase is
therefore **not** a "how many advisories can I ignore" tool. Its value would be:
- (i) `reachable` findings with a concrete path through the app into the
  dependency;
- (ii) a graded `unknown` that says *why*;
- (iii) `not_reachable` for distributions and modules the app genuinely never
  loads.

**Non-goals (do not build, do not describe as built):**
- No change to `/v1/triage`, its request or response shape, or its job table.
- No archive upload and no git URL input (R §1.2). `repo_url` stays NOT BUILT.
- No dependency resolution. No resolver runs, no sdist is built, no package
  code is imported or executed (R §2.2, R §10).
- No call-graph-based `not_reachable` for a dependency symbol. A "no path"
  result into a dependency is `unknown`, reason `NO_STATIC_PATH_IMPORTED`
  (R §5.3: a measured false `not_reachable` via `request.form`).
- No framework-aware analysis: properties, Django URLconf, signals, `Depends`,
  template-text filter calls. No change to L3's resolution capability, which is
  frozen (CLAUDE.md).
- No live OSV query at scan time. No runtime fetch of fix commits unless
  decision D4 says otherwise.
- No severity scoring of our own. The record's severity is shown as given.
- No web UI work. The UI redesign waits for this spec.
- No real-model run by the agent. Real-model runs are Raghav's only.
- No claim of exploitability or non-exploitability. `not_reachable` means "not
  loaded", never "safe".

## 1. Architecture (proposed)

```
POST /v1/scans {path}
      │  (api: validate path is relative; insert scans row, status=queued)
      ▼
worker claim (scans, SKIP LOCKED; same claim/finalize/reaper contract as triage_jobs, DECISIONS §8)
      │
      ├─ 1. locate:   /scan-inputs/<path>  (ro bind mount; resolve(strict) + is_relative_to; no symlinks followed)
      ├─ 2. inventory: lockfile → [(dist, version, wheel url+hash | status)]          (scan/inventory.py)
      ├─ 3. match:    pinned OSV snapshot → advisory groups per (dist, version)        (scan/advisories.py)
      ├─ 4. fetch:    wheels for matched dists + their lock-graph ancestors; sha256-verified (existing wheel-only rule)
      ├─ 5. index:    per-dist L1–L3 index, cached by (dist, version, wheel sha256, schema v) (scan/dist_index.py)
      ├─ 6. targets:  wheel diff last-affected ↔ first-fixed → changed-module set + candidate symbols (scan/targets.py)
      ├─ 7. decide:   import-closure rule R1–R4 → module/distribution not_reachable | unknown
      │               linked call graph from app start points → reachable | reachable_only_from_tests | unknown
      ├─ 8. rollup:   per advisory (§2.4)
      └─ 9. finalize: scan_findings rows + summary
```

New package `src/reachability/scan/`. The existing `index/` stays frozen,
except for **no-new-capability** helpers that the units below name explicitly
(a cross-version symbol key; computing D3 once per index). The LangGraph loop
is **not** in this flow (§2.6).

## 2. Data model and API (proposed)

### 2.1 HTTP

| Call | Success | Errors |
|---|---|---|
| `POST /v1/scans` body `{path: str}` (1–512 chars, relative, no `..`) | 202 `ScanOut`, `Location: /v1/scans/{id}` | 422 envelope (existing) |
| `GET /v1/scans/{id}` | 200 `ScanOut` | 404 envelope |

```
ScanOut = {
  id, status: queued|running|completed|failed, error|null, llm_mode|null,
  snapshot: {sha256, date},
  inventory: {source_file|null, target_env: "cp313-linux_x86_64",
              packages: [{name, version|null, status: pinned|unpinned|no_wheel|marker_skipped}]},
  summary: {reachable, reachable_only_from_tests, not_reachable, unknown, malicious_package?,
            unknown_by_reason: {CODE: n}},
  findings: [Finding]
}
Finding = {
  advisory_id, aliases[], dist, version, verdict, scope: symbol|module|distribution,
  reason_code|null, reason|null, sentence, path: CallEdge[]|null,
  targets: [{node_id, source, source_confidence, exists_in_installed, file, lineno}],
  changed_modules: [str]
}
```

### 2.2 Tables (one migration, `0005_create_scans`)

- `scans`: `id` uuid pk, `status`, `path`, `inventory_json`,
  `snapshot_sha256`, `llm_mode`, `error`, `worker_id`, `attempt_count`,
  `created_at`, `updated_at`.
- `scan_findings`: `id`, `scan_id` FK (on delete cascade), `advisory_id`,
  `aliases` json, `dist`, `version`, `verdict`, `scope`, `reason_code`,
  `reason`, `path_json`, `targets_json`, `changed_modules` json.
- `dist_index_cache`: pk (`dist`, `version`, `wheel_sha256`,
  `index_schema_version`), `blob` bytea, `created_at`.
- `advisory_targets_cache`: pk (`advisory_id`, `last_affected`,
  `first_fixed`, `method_version`), `changed_modules` json, `candidates` json.

### 2.3 Verdict per target (R §7.1)

- `reachable` / `reachable_only_from_tests`: `compute_reachability` over the
  linked index with **app start points only**. Library `__main__`, tests,
  fixtures, routes and CLI are never start points (R §5.3: 900+ polluting
  start points measured).
- Dependency target with no path → **`unknown`, `NO_STATIC_PATH_IMPORTED`**,
  never `not_reachable`.
- `not_reachable` only from the import-closure rule:

> **R1** the module is outside the import closure from {app modules} ∪ {every
> entry-point target declared by any installed distribution, any group}, where
> importing `a.b.c` executes `a` and `a.b`.
> **R2** no import-reachable non-literal `import_module`/`__import__` exists in
> its distribution, or in any distribution that depends on it in the lock graph.
> **R3** its distribution ships no `.pth` file.
> **R4** (advisory scope) R1–R3 hold for **every** module in the wheel diff's
> changed-module set, not just ranked symbols.

### 2.4 Advisory rollup

1. Any target `reachable` → `reachable` (shown path: the highest-provenance
   target).
2. Else any `reachable_only_from_tests` → that.
3. Else R1–R4 hold over the full changed-module set (or the whole distribution
   when no diff exists) → `not_reachable`, scope `module`/`distribution`.
4. Else `unknown`, most specific reason. Precedence:
   `VERSION_UNPINNED` > `NO_WHEEL` > `NO_FIX_VERSION` > `NATIVE_ONLY_CHANGE` >
   `DEPENDENCY_PIN_FIX` > `DYNAMIC_IMPORT` > `ENTRYPOINT_OR_PLUGIN` >
   `PTH_FILE` > `NO_STATIC_PATH_IMPORTED` > existing engine reasons.

A target ranking (model or deterministic) can change which path is *shown*. It
can never change steps 3–4.

### 2.5 Sentences

Fixed templates from R §7.3, one per verdict and per reason code. They are
tested as constants; no model-written prose.

### 2.6 What the LangGraph agent does here

Nothing on the verdict path. Targets come from the wheel diff, and queries are
deterministic. The optional model use is ranking candidates (unit A7), behind
`TRIAGE_LLM_MODE`, with a deterministic stub in CI.
`run_triage_loop_langgraph` is not called by scans.

### 2.7 Budgets (constants, each with a test)

| Budget | Value | When exceeded |
|---|---|---|
| Scan wall clock | 600 s | scan `failed`, error `"scan time budget exceeded"` |
| Advisories per scan | 200 | the rest get `unknown` `SCAN_BUDGET` |
| Target symbols per advisory | 20 | ranked first 20; R4 still uses the full module set |
| Distributions indexed per scan | 100 | `unknown` `SCAN_BUDGET` for the rest |
| Scan input | 20,000 files / 200 MB / depth 32 | scan `failed`, named limit |
| Wheel download | 120 s each (as today) | `unknown` `NO_WHEEL` with the pip error |
| Model calls | ≤ 1 per advisory (A7 only) | ranking falls back to the stub order |

## 3. Units

Each unit is one `/loop` cycle with its own [GATE]. Branch per unit:
`app-scan-aN-<slug>`. Smallest shippable first. Every gate includes: full
bare `pytest` (count reported against the previous unit), app starts clean, no
debug prints, and `git diff --stat main -- src/reachability/index` is empty
unless the unit names an index helper.

### A1: Offline advisory inventory (useful on its own)
- `src/reachability/scan/inventory.py`: strict parsers for `uv.lock`,
  `pylock.toml`, `poetry.lock`, `Pipfile.lock`, and fully pinned
  `requirements.txt` (`-r` within the root only). Precedence and status
  values per R §2.3. Markers are evaluated for the fixed target env.
- `src/reachability/scan/advisories.py`: load a snapshot zip (sha256-checked),
  exclude withdrawn records, group aliases (union-find), match by `versions`
  ∪ OSV-range evaluation (`introduced: "0"` means −∞; `last_affected`
  inclusive), and honour `last_known_affected_version_range`. Malware is
  classified per decision D5.
- `scripts/scan_inventory.py <dir> --snapshot <zip>` prints a table: dist,
  version, advisory, aliases, fixed-in.
- New pinned dependency: `packaging` (Apache-2.0 OR BSD-2-Clause), added to the
  plan first.
- Fixtures: `tests/fixtures/app_scan/advisories/` holds **the subset of OSV
  records the tests need**, copied from the pinned snapshot (sha256 `c7ea4e15…`,
  2026-11-12), plus a `NOTICE` (CC-BY-4.0 attribution). Lockfile fixtures are
  hand-written miniatures in each format.
- **Gate:**
  - G5 inventory exact-match on every lockfile fixture;
  - a matcher table test with ≥ 12 rows from real records: pre-release vs
    `introduced: "0"`, `last_affected`, empty `versions`, a non-PEP 440
    string → `unknown`, a withdrawn record excluded, an alias group collapsed;
  - an unpinned line yields status `unpinned`, never a guessed version;
  - no network in tests (a socket-blocking fixture).

### A2: Scan resource (API, table, worker), inventory-only verdicts
- Migration `0005`. `POST/GET /v1/scans`. Worker claims scans in the same poll
  loop. Reaper covers `scans`.
- Compose: worker gets `- ${SCAN_INPUTS_DIR:-./scan-inputs}:/scan-inputs:ro`
  and the snapshot as a read-only file mount. The API does not mount either.
- Path rules from R §1.1 (b). Discovery for scans walks with
  `followlinks=False` (a scan-side walker; `index/discovery.py` untouched).
- Every matched advisory → `unknown`, `TARGET_NOT_RESOLVED`, sentence
  *"Matched to your installed version; reachability not analysed yet."*
- OpenAPI snapshot (`web/openapi.json`) regenerated. `/v1/triage` schema diff
  must be empty.
- **Gate:**
  - claim/finalize/reaper tests mirror the `triage_jobs` ones;
  - path-escape tests (`..`, absolute, symlink to outside, symlink inside →
    skipped and listed);
  - `/v1/triage` OpenAPI unchanged;
  - `scripts/smoke_compose.sh` run by Raghav.

### A3: Distribution-level verdict (import closure)
- Fetch wheels for matched distributions and every lock-graph ancestor down to
  the app's direct dependencies (whole pinned set for graph-less formats).
  Fetch is wheel-only, with sha256 from the lock when present.
- L1-only per-dist import tables; app + entry-point roots (R1); dynamic-import
  taint over the lock graph (R2); `.pth` check (R3).
- Distribution never loaded → `not_reachable`, scope `distribution`.
  Otherwise `unknown` with the R-rule reason.
- **Gate:**
  - the measured cases as fixtures: a uvicorn-hosted app where `httptools` is
    never imported by app code must be **not** `not_reachable` (entry-point
    roots); a dynamic-import-tainted dist must be `unknown`;
  - eval G1 holds on every fixture added so far.

### A4: Fix diff targets and module-level verdict
- `scan/targets.py`: last-affected / first-fixed selection; wheel diff on a
  **stable cross-version key** `(module, qualname, ordinal)` (fixes R §4.3
  noise class iv, `@lineno` ids); drop version-string-only module changes; flag
  `NATIVE_ONLY_CHANGE` (no `.py` changes) and `DEPENDENCY_PIN_FIX`
  (`Requires-Dist` changed, no code change); cache in
  `advisory_targets_cache`.
- R4 over the full changed-module set → module-scope `not_reachable`, else
  `unknown`.
- **Gate:**
  - the 5 measured diffs (R §4.3) reproduced as tests over committed
    *miniature* wheels (no real wheels committed): the true symbol is in the
    candidate set, and the version-string change is filtered;
  - the jinja2-style overload shift yields no phantom add/remove pairs.

### A5: Linked cross-distribution call graph (`reachable`)
- `scan/dist_index.py`: per-dist L1–L3 with the wheel as its own root, cached.
  Link step re-resolves `ext:<module>.<name>` callees across cached indexes.
  The module → dist map comes from wheel `RECORD`; namespace-package merge;
  same-file collisions → `unknown`.
- D3 name-escape set computed once per linked index (no-new-capability helper
  in `index/`; behaviour identical, tested by the existing D3 tests).
- App start points only. A dependency "no path" → `NO_STATIC_PATH_IMPORTED`.
- **Gate:**
  - **parity**: the linked index's edge set equals the single-root combined
    build (R §5.2 method) on flaskr-style and FastAPI-style miniature fixtures;
  - the `request.form` regression fixture is `unknown`, not `not_reachable`;
  - per-query time on the largest fixture is reported.

### A6: Eval corpus and CI gate (stub lane)
- The fixture set from §4, labels as frozen in §4, `scripts/run_scan_eval.py`,
  and a new CI job `scan-eval` (no Postgres, no network: wheels and snapshot
  subset are committed miniatures or test-built).
- **Gate:** §4 decision rules applied verbatim and reported.

### A7 (optional): Ranking
- Deterministic stub ranker (symbol named in advisory text first, then
  lexical). Optional `GroqLLMClient` ranker behind `TRIAGE_LLM_MODE=groq`:
  advisory text through `sandbox_untrusted_text`; output ids must be in the
  candidate set, or pass the existence check.
- Fix-commit narrowing (`github.com/.../commit/<sha>.patch`) only if decision
  D4 allows it; intersect only.
- **Gate:**
  - property test: for every fixture, permuting the ranking never changes any
    advisory verdict (§2.4);
  - real-model ranking run by Raghav only.

### A8: Docs
- README section, `agent_docs/DEMO.md` scan walkthrough, `DECISIONS.md` §26
  (final text from the draft below), CLAUDE.md NOT BUILT updates (§7).
- **Gate:** every doc claim has a file:line.

## 4. Eval protocol (pre-registered; frozen at spec approval)

These rules are part of this spec. They may not be amended after any A6 run
starts. Changes before that need Raghav's approval, recorded as an amendment
with its date.

**Fixtures** (`tests/fixtures/app_scan/NN_name/`): an app tree, a lockfile,
and `label.json` = `{advisory_id: [allowed verdicts]}`. Advisory records come
from snapshot sha256 `c7ea4e15b83912960fc5cbd42c6003dd630e422ec4dfbfc4ed19445573e1b08c`.
Every vulnerable distribution appears in a **calls** app and an **installed,
not called** app:

| # | Advisory | Pair | App | Allowed |
|---|---|---|---|---|
| 01 | GHSA-r7w7-9xr2-qq2r (langchain-openai `_url_to_size`) | 1.1.13/1.1.14 | calls `get_num_tokens_from_messages` | {reachable, unknown} |
| 02 | same | same | installs, never imports | {not_reachable, unknown} |
| 03 | GHSA-gmhf-gg8w-jw42 (signxml `XMLVerifier.verify`) | 4.0.3/4.0.4 | calls `XMLVerifier().verify` | {reachable, unknown} |
| 04 | same | same | imports `signxml` for `XMLSigner` only | {unknown} |
| 05 | GHSA-qg2p-9jwr-mmqf (Django urlize) | 4.2.13/4.2.14 | calls `django.utils.html.urlize` | {reachable, unknown} |
| 06 | same | same | Django installed, urlize never called | {unknown} |
| 07 | GHSA-wqp7-x3pw-xc5r (Starlette StaticFiles) | 1.0.1/1.1.0 | mounts `StaticFiles(...)` | {reachable, unknown} |
| 08 | same | same | starlette app, no StaticFiles | {unknown} |
| 09 | GHSA-q34m-jh98-gwm2 (Werkzeug form file parsing) | 3.0.5/3.0.6 | Flask view reads `request.form` | {unknown} *(regression guard: the true answer is reachable, which static analysis cannot show)* |
| 10 | same | same | Flask app, never reads forms | {unknown} |
| 11 | none | – | `requirements.txt` with an unpinned line | that dist's advisories: {unknown} with `VERSION_UNPINNED` |
| 12 | none | – | lock with an sdist-only dist | {unknown} with `NO_WHEEL` |

"Reachable" here means the label author verified the call by reading the code.
Fixtures carry miniature stand-in wheels whose module and symbol layout copies
the real distribution's for the touched modules. **ASSUMPTION**: miniatures are
faithful enough. The A6 gate also runs fixtures 01, 03 and 05 once against the
real wheels locally, outside CI, and reports the result.

**Decision rules:**
- **G1 (hard)**: zero `not_reachable` on any row whose label set excludes it
  (rows 01, 03–10). One violation fails the phase gate.
- **G2 (hard)**: every row's verdict is in its allowed set, and every
  `unknown` carries the reason code named in the row where one is named.
- **G3 (hard)**: of rows 01, 03, 05, 07, at least **3 of 4** are `reachable`,
  with a path whose final callee is in the A4 candidate set.
- **G4 (reported only)**: `unknown` rate over all rows; distribution of reason
  codes.
- **G5 (hard)**: inventory exact match (dist, version, status) on every
  fixture lockfile.
- **G6 (hard)**: zero network access during the CI eval.
- Budget exhaustion is never counted as a pass for any row (§13 precedent).
- The stub lane runs in CI as job `scan-eval`. Model-ranked runs (A7) are
  Raghav's only and are reported against the same rules, never gating CI.

## 5. Decisions for Raghav (the GATE)

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | Input | (a) archive upload · (b) ro bind-mounted directory · (c) git URL | **(b)**: no new network or archive parser; (a)/(c) can be added later as front ends (R §1) |
| D2 | Inventory | (i) lockfile or fully pinned only · (ii) also allow `pip --dry-run --only-binary=:all:` resolution, labelled "resolved by scanner" | **(i)**: (ii) does not execute code (measured) but is not reproducible and not what the user runs (R §2.2) |
| D3 | What may be `not_reachable` | (α) import-closure only (R1–R4) · (β) α + call-graph no-path for "leaf" libraries · (γ) never in this phase | **(α)**: (β) fails on the measured `request.form` case; (γ) throws away the distribution-level answers that are sound |
| D4 | Advisory data and network | (p) mounted pinned OSV snapshot only · (q) p + optional github.com fix-commit fetch (off by default) · (r) live OSV API | **(p) for A1–A6, (q) only in A7**: offline, reproducible, no new egress on the verdict path |
| D5 | Malicious-package records (MAL-* and PYSEC malware, 6.1% of groups) and app-kind advisories | (m1) a fifth finding category `malicious_package`, outside the four verdicts · (m2) excluded from findings, listed in inventory warnings · (m3) treated like any advisory | **(m1)**: hiding them (m2) is worse than any reachability answer; (m3) makes a meaningless call-graph query. App-kind advisories go through the normal path. They usually end `unknown` or `not_reachable` (distribution) and are not special-cased |

## 6. Risks

- **The headline number disappoints.** A measured 0% and 3.9% eligibility for
  `not_reachable`. Mitigation: the spec states it in §0, and the UI/README
  must lead with `reachable` paths and reasons, not with a "safe" count.
- **Inversion of control.** Servers and frameworks call the app, so any
  call-graph negative into a framework is unsafe (measured). It is contained
  by D3 (α) and the `NO_STATIC_PATH_IMPORTED` mapping. Fixture 09 guards it.
- **Re-export gap** (`jwt.decode` → `?:decode`, measured). This loses
  `reachable` answers and is safe. Fixing it is new L3 resolution capability,
  which is out of scope (L3 frozen); a later phase can take it up.
- **Corpus-wide D1/D3 over dependency code** turn most answers into `unknown`
  (6 of 6 FastAPI probes). Accepted for this phase; scoping D1/D3 to the
  linked path is a separate decision with its own fixtures.
- **Bundled fixes** in one release (Django) give low symbol precision. The
  module-set rule (R4) is unaffected; displayed "primary symbol" may be wrong.
  The UI must show all candidates with provenance.
- **Snapshot staleness.** A pinned snapshot misses new advisories. Each scan
  records the snapshot date, and the summary says "advisories as of <date>".
- **Lock-graph incompleteness** (vendored or undeclared imports). R1's import
  closure is computed over all indexed dists, not over the lock graph, so a
  missing lock edge can only *add* `unknown`. **ASSUMPTION** that undeclared
  imports resolve to an installed dist at all; if not, they are `ext:` and
  unfollowed.
- **Cost.** 13.2 s to index the FastAPI combined tree, 3.9–4.9 s per query
  before the D3 cache (measured). Budgets in §2.7.
- **Licence.** CC-BY-4.0 attribution for any committed advisory subset (NOTICE
  file). Chainguard/Wolfi records are non-commercial, but they are not in the
  PyPI export (**ASSUMPTION**: not checked record by record).

## 7. NOT BUILT lines this phase would change

This would be added to CLAUDE.md NOT BUILT when A8 lands, and stay true after
the phase:
- App scan input by archive upload or git URL (scans take a relative path under
  a read-only mount only).
- Dependency resolution for unpinned requirements (an unpinned dist is
  reported `VERSION_UNPINNED`).
- Call-graph-based `not_reachable` for dependency symbols. A "no path" result
  into a dependency is `unknown` (`NO_STATIC_PATH_IMPORTED`).
- Framework-aware edges (property/descriptor access, Django URLconf, signals,
  FastAPI `Depends`, template-text filter calls).
- Live OSV queries; automatic snapshot refresh.
- Fix-commit fetching, unless A7 ships with D4 = (q).
- Severity scoring of our own.

Removed from NOT BUILT: nothing. `repo_url` acquisition stays NOT BUILT.

## 8. Draft `DECISIONS.md` §26 (draft only; not applied to DECISIONS.md)

> ## 26. App scan: dependency advisories, verdicts by import closure (draft, 2026-11-12)
>
> **Decision.** A new resource, `/v1/scans`, would take a relative path under a
> read-only mount, read a lockfile, match a pinned OSV PyPI snapshot, and
> return one verdict per advisory. `/v1/triage` is unchanged.
>
> **`not_reachable` comes only from the import closure (R1–R4), never from a
> call-graph negative into a dependency.** Measured on the Flask tutorial: the
> combined app+dependency call graph reports `not_reachable` for werkzeug's
> multipart parser, which `request.form` runs at runtime
> (`APP_SCAN_RESEARCH.md` §5.3). Attribute loads that run code and
> servers that call the app are outside L3's model, and L3 is frozen. A call-graph
> "no path" into a dependency is therefore `unknown`
> (`NO_STATIC_PATH_IMPORTED`).
>
> **The import closure starts from every installed entry point, not only the
> app**, because the process host (uvicorn under `fastapi run`) and plugins
> load modules the app never imports. Without those roots, `httptools` and
> `websockets` appeared "never imported" in the FastAPI template.
>
> **Advisory scope uses the full changed-module set of the wheel diff**, so no
> ranking (deterministic or model) can turn `unknown` into `not_reachable`. The
> model never sets a verdict; it orders candidates only.
>
> **Inventory needs a lockfile or a fully pinned requirements file.** No
> resolver runs. Unpinned → `unknown` (`VERSION_UNPINNED`). No wheel → `unknown`
> (`NO_WHEEL`). This is the same wheel-only rule as `acquisition.py`.
>
> **Accepted consequence.** Under these rules 0 of 131 (Flask tutorial) and 87
> of 2,241 (FastAPI template) dependency modules can ever be `not_reachable`. The
> phase's value is `reachable` with a path, plus a graded `unknown`. This is
> stated in the README and is not presented as a "safe to ignore" count.
>
> **Frozen eval rules:** `PHASE_APP_SCAN.md` §4 (G1/G2/G3/G5/G6 hard, G4
> reported).
