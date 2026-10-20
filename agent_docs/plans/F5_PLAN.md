# Plan: F5 (docs + demo) and F6 (verification + tag v0.3.0)

## Approach
F5 is a docs-only change on branch `frontend-f5-docs`. Every sentence is checked against code (paths and lines from `.agent/exploration.md`, spot-verified: `web/package.json:4` is `0.0.0`; `Layout.tsx:21-26` footer has no version). F6 runs on `main` after F5 merges: full bare verification, then an annotated tag only if everything passes. Nothing is pushed.

## Rejected alternatives
- Put the UI walkthrough in README only: DEMO.md is where the offline-wheel stack is documented, so README links to it.
- Inline §18/§19 text into the new §20: duplication drifts; link instead.
- Render a version in the footer as part of F5: needs `web/src` edits, which are off-limits (see resolved decision D1).

## Steps

### F5 (branch `frontend-f5-docs`, from clean `main`)
0. Pre-flight: `git status --porcelain` empty; `git stash list` inspected (do not pop, drop, or apply the existing stash; record its contents with `git stash show --stat` in the report only); `git switch -c frontend-f5-docs`.
1. `README.md` : insert "Web UI" section between Quickstart end (`:107`) and `## Results` (`:109`). Content, each item traced:
   - Compose: `docker compose up -d --wait`, open `http://127.0.0.1:8080` (`WEB_PORT` override, `docker-compose.yml:63`; bound to 127.0.0.1).
   - Dev: `cd web && npm ci && npm run dev`, Node >=22.22.2 (`web/package.json:7`, `web/.nvmrc`), `API_PROXY_TARGET` default `http://127.0.0.1:8000` (`web/vite.config.ts:4-6`). State that dev still needs Postgres, `migrate`, and a worker or jobs stay `queued` (`docker-compose.yml:44-50`). Do not state Vite's port (unverified).
   - Shows: verdict and reason, call path table with low-confidence marks (`PathTable.tsx:14`), agent commentary labelled "not used to derive the verdict" (`Commentary.tsx:6-7`), tool trace (`ToolTrace.tsx:13`), LLM mode label (`displayText.ts:72-77`).
   - Limitations, plain: local only, no auth (`Layout.tsx:15`); recent jobs in this browser only, max 20 (`recentJobs.ts:9`); package + version only, no `repo_url` (`NewJobPage.tsx:209`); compose default is stub mode, not a real model; Package is not shown on the job page and Target is "not recorded" for queued/running/failed jobs because `TriageOut` has no target (`main.py:121-134`, DECISIONS §18); R9 trims `/extracted/` prefixes only in path-table `file` and verdict `reason` (`PathTable.tsx:41`, `VerdictSection.tsx:61`), not in tool trace, commentary, or error text; nginx resolves `api` once at startup, so `docker compose restart web` after recreating `api` (`web/nginx.conf:21-22`); real-model injection resistance is not claimed (DECISIONS §17).
   - Reference both screenshots: `agent_docs/screenshots/app-trace-grouped-light-1280.png`, `agent_docs/screenshots/app-capped-light-1280.png`.
   - Add a `web/` bullet to `## Repo` (`:166-196`); add a one-line pointer in Limitations (`:150-164`) to the Web UI limits. Moderate length, no marketing.
2. `agent_docs/DEMO.md` : add "UI walkthrough" section and update the stack line at `:29` to include `web`. Exact steps: `python3 scripts/build_fixture_wheels.py --out <DIR>`; `FIXTURE_WHEELS_DIR=<DIR> docker compose -f docker-compose.yml -f docker-compose.smoke.yml up -d --build --wait`; open `http://127.0.0.1:8080`; submit `reach-fx-02` / `1.0` / `pkg.sink` / `vulnerable` (expect Reachable, 3 edges, stub mode), `reach-fx-09` (No path found), `reach-fx-13` (Unknown, last edge low-confidence `dynamic_dispatch`). Note the Target "not recorded" while queued/running (`JobPage.tsx:121`). Do not quote job ids or tmp paths from `web/design/samples` (v0.2.0 captures). Add one placeholder line: `agent_docs/screenshots/demo.gif` : to be recorded. Do not create the GIF. Say `scripts/demo.sh` is the non-browser equivalent of the same three cases.
3. `CLAUDE.md` : (STABLE file: edits take effect only after `/clear`; tell the user.) Edit line 6 stack to add `web/` (React 19, TypeScript, Vite, react-router, npm, nginx). Update line 9 "Lint/typecheck command: none configured" to name `web/` `npm run typecheck` and `npm run lint` (`web/package.json:12-13`) and keep "none" for Python. Frontend paragraph (`:28-42`): F5 moves to built; keep the Package/Target caveat and nginx-restart sentence. NOT BUILT list gains: server-side job list, auth on the web UI, target in `TriageOut`, `repo_url` acquisition. Replace "the CI `web` job has not yet run on GitHub" with: it has run and passed on GitHub. Do not cite a run URL.
4. `DECISIONS.md` : append `## 20. Frontend decisions (20 Oct 2026)` (date format as at `DECISIONS.md:1342,1351`) after §19 (`:1351`), recording decisions 1-8 from `PHASE_FRONTEND.md:42-84` with reasons (same-origin over CORS; generated types over hand-written; localStorage over a list endpoint; the rest as written there). Evidence-first rendering is spec §3 and the sourcehut/GOV.UK design is §6 (`PHASE_FRONTEND.md:227`), not numbered decisions 1-8: include them in §20 as clearly labelled additions citing those sections, not as part of 1-8. Decision 8 is "match `web/design/reference.html`". Link §18 and §19; do not restate them. Read `PHASE_FRONTEND.md:42-84` first and copy decision numbering exactly.
5. `agent_docs/PHASE_FRONTEND.md` : line 3 status: F1-F5 built, F6 pending; add F4 status line (none exists). Add "Open items from F4 review": (a) `Host $host` drops the non-default port, `web/nginx.conf:25` and `:31` (not 24/30; those are `proxy_pass`); (b) smoke check (k) POSTs a job it never polls, `scripts/smoke_compose.sh:151-164`, judged harmless in `.agent/review.md:20-23`. Do not edit the §0 non-goals.
6. `agent_docs/plans/F5_PLAN.md` : copy this file, then edit the copy: record the user decisions as resolved (no open-decision text), carry no SHAs from `.agent/` files, and describe the step-8 check without spelling its pattern.
7. Version per resolved decision D1 = A (footer shows no version, docs say so; web/src untouched): `web/package.json:4` and `web/package-lock.json:3,9` to `0.3.0`.
8. Review: run the reviewer on `git diff main...frontend-f5-docs`. Added-line checks: run the em-dash and hype-word check (an em dash plus the four words the user banned) over the added lines of the diff, excluding this file and `+++` headers. It must return no lines. Em dashes already in pre-existing text are not mine, but lines I add or rewrite (including CLAUDE.md lines 6, 9, 28-42 and text copied from PHASE_FRONTEND.md:42-84) must have none: reword to commas, colons or periods. Any hit in a line I did not author or edit is reported, not fixed. Fix findings, then merge to `main` with `--no-ff`.

### F6 (on `main`, after F5 merge)
9. `git status --porcelain` empty and `git branch --show-current` is `main`. If dirty, stop and report; do not stash, do not clean.
10. `.venv/bin/pytest` (bare, full). Report the exact current passed/skipped/failed/error counts (user: not the 366/3 baseline). Prior baseline: 366 passed, 3 skipped, from `.agent/test-report.md:2` (a prior bare run). Any collection error is a failure; do not quote the baseline as the current result.
11. Web checks in a container using the node image digest pinned in `web/Dockerfile` (package.json:7 needs node >=22.22.2; an unpinned `node:22` may be older), run with `--user $(id -u):$(id -g)` so node_modules is not root-owned; a node:22 container with the repo mounted, not the host node: `npm ci && npm run typecheck && npm run lint && npm test && npm run build` in `web/`. Report the vitest file/test count from output (last known 9 files / 123 tests, pre-merge F4, `.agent/progress.md:12`). Container must not leave `node_modules` or `dist` tracked; confirm `git status --porcelain` is still empty after.
12. `docker compose build`.
13. `bash scripts/smoke_compose.sh` on the clean tree, then `bash scripts/demo.sh`. Both must exit 0.
14. If and only if 9-13 all pass: `git tag -a v0.3.0 -m <5-8 lines>`. Message states only what code shows: web UI under `web/` (submit form, job page with verdict, path, commentary, tool trace), nginx web service in compose, offline-wheel UI walkthrough in DEMO.md, test counts from step 10 and 11, stub-mode default in compose, real-model injection resistance unclaimed. No push. Report tag SHA (`git rev-parse v0.3.0^{commit}`) and tag object SHA.

## Failure rule (F6)
F5 is merged to `main` before F6 verifies. If any F6 step fails: do not tag, stop and report. Fix forward on a new branch only with user approval; do not revert the merge on my own.

## Success criteria
- `git diff main...frontend-f5-docs --stat` lists only: README.md, CLAUDE.md, DECISIONS.md, agent_docs/DEMO.md, agent_docs/PHASE_FRONTEND.md, agent_docs/plans/F5_PLAN.md, plus web/package.json and web/package-lock.json (D1 = A). Nothing under `main.py`, `src/`, `alembic/`, `web/src`.
- The em-dash and hype-word grep in step 8 returns no lines.
- No added line says a §0 non-goal is built (server-side list, auth, `repo_url`, injection resistance).
- Each limitation bullet in README has a file:line in this plan that the reviewer can open.
- F6: bare `.venv/bin/pytest` shows 0 failed and 0 errors; web checks exit 0; `docker compose build`, smoke, and demo exit 0; `git tag -n8 v0.3.0` shows the annotated message; `git status --porcelain` empty; `git log origin/main..main` shows the work is unpushed.

## Out of scope
Pushing; the demo GIF; any change to `main.py`, `src/`, `alembic/`, `web/src`; the F4 review items beyond recording them; making the footer show a version ; `.env.example` `WEB_PORT` line (optional, skipped); re-verifying the CI `web` job result.

## Resolved decisions
- D1 = A: bump `web/package.json:4` and `web/package-lock.json:3,9` to 0.3.0 only. The footer (`Layout.tsx:21-26`) shows no version and the docs say so. `web/src` untouched.
- Spec F6 says "push"; the user said do not push. This plan does not push.
- CLAUDE.md records the CI `web` job as passed on GitHub. The v0.3.0 commit itself will not have run in CI until it is pushed.
- Branch name: `frontend-f5-docs`.
- The existing stash is not touched.
- SHAs in docs are cited only from the commit log.
- `main.py:23` sets no version, so openapi reports `0.1.0` (`web/openapi.json:367`). Untouched.
- CLAUDE.md edits take effect only after `/clear`, `/compact`, or restart.
- Fixture 13 allows `["unknown","reachable"]` in label.json, but demo and smoke check exact `unknown`; the walkthrough says so.
- F6 verification happens after F5 merges. If F6 fails: do not tag, stop, report.
