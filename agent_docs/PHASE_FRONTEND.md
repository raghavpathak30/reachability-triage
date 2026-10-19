# Phase Frontend — React + TypeScript web UI

Status: spec, not built. Baseline: `main` at `3d5c633` (tag `v0.2.0`).
Target tag on completion: `v0.3.0`.

## 0. Goal and non-goals

**Goal.** A local web UI over the existing HTTP API that lets someone submit a
triage job, watch it run, and read the verdict *with its evidence* — call path,
reason, tool-call trace, and which LLM mode produced it. It ships inside the
existing Docker Compose stack and is the surface the demo GIF is recorded on.

**Non-goals (do not build, do not describe as built):**
- No backend behaviour change. `main.py`, `src/`, `alembic/` stay untouched
  except for the one OpenAPI snapshot test in F1. The test baseline (365 passed, 3 skipped at v0.2.0; 366 / 3 after F1) must be
  unchanged in count and result, plus whatever later units add.
- No auth, no users, no deployment. Local only, same as Phase 7.
- No server-side job list. There is no `GET /v1/triage` list endpoint; the
  "recent jobs" panel is browser-local (`localStorage`) and the UI says so.
- No `repo_url` submission. `acquisition.py:37,68` fails `repo_url` jobs fast.
  The form offers package + version only and states that repo URLs are not
  supported yet.
- No claim about real-model injection resistance anywhere in UI copy.
  It is unclaimed (`DECISIONS.md` §17).

## 1. API contract the UI consumes (as shipped in `main.py`)

| Call | Success | Errors |
|---|---|---|
| `GET /healthz` | 200 `{"status":"ok"}` | — |
| `POST /v1/triage` body `{package, version, target_module, target_symbol?}` | 202 `TriageOut`, `Location: /v1/triage/{id}` | 422 `{"error":{"code":"VALIDATION_ERROR","message","details":[{loc,msg}]}}` |
| `GET /v1/triage/{id}` | 200 `TriageOut` | 404 `{"error":{"code":"NOT_FOUND","message"}}` |

`TriageOut = {id, status: queued|running|completed|failed, finding|null, error|null, llm_mode: "stub"|"groq"|null}`

`finding = {result: {target_module, target_symbol|null, verdict, path: CallEdge[]|null, reason|null}, rationale, tool_calls: [{sequence, tool_name, arguments, result}]}`

`CallEdge = {caller_id, callee_id, confidence: high|medium|low, file, lineno, resolution_rule}`

`verdict ∈ reachable | reachable_only_from_tests | not_reachable | unknown`

## 2. Fixed decisions

1. **Location and tooling.** Everything lives in `web/`. Vite + React + TypeScript
   (`strict: true`, `noUncheckedIndexedAccess: true`). npm with a committed
   `package-lock.json`; CI and Docker use `npm ci`. Every dependency pinned to an
   exact version (no `^`/`~`) — the project's "pin everything" convention
   extends to JS. New deps are listed in `.agent/plan.md` before they are added.
2. **Same origin, no CORS.** Dev: Vite `server.proxy` forwards `/v1` and
   `/healthz` to `http://127.0.0.1:8000`. Compose: an nginx container serves the
   built SPA and reverse-proxies `/v1/` and `/healthz` to `api:8000`. The API
   gets no CORS middleware.
3. **Types come from the API, not from memory.** A committed snapshot
   `web/openapi.json` is generated from `app.openapi()`; TypeScript types are
   generated from it with `openapi-typescript`. A pytest test fails if the
   snapshot differs from `app.openapi()`, so backend drift breaks CI. If F1
   finds the `finding` dataclass tree is not fully typed in the schema, it
   stops at the gate and reports — it does not hand-write a guess.
4. **Routing.** Two routes: `/` (submit + recent jobs) and `/jobs/:id`
   (job view). `react-router` or equivalent; nginx `try_files ... /index.html`.
5. **Data fetching.** Plain `fetch` wrapped in one typed client module; no
   global state library. Polling is a custom hook with `AbortController`:
   start at 1 s, back off ×1.5 to a 5 s ceiling, stop on `completed`/`failed`,
   stop after 10 minutes with a visible "still running — resume polling"
   control. Unmount aborts in-flight requests.
   *Status (F2, shipped; the rules above are otherwise unchanged):* a 404 or any
   other 4xx stops polling at once. Network errors, 5xx and unparseable
   responses keep the normal backoff schedule and stop only after 3 consecutive
   failures, keeping the last good data; any success resets the count. The
   10-minute cap runs from the first poll, and `retry()`/`resume()` (the same
   function) restart the cap and the failure count. The visible "Resume checking"
   control is built (F3, `web/src/routes/JobPage.tsx`). `web/src/hooks/usePollTriage.ts`.
6. **Untrusted text.** Tool results, rationale, reasons, file paths and error
   strings are repo- or advisory-derived. Render them as React text only.
   `dangerouslySetInnerHTML` is banned via ESLint (`react/no-danger: error`).
   No markdown rendering of rationale. nginx sends
   `Content-Security-Policy: default-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'`
   plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
7. **localStorage** holds only recent job IDs + target summary + submit time
   (max 20). Every read/write in try/catch; the UI works with it unavailable.
8. **Visual style: match `web/design/reference.html`.** That file is the
   design. Its `:root` tokens and rules move into `web/src/styles/` verbatim;
   components reproduce its markup structure. Deviations need a reason in
   `.agent/plan.md`. See §6 for the rules behind it.

## 3. Evidence-first rendering rules (the point of this phase)

These are acceptance criteria, each covered by a component test.

- **R1 — never a bare verdict.** A verdict is rendered only together with its
  path (when non-null) or its reason (when non-null). If a completed job has
  both `path` and `reason` null, the UI shows the verdict plus an explicit
  "no evidence returned" warning rather than a clean result.
- **R2 — `unknown` is not failure and not success.** Neutral styling, and the
  reason shown prominently. `not_reachable` is never styled as "safe" or
  green-tick; label it "no path found" with its reason.
- **R3 — path as ordered edges.** Each edge: `caller_id → callee_id`,
  `file:lineno`, confidence badge, `resolution_rule`. `low` confidence edges are
  visually flagged and the path header states how many low-confidence edges it
  contains.
- **R4 — LLM mode always visible.** `stub` renders as "stub (deterministic
  policy, not a real model)"; `groq` renders as "groq"; `null` renders as
  "not recorded". Shown on every job view, not hidden behind a toggle.
- **R5 — rationale is labelled commentary.** Heading "Agent commentary",
  note under it: "Written by the agent to explain its steps. Not used to
  derive the verdict." (matches `agent_models.py` docstring).
- **R6 — tool-call trace.** Collapsible, ordered by `sequence`, showing
  tool name, arguments (pretty JSON as text), and result (text, preserved
  whitespace, scroll-capped). Consecutive identical calls are grouped with a
  count — this makes the Phase 9 looping pattern visible, it does not fix it.
- **R7 — failure states.** `failed` shows the `error` string. 404 shows "job
  not found" with the ID. Network failure during polling shows a retry
  control and does not discard the last good response.
- **R9 — display paths, not server temp dirs.** `CallEdge.file` and `reason`
  contain absolute worker paths such as
  `/tmp/reachability-triage-xamygun_/extracted/pkg/entry.py`. The UI shows
  the part after `/extracted/` (`pkg/entry.py:5`); if the marker is absent it
  shows the full string unchanged. This is display only; the stored data is
  not altered. The redaction marker `[REDACTED: instruction-like text]` in
  tool results is shown muted with a title explaining that the sandbox always
  redacts absolute paths (`sandbox.py` category 3a), so it does not read as
  an attack.
- **R8 — form errors.** Client-side checks mirror the pydantic rules
  (package and version both required, `target_module` non-empty), but the
  server is authoritative: a 422 renders `message` and each `details[].loc`/
  `msg` next to the field it names.

## 4. Units

Each unit is one `/loop` cycle with its own [GATE]. Branch per unit, named
`frontend-fN-<slug>`.

### F1 — Scaffold, typed client, contract snapshot
- `web/` Vite React TS project, ESLint (incl. `react/no-danger`), Vitest +
  React Testing Library, scripts: `dev`, `build`, `typecheck`, `lint`, `test`,
  `gen:api`.
- `scripts/dump_openapi.py` writes `web/openapi.json` from `app.openapi()`;
  `tests/test_openapi_snapshot.py` asserts equality.
- `web/src/api/client.ts`: `getHealth`, `createTriage`, `getTriage`, an
  `ApiError` type carrying `status`, `code`, `message`, `details`.
- Vite dev proxy.
- **Gate:** `pytest` = baseline + new snapshot test, all passing; in `web/`:
  `npm ci && npm run lint && npm run typecheck && npm test && npm run build`
  all pass; `git diff --stat main -- main.py src alembic` is empty.

### F2 — Submit form, polling hook, recent jobs
- `/` route: form (R8), submit → navigate to `/jobs/:id`.
- `usePollTriage(id)` hook (decision 5) with fake-timer tests for backoff,
  terminal stop, 10-minute cap, abort on unmount, network-error retention (R7).
- Recent-jobs panel from localStorage (decision 7), labelled "this browser
  only".
- **Gate:** all web checks pass; hook tests cover every bullet above.

### F3 — Job view (evidence rendering)
- `/jobs/:id`: status, target, R1–R7.
- Component tests: one per rule R1–R7 and R9. Start from the real stub-mode
  responses already committed in `web/design/samples/` (fixtures 02, 09, 13
  at v0.2.0: reachable with a 3-edge path, not_reachable with a reason,
  unknown with a low-confidence `dynamic_dispatch` edge). Hand-derive only
  the cases stub mode cannot produce (`failed`, repeated tool calls, both
  path and reason null) and mark them `derived` in the filename.
- **Gate:** all web checks pass; a grep for `dangerouslySetInnerHTML` in
  `web/src` returns nothing.

### F4 — Compose + CI
- `web/Dockerfile`: multi-stage, Node build stage → unprivileged nginx runtime,
  both base images pinned by tag + digest (Dockerfile precedent).
- `web/nginx.conf`: SPA fallback, `/v1/` and `/healthz` proxy to `api:8000`,
  headers from decision 6.
- `docker-compose.yml`: `web` service, `127.0.0.1:${WEB_PORT:-8080}:8080`,
  `depends_on: api: service_healthy`, healthcheck hitting nginx.
- CI: new `web` job (`npm ci`, lint, typecheck, test, build).
  `scripts/smoke_compose.sh`: add a check that `GET /` through `web` returns
  the SPA and that one fixture job submitted *through the web proxy* reaches
  its allowed verdict. Existing smoke checks unchanged.
- **Gate:** `scripts/smoke_compose.sh` passes locally (run by Raghav, clean
  tree); response headers from decision 6 verified with `curl -I`.

### F5 — Docs and demo
- README: "Web UI" section — how to run (compose and dev), what it shows,
  limitations (local only, no auth, recent jobs browser-local, package+version
  only, stub mode default, real-model injection resistance unclaimed).
- `agent_docs/DEMO.md`: UI walkthrough using the offline fixture wheels.
- `CLAUDE.md`: STABLE stack line updated to include `web/` (React, TS, Vite,
  npm); NOT BUILT gains "server-side job list" and "auth on the web UI".
- `DECISIONS.md`: new section recording decisions 1–8 and why (same-origin
  over CORS; generated types over hand-written; localStorage over a list
  endpoint).
- Demo GIF: recorded by Raghav, not the agent. Docs reference its path; the
  agent leaves a TODO marker only if the file does not exist yet.
- **Gate:** every claim in changed docs traceable to code; no doc says
  anything listed in §0 non-goals is built.

### F6 — Merge / orchestrator
- Merge F1–F5 branches to `main` in order, resolving doc conflicts against
  the code, not against either branch's prose.
- Full verification on merged `main`: `pytest` (count reported vs baseline),
  all web checks, `scripts/smoke_compose.sh`.
- Clean working tree, push, tag `v0.3.0`.
- **Gate:** report lists test count before/after, smoke result, tag SHA.

## 5. Risks

- **Checked at spec time (v0.2.0):** `app.openapi()` fully types the finding
  tree — `TriageFinding`, `ReachabilityResult`, `CallEdge`, `ToolCallRecord`,
  `Verdict`, `Confidence`, `ResolutionRule` are all named schemas, and
  `CallEdge.file` is `{"type":"string","format":"path"}`. Decision 3's stop
  condition should not fire; F1 still re-verifies.
- **OpenAPI lies about errors.** The schema advertises FastAPI's default
  `HTTPValidationError` (`{"detail":[...]}`) for 422, but the custom handlers in
  `main.py` return `{"error":{"code","message","details"?}}` for 422 and 404.
  The client must not use the generated 422 type. `ApiError` parsing is
  hand-written against the envelope in §1, with tests built from real
  responses. Fixing the OpenAPI declaration is a backend change and out of
  scope here; record it as an open item in `DECISIONS.md`.
- **Path inconsistency (found at spec time, backend, out of scope):** tool
  results redact absolute paths, but `result.path[].file` and `result.reason`
  return them raw to API clients. Local-only today, so low impact; record in
  `DECISIONS.md` as an open item to return repo-relative paths from the API.
- Phase 10 (repeated-call guard) is not on `main` at this spec's baseline. If
  it lands first, F1 regenerates the snapshot; R6's grouping stays useful
  either way.

## 6. Design rules (why the reference looks the way it does)

The reference is modelled on engineering tools designed by people, not on
SaaS landing pages: sourcehut (grey, content first, one colour for the
primary action, red only for danger, icons only with text) and the GOV.UK
Design System (labels above inputs, heavy input borders, yellow focus ring,
an error summary at the top of the form that links to each field). Both are
built for people trying to get a job done, which is what this tool is.

**Do**
- Content first. The verdict and its evidence are the page; navigation is a
  grey band with two links.
- Greys plus one ink-blue accent for links and the one primary button.
  Brick red only for `reachable` and errors; ochre only for low confidence
  and `reachable_only_from_tests`. `not_reachable` and `unknown` are plain
  text colour: colour means "act on this", nothing else.
- Hierarchy from size and weight, not from boxes. Tables for structured
  evidence (path edges, recent jobs). A `<details>` for the tool trace.
- System font stack for prose, system monospace for identifiers, paths,
  package specs and JSON. No web fonts (also keeps the CSP at `'self'`).
- Square-ish corners (0 to 2px). Focus: 3px `#ffdd00` outline with a dark
  ring, the same in both themes.
- Status as words: "Running. Last checked 2 seconds ago." Time and counts
  are stated, not animated.
- Plain, specific copy. Buttons say what they do ("Start triage",
  "Resume checking"). Error messages say what to fix ("Enter a version").

**Do not** (common tells of generated UI; all are lint or review failures)
- Inter or Geist; and equally the fashionable "alternative" picks (Space
  Grotesk, Fraunces, editorial italic serif headlines).
- Purple, indigo or blue-to-purple gradients; gradient text; cyan-on-dark;
  glows, halos, glassmorphism, grid-line backgrounds.
- Cards around every block, cards inside cards, a coloured stripe down one
  side of a box, thin border plus big soft shadow, large corner radii.
- Pill badges, eyebrow labels above headings, a hero number with a stats
  row, emoji as icons, icon tiles above headings.
- Pulsing or blinking status dots, spinners on static data, bounce easing,
  hover zoom. The only motion allowed is the native `<details>` toggle.
- Em dashes in UI copy, and hype words ("powerful", "seamless", "supercharge").

Review step in F3: render the built app next to `reference.html` at 1280px
and 390px, light and dark, and list every visible difference.

Sources consulted: impeccable.style/slop (catalogue of generated-UI tells),
The Fountain Institute, "7 Signs a UI Has Been Vibe Coded", Drew DeVault,
"Sourcehut's spartan approach to web design" (2019), GOV.UK Design System
colour, error summary and validation pages.
