# Plan: Frontend Unit F2 (submit form, usePollTriage, recent jobs)

Branch: `frontend-f2-<slug>`. All paths under `/home/raghavp/reachability/web/` unless absolute. Baseline from `.agent/exploration.md` and `agent_docs/PHASE_FRONTEND.md` §2, §3 R7/R8, §4 F2, §6.

## Approach
Add react-router with two routes (`/` form, `/jobs/:id` placeholder only; the job view is F3). Build the form, a `usePollTriage` hook and a localStorage recent-jobs list on top of the unchanged `src/api/client.ts`. Pure logic (validation, 422 mapping, storage, backoff) lives in small modules so each F2 bullet has a direct unit test. The reference.html `:root` tokens and screen 1 rules move into `src/styles/` verbatim, with inline `style=` turned into classes.

## Dependencies (listed first, per decision 1)
- `react-router` ONLY (not `react-router-dom`), v7 line. **Resolved version: `7.18.4`** (resolved 2026-10-16 via `npm view react-router@7`; published 2026-09-15; peerDependencies `react >=18`, so compatible with installed React 19.3.1). The registry `latest` tag is 8.x; 7.x is deliberately chosen per user amendment 4. `.npmrc` already has `save-exact=true`. Install: `npm install react-router@7.18.4`, then check `package.json` has no range. All imports come from `react-router`.
- No other new dependency. `@testing-library/user-event` is NOT added; tests use `fireEvent`.
- client.ts contract: unchanged. Nothing in F2 needs a change to it.

## Rejected alternatives
- Hand-rolled `history` routing: more code than a pinned router, and F3/F4 need `/jobs/:id` deep links anyway.
- Polling via `setInterval`: overlapping requests on slow responses; a chained `setTimeout` after each response is correct.
- Adding `user-event`: extra dependency for no gain over `fireEvent`.
- Putting status into localStorage to feed "Last seen": contradicts decision 7 (see resolution 3).

## Steps (ordered)
1. (Done) react-router version resolved and recorded above, before install.
2. `package.json`, `package-lock.json` — add `react-router@7.18.4` exact — routing.
2b. `design/reference.html` — screen 1 recent-jobs table to three columns (drop "Last seen") — keeps design and code in agreement (user amendment 3).
3. `src/styles/tokens.css` — copy reference.html `:root` (L12-32) and dark `@media` (L33-50) verbatim — decision 8. Do NOT copy `.ref-label`/`.screen` (L154-156).
4. `src/styles/base.css` — copy base (L52-68), layout (L71-86), forms (L124-143), `.error-summary` (L145-148), `.status-line` (L150), footer (L152), tables (L104-111) verbatim, plus new classes `.optional` (font-weight 400), `.form-note` (max-width 30rem), `.panel-note` (font-weight 400) replacing the three inline styles. Imported from `src/main.tsx`.
5. `src/main.tsx` — import the two CSS files — styles load.
6. `src/components/Layout.tsx` — `header.bar` (name, nav, env span) + `<Outlet/>` in `main.wrap` + footer per reference L164-170, L318 — shared chrome. No version string hard-coded unless the footer text in reference is kept as written there.
7. `src/lib/formErrors.ts` — `validateForm(values)` (package, version, target_module trimmed non-empty; symbol optional), `FIELD_IDS` map, `mapApiDetails(details)` returning `{ byField, formLevel }` — resolutions 1 and 2.
8. `src/lib/recentJobs.ts` — `loadRecent()`, `saveRecent(entry)`; key `reachability.recentJobs.v1`; entry `{id, target, pkg, submittedAt}`; newest first, dedupe by id, max 20; every storage access in try/catch, returns `[]`/no-ops when unavailable or JSON is malformed — decision 7. `loadRecent` also validates SHAPE: parsed value must be an array; each entry must be an object with string `id`, `target`, `pkg` and a `submittedAt` that parses to a valid date; invalid entries are dropped individually, never thrown. Test with valid JSON of wrong shape (`{}`, `null`, `[1,"x",{"id":5}]`).
9. `src/hooks/usePollTriage.ts` — hook per resolution 4 (amended: 3-consecutive-failure rule); exports pure `nextDelay(prev)` (1000 → x1.5 → cap 5000).
10. `src/components/RecentJobs.tsx` — heading "Recent jobs" with `.panel-note` "· stored in this browser only"; table columns Target / Package / Submitted; row link to `/jobs/${encodeURIComponent(id)}` as `a > code` (id is stored data, so encode it); empty list renders a short "No jobs submitted from this browser yet." Text only, no `dangerouslySetInnerHTML`.
11. `src/routes/NewJobPage.tsx` — form per reference L171-205 using `<form onSubmit>` and `button.primary[type=submit]` "Start triage" (deviation from ref `type=button`: a real form needs submit). Flow: validate → on errors render `div.error-summary[role=alert][tabindex=-1]` ("There is a problem", `a[href="#<id>"]` per field error) and focus it; else `createTriage` with an AbortController (aborted on unmount, button disabled while in flight) → `saveRecent` → `navigate('/jobs/' + body.id)`. Server ApiError 422 → `mapApiDetails`; other ApiError or NetworkError → form-level summary item with the error message. Includes `.form-note` "Repository URLs are not supported yet. Only published packages can be checked." and `<RecentJobs/>`.
12. `src/routes/JobPage.tsx` — placeholder only: `h1` "Job", the ID as text. No hook wiring, no evidence rendering (F3).
13. `src/App.tsx` — `<Routes>`: Layout with `/` and `/jobs/:id`; BrowserRouter goes in `src/main.tsx` (edit it in step 5's file, same change set). Tests use MemoryRouter.
14. `src/App.test.tsx` — replace the old "Reachability triage" h1 assertion with a MemoryRouter test that `/` shows "New triage job" and `/jobs/abc` shows the placeholder.
15. Tests (colocated, explicit vitest imports, `afterEach(cleanup)`, `vi.stubGlobal('fetch', ...)`; fixtures from `src/api/__fixtures__/`): `src/hooks/usePollTriage.test.tsx`, `src/lib/formErrors.test.ts`, `src/lib/recentJobs.test.ts`, `src/routes/NewJobPage.test.tsx`, `src/components/RecentJobs.test.tsx`.

## Resolutions of exploration ambiguities
1. **422 loc to field id.** Keep reference ids. `FIELD_IDS = {package:'package', version:'version', target_module:'module', target_symbol:'symbol'}`. A detail with `loc` of length 2 and `loc[0]==='body'` and a known key sets that field's error (`span.field-error`, `.field.error`) and a summary link. Unknown key → form-level. Recommend: mapping table, not renaming ids (matches the reference markup).
2. **`["body"]` loc** (422-missing-version.json) names no field: show server `msg` as a summary item without link, and under the form. Never discarded. Also render the top-level `message` once if it adds text not already shown. Client validation requires package AND version (no repo_url in the form); the server remains authoritative, so server text is shown verbatim when it arrives.
3. **"Last seen" column vs decision 7 (drop ACCEPTED by user).** Drop the column (3 columns). `web/design/reference.html` screen 1 is updated to the same three-column table (thead, rows, any colspan/CSS touching the 4th column) in the same commit as the code so design and code agree. Reason for deviation from reference: decision 7 permits only id, target summary and submit time; storing status would add a stale value that F2 has no code path to update. State this deviation in the gate report. Alternative (not chosen): amend decision 7 to add a status field.
4. **Polling semantics (amended by user).** Chained `setTimeout`: first request immediately, then wait `nextDelay` after each response (1000, 1500, 2250, 3375, 5000, 5000...). The delay schedule is NOT reset by failures: failed attempts continue the normal backoff schedule. Terminal: `completed`/`failed` body -> stop. **404 `NOT_FOUND` -> state `not_found`, stop immediately.** **Other 4xx ApiError -> state `error`, stop immediately.** **NetworkError, 5xx ApiError and `UNPARSEABLE_RESPONSE` (and `UNPARSEABLE_ERROR`) are transient: keep polling on the normal schedule, count consecutive failures, and stop only on the 3rd consecutive failure** -> state `error`, `data` retained from the last good response, retry control shown. **Any successful response resets the failure count to 0.** **Cap: 10 minutes counted from the first poll** (not from each response). Mechanism: a separate deadline `setTimeout(600000)` armed at start (and re-armed by `retry()`/`resume()`), plus next-poll delay clamped to the remaining time, so no request ever fires after 600000 ms. When the deadline fires: abort any in-flight request, discard its result, set state `capped`, no further requests, `resume()` offered. Last good `data` is kept. **`retry()` (and `resume()`) restarts both the 10-minute cap clock and the failure count**, and resets the delay to 1000. Unmount/id change aborts the in-flight fetch; AbortError is swallowed, never surfaced. Return shape: `{ data, state: 'polling'|'done'|'capped'|'not_found'|'error', error, failureCount, lastCheckedAt, resume, retry }`. Lint-safe design (react-hooks 7.1.1): `failureCount` and `state` are React state (not refs read in render); the loop's mutable bookkeeping lives in refs touched only inside effects/callbacks; no synchronous setState in the effect body (first poll is invoked via the async loop, not inline setState); `retry`/`resume` bump a `runId` state that is an effect dependency, so the loop restarts through the effect, and an id change does the same. StrictMode double-mount must leave exactly one live loop.
5. **Inline style vs CSP** (`style-src 'self'` in F4): no `style=` attributes anywhere in F2 markup; use classes from step 4. Add a test asserting rendered `/` has no element with a `style` attribute.
6. Other: submit navigates by response body `id` (client exposes no headers); `/jobs/:id` is a placeholder; copy "Resume checking" (reference L316) not "resume polling"; no em dashes or emoji in copy.

## F2 bullet to test mapping
| F2 bullet | Covered by |
|---|---|
| `/` form (R8), client checks mirror pydantic | `NewJobPage.test.tsx` (empty submit shows summary + field errors, no fetch call), `formErrors.test.ts` |
| R8 server 422 renders message and each loc/msg by field | `formErrors.test.ts` and `NewJobPage.test.tsx` using `422-empty-body.json` (field mapping incl. `target_module` → `#module`) and `422-missing-version.json` (`["body"]` form-level) |
| submit → navigate to `/jobs/:id` | `NewJobPage.test.tsx` (stubbed 202, MemoryRouter shows placeholder with the id; entry saved) |
| hook: backoff | `usePollTriage.test.tsx` fake timers: request times at 0, 1000, 2500, 4750, 8125, 13125 ms; plus `nextDelay` unit |
| hook: terminal stop | `completed` and `failed` bodies each: no further fetch after advancing timers |
| hook: 10-minute cap | advance 10 min: state `capped`, fetch count frozen; `resume()` restarts cap, failure count and delay at 1000 ms |
| hook: abort on unmount | inspect `init.signal.aborted` after unmount; no state error, no later fetch |
| hook: one blip then success | ok, then one NetworkError, then ok: polling continues on schedule, failure count back to 0, state stays `polling` (also a 5xx and an `UNPARSEABLE_RESPONSE` variant) |
| hook: three consecutive failures | ok (data kept), then 3 failures (NetworkError; repeat for 5xx and UNPARSEABLE_RESPONSE): state `error`, no 4th request, `data` still the first body, retry control available; `retry()` resets failure count and cap clock and polls again |
| hook: failure count resets | fail, fail, ok, fail, fail: still polling (count never reaches 3) |
| hook: 404 (R7) | `404-valid-uuid.json`: state `not_found`, stops at once, exactly one request |
| hook: other 4xx | e.g. 400/422: state `error`, stops at once, exactly one request |
| hook: cap from first poll | cap measured from first poll across responses; `retry()` restarts cap clock |
| recent jobs from localStorage, "this browser only" | `recentJobs.test.ts` (order, dedupe, max 20, malformed JSON, throwing `getItem`/`setItem`) and `RecentJobs.test.tsx` (label text present; renders with storage throwing) |
| hook: cap deadline | at exactly 600000 ms no request has fired after the deadline, an in-flight request at the deadline is aborted and its result discarded (abort-aware fetch stub, `advanceTimersByTimeAsync`) |
| hook: StrictMode / id change | rendered inside `<StrictMode>`: exactly one live loop (one request at t=0); changing `id` aborts the old loop and starts a fresh one, no stale data from the old id |
| recent jobs: wrong-shape storage | `recentJobs.test.ts` and `RecentJobs.test.tsx`: valid JSON of wrong shape renders empty list, no crash; id with `/` or `?` is encoded in the link |
| app wiring | `App.test.tsx` |

## Test-harness notes (from critique)
Fake-timer tests use `vi.useFakeTimers()` with `advanceTimersByTimeAsync`; the `fetch` stub is abort-aware (rejects with `AbortError` when `init.signal` aborts) so unmount/cap tests are meaningful. Submitted values are trimmed before being sent (tested). The top-level 422 `message` is shown only when it is not strictly equal to any already-rendered item text. Footer text and nav follow reference.html verbatim; "Submitted" renders `toLocaleString()` of the ISO timestamp in a `<time dateTime>`.

## Success criteria
- From `/home/raghavp/reachability`: bare `pytest` reports 366 passed, 3 skipped, no collection errors (F2 touches no Python).
- `git diff --stat main -- main.py src alembic` is empty. (`.agent/` is gitignored, `.gitignore:6`, so it cannot be diffed; instead `git status --short` shows changes only under `web/`.)
- In `web/`: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` all exit 0. `npm ls react-router` shows the exact pinned version; `package.json` has no `^`/`~` on it.
- `grep -rn "console\.\|debugger" web/src` finds nothing in non-test code; `grep -rn "style=" web/src` finds nothing in non-test code.

## Gate-report checklist
- Test counts for pytest (bare) and vitest, exactly as printed; lint/typecheck/build exit codes.
- react-router `7.18.4` as installed (`npm ls react-router`), and confirmation `react-router-dom` is absent.
- The reference.html screen 1 diff (Last seen column removed).
- Each F2 bullet with its test file and test name (table above).
- Deviations stated with reasons: "Last seen" column dropped, `type=submit`, inline styles to classes.
- `npm run dev` with the API proxy (or vite preview): render `/` next to `web/design/reference.html` screen 1 (light and dark scheme, with and without error state) and list visible differences. Capture states: empty, 422 field error, `["body"]` error, populated recent jobs.
- Confirmation that the app starts clean and no debug output exists.

## Out of scope
Job view and evidence rendering (R1-R6, R9), wiring `usePollTriage` into `/jobs/:id`, Docker/nginx/CSP/CI (F4), docs (F5), repo_url submission, any change to `main.py`, `src/`, `alembic/`, `client.ts`, or the OpenAPI snapshot (note: `web/design/reference.html` IS edited, screen 1 table only), status storage in localStorage.
