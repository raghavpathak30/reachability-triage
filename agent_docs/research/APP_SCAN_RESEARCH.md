# App scan: research report

Status: research only. Nothing in this file is built. Every design in it is
**proposed**. Where this report's design sections (§4.5–§4.7, §5.5, §7, §9)
differ from `agent_docs/PHASE_APP_SCAN.md`, the spec wins. In particular, the
spec issues **no `not_reachable` at all** in this phase, and it adds an
evidence level to every finding.

**Corrections (2026-11-12, after the plan critique; the original text is
replaced, not kept):**
- The "87 of 2,241 dependency modules eligible for `not_reachable`" figure is
  **withdrawn**. Under a corrected, corpus-wide rule, **0** modules are eligible
  in both apps (§0.4, §5.5).
- The Flask tutorial unresolved-edge figure was miscounted as "101 of 182
  (55%)". The correct figure is 104 of 177 app edges unresolved (59%), of
  which 101 (57%) are `unresolved_attribute` (§5.3). Branch `app-scan-research`, from `main` at `2a540d5` (tag v0.3.5).
Date of all reads and measurements: **2026-11-12**.

**How claims are marked.**
- `[src: URL, read 2026-11-12]`: a web source. Vendor pages are tagged
  **(vendor claim)**. They are statements, not evidence.
- `[measured]`: run locally. The command and a summary of its output are given.
  Scripts, downloads and results live under `/tmp/app-scan-research/`, outside
  the repo. Nothing from there is committed. That directory is ephemeral, so the
  commands are stated in full enough to re-run.
- **ASSUMPTION**: not verified. It must be checked before anyone relies on it.
- `[repo: file:line]`: read from this repo at `2a540d5`.

The measurement environment was Python 3.13.5 (`.venv` and system), pip from
`.venv`, git 2.47.3 and Linux x86_64 `[measured]`.

---

## 0. What surprised us

1. **Advisories almost never name the vulnerable function in a
   machine-readable field.** The PyPA advisory format supports
   `affected[].ecosystem_specific.imports` (module plus attribute)
   `[src: https://github.com/pypa/advisory-database, read 2026-11-12]`. Only
   **2 of 14,236** live non-malware PyPI records in the OSV export use it
   (`PYSEC-2026-4009`, `PYSEC-2026-4010`) `[measured, §4.1]`. In practice the
   target symbol has to be derived.
2. **A naive "index the app plus its dependency wheels together" approach gives
   a confidently wrong `not_reachable` on the textbook Flask case.** The Flask
   tutorial app reads `request.form["username"]` (`flaskr/auth.py:54`). At
   runtime that runs werkzeug's multipart parser. Our engine, indexing app plus
   wheels as one root, answers `not_reachable` for
   `werkzeug.sansio.multipart:MultipartDecoder` and for
   `werkzeug.wrappers.request:_load_form_data`. `request.form` is a
   `cached_property` *attribute load*, not an `ast.Call`, so L3 never sees an
   edge `[measured, §5.3]`. This is the worst bug class this project defines.
3. **On a larger app the same naive approach goes the other way: everything is
   `unknown`.** On the FastAPI full-stack template, all 6 probe queries returned
   `unknown`. Most were caused by D1 (a corpus-wide opaque-call check) firing on
   `fastapi/encoders.py:337`, which is reachable from the app's tests
   `[measured, §5.3]`.
4. **No sound `not_reachable` for a dependency was found on either app.** An
   import-graph rule (§5.5) first appeared to leave 87 of 2,241 FastAPI-template
   dependency modules eligible. **That figure is withdrawn.** The eligible set
   included code that is loaded at runtime without any literal import:
   - **psycopg**: the template never imports it. It builds the string
     `"postgresql+psycopg://"` (`backend/app/core/config.py:39`), and SQLAlchemy
     loads the driver with `__import__(modulepath)`
     (`sqlalchemy/util/langhelpers.py:351`) `[measured: read both files]`.
   - **tzdata**: loaded by stdlib `zoneinfo`. This is a known stdlib behaviour,
     not traced in this loop (**ASSUMPTION**).

   The original rule only tainted the lock-graph dependencies of a dynamic
   importer. Config-driven imports go the other way: the importer loads
   whatever the config names. A rule that treats any import-reachable
   non-literal import as corpus-wide (the D1 precedent) leaves **0 eligible on
   both apps**, because both have import-reachable dynamic imports (5 modules
   in flaskr, 24 in FastAPI) `[measured]`. The product's value is therefore:
   - (i) `reachable` with a concrete path;
   - (ii) `unknown` with a graded evidence level and reason.

   The spec issues no `not_reachable` in this phase.
5. **About half of PyPI advisories are about applications, not library APIs.**
   Examples are pyLoad, Open WebUI, MLflow server and Cobbler. In the hand
   labelling, 28 of the 57 non-malware advisories describe a server, tool or UI
   whose vulnerable code runs inside that product's own process `[measured,
   §4.2]`. "Your code reaches it" barely applies to these.
6. **The OSV PyPI export has malware outside the `MAL-` prefix.** 12,020
   `MAL-*` records sit in the export. Among the 7,086 live non-MAL groups, 42
   are identifiable as malware by structure: a `MAL-*` alias (41) or a summary
   starting "Malicious code in" (38), with overlap. 9 more carry CWE-506.
   Examples are the `PYSEC-2026-*` "Hades"/Shai-Hulud entries `[measured]`.
   Some malware records carry none of these signals (e.g. `PYSEC-2025-7`,
   imblog). Reachability is meaningless for these: installing the package is the
   compromise.

   *Correction:* an earlier draft said "431 groups (6.1%)". That was a keyword
   count of "malicious", which also matches ordinary advisories ("malicious
   input"), so it was wrong.
7. **A wheel diff finds the fix, but releases bundle fixes.** The single Django
   4.2.13→4.2.14 diff holds the fixes for several advisories. Only 1 of its 8
   changed functions belongs to the urlize advisory `[measured, §4.3]`. Our own
   node-id scheme also adds noise: overloaded functions get `@<lineno>`
   suffixes, so a line shift looks like a removed function plus an added one
   (15 phantom pairs in jinja2 3.1.3→3.1.4) `[measured]`.
8. **`compute_reachability` re-parses every module on every call** (D3,
   `[repo: src/reachability/index/reachability.py:136]`,
   `[repo: src/reachability/index/edges.py:454-465]`). That cost 3.9–4.9 s per
   query on a 2,281-module combined index `[measured]`. One scan means many
   queries, so this would need caching per index.
9. **Python's `zipfile.extractall` neutralises `..` and absolute paths but not
   archive bombs, and it writes symlink entries as plain files.** A 49 KB zip
   expanded to 50 MB with no complaint `[measured, §1.2]`.

---

## 1. Input: how the user gives us their app

### 1.1 Options compared

| | (a) archive upload (zip / tar.gz) | (b) local directory path (compose bind mount) | (c) git URL |
|---|---|---|---|
| New network destination | none | none | arbitrary git hosts (SSRF surface) |
| Parses attacker-shaped container format | yes | no | yes (git packfiles, config) |
| Path escape | zip-slip, tar `..`/absolute/links | symlinks inside the tree pointing out of the mount | symlinks in the checkout; submodule paths |
| Resource exhaustion | archive bomb, file count | huge trees | huge repos, LFS, deep history |
| Code execution risk | none if never imported/executed | none if never imported/executed | hooks, `ext::` transport, filters/`smudge` via config, submodule tricks |
| Fits "local only" (Phase 7) | yes | yes, the most natural fit | weakest |
| UX | needs an upload endpoint (multipart) | user edits a `.env`/compose override | best |

**Risks and mitigations, (a) archives:**
- *Zip-slip.* Measured on Python 3.13.5: `zipfile.ZipFile.extractall` rewrote
  `../escape.txt` and `/abs.txt` to land inside the destination `[measured:
  /tmp/app-scan-research/ziptest, a crafted zip with ../escape.txt, /abs.txt, a
  symlink entry and a 50 MB deflated file; all five entries landed under out/]`.
  We would not rely on that alone. Proposed: walk `infolist()` first and
  reject any name that is absolute, contains `..`, contains a drive letter or a
  backslash, or normalises outside the root.
- *Symlinks.* `zipfile` wrote a symlink-mode entry as a regular 11-byte file
  `[measured]`. For tar, the `data` extraction filter refuses absolute links,
  links outside the destination, device files and pipes. It became the default
  only in Python 3.14
  `[src: https://docs.python.org/3/library/tarfile.html, read 2026-11-12]`.
  Proposed: reject any archive that contains a symlink or hardlink entry at all
  (zip `external_attr` S_IFLNK; tar `issym()`/`islnk()`), and always pass
  `filter="data"` explicitly.
- *Bombs.* `zipfile` does not limit expansion: 49,108 bytes on disk became a
  50,000,000-byte file `[measured]`. The tarfile docs say filters "do not
  prevent denial-of-service attacks" `[src: tarfile docs, read 2026-11-12]`.
  Proposed: a declared-size check before extraction, then a streaming byte
  counter during extraction, because declared sizes can lie (**ASSUMPTION**
  for zip: whether a lying `file_size` is caught before bytes are written is
  not measured). Limits would be stored as constants (proposal: 50 MB
  compressed, 200 MB extracted, 20,000 files, 255-byte names, depth 32), and
  only `.py`, `.toml`, `.lock`, `.txt`, `.cfg` and `.json` files would be kept.
- *Upload endpoint.* A multipart upload adds a parser and a request-size limit
  in FastAPI and nginx (`client_max_body_size`). That is a new attack surface in
  the API container, which today has none for files.

**Risks and mitigations, (b) local directory:**
- The worker sees only a read-only bind mount (proposed `/scan-inputs:ro`),
  configured by the operator in compose. The request carries a *relative* path
  under that root.
- *Path escape through the request.* Resolve `root / rel` with
  `Path.resolve(strict=True)` and require `is_relative_to(root)`. Reject `..`
  segments and absolute paths before resolving.
- *Symlinks inside the tree.* `Path.rglob` in discovery would follow
  symlinked files (**ASSUMPTION** for the 3.13 symlinked-dir behaviour; not
  measured). Proposed: walk with `os.walk(followlinks=False)`, skip symlinked
  files, and record them in the scan report as "skipped: symlink".
- *Size.* The same file-count and byte limits as (a). No decompression, so no
  bomb.
- *No code execution.* The tree is only `ast.parse`d, never imported. This is
  already how acquisition treats wheels `[repo:
  src/reachability/triage/acquisition.py:24-35]`.

**Risks and mitigations, (c) git URL:**
- *Protocols.* Git's own default policy allows `http`, `https`, `git` and
  `ssh`, forbids `ext`, and gives everything else, including `file`, the `user`
  policy `[measured: man git-config, git 2.47.3, protocol.allow]`. Proposed:
  run `git -c protocol.allow=never -c protocol.https.allow=always clone` so
  only https is possible, and validate the URL scheme in pydantic as well.
- *SSRF.* The URL host must be resolved and every resolved address checked
  against private, loopback, link-local and metadata ranges. Redirects and DNS
  rebinding between the check and the connect remain a gap unless the
  connection is pinned to the checked IP. Git offers no direct way to do that;
  it would need a proxy (**ASSUMPTION**: no in-git pinning; not verified). This
  is the deferred work in `DECISIONS.md` §3 ("SSRF Protection on `repo_url`").
- *Hooks.* A fresh clone has no hooks from the remote. A local `core.hooksPath`
  could still be set by a global config. Proposed: `-c core.hooksPath=/dev/null`
  and `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`.
- *Submodules.* Never pass `--recurse-submodules`. `file://` submodules were
  CVE-2022-39253, the reason git changed the `file` policy (**ASSUMPTION**:
  the CVE-to-default-change link comes from a WebFetch summary that admitted it
  could not see the relevant section; the `user` default itself was verified
  locally above).
- *LFS.* Set `GIT_LFS_SKIP_SMUDGE=1`. We only need `.py` and lock files.
- *Size.* `--depth 1 --single-branch --no-tags`, with a timeout and a byte
  limit on the clone directory.

### 1.2 Recommendation

**(b) a local directory under a read-only compose bind mount**, for the first
units. Reasons:
1. It adds no new network destination and no new parser of
   attacker-structured containers. The service is local-only (`DECISIONS.md`
   §15), and the user already has the code on the same machine.
2. The only new check is "resolved path stays under the mount root, no
   symlinks followed". That is small, fully testable offline, and needs no
   fixtures that are themselves attack payloads.
3. (a) and (c) can be added later as *acquisition front ends* that produce the
   same "directory under a scratch root" contract. (c) inherits the existing
   SSRF deferral, which this phase would not lift.

---

## 2. Dependency inventory

### 2.1 Sources and what each gives

| File | Exact versions? | Transitive? | Hashes / URLs | Graph edges | Parse with |
|---|---|---|---|---|---|
| `uv.lock` | yes (`[[package]] version`) | yes | wheel `url` + `hash` per file | yes (`dependencies`, `optional-dependencies`) | `tomllib` (stdlib) |
| `poetry.lock` | yes | yes | `files = [{file, hash}]`, **filename only, no URL** | yes (`dependencies` table) | `tomllib` |
| `Pipfile.lock` | yes (`"version": "==x"`) | yes (flattened) | `hashes` list, no URL | **no** | `json` (stdlib) |
| `pylock.toml` (PEP 751) | yes when `version` is set (optional) | yes | `[[packages.wheels]]` url/hashes | optional `packages.dependencies`, "purely informational" | `tomllib` |
| `requirements.txt` | only if every line is `==` | only if the file is a full freeze | optional `--hash` | no | own strict parser over `packaging.requirements` |
| `pyproject.toml` (PEP 621 / Poetry) | rarely (ranges) | no | no | no | `tomllib` (top-level intent only) |

Evidence:
- `uv.lock` structure: `[measured: fastapi/full-stack-fastapi-template @ aabb901,
  uv.lock version 1 revision 5, 80 [[package]] entries; each has
  name/version/source, dependencies, wheels=[{url, hash}]]`. A runtime
  closure of the `app` member, with dev groups excluded, has 64 packages and 9
  marker-guarded edges. **One package, `dkimpy 1.1.8`, has no wheel, only an
  sdist** `[measured: scripts/uvlock_closure.py]`.
- `poetry.lock`: `[measured: python-poetry/poetry main, poetry.lock
  lock-version 2.1, 77 packages; keys dependencies, description, extras, files,
  groups, markers, name, optional, python-versions, version; files[0] =
  {file: 'anyio-4.15.1-py3-none-any.whl', hash: 'sha256:…'}]`. To download, a
  filename has to be mapped to a URL through the PyPI JSON API. Every package
  had a `groups` field, so dev/main filtering is possible.
- `Pipfile.lock`: `[measured: pypa/pipenv main, top-level keys _meta,
  default, develop; entries {hashes:[…], version:'==0.4.4'}]`. There is no
  graph, so the §5 "only dependencies on a path to the vulnerable package"
  pruning is impossible. The fallback is to index everything in `default`.
- PEP 751: Status Final, resolution 31-Mar-2025. `packages.version` is
  optional. `packages.dependencies` "Tools MUST NOT use … it is purely
  informational for auditing purposes"
  `[src: https://peps.python.org/pep-0751/, read 2026-11-12]`. pip support:
  experimental `pip lock` (25.1) and `pip install -r pylock.toml` (26.1),
  described as not production ready `[src:
  https://pydevtools.com/handbook/explanation/what-is-pep-751/ and pip 26.1
  coverage, read 2026-11-12; third-party handbook, not pip's own docs]`.
  osv-scanner lists `pylock.toml` and `uv.lock` among its supported Python
  lockfiles
  `[src: https://google.github.io/osv-scanner/supported-languages-and-lockfiles, read 2026-11-12]`.
- `requirements.txt` hazards: `-r`/`-c` includes, environment markers,
  `--hash`, `--index-url`/`--extra-index-url` (a different index is a different
  package universe), VCS/URL requirements (`pkg @ https://…`, `git+…`), editable
  `-e`, and unpinned names.

### 2.2 Resolving unpinned requirements is off the table

Resolving `flask` to a full set needs a resolver against a live index. A
resolver can build sdists to get metadata, which runs package code. The
acquisition docstring forbids exactly that `[repo: src/reachability/triage/acquisition.py:24-35]`.
`pip install --dry-run --only-binary=:all:` does resolve without building:
`[measured: .venv pip, "pip install -q --dry-run --ignore-installed
--only-binary=:all: --report r.json flask" → 7 packages (Flask 3.1.3, blinker
1.9.0, click 8.5.1, itsdangerous 2.2.0, Jinja2 3.1.6, MarkupSafe 3.0.4,
Werkzeug 3.1.9), no build step in output]`. But the answer depends on the
date, the index and the interpreter. That makes a scan non-reproducible, and it
is not the set the user actually runs. pip-audit makes the same point: without
`--no-deps`/`--require-hashes` it "may have to perform its own dependency
resolution", and `pip-audit -r INPUT` is "functionally equivalent to `pip
install -r INPUT`" `[src: https://github.com/pypa/pip-audit, read 2026-11-12]`.

### 2.3 Proposed rule

> **Inventory rule.** A scan needs one of: `uv.lock`, `poetry.lock`,
> `pylock.toml`, `Pipfile.lock`, or a `requirements.txt` in which **every**
> requirement line is `name==version` (after following `-r` includes within
> the scan root only). The service never runs a resolver. A requirement it
> cannot pin exactly (range, VCS/URL, editable, `-e`, unknown option such as
> `--index-url`) becomes an inventory entry with status `unpinned` and is
> reported as such. Every advisory for that package is reported `unknown`,
> reason `"version not pinned in <file>; the installed version cannot be
> determined without running a resolver"`. If no supported file exists, the
> scan finishes with zero findings and one scan-level `unknown`, reason `"no
> lockfile or fully pinned requirements file found"`. It does not fail
> silently.

Choosing one file when several exist: the proposed precedence is `uv.lock` >
`pylock.toml` > `poetry.lock` > `Pipfile.lock` > `requirements.txt`. The
report says which file was used. Environment markers: proposed evaluation
against a fixed target environment (CPython 3.13, linux x86_64), recorded on
the scan. Packages whose marker is false are listed as `skipped (marker)`, not
silently dropped.

### 2.4 Libraries

- **`tomllib`, `json`** (stdlib): enough for all four lockfiles.
- **`packaging`** (`packaging.requirements.Requirement`,
  `packaging.version.Version`, `packaging.markers`): licence "Apache-2.0 OR
  BSD-2-Clause", latest 26.3, last upload 2026-08-04 `[measured: PyPI JSON API]`.
  Maintained by PyPA. It is not in this repo's `requirements.txt` today, so
  adding it is a new pinned dependency: plan first, then `requirements.txt`, per
  CLAUDE.md.
- **Not recommended: `pip-requirements-parser`** (MIT): last upload
  2022-12-21 `[measured: PyPI JSON API]`. It looks unmaintained. A strict
  in-house parser that *rejects* what it does not understand fits the "never
  guess" rule better than a permissive one.

---

## 3. Vulnerability data

### 3.1 Comparison

| | OSV.dev API | OSV PyPI export (GCS) | PyPA advisory-database | GitHub Advisory Database |
|---|---|---|---|---|
| Format | OSV JSON | OSV JSON per record, zipped | OSV YAML (`vulns/`) | OSV JSON, plus `database_specific` (GitHub says not to rely on it) |
| Range expression | OSV `ranges` (ECOSYSTEM events `introduced`/`fixed`/`last_affected`) + explicit `versions` | same | same (`PYSEC-*`) | same; `last_known_affected_version_range` for what OSV cannot express |
| Licence | per source record | per source: GHSA and PyPA CC-BY-4.0, OpenSSF Malicious Packages Apache-2.0 | CC-BY-4.0 | CC-BY-4.0 |
| Rate limits | "Currently there are no limits on the API" | none (static bucket) | n/a (git) | n/a for git; REST API unauth 60/h `[measured]` |
| Offline | no | **yes** | yes (clone) | yes (clone) |
| Contains | all of the right-hand columns | GHSA + PYSEC + MAL + OSV ids | PYSEC only | GHSA only |

Sources: OSV API `[src: https://google.github.io/osv.dev/api/, read
2026-11-12]`; querybatch returns only `id` and `modified` per vuln, paginates
past 1,000 per query or 3,000 per batch, and "these numbers can vary slightly"
`[src: https://google.github.io/osv.dev/post-v1-querybatch/, read 2026-11-12]`.
Export bucket layout `gs://osv-vulnerabilities/<ECOSYSTEM>/all.zip`, exported
"continuously", per-source licence table `[src:
https://google.github.io/osv.dev/data/, read 2026-11-12]`. PyPA DB `[src:
https://github.com/pypa/advisory-database, read 2026-11-12]`. GHSA DB `[src:
https://github.com/github/advisory-database, read 2026-11-12]`. GitHub REST
unauth limit: `[measured: curl -I
https://api.github.com/repos/django/django/commits/7285644… → x-ratelimit-limit:
60]`.

The export already contains both PyPA and GHSA records. The PyPI export
downloaded today has 6,845 `GHSA-*`, 7,986 `PYSEC-*`, 12,020 `MAL-*` and 12
`OSV-*` records `[measured]`. **Recommendation: use the OSV PyPI export only.**
It is one file, offline, reproducible, and holds both curated databases.

### 3.2 Matching an installed version

OSV says a version is affected if it is in `affected[].versions` or falls in
an ECOSYSTEM range evaluated by the OSV event algorithm with PEP 440 ordering.
Measured over every live non-MAL PyPI `affected` entry (19,376) `[measured:
scripts/range_vs_versions.py]`:
- 123 entries have no ECOSYSTEM range and 666 have a range but an empty
  `versions` list, so neither source alone is enough.
- 1,360 use `last_affected` (inclusive) instead of `fixed`.
- 195 entries list version strings that are not PEP 440.
- My quick range evaluator disagreed with the `versions` list on 125 entries.
  The examples inspected (`0.0.0rc1` against `introduced: "0"`) are bugs in my
  evaluator: PEP 440 orders pre-releases below `0`, while OSV's `"0"` means
  "from the beginning". So the 125 count is **not** a measured data
  inconsistency. It is a measured demonstration that this matching is easy to
  get wrong.

Proposed rule: *affected = in `versions` OR in ranges (OSV algorithm,
`introduced: "0"` meaning −∞)*. Take the union, because the conservative
direction is to match more advisories. An unparseable installed version or
range bound gives that advisory `unknown`, reason `"version comparison not
possible: <detail>"`. GitHub's `last_known_affected_version_range` (1,408
affected entries) is a free-text constraint such as `<= 1.2.3` and would also be
honoured `[measured: field count §4.1]`. Proposed tests: a table of fixture
rows from real records, including the pre-release, `last_affected` and
`introduced: "0"` cases.

### 3.3 Alias groups and exclusions

- Records alias each other (GHSA ↔ PYSEC ↔ CVE). Grouping live non-MAL records
  by union-find over `aliases` gives **7,086 groups from 14,236 records**
  `[measured: scripts/osv_stats.py]`. The proposal is one finding per group,
  with the representative GHSA id preferred and every alias listed.
- `withdrawn` records (607) are excluded `[measured]`.
- **Malicious-package records** are not reachability questions. Per spec D5
  (amended 2026-11-12), the classifier uses structured fields only.
  - **Primary signal:** the OSV id or an alias starts `MAL-`, the OpenSSF
    malicious-packages prefix.
  - **Secondary signal:** CWE-506 in `database_specific.cwe_ids`.
  - **No keyword matching.**

  Measured on the pinned snapshot, per record, first matching rule wins
  `[measured]`:

  | Rule | Records | Non-withdrawn |
  |---|---|---|
  | id starts `MAL-` | 12,020 | 11,995 |
  | id not `MAL-`, an alias starts `MAL-` | 42 | 42 |
  | neither, CWE-506 | 8 | 8 |
  | total of 26,863 records | 12,070 | |

  Command:
  ```
  cd /tmp/app-scan-research/osv && sha256sum PyPI-all.zip    # c7ea4e15…08c
  python3 -I - <<'EOF'
  import zipfile, json
  z = zipfile.ZipFile('PyPI-all.zip')
  # for each member: json.loads; MAL- id? else MAL- alias? else 'CWE-506' in database_specific.cwe_ids?
  # tally each bucket, and each bucket's records without a 'withdrawn' field
  EOF
  ```
  At group level (R §0.6), one advisory group has a "Malicious code in" summary
  but no `MAL-` alias; dropping keyword matching loses it. Records with no
  structural signal (e.g. `PYSEC-2025-7`) are not detected. They fall through to
  a normal finding, which is visibly wrong but never claims safety.

### 3.4 Network change

The worker's only outbound use today is `pip download` against PyPI `[repo:
src/reachability/triage/acquisition.py:77-87]`. Compose gives the worker a default network with no
egress policy and no proxy `[repo: docker-compose.yml, worker service]`, so
it can reach anything; nothing is "blocked". Proposed:
- **No runtime call to OSV at all.** The snapshot is a file mounted read-only
  into the worker (`/advisory-data/osv-pypi-<date>.zip`) and verified by
  sha256 at startup. It is loaded on demand, not on the request path.
- Wheels for dependencies come from PyPI exactly as today
  (`files.pythonhosted.org` via pip, or the lock's own URLs with hash check).
- Optional, later: `github.com` for fix-commit patches (§4.4). It is off by
  default.
- A recorded list of destinations goes in §10.

### 3.5 Pinned snapshot for evals

Today's export: `PyPI/all.zip`, 37,790,549 bytes, sha256
`c7ea4e15b83912960fc5cbd42c6003dd630e422ec4dfbfc4ed19445573e1b08c`,
downloaded 2026-11-12 11:24:43 UTC from
`https://osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip` `[measured]`.
The bucket is overwritten continuously, so a URL is not a pin. Proposed: the
eval corpus pins by **sha256 plus download date**, and stores only the subset of
records the fixtures need (a few dozen JSON files, CC-BY-4.0 with attribution
in a `NOTICE`) under `tests/fixtures/app_scan/advisories/`. The full 38 MB zip
is not committed. A script would re-download and verify, and fail loudly if the
hash differs.

---

## 4. Which function is vulnerable (the hardest part)

### 4.1 Population-wide machine-readable fields

Population: the OSV PyPI export above. **Excluding** `MAL-*`: 14,843 records.
Excluding `withdrawn`: 14,236. Alias groups: 7,086 `[measured: python3 -I
scripts/osv_stats.py extracted 60]`.

Field presence over the 14,843 non-MAL records `[measured]`:
- `affected[].ecosystem_specific` present on 831 of 20,480 affected entries.
  Keys seen: `severity` 11, **`imports` 2**, `fixed_range` 1.
- `affected[].database_specific` keys: `source` 20,480,
  `last_known_affected_version_range` 1,408, `introduced_range` 4,
  `fixed_range` 1. **No symbol fields.**
- Top-level `database_specific`: `cwe_ids`, `github_reviewed`,
  `github_reviewed_at`, `nvd_published_at`, `severity`. **No symbol fields.**

The only two `imports` entries:
`PYSEC-2026-4010 {"attribute": "edit_file_with_agent", "modules":
["local_operator.server.routes.chat"]}` and `PYSEC-2026-4009 {"attribute":
"import_agent", "modules": ["local_operator.agents"]}`.

Free-text signals over the 7,086 groups (representative text only), which are
cheap regex heuristics, not ground truth `[measured]`:

| Signal | Groups | % |
|---|---|---|
| backticked identifier `` `x.y` `` | 3,180 | 44.9 |
| dotted `a.b.c` | 2,106 | 29.7 |
| `name()` | 1,939 | 27.4 |
| a `.py` filename | 1,942 | 27.4 |
| has a `fixed` event | 6,133 | 86.6 |
| has a `/commit/` URL (any ref type) | 4,370 | 61.7 |
| has a `/pull/` URL | 1,755 | 24.8 |
| has a reference of type `FIX` | 1,676 | 23.7 |
| has a GIT range | 41 | 0.6 |

Commit-URL hosts over the 4,428 groups with any `/commit/` or `/commits/`
URL: github.com 4,388, opendev.org 18, gitlab.com 9, git.openstack.org 6,
others ≤ 3 each `[measured]`. GHSA records mostly type commit links as `WEB`,
not `FIX`, so filtering on `FIX` alone would miss most of them.

### 4.2 Hand-labelled random sample

**Sample:** 60 groups, `random.seed(20261112); random.sample(sorted(reps), 60)`,
where `reps` is one representative id per alias group (GHSA preferred) from the
7,086 live non-MAL groups. One rater (this loop); no second rater, so no
inter-rater agreement. Full text was read for every record whose first 650
characters were ambiguous. Labels are in `/tmp/app-scan-research/osv/labels.txt`.

Label meanings:
- **F**: names a Python function, method or class of the affected package.
- **M**: names only a module, file, route or component.
- **N**: no Python code location.
- **C**: names only a native (C/C++) symbol.
- **X**: malicious package.

Kind: **LIB** means an importing app calls its API. **APP** means a
server/tool/UI whose vulnerable code runs in its own process.

| | F | M | N | C | X | total |
|---|---|---|---|---|---|---|
| LIB | 18 | 1 | 7 | 3 | – | 29 |
| APP | 12 | 5 | 11 | – | – | 28 |
| MAL | – | – | – | – | 3 | 3 |
| **all** | **30** | **6** | **18** | **3** | **3** | **60** |

- Of the 57 non-malware groups, **30 (53%)** name a Python function, method or
  class somewhere in their text. Among the **29 library advisories, 18 (62%)**
  do.
- **3 of 60 are malware**, and all 3 are `PYSEC-*`, not `MAL-*`
  (`PYSEC-2026-594` mrbios, `PYSEC-2026-581` dynamo-release, `PYSEC-2025-7`
  imblog).
- Named symbols are often wrong for our purpose even when present. Examples:
  [19] mako names stdlib `cgi.escape`. [49] fickling names stdlib `runpy`.
  [52] pyLoad names a JavaScript function. [15]/[34] TensorFlow names a public
  op whose bug is in a C++ kernel.
- The regex heuristic (backtick or `name()`) against the F label on the 57:
  tp 21, fp 6, fn 9, tn 21. That is precision 0.78 and recall 0.70 `[measured]`.
  A regex is not a usable extractor on its own.

Wheel availability for a diff `[measured: scripts/wheel_pair_avail.py, PyPI
JSON API]`. On the 57 non-malware groups:
- **35 (61%)** have a last-affected version and a first-fixed version that
  **both** have a wheel. 28 of those pairs are pure-python wheels on both sides.
- 10 have no `fixed` version.
- 10 are missing a wheel on at least one side.
- 2 list no affected version below the fix.

Among the 30 F-labelled groups, 22 have a wheel pair.

### 4.3 (a) Fixed-version wheel diff: measured on 5 real advisories

Method `[measured: scripts/wheel_diff.py]`:
1. Download the last-affected and first-fixed wheels with `pip download
   --only-binary=:all: --no-deps`, each into its own empty directory, and
   extract them.
2. Enumerate symbols with **our own L1/L2 index** (`build_l1_index`,
   `build_symbol_index`), keyed by `node_id`.
3. Hash each function/method `ast.dump` (line numbers are not included in
   `ast.dump` by default). Also hash a docstring-stripped variant, class-body
   non-def statements, and module-level non-def/non-import statements.
4. Report changed, added and removed symbols, with test modules and
   docstring-only changes split out.

The wheels are never imported.

| Advisory | Pair | Modules | Changed / added / removed (non-test) | True target present? | Noise |
|---|---|---|---|---|---|
| GHSA-qg2p-9jwr-mmqf (Django urlize DoS) | 4.2.13→4.2.14 | 871 | 8 / 6 / 0 | yes: `django.utils.html:Urlizer.trim_punctuation` + 5 added helpers (`CountsDict`, `Urlizer.trailing_punctuation_chars_*`, `wrapping_punctuation_openings`) | `module django` (version string); `check_password`, `Storage.save`, `validate_file_name`, `get_supported_language_variant`, `parse_accept_lang_header`: **fixes for other advisories in the same release** |
| GHSA-wqp7-x3pw-xc5r (Starlette StaticFiles UNC SSRF) | 1.0.1→1.1.0 | 34 | 4 / 0 / 0 | yes: `starlette.staticfiles:StaticFiles.lookup_path` | `module starlette` (version), `HTTPEndpoint.dispatch`, `FileResponse.__init__` (minor-release changes) |
| GHSA-gmhf-gg8w-jw42 (signxml HMAC timing) | 4.0.3→4.0.4 | 13 | 6 / 0 / 0 | yes: `signxml.verifier:XMLVerifier.verify`; fix commit touches only `verify` | `Namespace.__getattr__`, `SignatureConfiguration`, `VerifyResult` (class-body changes), `_verify_reference`, `_verify_signature_with_pubkey` |
| GHSA-r7w7-9xr2-qq2r (langchain-openai DNS rebinding) | 1.1.13→1.1.14 | 20 | 2 / 1 / 0 | yes: `langchain_openai.chat_models.base:_url_to_size` (+ added `_get_ssrf_safe_client`) | module-level change in the same module only |
| GHSA-h75v-3vvj-5mfj (Jinja2 xmlattr) | 3.1.3→3.1.4 | 25 | 26 / 15 / 15 | yes: `jinja2.filters:do_xmlattr` | `module jinja2` (version); 24 other changed symbols across compiler/environment/filters/parser/sandbox/utils; **15 add/remove pairs are our `@<lineno>` collision ids shifting** (`do_map@1486`→`do_map@1500`, …) |

Timing: 0.07–4.3 s per pair (Django is the slow one) `[measured]`.

Findings:
- **Recall was 5/5.** In every case the advisory's own function is in the
  changed or added set. For pure-python code this is expected: any change to a
  `.py` file in the wheel shows up. **ASSUMPTION**: the fix ships in the very
  next release after the last affected one. If it ships later, the diff spans
  more unrelated releases.
- **Precision is poor and varies**: 1/1 changed (langchain-openai), 1/3
  (starlette), 1/8 (Django), roughly 1/26 (jinja2).
- Noise classes seen: (i) version strings at package top level, which are easy
  to drop; (ii) other advisories fixed in the same release; (iii) unrelated
  changes when the fix is in a minor release; (iv) our own `@<lineno>`
  collision suffix. (iv) is a bug in using `node_id` as a cross-version key.
  The proposed key is `(module, qualname, ordinal among same-qualname
  definitions)`.
- No test-module noise appeared in these five. Wheels rarely ship tests; one
  jinja2 change was in a test-named module.
- Failure modes: no fixed version (10/57 in the sample); no wheel on one side
  (10/57); native-only fixes (no `.py` changes, empty diff); fixes made by
  bumping a dependency (`Requires-Dist` change, no code change).

**Why the diff is still useful despite poor precision.** The *symbol set*
supports `reachable` only where a path to a changed symbol exists, and a path
to a noise symbol would be a false positive. That is why the spec grades
target confidence: *high* means named in the advisory text **and** changed in
the diff, and *medium* means only one of the two. That is also why ranking (c)
or fix-commit narrowing (b) only orders candidates.

*Withdrawn claim:* an earlier draft said the diff's module set "supports a sound
module-level `not_reachable`". It does not. A mixed `.py` + template/native fix
leaves the vulnerable file outside the module set. The adjacent
last-affected/first-fixed pair can differ in layout from the installed version.
And module-level import evidence is defeated by config-driven imports (§0.4).
The spec issues no `not_reachable`.

### 4.4 (b) Fix-commit diff

- Presence: 61.7% of groups carry a `/commit/` URL, 99% of those on
  github.com; 23.7% carry a `FIX`-typed reference `[measured, §4.1]`.
- Reachability without a token: `https://github.com/<o>/<r>/commit/<sha>.patch`
  returned HTTP 200 for all 3 tried (django 7285644…, signxml 1b501fa…, jinja
  0668239…) `[measured: curl -sS -L -o x.patch -w "%{http_code}"]`. The REST
  API unauth limit is 60 requests/hour `[measured: x-ratelimit-limit header]`.
  The limit on the non-API `.patch` endpoint is not documented in anything
  read here (**ASSUMPTION**: it is also limited, at an unknown rate).
- Precision on these 3 is much better than (a). Django's patch touches only
  `django/utils/html.py`, `docs/releases/*` and `tests/utils_tests/test_html.py`.
  signxml touches only `signxml/verifier.py` (`verify`). jinja touches only
  `src/jinja2/filters.py` (`do_xmlattr`), `CHANGES.rst` and a test `[measured:
  grep "^+++ \|^@@" on each patch]`.
- Caveats:
  - Hunk headers name the *preceding* def, not necessarily the changed one, so
    changed line ranges must be mapped onto the AST of the fixed version.
  - Paths are repo paths (`src/jinja2/…`), not installed module paths. They
    need a mapping, then a check that each symbol exists in the *installed*
    wheel.
  - Commits can be on main rather than the release branch.
  - "Commit" links in advisories are sometimes the vulnerable commit or an
    unrelated one (**ASSUMPTION**, not measured).
  - It adds github.com as a network destination, and the commit content is
    untrusted text (§10).
- Proposed role: **narrowing only**. Intersect with (a): a symbol that is in
  the fix commit *and* changed in the wheel diff is the strongest candidate. A
  commit that touches no symbol in the wheel diff is ignored, with a recorded
  reason.

### 4.5 (c) LLM proposal

Not measured: real-model runs are Raghav's only, per the task. Design:
- **Input:** advisory summary/details (sandboxed, §10) plus the candidate list
  from (a) (∩ (b) when present), given as `node_id`s with a few lines of diff
  context each.
- **Output:** a ranked subset of the *given* candidate ids, plus at most N
  free-form symbols. Free-form symbols are only accepted if static analysis
  finds them in the installed version's symbol table.
- **The model never sets a verdict.** It decides the *order* of symbols to
  query and which symbol is shown as "primary". The verdict for every queried
  symbol still comes from `compute_reachability`.
- Failure modes and containment:
  - *Hallucinated symbol*: rejected by the existence check. Recorded as
    `rejected: not found in <dist>==<ver>`.
  - *Ranking a noise symbol first*: it can change which path is shown first.
    It cannot create a `not_reachable`, because the spec never issues one.
  - *Ranking the true symbol out*: the spec queries every candidate up to the
    budget, ranked or not. A ranking can only reorder.
  - *Superseded*: the spec forbids free-form model symbols. The model may only
    rank symbols already in the named ∪ changed sets.
  - *Injection via advisory text*: the existing `sandbox_untrusted_text`
    boundary, plus the existence check.
- Stub lane: a deterministic stub that ranks by "symbol named in the advisory
  text, then lexical order". The CI eval runs only this.

### 4.6 (d) Module-level fallback

When no symbol set exists (no fixed version, no wheel pair, native-only
change), the question becomes "is the affected *distribution* imported by the
app's own code?". Both answers give verdict `unknown`, with different evidence
levels (spec §2.3):
- not imported → `package_installed_not_imported`. A host, plugin or
  config-driven import can still load it (§0.4), so this is **never**
  `not_reachable`;
- imported → `no_target_symbol`.

*Withdrawn:* an earlier draft mapped "not imported" to a distribution-scope
`not_reachable`.
- **Never** `not_reachable` for a *function* from module-level evidence that
  the module *is* imported. Never `reachable` from module-level evidence alone.
  "The module is imported" is not "the function is called".

### 4.7 Recommended order

1. **(a) wheel diff → candidate symbols and the complete changed-module set.**
   It is deterministic and needs only PyPI.
2. **(b) fix-commit narrowing**, if enabled and a github.com commit exists.
   Intersect only.
3. **(c) LLM ranking** of candidates (stub in CI), plus existence-checked extra
   symbols.
4. **(d) module/distribution fallback** when (a) yields nothing usable.

The spec issues no `not_reachable`, so no step here can produce one. The
remaining wrong-answer risk is a `reachable` whose path ends at a noise
symbol. Target confidence (spec §2.4) and G3's labelled true symbol (spec §4)
address that.

---

## 5. Call graph across the app and its dependencies

### 5.1 Today

`build_l1_index(root)` treats the top-level names found under one root as
first-party `[repo: src/reachability/index/build.py:10-11]`. Everything else
is `THIRD_PARTY` `[repo: src/reachability/index/imports.py:10-18]`, and edges
to it end as `ext:` callee ids that are not followed. `discover_modules`
prefers `<root>/src` if it exists `[repo: src/reachability/index/discovery.py:28-30]`.

### 5.2 Measurement setup

**Apps:**
- **flaskr**: the Flask tutorial (`pallets/flask` `examples/tutorial`, commit
  `49943b5`, 2026-10-30). Unpinned `dependencies = ["flask"]`, resolved for
  measurement only with `pip --dry-run --only-binary=:all:` (§2.2): 7 wheels.
- **FastAPI template**: `fastapi/full-stack-fastapi-template` `backend/`, commit
  `aabb901`, 2026-11-03. Runtime closure from `uv.lock`: 64 packages, 63
  wheels, `dkimpy` sdist-only. Wheels were downloaded from the lock's own URLs
  and **sha256-verified** `[measured: scripts/fetch_deps.py]`.

Combined index: app top-level packages plus every wheel's top-level packages
copied into one root (no top-level name collisions in either app `[measured]`),
then the unmodified `build_repo_index`. Script:
`scripts/measure_index.py <app_root> <site> <combined> '<queries json>'`.

### 5.3 Results `[measured]`

| | flaskr | FastAPI template |
|---|---|---|
| App alone: modules / edges / entrypoints / time | 9 / 177 / 30 / 0.01 s | 40 / 765 / 86 / 0.04 s |
| Dependencies indexed one by one: modules / edges / time (sum) | 131 / 10,369 / 0.79 s | 2,242 / 122,839 / 11.19 s |
| Largest dependencies by modules | Werkzeug 52, Jinja2 25, Flask 24 | pygments 339, sqlalchemy 256 (31,449 edges, 3.04 s), sentry-sdk 194, dnspython 161, pydantic 105 |
| Combined: modules / edges / time / peak RSS | 140 / 10,546 / 0.81 s / 32 MB | 2,281 / 123,535 / 13.17 s / 117 MB |
| App→dependency edges resolved (not `ext:`, not `?:`) | 30 | 81 |
| `ext:` edges remaining (stdlib + missing) | 3,468 | 32,663 |
| Entrypoints: total / in app | 35 / 30 | 987 / 86 |
| Library entrypoints that would pollute | 2 `__main__` + 3 CLI | 87 `__main__`, 723 test functions, 59 fixtures, 18 routes, 14 CLI |
| Per-query `compute_reachability` | 0.23–0.26 s | 3.9–4.9 s |

App-originating edges by resolution rule `[measured: scripts/app_edge_rules.py]`:
- flaskr: 177 app edges. **104 unresolved (`?:` callee) = 59%**:
  `unresolved_attribute` 101 (57%), `self_unresolved` 2, `unresolved_name` 1.
  The rest: dependency via `import_alias` 30, app-internal 25, `ext:` 18
  (corrected; an earlier draft said "101 of 182 (55%)").
- FastAPI: 765 app edges. **468 unresolved = 61%**: `unresolved_attribute` 467,
  `self_unresolved` 1. The rest: app-internal 173, dependency via `import_alias`
  75, via `module_attribute` 6.

Unresolved app edges by receiver shape `[measured: scripts/unresolved_breakdown.py,
which reads the AST at each edge's file:lineno]`:

| Receiver shape | flaskr (of 104) | FastAPI (of 468) |
|---|---|---|
| `local_var.method()`: object of unknown type | 74 (71%) | 231 (49%) |
| `Imported.attr()` via `from x import Imported` (class or object) | 7 (7%) | 133 (28%) |
| `imported_module.attr()` via `import x` (re-export gap, e.g. `jwt.decode`) | 2 (2%) | 52 (11%) |
| `call().method()` | 14 (13%) | 44 (9%) |
| `a.b.method()` chain | 6 (6%) | 6 (1%) |
| `self.`/`cls.method()` | 0 | 0 |
| other | 1 | 2 |

Property reads such as `request.form` are not in this table. They produce no
edge at all today, so they are invisible in the unresolved rate.
- Most unresolved edges are method calls on objects of unknown type
  (`session.add`, `db.execute`).
- One is a **re-export gap**: `jwt.decode(...)` with `import jwt`, where
  `jwt/__init__.py:9` has `from .api_jwt import … decode`, still ends as
  `?:decode` `[measured]`.

**Probe queries** (app entrypoints only, then all entrypoints):

| App | Target | App-only | All entrypoints | Correct answer (manual reading) |
|---|---|---|---|---|
| flaskr | `werkzeug.security:generate_password_hash` | reachable, `flaskr.auth:register → werkzeug.security:generate_password_hash` | same | reachable ✔ |
| flaskr | `markupsafe:escape` | reachable, `register → flask.helpers:redirect → werkzeug.utils:redirect → markupsafe:escape` | same | reachable ✔ (4 hops across 3 dists) |
| flaskr | `jinja2.environment:render` | unknown (low-confidence `?:render`) | same | reachable (render_template) |
| flaskr | `werkzeug.serving:run_simple` | **not_reachable** | reachable via `werkzeug.testapp` (library `__main__`) | depends on how the app is run (`flask run` calls it); with library entrypoints the answer is "reachable" for the wrong reason |
| flaskr | `werkzeug.sansio.multipart:MultipartDecoder` | **not_reachable** | – | **reachable** at runtime via `request.form` → `Request.form` (cached_property) → `_load_form_data` → parser. **False `not_reachable`.** |
| flaskr | `werkzeug.wrappers.request:_load_form_data` | **not_reachable** | – | **reachable**. **False `not_reachable`.** |
| FastAPI | `starlette.staticfiles:lookup_path`, `starlette.formparsers:parse`, `multipart.multipart:parse_options_header`, `jinja2.filters:do_xmlattr` | unknown (D1: opaque call at `fastapi/encoders.py:337`, reachable from app tests) | unknown (D1 from `sqlalchemy.testing…`) | mixed; none confidently answerable |
| FastAPI | `jwt.api_jwt:decode` | unknown (`?:decode`, re-export gap) | unknown | reachable (`app.utils:verify_password_reset_token`) |

Conclusions:
1. Cross-distribution paths **do work** for explicit, import-resolved calls
   (flaskr → flask → werkzeug → markupsafe).
2. **Library entrypoints must be excluded.** They add 900+ start points in the
   FastAPI case and produce "reachable" for the wrong reason.
3. **A call-graph "no path found" is not safe evidence for dependency
   targets.** Two reasons were measured: framework inversion of control (the
   server calls the app), and attribute loads that run code (properties,
   `cached_property`, descriptors, `__getattr__`). Both are outside L3's
   `ast.Call`-only model. L3 is frozen against new resolution capability
   (CLAUDE.md L5 paragraph).
4. D1/D3 are corpus-wide by design (`DECISIONS.md` §4). Over 2,000 dependency
   modules that design makes nearly every negative answer `unknown`, which is
   safe but uninformative.

### 5.4 Which dependencies need indexing

- For a `reachable` search, only distributions on a **lock-graph path** from the
  app's direct dependencies to the vulnerable distribution are needed. The
  vulnerable code is reached through some chain of imports, and the lock graph
  over-approximates what can import what (**ASSUMPTION**: the lock graph
  includes every runtime import edge. Vendored copies and undeclared imports
  break this, and the import closure in §5.5 is the guard). For `Pipfile.lock`
  and requirements files there is no graph, so all pinned distributions are
  indexed.
- **Module → distribution mapping** comes from the wheel itself: each wheel's
  top-level entries next to its `.dist-info` (`top_level.txt` is optional and
  not reliable), plus `RECORD` for exact file ownership. Two distributions
  providing the same top-level name (namespace packages, forks) is a
  **collision**. Proposed: merge namespace-package directories, and for a
  same-file collision give `unknown` for advisories in either distribution
  (0 collisions measured in both apps).
- **Index cache per `(dist, version, wheel sha256)`**, storing L1–L3 output
  computed *with that wheel as its own root* (so its `ext:` edges are
  unresolved placeholders), then **linked** at query time by re-resolving
  `ext:<module>.<name>` callee ids against the other cached indexes. Proposed
  storage: a pickled or JSON blob in a new table keyed by the hash, with an
  index-schema version so the cache is invalidated when L1–L3 change.
  Measured cost avoided per hit: 11.2 s of the 13.2 s FastAPI combined build is
  dependency indexing `[measured]`.
- **Not measured**: whether link-time re-resolution reproduces the combined
  index's edges exactly. It would be the first gate of the cross-index unit
  (parity against the combined build, like Phase LangGraph's U5 parity
  harness).

### 5.5 Start points and the import-closure rule

**Start points:** only the app's own entrypoints (`__main__`, console scripts
from the *app's* `pyproject.toml`, route/CLI/Celery decorators, and app tests
counted as tests). Library `__main__`, library tests and fixtures, and library
CLI decorators are never start points.

**Historical (withdrawn): the import-closure `not_reachable` rule.** This
subsection records what was measured. The rule below is **not** proposed any
more. The spec issues no `not_reachable` in this phase, and uses import-closure
facts only as *evidence levels* on `unknown` findings.

> A dependency module *m* may be reported `not_reachable` only if all hold:
> - **R1** *m* is not in the import closure from the start set, where the start
>   set is the app's modules **plus every entry-point target declared by any
>   installed distribution** (`entry_points.txt`, all groups, including
>   console scripts such as `uvicorn` and `pytest11` plugins), and importing
>   `a.b.c` counts as importing `a` and `a.b`;
> - **R2** no module in the closure contains a non-literal
>   `importlib.import_module(...)`/`__import__(...)` whose distribution is
>   *m*'s distribution or depends on it (lock-graph closure);
> - **R3** *m*'s distribution ships no `.pth` file (executed at interpreter
>   start). None of the 70 wheels in the two apps ships one `[measured: find
>   site -name '*.pth' → empty]`, so R3 is untested by these apps;
> - **R4** for an advisory, every module in the §4.3 diff's changed set (not
>   just the ranked symbols) satisfies R1–R3.
>
> Otherwise the verdict is `unknown`, with a reason that names which of R1–R4
> failed.

Measured eligibility `[measured: scripts/import_closure.py,
scripts/safe_notreach.py, scripts/safe_notreach_strict.py]`:

| | flaskr | FastAPI template |
|---|---|---|
| Dependency modules | 131 | 2,241 |
| Import-reachable from app only (R1 without entry-point roots) | 121 (92.4%) | 1,107 (49.4%) |
| + entry-point roots of all installed dists (23 roots, FastAPI) | – | 1,352 |
| Distributions with an import-reachable non-literal dynamic import | 3 (flask, jinja2, werkzeug) | 10 (anyio, dns, jinja2, mako, pydantic, pygments, rich, sentry_sdk, sqlalchemy, …) |
| Distributions tainted after lock-graph propagation | – | 49 of 63 |
| Eligible under R1+R2 with lock-graph taint (**withdrawn rule**) | 0 | 87, withdrawn: includes psycopg (loaded from a URL string, `config.py:39`) and tzdata (loaded by stdlib `zoneinfo`) |
| **Eligible under a corpus-wide dynamic-import rule (D1 precedent)** | **0** | **0** |

Why entry-point roots matter, measured: without them, `uvicorn`, `httptools`,
`websockets`, `uvloop` and `watchfiles` are "never imported" by the FastAPI
app `[measured: never-imported list]`. But the template's container runs `CMD ["fastapi", "run", "--workers", "4"]`
`[measured: apps/fastapi-tpl/backend/Dockerfile:65]`, i.e. uvicorn, which loads them. A rule without entry-point roots would have reported a false
`not_reachable` for, say, an h11/httptools request-smuggling advisory.

Why `sentry_sdk.integrations.django` is a warning: it is not import-reachable
by literal imports, but `sentry_sdk.integrations` does import by string
`[measured: dynamic-import list]`. Lock-graph taint happened to cover this
case. It does not cover psycopg, whose importer (SQLAlchemy) does not depend on
it; the dependency runs the other way. That is why the rule is withdrawn.

### 5.6 Frameworks that hide calls

Measured above: property and `cached_property` access (werkzeug `Request.form`),
server-calls-app inversion (`run_simple`, uvicorn), and re-export through
`__init__` (`jwt.decode`). Known, not measured here: Django URLconf (`path()`
lists: not detected, `[repo: entrypoints.py]` has no URLconf rule), Django
signals (`post_save.connect(handler)`, a D3-style name escape), plugin
registries (setuptools entry points, which R1 covers for imports but not for
calls), `getattr` dispatch (D1), FastAPI `Depends(...)` (a function passed by
reference, D3), pydantic validators, SQLAlchemy events, Celery
`autodiscover_tasks`, and template engines calling filters by name from
template *text* (`{{ x|xmlattr }}`) that is never Python source. The last one
matters for the Jinja2 xmlattr advisory: only templates can reach
`do_xmlattr`, so static Python analysis can at most say "the Jinja2 environment
is used".

---

## 6. Prior art

| Tool | What it does for Python | Evidence type |
|---|---|---|
| **pip-audit** (PyPA, Apache-2.0) | Audits the environment, requirements, `pyproject.toml`, `pylock.*.toml`. Data from the PyPI JSON API (PyPA DB) or OSV. **No reachability**: "pip-audit is not a static code analyzer. It analyzes dependency trees, not code." `--no-deps` needs every requirement pinned; `--disable-pip` needs hashes or `--no-deps`. `[src: https://github.com/pypa/pip-audit, read 2026-11-12]` | project docs |
| **osv-scanner** | Python lockfiles: `Pipfile.lock`, `poetry.lock`, `requirements.txt`, `pdm.lock`, `pylock.toml`, `uv.lock` `[src: https://google.github.io/osv-scanner/supported-languages-and-lockfiles, read 2026-11-12]`. Call analysis: the official page I fetched does not mention it. Search results describe Go (govulncheck) and experimental Rust only, with **no evidence of Python call analysis** `[src: search "osv-scanner call analysis supported languages", 2026-11-12; incl. https://github.com/google/osv-scanner]`. The docs call-analysis page URL I tried returned 404 `[measured]`. | project docs + search summary |
| **Semgrep Supply Chain** | Reachability "for GA languages". Generates rules for new advisories from reviewed GHSA, Electron notes and OSV. The page I read does not say whether Python reachability is GA, nor how rules are authored `[src: https://docs.semgrep.dev/semgrep-supply-chain/overview, read 2026-11-12]` **(vendor claim)** | vendor docs |
| **Snyk** | Python reachability (pip, poetry, pipenv) GA. Vulnerable elements come from the *fix commit*, then "DeepCode AI program analysis" and NLP rank root-cause candidates, then **security experts manually verify**. Statuses `REACHABLE`, `NO PATH FOUND`, `NOT APPLICABLE`. Docs say `NO PATH FOUND` "does not mean that the vulnerability is completely unreachable", and analysis "can under-approximate … e.g. with reflection" `[src: https://docs.snyk.io/manage-risk/prioritize-issues-for-fixing/reachability-analysis, read 2026-11-12]` **(vendor claim)**. GA dates 2025-12-11 to 2026-01-12 per release notes `[src: https://updates.snyk.io/reachability-for-python-ga/ via search, 2026-11-12]` | vendor docs |
| **Endor Labs** | Function-level reachability with call paths for Python among others. Needs a successful build; falls back to "pre-computed reachability" that "assumes all direct dependencies are reachable". Labels "Reachable Function" and "Potentially Reachable Function" `[src: https://docs.endorlabs.com/scan/sca/reachability-analysis/pre-computed-reachability via search, 2026-11-12]` **(vendor claim)** | vendor docs |
| **PyCG** (Salis et al., ICSE 2021) | Static call graphs. Reported ≈99.2% precision, ≈69.9% recall on its own micro- and macro-benchmarks; ≈0.38 s per kLOC `[src: https://arxiv.org/abs/2103.00587 and https://2021.icse-conferences.org/details/icse-2021-papers/39/PyCG-Practical-Call-Graph-Generation-in-Python, read 2026-11-12]` | **peer-reviewed** (ICSE) |
| **Jarvis** (Huang et al.) | Application-centred, flow-sensitive call graphs. Claims +84% precision over PyCG and ≥20% more recall in application-centred analysis; 8.16 s and 227 MB average `[src: https://arxiv.org/abs/2305.05949v5, read 2026-11-12]`. Peer-review venue **not verified** here | arXiv preprint (as verified) |
| **pyan**, **Scalpel** | No published precision/recall found in this loop. **Not researched further; gap.** | – |

**Copy:**
- Snyk's pipeline shape (fix commit → candidates → ranking → verification) and
  its honest wording that "no path found" is not "unreachable".
- Endor's two-tier labels (Reachable / Potentially Reachable).
- pip-audit's "pinned or hashed, or we don't resolve" posture.
- Jarvis's *application-centred* framing: analyse only what the app can reach.

**Must not claim:**
- Any precision or recall figure for our engine from these papers. PyCG's
  numbers are on PyCG's benchmark.
- That `not_reachable` means "not exploitable".
- Parity with Snyk or Endor, which have human-curated symbol data.
- That we do framework-aware analysis.
- Python call analysis "like osv-scanner": it has none for Python, as far as
  this loop could verify.

---

## 7. Verdicts and output

> **Superseded by spec §2.3–§2.6.** This section is the first draft. The spec
> replaces its `not_reachable` rows, rollup step 3 and reason codes with:
> - no `not_reachable` in this phase;
> - four evidence levels;
> - target confidence high/medium/none;
> - a closed reason-code registry.
>
> This section is kept as the research record.

### 7.1 Per (app, advisory, target symbol)

The existing four verdicts, computed by `compute_reachability` restricted to
app start points, **except** that for a dependency target a call-graph "no
path" result is mapped to `unknown`, reason code `NO_STATIC_PATH_IMPORTED`.
`not_reachable` comes only from the §5.5 module rule.

Each target carries **provenance**:

| Field | Values |
|---|---|
| `source` | `osv_imports` (machine-readable field) · `wheel_diff` · `fix_commit` (∩ wheel_diff) · `model_ranked` · `model_proposed` (existence-checked) · `module_fallback` |
| `source_confidence` | `high` (osv_imports, or fix_commit ∩ wheel_diff), `medium` (wheel_diff alone, ranked first by a deterministic rule), `low` (model_proposed, or wheel_diff with > K candidates) |
| `exists_in_installed` | bool, with `dist==version` and file:line |
| `diff_context` | changed / added, module, qualname |

### 7.2 Advisory rollup

1. Any symbol `reachable` (non-test path) → advisory `reachable`. Show the
   highest-provenance reachable symbol and its path.
2. Else any `reachable_only_from_tests` → `reachable_only_from_tests`.
3. Else, if R1–R4 (§5.5) hold for **every module in the full changed-module
   set** (or for the whole distribution under (d)) → `not_reachable`, scope
   `module` or `distribution`.
4. Else → `unknown`, with the most specific reason code. Precedence:
   `VERSION_UNPINNED` > `NO_WHEEL` > `NO_FIX_VERSION` > `NATIVE_ONLY_CHANGE` >
   `DYNAMIC_IMPORT` > `ENTRYPOINT_PLUGIN` > `NO_STATIC_PATH_IMPORTED` >
   `LOW_CONFIDENCE_PATH` / `OPAQUE_CALL` / `NAME_ESCAPE` (existing).

A model ranking can only change *which* reachable path is shown first. It never
changes the advisory verdict, because steps 3–4 use the full module set.

### 7.3 Plain-English sentence per verdict (proposal)

- `reachable`: *"Your code calls `<symbol>` (the function this advisory is
  about) through this path: `<a> → <b> → <symbol>`."*
- `reachable_only_from_tests`: *"Only your tests call `<symbol>`; we found no
  path from your app's own start points."*
- `not_reachable` (module): *"None of the modules changed by the fix
  (`<m1>`, …) can be imported by your app or by any installed tool or plugin,
  so this advisory's code is never loaded."*
- `not_reachable` (distribution): *"Nothing in your app, and no installed tool
  or plugin, imports `<dist>`; this advisory's code is never loaded."*
- `unknown`: *"We could not decide: `<reason sentence>`."* For example:
  - `NO_STATIC_PATH_IMPORTED`: *"your app loads `<module>` but we found no
    call path; frameworks often call such code for you (for example when you
    read `request.form`), which we cannot see."*
  - `VERSION_UNPINNED`: *"`<dist>` is not pinned, so we do not know which
    version you run."*
  - `NO_WHEEL`: *"`<dist>==<v>` has no wheel; we never build source
    packages."*
- `malicious_package` (if kept as a category): *"This exact version is known to
  be malicious. Remove it; reachability does not apply."*

### 7.4 App summary

> Amended by spec §2.3 (2026-11-12). Unpinned packages produce no findings,
> but when any exist the summary **leads** with: "N packages are not pinned;
> they have M known advisories (any version). Pin them to check." Fixture
> T10 and gate G8 check this.

Counts per advisory verdict. The list is ordered `reachable` first (with
severity from the record's `severity`/`database_specific.severity`, shown
as given, not recomputed), then `unknown` grouped by reason code, then
`not_reachable`. It also lists inventory problems (unpinned, no wheel, marker
skipped). The **headline is never "N of M vulnerabilities are safe to ignore"**;
it is *"K advisories reach your code; U could not be decided (reasons…); N are
not loaded"*.

---

## 8. API and data model (proposal)

> Superseded by spec §2.1–§2.2 (adds `evidence_level`, snapshot fields, and a `malicious` list shown first).

- `POST /v1/scans` body `{"path": "<relative path under the scan mount>"}` →
  202 `ScanOut`, `Location`.
- `GET /v1/scans/{id}` → `ScanOut = {id, status, error, llm_mode, inventory:
  {source_file, packages:[{name, version, status}]}, summary: {reachable,
  reachable_only_from_tests, not_reachable, unknown, by_reason}, findings: [...]}`.
- Optionally `GET /v1/scans/{id}/findings/{fid}` for the full evidence.
- `/v1/triage` is unchanged.

**Tables (one Alembic migration, 0005):**
- `scans`: id, status, path, `inventory_json`, `snapshot_sha256`, `llm_mode`,
  error, `worker_id`/`attempt_count`/timestamps (same claim, finalize and reaper
  contract as `triage_jobs`, `DECISIONS.md` §8).
- `scan_findings`: id, scan_id FK, advisory id + aliases, dist, version,
  verdict, scope (`symbol|module|distribution`), reason_code, reason, path
  JSON, `targets_json` (provenance per symbol).
- `dist_index_cache`: (dist, version, wheel_sha256, index_schema_version) → blob.
- `advisory_targets_cache`: (advisory id, last_affected, first_fixed, method
  version) → candidate set.

**Worker:** a second claim query over `scans`, sharing the poll loop. One scan
= inventory → match → per advisory (targets → queries) → rollup → finalize.
Queries reuse a single linked index per scan. D3 would be computed once per
index, not once per query (§0.8).

**The LangGraph agent's role:** *none for the verdict*. The triage loop asks a
model to pick tools until it confirms a target; in a scan, the targets come from
§4 and the queries are deterministic. The only model use is §4.5 ranking,
behind `TRIAGE_LLM_MODE`, with the stub in CI. Recommendation: **do not route
scans through `run_triage_loop_langgraph`.**

**Budgets (proposals, constants with tests):**
- Scan wall clock 10 min.
- ≤ 200 advisories per scan; past that, the rest get `unknown` with reason
  "scan budget".
- ≤ 20 target symbols per advisory.
- ≤ 100 distributions indexed.
- Per-wheel download 120 s (as today).
- Model calls ≤ 1 per advisory, with the existing tool-call budget of 30 not
  used.

---

## 9. Evaluation (pre-registered design; the spec freezes it)

> Superseded by spec §4. G3 is now pinned to a labelled true symbol. G1 no longer applies, because no `not_reachable` is issued. Trap and evidence-level fixtures were added. The phase gate runs on real wheels.

Fixture shape: small apps under `tests/fixtures/app_scan/<NN_name>/`. Each has
an app tree, a `uv.lock` or pinned `requirements.txt`, and a `label.json` with
an allowed verdict set per advisory. Every vulnerable package appears in **two**
apps: *calls* (expected `reachable`) and *installs-but-never-calls*.

Candidate advisories, from §4.2/§4.3, with wheels on both sides and a
deterministic expected answer:

| Advisory | Pair | "calls" app | "installed only" app | Expected for the second app |
|---|---|---|---|---|
| GHSA-r7w7-9xr2-qq2r langchain-openai `_url_to_size` | 1.1.13 / 1.1.14 | calls `get_num_tokens_from_messages` with an image URL | imports nothing from langchain_openai | `not_reachable` (distribution) **if** R1–R3 hold; else `unknown`, never `not_reachable` |
| GHSA-gmhf-gg8w-jw42 signxml `XMLVerifier.verify` | 4.0.3 / 4.0.4 | calls `XMLVerifier().verify(...)` | imports `signxml` only for `XMLSigner` | `unknown` (module imported) |
| GHSA-qg2p-9jwr-mmqf Django urlize | 4.2.13 / 4.2.14 | calls `django.utils.html.urlize` directly | Django installed, urlize never called | `unknown` (imported framework) |
| GHSA-wqp7-x3pw-xc5r Starlette StaticFiles | 1.0.1 / 1.1.0 | mounts `StaticFiles(...)` | starlette app without StaticFiles | `unknown`; `not_reachable` is forbidden (`staticfiles` is imported by a framework path, **ASSUMPTION**) |
| Werkzeug multipart (GHSA id to be picked from the snapshot) | from snapshot | reads `request.form` | Flask app that never reads forms | `unknown` for **both**; this is the §5.3 regression guard |

Decision rules the spec freezes (§17 style), before any run:
- **G1 (hard)**: zero `not_reachable` on any advisory labelled reachable.
- **G2 (hard)**: every label row's verdict is in its allowed set.
- **G3 (hard)**: on "calls" apps whose call is a direct import-resolved call,
  ≥ 4 of 5 `reachable` with a path ending at a symbol in the fix diff.
- **G4 (reported)**: `unknown` rate.
- **G5 (hard)**: inventory exact-match on every fixture lockfile.
- The stub lane runs in CI. Real-model runs are Raghav's only.

---

## 10. Safety boundary

- **Static only.** The app and its dependencies are `ast.parse`d, never
  imported or executed. Wheel-only stays. A distribution with no wheel →
  `unknown`, reason `NO_WHEEL` (measured case: `dkimpy 1.1.8`, sdist only).
- **New network destinations (proposed):**
  - `files.pythonhosted.org` / `pypi.org`, for wheels as today; plus the PyPI
    JSON API, to map `poetry.lock` filenames to URLs.
  - **Optional, off by default:** `github.com` `.patch` URLs for §4.4.
  - **None at scan time** for advisory data; the OSV snapshot is mounted.
  - `osv-vulnerabilities.storage.googleapis.com` only from an operator-run
    refresh script, never from the worker.
- **New untrusted text and how each is handled:**

| Input | Where it goes | Handling |
|---|---|---|
| App source files | `ast.parse` only | size/count limits; `SyntaxError` → unparsed list (existing behaviour) |
| App file paths (names, symlinks) | discovery, reports | `os.walk(followlinks=False)`; resolved-under-root check; names shown in reports pass through `sandbox_untrusted_text` (`[repo: src/reachability/triage/sandbox.py:166]`) |
| Lockfile contents | inventory | strict parser; unknown keys/forms → `unpinned`/`unknown`, never guessed; URLs only from `files.pythonhosted.org` |
| Advisory `summary`/`details` | model prompt (§4.5) and UI | `sandbox_untrusted_text` before any prompt; shown in UI as text (React escapes) |
| Fix-commit patches | candidate narrowing; model prompt | parsed as unified diff only; sandboxed before any prompt |
| Wheel contents | index | as today, plus a zip-slip/symlink check before `extractall` (§1.1) |
| Model output | ranking only | ids must be in the candidate set or pass the existence check; never a verdict |

---

## Appendix A: Reproduce

All scripts are in `/tmp/app-scan-research/scripts/` (ephemeral, not
committed). Commands, run from `/tmp/app-scan-research`:

```
curl -sS -o osv/PyPI-all.zip https://osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip   # sha256 c7ea4e15…08c
python3 -I scripts/osv_stats.py osv/extracted 60                          # §4.1, sample draw
.venv/bin/python -I scripts/unresolved_breakdown.py combined/fastapi app,tests   # §5.3 receiver shapes
.venv/bin/python -I scripts/wheel_pair_avail.py                           # §4.2 wheel pairs
scripts/fetch_pair.sh django 4.2.13 4.2.14                                # §4.3 (and 4 others)
.venv/bin/python -I scripts/wheel_diff.py wheels/django-4.2.13/x wheels/django-4.2.14/x
python3 -I scripts/uvlock_closure.py apps/fastapi-tpl/uv.lock app results/fastapi-deps.json
python3 -I scripts/fetch_deps.py results/fastapi-deps.json site/fastapi
.venv/bin/python -I scripts/measure_index.py apps/fastapi-tpl/backend site/fastapi combined/fastapi '<queries>'
.venv/bin/python -I scripts/safe_notreach_strict.py combined/fastapi site/fastapi app,tests apps/fastapi-tpl/uv.lock
.venv/bin/python -I scripts/range_vs_versions.py                          # §3.2
```

## Appendix B: Gaps this loop did not close

- No measurement of pyan or Scalpel accuracy.
- No real-model ranking run (by rule).
- Only 2 apps were indexed (the task asked for 2 or 3).
- No confirmation of the `.patch` endpoint rate limit.
- The 60-advisory labels have a single rater.
- The Jarvis peer-review venue was not verified.
