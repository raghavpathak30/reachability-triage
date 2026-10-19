# Plan: Frontend Unit F3, job view at /jobs/:id (evidence rendering)

## Approach
Replace the placeholder `web/src/routes/JobPage.tsx` with a view driven by `usePollTriage` (unchanged; its contract is in `web/src/hooks/usePollTriage.ts:5-53`, and `client.ts` is unchanged). Rendering is split into small presentational components under `web/src/components/job/`, plus one pure display-text module (`web/src/lib/displayText.ts`) that does the R9 path trimming and redaction-marker splitting, so the rules are unit-testable and nothing is mutated. Target and package are not in `TriageOut`, so the target comes from `finding.result` when present, else from the localStorage `RecentJob` for that id (`web/src/lib/recentJobs.ts`), else an explicit fallback. Missing CSS is copied verbatim from `reference.html` L89-122 (confirmed absent from `base.css`; only `.error-summary` matched my grep), with inline `style=` replaced by classes.

## Decisions (made here because the spec is silent; flag in review)
- **Amendment 2 (user-approved).** Title/target trust boundary: the `Target` and `Package` facts and every verdict-derived element (verdict section, path table, commentary, trace) come ONLY from `finding.result` / `finding`. The localStorage `RecentJob` may be used ONLY for the page `<h1>` and ONLY while `finding` is null; it is rendered as a plain React text node. Once `finding` exists the h1 is `target_module[:target_symbol]` from `finding.result`. When `finding` is null the Target fact reads "not recorded" (RecentJob is NOT used for facts), and the h1 is RecentJob.target for this id, else `Job <first 8 chars of id>`. **Package fact is dropped** (critique blocker 2): `finding.result` has no package field (verified against `stub-reachable.json`: result keys are target_module, target_symbol, verdict, path, reason), and RecentJob may not feed facts. Facts list = Target, Status, LLM mode, Job ID. Deviation from reference screens 2-3 (which show Package) goes in the deviation list. Step 14 creates the Target fact explicitly.
- `done` is not `completed`: branch on `data.status` (failed: `finding` null, show `error`).
- Empty `path: []` counts as no evidence (R1 warning "No evidence returned").
- Retry during auto-polling: `retry` is the hook's existing function; calling it restarts the 10-minute cap clock. Accepted, noted in deviation list; hook is not changed.
- Retry control shows whenever `error !== null` (hook keeps `state: 'polling'` until the 3rd failure) and in state `error`; last good `data` stays rendered. "Resume checking" only in state `capped`. Both call the same hook function (`resume` and `retry` are one function); use `resume` for capped and `retry` for errors, for naming only.
- Copy not fixed by the spec: reachable = "Reachable", reachable_only_from_tests = "Reachable only from tests" (ochre), unknown = "Unknown", not_reachable = "No path found" (plain text colour, per R2). Status words: Queued / Running / Completed / Failed.
- "Last checked N seconds ago": tick state updated by a `setInterval` inside an effect (avoids `Date.now()` in render, which may trip react-hooks 7.1.1 purity lint; unverified, lint decides).
- **Amendment 1 (user-approved).** Tool-call grouping (R6): two calls are "identical" iff same `tool_name` AND same `arguments`, compared by canonical JSON (keys sorted recursively). Same tool with different arguments are separate rows. Only consecutive (by `sequence`) identical calls group. A group renders one row with the sequence as a range (`3–15`, single `N` when count is 1), the label `span.repeat` "same call N times in a row", and the FIRST call's result shown once (later results are not compared or shown).
- **Amendment 3 (user-approved).** When verdict is `unknown` and `path` is non-empty, the path section heading is "Partial path" (otherwise "Call path", as in the reference). Running state line is exactly "Running. Last checked N seconds ago." Capped state line is exactly "Stopped checking after 10 minutes." followed by a `button.link` "Resume checking". Both copied from `reference.html` screen 3 (L315-316).
- R9 path regex: remove everything from the start of the whitespace-delimited token through the first `/extracted/` (`/\S*?\/extracted\//g`, non-greedy per token). Known limit: paths containing spaces are not handled; no marker means the string is unchanged. Applied to `CallEdge.file`, `reason`, `error`, `rationale`, tool `result`, and tool `arguments` text. Tighten (critique): token start excludes leading quotes/parens/brackets (so `('/tmp/x/extracted/a.py')` keeps `('`), require the path to be absolute (starts with `/`) so a relative `pkg/extracted/...` is untouched, and bound the token length (e.g. `[^\s]{0,512}?`) to avoid quadratic backtracking. Unit tests cover each. **Amendment 4 (user decision, narrows R9 to the spec):** the trim is applied ONLY to `CallEdge.file` (path table location) and the `reason` text, via `displayPath`. Tool `result`, tool `arguments` (rendered as raw pretty JSON, `JSON.stringify(args, null, 2)`, no trim), `rationale` and `error` (including `error.message`) render exactly as received. `RedactedText` still applies to those strings (marker muted with sandbox title), without `displayPath`. This supersedes the earlier list of fields above.
- Breadcrumb: "Jobs" links to `/` (no list route exists).
- All untrusted text rendered as React text nodes only.

## Rejected alternatives
- Change `usePollTriage` or `client.ts` to expose target/package: no backend field exists, and the spec says not to change contracts without need; localStorage fallback needs no contract change.
- Whole-string prefix strip for R9: fails on `reason` in `stub-unknown.json` where the path is mid-sentence.
- Render `retry` only in state `error`: would hide the control for the first two network failures, contradicting R7 intent.
- Add Playwright to `web/package.json`: scope and dependency policy; install outside the repo for the review step only.
- Pill badges for confidence: forbidden by spec section 6; plain text plus `.conf-low`.
- Copy the reference "3 more calls on step_b..." row: it wrongly collapsed calls with different arguments; replaced per Amendment 1 (see step 2a).

## Steps
1. Environment check (no file edits). From `/home/raghavp/reachability/web`: `node --version` (need >=22.22.2 per `web/package.json` engines, `.nvmrc` is `22`), `npm --version`, then `npm ci`. If node is missing or too old, stop and report; do not claim any web check passed.
2a. `web/design/reference.html` screen 2 (L268-270, in the same commit as the code): replace the single "2–4 ... 3 more calls on step_b, step_a, pkg.entry" row with three separate `.call` rows, since calls 2-4 have different arguments. Use the real data from `stub-reachable.json`: seq 2 `find_callers {"node_id": "pkg.layer_b:step_b"}` -> CallEdge caller `pkg.layer_a:step_a` lineno=5; seq 3 `find_callers {"node_id": "pkg.layer_a:step_a"}` -> CallEdge caller `pkg.entry` lineno=4; seq 4 `find_callers {"node_id": "pkg.entry"}` -> `[]`, each with the same `.call-head` / `.redacted` markup as rows 0-1. Summary stays "Tool calls (5)". Screen 3's "3–15 same call 13 times in a row" row (L311) stays: same node_id, so it is a valid group.
2. `web/design/samples/failed.derived.json` — new raw TriageOut: `status: failed`, `finding: null`, `error` like `"AcquisitionError: ... "` including the redaction marker, `llm_mode: "stub"`. Before writing, check `job_runner.py` L142-157/L195-200 shape (already confirmed in exploration) and state in the test comment that failed is derived because the samples dir contains only completed runs (exploration gotcha 20: not proven stub cannot produce it).
3. `web/design/samples/repeated-calls.derived.json` — completed, unknown/not_reachable verdict, `tool_calls` with 3 consecutive identical `find_callers` (same arguments) between distinct calls, one result containing `PosixPath('[REDACTED: instruction-like text]')`.
4. `web/design/samples/no-evidence.derived.json` — completed, `path: null`, `reason: null`, verdict `unknown`.
5. `web/design/samples/groq-mode.derived.json` and `web/design/samples/null-mode.derived.json` — copies of a completed shape with `llm_mode: "groq"` / `null`. (Kept because the task explicitly lists groq and null mode as derived fixtures.) Playwright fallback: if the browser download fails, report it and stop at that item; do not substitute another tool silently.
6. `web/src/styles/base.css` — append `.facts`, `.facts dt/dd`, `.verdict` (+ `.label`, `.value`, `.value.reachable`, `.value.tests-only`, `p`), `.reason`, `.reason b`, `.note`, `blockquote.commentary`, `details`, `summary`, `.call`, `.call-head`, `.call pre`, `.redacted`, `.repeat` from `reference.html` L89-122, plus a new `.crumb` class replacing the breadcrumb inline style; do not copy `.ref-label`/`.screen` (L154-156). Reason: base.css lacks them.
7. `web/src/lib/displayText.ts` — `displayPath(s)`, `splitRedactions(s): {text, redacted}[]` using marker `[REDACTED: instruction-like text]` (`sandbox.py:70`), `groupToolCalls(calls)` (sorts a COPY by `sequence`, treats undefined/null `calls` as empty, never mutates input), `lowConfidenceCount(path)`, `llmModeLabel(mode)`. Reason: pure logic, testable.
8. `web/src/lib/displayText.test.ts` — unit tests for the helpers (mid-sentence path, no-marker unchanged, marker split, grouping boundaries, empty trace) plus Amendment 1 tests: `R6 does not group three find_callers calls on different node_ids` (3 groups of count 1) and `R6 groups three find_callers calls on the same node_id` (1 group, count 3, range first..last, first result kept); also key-order independence (`{"a":1,"b":2}` equals `{"b":2,"a":1}`).
9. `web/src/components/job/RedactedText.tsx` — renders `splitRedactions` output with `span.redacted` and `title="Removed by the injection sandbox. Absolute paths are always redacted."`.
10. `web/src/components/job/VerdictSection.tsx` — R1/R2: verdict word, sentence, reason (via RedactedText + displayPath), "No evidence returned" warning when path null/empty and reason null.
11. `web/src/components/job/PathTable.tsx` — R3: table columns `# | Caller | Callee | Location | Confidence | Rule` in `.table-scroll`, `tr.low`, `td.conf-low`, header summary "N edges, M low confidence", location `displayPath(file):lineno`.
12. `web/src/components/job/ToolTrace.tsx` — R6: `<details><summary>Tool calls (N)</summary>`, sorted by `sequence`, grouped, args as pretty JSON text, result in `pre`. Arguments and results are shown raw (Amendment 4).
13. `web/src/components/job/Commentary.tsx` — R5: h2 "Agent commentary", note "Written by the agent to explain its steps. Not used to derive the verdict.", `blockquote.commentary` through RedactedText.
14. `web/src/routes/JobPage.tsx` — facts: Target (finding.result only, else "not recorded"), Status, LLM mode, Job ID (no Package, see Decisions). Unknown/unexpected verdict string renders neutral "Unknown" with the raw value as text; `status: completed` with `finding: null` renders an explicit "No result returned" warning. Queued state shows "Queued." (not the Running line); when `lastCheckedAt` is null the line reads "Running. Not checked yet."; N comes from a tick state. Guard `id` undefined (render not-found copy without calling the hook: split into outer `JobPage` and inner `JobView({id})` so the hook is never conditional), call `usePollTriage`, compose: breadcrumb, h1, facts (Package, Status, LLM mode R4, Job ID), status line, verdict, path, commentary, trace; states: `not_found` ("Job not found" plus the id, R7), `failed` (error text in `.error-summary` style, red), network error with retry button and kept data, capped with "Resume checking" `button.link`, queued/running with "Running. Last checked N seconds ago.".
15. `web/src/routes/JobPage.test.tsx` — component tests (table below). Real samples imported from `../../design/samples/*.json` in test files only (ESLint exempts `*.test.*`); `vi.stubGlobal('fetch', ...)`, `afterEach(cleanup)`, `fireEvent`, `MemoryRouter` + `Routes`, fake timers where polling is needed, pattern from `web/src/hooks/usePollTriage.test.tsx`. Each test asserts rendered text or DOM structure.
16. `web/src/App.test.tsx` — rewrite L17-25: route `/jobs/abc` with a stubbed fetch returning 404 `NOT_FOUND`; assert "Job not found" and `abc`. Reason: the placeholder assertions (`heading "Job"`, `getByText('abc')`) break.
17. Run web checks (from `web/`, Node 22): `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`. Fix failures (including any purity lint on the clock).
18. Bare `pytest` from repo root, full run; record counts versus baseline 366 passed / 3 skipped. A collection error is a failure.
19. Visual review (manual, outside repo): install Playwright into the scratchpad dir `/tmp/claude-1000/-home-raghavp-reachability/e789d234-5f6f-4ee2-ba8a-efd75779b333/scratchpad/pw` at an exact version chosen with `npm view playwright version` and written into the gate report; not added to `web/package.json`. Serve `web/dist` via `vite preview` with `page.route` stubbing `/v1/triage/*` using the samples and derived files. Capture light and dark (`colorScheme`) at 1280 and 390 for reachable, not_reachable, unknown, failed, running; compare with `reference.html` screens 2 (L219-273) and 3 (L275-319); record `document.documentElement.scrollWidth` vs `clientWidth` at 390 for each. List every deviation with its reason in the gate report. Known planned deviations: no Package fact (no backend field); h1 heading none (heading is "Call path", or "Partial path" per Amendment 3); retry restarts the cap; no inline styles (CSP, F4); breadcrumb links to `/` not `#`; reference "3 more calls" row replaced by R6 grouping; reference reason shown pre-trimmed while the app trims raw data; added copy for failed/queued/reachable_only_from_tests/null target fallback.
20. Grep gates: `grep -rn dangerouslySetInnerHTML /home/raghavp/reachability/web/src` returns nothing; also `style=` and `console\.|debugger` in `web/src`.
21. `cp /home/raghavp/reachability/.agent/plan.md /home/raghavp/reachability/agent_docs/plans/F3_PLAN.md`, then on the feature branch (create if still on main, e.g. `frontend-f3-job-view`) `git add` the web files and `agent_docs/plans/F3_PLAN.md` by name and commit. Do not push. Do not touch `.agent/` contents beyond this plan, `main.py`, `src/`, `alembic/`.

## Rule to test table (all in `web/src/routes/JobPage.test.tsx` unless noted)
| Rule | Planned test name | Fixture |
|---|---|---|
| R1 | `R1 renders the verdict together with its path (reachable sample)` | stub-reachable |
| R1 | `R1 renders the verdict together with its reason (not_reachable sample)` | stub-not_reachable |
| R1 | `R1 shows a no evidence returned warning when path and reason are both null` | no-evidence.derived |
| R2 | `R2 unknown renders neutral with the reason prominent` | stub-unknown |
| R2 | `R2 not_reachable is labelled no path found and has no safe or success styling` | stub-not_reachable |
| R3 | `R3 renders ordered edges with location, confidence and rule` | stub-reachable |
| R3 | `R3 flags low confidence edges and states the count in the path header` | stub-unknown |
| R4 | `R4 stub mode label` / `R4 groq label` / `R4 null renders not recorded` | stub-reachable / groq-mode.derived / null-mode.derived |
| R5 | `R5 labels the rationale as agent commentary with the not-used-for-verdict note` | stub-reachable |
| R6 | `R6 trace is collapsible and renders five separate rows for stub-reachable` | stub-reachable (asserts 5 `.call` rows, calls 2-4 separate) |
| R6 | `R6 orders by sequence even when the response is out of order` | repeated-calls.derived with shuffled array order |
| R6 | `R6 non-consecutive identical calls do not group` | displayText.test.ts |
| R6 | `R6 missing tool_calls renders no trace without throwing` | derived/inline body with tool_calls omitted |
| R6 | `R6 groups consecutive identical calls with a count` | repeated-calls.derived |
| R7 | `R7 failed job shows the error string` | failed.derived |
| R7 | `R7 404 shows job not found with the id` | 404 body (inline, matches `404-valid-uuid.json` envelope) |
| R7 | `R7 network failure keeps the last good status and id and shows retry` | a `running` body first, then network errors (a `completed` body halts polling at `usePollTriage.ts:95-106`, so errors never occur; the hook's own tests use running first). Asserts the retained status text, id and the retry button; it does NOT claim a retained verdict. |
| R7 | `R7 capped state shows Resume checking and it restarts polling` | running body, fake timers |
| R9 | `R9 shows file paths after /extracted/` | stub-reachable |
| R9 | `R9 replaces a mid-sentence path in reason` | stub-unknown |
| R9 | `R9 leaves a string without the marker unchanged` | displayText.test.ts |
| R9 | `R9 renders the redaction marker muted with the sandbox title` | repeated-calls.derived / failed.derived |
| R9 | `R9 shows a tool result containing /extracted/ exactly as received` / `R9 shows a failed error string containing /extracted/ exactly as received` / `R9 shows commentary exactly as received` / `R6 arguments render as raw pretty JSON with no path trimming` | repeated-calls.derived / failed.derived / stub-reachable |
| R6 | `R6 does not group same-tool calls with different arguments` / `R6 groups same-tool same-arguments calls and shows a sequence range` | displayText.test.ts + repeated-calls.derived |
| R3 | `R3 titles the table Partial path when verdict is unknown and path is non-empty` | stub-unknown |
| R7 | `R7 running state reads Running. Last checked N seconds ago.` / `R7 capped state reads Stopped checking after 10 minutes. with Resume checking` | running body, fake timers |
| extra | `tampered localStorage RecentJob never changes the verdict section, target fact or path table; it only affects the h1 while finding is null, rendered as text (e.g. an `<img onerror>` string appears as literal text)` | stub-reachable + queued body + localStorage seed |
| extra | `target falls back to RecentJob then to the id` ; `App renders job view at /jobs/:id` | in JobPage.test.tsx / App.test.tsx |

## Success criteria
- All tests above pass: from `/home/raghavp/reachability/web`, `npm test` (vitest run) exits 0.
- Gate-report checklist (each item reported with the command output, no claim without evidence):
  - [ ] Bare `pytest` from repo root: 366 passed, 3 skipped (or any difference explained); no collection errors.
  - [ ] Node version and `npm ci` result (Node 22).
  - [ ] `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` all exit 0.
  - [ ] Playwright screenshots light and dark at 1280 and 390 for reachable, not_reachable, unknown, failed, running, each compared to reference screens 2 and 3, with scrollWidth vs clientWidth at 390 recorded and a deviation list with reasons.
  - [ ] `grep -rn dangerouslySetInnerHTML web/src` returns nothing; same for `style=` and `console.|debugger`.
  - [ ] `git diff --stat` shows no changes to `main.py`, `src/`, `alembic/`, `usePollTriage.ts`, `client.ts`.
  - [ ] `agent_docs/plans/F3_PLAN.md` exists and the commit is on the branch, unpushed.
- App starts clean, no debug prints (project CLAUDE.md).

## Out of scope
- F4 (nginx, CSP, compose frontend container); backend changes (no target/package in `TriageOut`); a jobs list route; hook or client changes; runtime validation of response bodies; adding Playwright or any dependency to `web/package.json`; updating CLAUDE.md "NOT BUILT" frontend text (do this only after the merge, with file and line evidence); R8 (form only, F2); pushing.

## Open questions for the caller (defaults chosen above)
- Verdict sentences and status copy are invented where the spec is silent.
- Whether `path: []` should count as evidence (plan: no).

## F5 open items (for DECISIONS.md; recorded here at user request)
- `GET /v1/triage/{id}` does not return the submitted target (package, version, target_module, target_symbol), so the job page cannot show Package for queued, running or failed jobs. The backend fix is to include target in `TriageOut`. Until then the page shows Target only from `finding.result` (completed jobs), and the h1 may use the localStorage `RecentJob` while `finding` is null (Amendment 2).


## Known limitations (for F5 docs / DECISIONS.md)
- R9 trim limits: a `file=`-prefixed token is left untouched; with repeated `/extracted/` segments the trim ends at the first match, e.g. `/tmp/a/extracted/b/extracted/c.py` becomes `b/extracted/c.py`; paths containing spaces are not handled.
- R9 is applied only to `CallEdge.file` and `reason`; tool results, arguments, rationale and error strings show worker temp paths when they contain them.
- Deviation: Caller and Callee are separate table columns (matches `reference.html`), not R3's "caller_id -> callee_id" form.
