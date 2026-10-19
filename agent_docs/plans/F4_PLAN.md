# Plan: Frontend F4 (web container, nginx, compose service, CI web job, smoke additions)

## User amendments (approved at plan gate, 2026-10-19) — these override the steps below
- A1 `.nvmrc` -> `22.22.2`: approved. A2 `demo.sh` free `WEB_PORT`: approved.
- A3 (R1): `web/.dockerignore` does NOT ignore `design/samples` (only `node_modules`, `dist`). Dockerfile keeps `RUN npm run build` (tsc -b + vite build); no fallback to bare `vite build`; step 12 experiment is dropped. Gate check: grep `dist` for a sample job id, expect no match (sample JSON must not be bundled).
- A4 Security headers live in ONE file, `web/nginx/security-headers.conf`, `include`d in both the server block and the `/assets/` location. Dockerfile copies it to the path the include names. Confirmed by user: exactly THREE headers — Content-Security-Policy (incl. `frame-ancestors 'none'`), X-Content-Type-Options nosniff, Referrer-Policy no-referrer. No X-Frame-Options.
- A5 Both extra smoke checks required: (i) inline `<script>` body / `<style` / `style=` guard on served `/`; (ii) proxied `GET $WEB_BASE/v1/triage/not-a-uuid` must return 404 with the API's JSON error envelope AND all security headers. (Verify against the api that not-a-uuid really yields 404, not 422; if not, stop and report rather than loosen.)

## Critique follow-ups (critic verdict APPROVE, 0 blocking; non-blocking items folded in)
- C1 Stale text: the "Rejected alternatives" bullet about `include`-ing a shared headers snippet is superseded by A4 (include is now required). Ignore the `;;` typo in step 5's healthcheck text.
- C2 Check (k): either assert the `Location` header explicitly (a small urllib POST to `$WEB_BASE/v1/triage` checking `Location: /v1/triage/<id>`), or drop the "Location passthrough" claim. `triage_client.py:49,71` does not read it. Plan: add the explicit assertion.
- C3 Smoke and demo.sh: pick `WEB_PORT` so it differs from `API_PORT` (retry until distinct).
- C4 Inline-script guard must allow `<script type="module" crossorigin src=...>`; fail only on a `<script>` with no `src` or with a non-empty body, `<style`, or ` style=`.
- C5 Build stage uses a Debian-slim Node tag (not alpine; Vite 8 native optionals). Runtime stays alpine nginx-unprivileged (busybox wget).
- C6 Expect the `compose-smoke` job (15 min timeout) to run longer; check its timeout headroom after the gate run.

- A6 CI timeout: after the gate run, report compose-smoke wall time; if it exceeds 10 of 15 min, raise `compose-smoke` `timeout-minutes` to 20 in the same branch and say so. If Docker is unavailable to the agent, stop before steps 11-13 and hand the user exact commands. Plan is copied to `agent_docs/plans/F4_PLAN.md`; do not push.

## Approach
Add a self-contained `web/` image (Node 22 build stage, `nginxinc/nginx-unprivileged` runtime on 8080) whose nginx serves the built SPA with the decision-6 headers and reverse-proxies `/v1/` and `/healthz` to `api:8000`. Wire it into compose as a `web` service gated on a healthy `api`, add a CI `web` job, and extend `scripts/smoke_compose.sh` to prove the SPA, the headers, and one fixture job through the web proxy. Nothing under `main.py`, `src/`, `alembic/`, or `web/src` changes. Everything is verified by running it (digests by registry lookup, CSP by grepping the real `dist`, headers by `curl -I`), not by reading source. The exploration was source-only; the CSP pre-check was later done by the caller in a node:22.23.3 container, and step 11 re-verifies it.

## Rejected alternatives
- Build context `.` with `web/Dockerfile`: drags the whole repo into the web build and makes the root `.dockerignore` apply. Use `build: ./web`.
- Serving the SPA from FastAPI: contradicts decision 2 (same origin via nginx).
- `proxy_pass` with a variable and `resolver 127.0.0.11` to re-resolve `api`: changes URI handling for `/v1/`. A static `proxy_pass http://api:8000;` is enough for a local-only stack, and the staleness risk is noted below.
- `include`-ing a shared headers snippet: adds a file for three lines. Repeat the headers in the one extra location instead (only `/assets/` needs its own `add_header`).
- A `'unsafe-inline'` or hash allowance in the CSP: not needed, since the built `dist` has no inline script or style.
- Healthcheck on `/healthz`: it proxies to the api and would mask an nginx fault. Check `/` (nginx-only).
- Using `npm run build` in the Docker build stage with `design/samples` ignored: see Risk R1. `tsc -b` would fail.

## Steps
1. Look up the digests (no file change, recorded in steps 2 and 3). Run `docker buildx imagetools inspect node:<tag>` and `docker buildx imagetools inspect nginxinc/nginx-unprivileged:<tag>`. Choose exact tags first, e.g. a full `node:22.x.y-<variant>` with x.y >= 22.22.2 (the caller saw v22.23.3 on the `node:22` pull; confirm the exact patch tag exists and that `docker run --rm node:<tag> node -v` prints it), and an exact-version nginx-unprivileged alpine tag. Take the multi-arch index digest ("Digest:" line), not a per-platform digest. Never guess; the `node:22` index digest the caller observed (`sha256:0e5f9065...2da8`) is only valid for the floating tag. Re-pin to the exact tag and use whatever the lookup returns. Record the date (2026-10-19).
2. `web/Dockerfile` (new) — Multi-stage:
   - Header comment in the root `Dockerfile` format: `# Base pinned to an exact tag plus the digest observed at build time` / `# (2026-10-19); refresh periodically.`, one comment line per base if they were looked up separately.
   - Build stage `FROM node:<exact-tag>@sha256:<digest> AS build`, `WORKDIR /app`.
   - `COPY package.json package-lock.json .npmrc ./`, `RUN npm ci` (so `engine-strict` applies; the image Node must be >= 22.22.2 or `npm ci` fails loudly, which is the intended guard).
   - Then `COPY . .` (filtered by `web/.dockerignore`).
   - Build command: `RUN npm run build` (per A3).
   - Runtime stage `FROM nginxinc/nginx-unprivileged:<exact-tag>@sha256:<digest>`. `COPY --from=build /app/dist /usr/share/nginx/html`. `COPY nginx.conf /etc/nginx/conf.d/default.conf` (replaces the stock file; no `user` or `pid` directives). `EXPOSE 8080`. Leave the image's default non-root user (uid 101) in place; add no `USER 0`.
   - Why: tag+digest precedent (root `Dockerfile:1-3`), `npm ci` per decision 1, non-root per the root `Dockerfile` `USER 10001` precedent.
3. `web/nginx.conf` (new; installed as `conf.d/default.conf`, so it holds a `server {}` block only):
   - `listen 8080;`, `server_tokens off;`, `root /usr/share/nginx/html;`.
   - Per A4, the three headers are defined once in `web/nginx/security-headers.conf` (each with `always`) and pulled in with `include /etc/nginx/security-headers.conf;` at server level and inside `/assets/`; Dockerfile copies the file there. The inline text below describes its content, not duplicated config. Original note: at server level, with `always` on each: `add_header Content-Security-Policy "default-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'" always;` (byte-for-byte the decision-6 string), `add_header X-Content-Type-Options "nosniff" always;`, `add_header Referrer-Policy "no-referrer" always;`.
   - `location / { try_files $uri /index.html; }` (no own `add_header`, so the server-level ones inherit).
   - `location /v1/ { proxy_pass http://api:8000; }` and `location = /healthz { proxy_pass http://api:8000; }`. Plain `proxy_pass` without a URI part forwards the original path. Add `proxy_set_header Host $host;` and `X-Forwarded-For`. Do NOT set `proxy_intercept_errors` or `proxy_redirect` (the API 404/422 JSON envelope and the relative `Location: /v1/triage/{id}` must pass through untouched).
   - `location /assets/ { try_files $uri =404; add_header Cache-Control "public, max-age=31536000, immutable" always; include /etc/nginx/security-headers.conf;; }`. This is the one location with its own `add_header`, so the inheritance rule forces the repeat. `=404` makes a missing asset a real 404, not index.html with 200. Keep it as one of the two documented deliberate repeats; comment it in the file.
   - Optionally `location = /favicon.ico { return 204; }` with the three headers repeated (avoids index.html served as the favicon). Include it only if you accept the extra repeat; otherwise skip it.
   - Note: nginx adds the server-level `add_header`s to proxied responses too. Confirm the api does not send its own CSP (it does not; `main.py` has no such middleware), so no duplicate headers appear.
4. `web/.dockerignore` (new) — `node_modules`, `dist` only (per A3, `design/samples` is NOT ignored; supersedes the original scope line). Also add `.git`-style noise only if present under `web/`. Do not ignore `src/**/__fixtures__`, `tsconfig*.json`, `.npmrc`, `openapi.json`, `index.html`, `vite.config.ts`. See R1 for the `design/samples` side effect.
5. `docker-compose.yml` — add a `web` service after `worker`. Existing services untouched.
   - `build: ./web`
   - `ports: ["127.0.0.1:${WEB_PORT:-8080}:8080"]`
   - `depends_on: api: condition: service_healthy`
   - `healthcheck: test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]`. Exec form, no whitespace inside elements (podman note in `scripts/healthcheck.py:3-7`), `127.0.0.1` not `localhost` (IPv6 `::1` risk), busybox wget (verify with `docker run --rm --entrypoint wget <web image> --help`). Add `interval: 2s`, `timeout: 5s`, `retries: 30` to match the other services.
   - A short comment above the healthcheck: it proves nginx serves `/`, not that the api proxy works (the smoke script does).
6. `.github/workflows/tests.yml` — append a `web` job (existing jobs unchanged), `runs-on: ubuntu-latest`, `timeout-minutes: 10`, `defaults.run.working-directory: web`. Steps: `actions/checkout@v4`; `actions/setup-node@v4` with `node-version-file: web/.nvmrc`, `cache: npm`, `cache-dependency-path: web/package-lock.json`; then `npm ci`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`.
   - Node >= 22.22.2 problem: `.nvmrc` says only `22`, and `setup-node` resolves `22` to the latest 22.x on the runner manifest. That should satisfy `engines >=22.22.2`, but it is not guaranteed on a stale manifest, and `engine-strict` makes `npm ci` fail then. Preferred fix: change `web/.nvmrc` to `22.22.2` (edit in step 7) so the floor is explicit and matches `package.json` `engines`. Keep a comment in the job header explaining that `.nvmrc` mirrors the `engines` floor and must be bumped together with it. A second line of defence is the `npm ci` itself failing under `engine-strict`.
7. `web/.nvmrc` — change `22` to `22.22.2`. Why: makes CI `node-version-file` resolve to exactly the engines floor (step 6). If you prefer not to touch `.nvmrc`, the alternative is `node-version: "22"` in the job plus a `node --version` assertion step, but then the "node-version-file" requirement is dropped; confirm with the caller before taking it.
8. `scripts/smoke_compose.sh` — keep checks (a)-(g) exactly as-is. Edits:
   - Port selection: after the `API_PORT` block (lines 45-49), add a `WEB_PORT` block with the same free-port idiom (`if [ -z "${WEB_PORT:-}" ]; then ... bind(("127.0.0.1",0))`), `export WEB_PORT`, `WEB_BASE="http://127.0.0.1:${WEB_PORT}"`. It must be exported before `up`, or the stack binds the default 8080 and collides with a local dev server or other runs. Update the header comment's Env list (`WEB_PORT`).
   - After check (b), add (h) SPA: GET `$WEB_BASE/` returns 200 and the body contains `<div id="root">` (confirm the exact root markup in the built `dist/index.html` before hardcoding the string). Use the same `$PYTHON` urllib style as (b).
   - (i) CSP on `/`: assert the response header `Content-Security-Policy` equals the exact decision-6 string, and `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` are present.
   - (j) CSP on a genuine 404. Use `GET $WEB_BASE/assets/does-not-exist.js`: the `/assets/` location uses `=404`, so nginx itself returns 404 (an SPA-fallback server would return 200 for any other path, so `/nonexistent` is NOT a valid 404 probe). Assert status 404 and the same three headers (this proves `always` works and the `/assets/` repeated headers exist). Also optionally `GET $WEB_BASE/v1/triage/00000000-0000-0000-0000-000000000000` (proxied api 404): assert 404 and headers. In Python, `urllib` raises `HTTPError` on 404; read headers from the exception (`e.headers`), not from a success response.
   - (k) One case through the proxy: `triage_client.py run --base "$WEB_BASE" ... --case reachable` (the existing loop at 73-78 keeps using `$BASE`; add the proxied case after it, or one extra loop iteration, same `check` call). This proves `/v1/` POST, `Location` passthrough, and GET polling via nginx.
   - Cleanup (`down -v --rmi local`) already removes the web image. `up -d --build --wait` already waits on the web healthcheck.
9. `scripts/demo.sh` — add the same free-`WEB_PORT` block next to the `API_PORT` block (lines 61-65) and `export WEB_PORT`. Why: demo.sh builds the same compose file, so without it the web service binds fixed 8080 and collides. No other change; it keeps using `$BASE` for the api.
10. Run the lockfile/context sanity checks (no file change): `ls web/package-lock.json web/.npmrc`; `docker compose config` must parse.
11. CSP verification, in a node:22 container (host node is v20, which `engine-strict` rejects): `docker run --rm -v "$PWD/web":/w -w /w node:<exact-tag> sh -c 'npm ci && npm run build'`, then on `web/dist`:
    - `grep -nE '<script|<style| style=' dist/index.html`: expect exactly one `<script type="module" ... src=...>` and no inline bodies, no `<style>`.
    - `grep -lE "createElement\(.style.\)|insertRule|adoptedStyleSheets" dist/assets/*.js`: matches are expected only from React 19 internals. The React 19 style-resource hoisting code is dead code, because it runs only for `<style href precedence>` elements and `web/src` renders none. Record this in the gate report.
    - Regression guard: `web/src` contains no `style=`/`<style` (existing `NewJobPage.test.tsx:62-64` asserts no inline `style` attribute on that page). Add the dist grep as an explicit smoke assertion: in `scripts/smoke_compose.sh` fetch `$WEB_BASE/` and fail if the HTML contains `<style` or ` style=` or an inline `<script>` body. That is a cheap guard against a future Vite/React change. No new test dependency, no `web/src` change.
12. (Superseded by A3: no experiment.) Instead: grep `dist` for a job id taken from `web/design/samples/*.json`; expect no match. Report it at the gate.
13. Build and run the real stack: `docker compose build web`, then `WEB_PORT=8080 docker compose up -d --build --wait` (or a free port), then the `curl -I` checks listed in the gate checklist. Then commit and run `bash scripts/smoke_compose.sh` on the clean tree (it refuses a dirty tree; exit 2).

## Success criteria
- `docker buildx imagetools inspect` was run for both bases; the `FROM` lines carry exact tag plus index digest; the date comment is 2026-10-19; node tag version >= 22.22.2.
- `docker compose -f docker-compose.yml config` exits 0 and shows the `web` service port `127.0.0.1:8080:8080` (default) and `depends_on.api.condition: service_healthy`.
- `docker compose build web` succeeds; `docker compose up -d --build --wait` reaches all-healthy; the container runs as non-root (`docker compose exec web id` shows uid 101, not 0).
- `curl -I http://127.0.0.1:8080/` returns 200 with exactly one each of the CSP (byte-for-byte decision 6), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and no nginx version in `Server`.
- `curl -I http://127.0.0.1:8080/jobs/x` returns 200 (SPA fallback) with the same headers.
- `curl -i http://127.0.0.1:8080/v1/triage/not-a-uuid` returns the API's own error status/JSON envelope (proxied, not index.html) with the three headers also present. `curl -I http://127.0.0.1:8080/assets/missing.js` returns 404 with the headers.
- `curl http://127.0.0.1:8080/healthz` returns the api's 200 body.
- `bash scripts/smoke_compose.sh` ends with `SMOKE OK` including new checks (h)-(k), and the old checks pass unchanged. Run this on a committed clean tree.
- Web checks, in a node:22 container (not host v20): `npm ci && npm run lint && npm run typecheck && npm test && npm run build` all exit 0.
- Full bare `pytest` from the repo root: exact command `pytest`. Report the count only from this bare run, against the caller's baseline of 366 passed / 3 (skipped or deselected as it was last reported; state the real numbers). A collection error is a failure. This change touches no Python under test, so the count should not move.
- App starts clean, no debug prints (project success criterion): `docker compose logs web api` show no nginx `[emerg]`/`[warn]` and no stray prints.

## Gate report checklist
- Bare `pytest` result vs 366/3 (counts, any collection error called out).
- Web checks output (lint, typecheck, test, build) with the Node version used (`node -v` >= 22.22.2).
- `docker compose config` output summary.
- `docker build` output tail for `web/Dockerfile` (the resolved tags and digests, final image id).
- `curl -I` on `/`, `/jobs/x`, `/v1/triage/not-a-uuid` via 8080 (full headers pasted).
- `grep` results on `dist` (step 11) and the dead-code note.
- `bash scripts/smoke_compose.sh` result (full tail incl. `SMOKE OK`), with commit SHA tested.
- Which 404 probe the smoke script uses (`/assets/does-not-exist.js`, plus optionally the proxied `/v1/` 404).
- Outcome of the R1 experiment and the chosen build command.

## Risks
- R1 (resolved by A3; kept for history): `web/tsconfig.app.json:16` has `"include": ["src", "design/samples"]` and the tests (`web/src/routes/JobPage.test.tsx:5-12`, `web/src/api/client.test.ts:8`) import `design/samples/*.json`. Excluding `design/samples` in `web/.dockerignore` therefore likely breaks `tsc -b` inside `npm run build`. Mitigation is step 12 (`npx vite build` in Docker, typecheck in the CI `web` job). Do not edit `tsconfig.app.json` or `web/src` to work around it.
- R2: nginx-unprivileged cannot bind 80 or write outside `/tmp` and cache dirs; do not add `user`/`pid`/`error_log /var/log` directives. If the stock image differs on config layout, check with `docker run --rm --entrypoint cat <img> /etc/nginx/nginx.conf` that `include /etc/nginx/conf.d/*.conf` is present.
- R3: `add_header` inheritance (see step 3): any later location with its own `add_header` silently drops the security headers. The smoke 404 check on `/assets/` is what catches this.
- R4: nginx resolves `api` once at startup. A restarted api container with a new IP will break the proxy until web restarts. Accepted for a local-only stack, and state it in the DECISIONS note.
- R5: `/favicon.ico` has no link tag; without the optional 204 rule the browser gets index.html. Cosmetic only.
- R6: busybox `wget` presence and `--spider` support are unverified; check in step 5 before relying on it.
- R7: digests drift. The pinned digest is a date-stamped snapshot (comment says refresh periodically); an exact tag whose digest changes upstream still builds, but only because the digest is pinned.
- R8: smoke depends on a free port picked then released (racy but identical to the existing `API_PORT` idiom).

## Out of scope
- Any edit to `main.py`, `src/`, `alembic/`, `web/src`, `web/openapi.json`, `tsconfig*.json`, or `docker-compose.smoke.yml`.
- TLS, auth, HTTP caching beyond `/assets/`, gzip tuning, rate limiting, deployment.
- Healthcheck proving the api proxy path (smoke does that).
- Re-resolving `api` DNS at request time.
- Adding `web/node_modules`/`web/dist` to the root `.dockerignore` (optional speedup only; skip unless the `build: .` api context is observed to be slow).
- Docs edits now: the CLAUDE.md STABLE block is not edited mid-session. See the Docs-phase notes below.

## Docs-phase notes (after the gate, in a separate step)
- `CLAUDE.md` NOT BUILT paragraph: remove "Not built yet: the nginx/CSP/compose frontend container (F4)" and state F4 as built, with file references verified by line number (`web/Dockerfile`, `web/nginx.conf`, the `web` service in `docker-compose.yml`, the CI `web` job, smoke checks (h)-(k)). Keep the "job page cannot show Package or Target" remark (DECISIONS §18). Keep "local only: no auth, no deployment". Edit takes effect after `/clear`.
- `DECISIONS.md`: a new section after §18 (F5 owns the consolidated decisions 1-8 section) recording: the pinned bases with tag, digest and date; the R1 outcome (why Docker builds with `vite build` and where typecheck is gated); the `.nvmrc` pin to the engines floor; `add_header` repeat in `/assets/`; the dead-code React style-hoisting note and the smoke guard; static `api` DNS resolution (R4); the 404 probe choice; `WEB_PORT` handling in smoke and demo.sh.
- `agent_docs/PHASE_FRONTEND.md` F4 section status line and `agent_docs/DEMO.md` mention of the web port (the demo prints `WEB_PORT` only if the script is changed to say so; not required).
