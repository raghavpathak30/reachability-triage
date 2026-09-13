# Phase — LangGraph adoption for the triage agent loop

Drop this in `agent_docs/PHASE_LANGGRAPH.md`. It is the spec the `/loop` planner reads.

---

## 0. Scope resolution (read this first)

**This phase is opt-in and goal-driven, not reactive to a measured gap.**
`.agent/investigation.md` (the research spike that precedes this phase) concluded
its own analysis with an explicit **defer** recommendation: LangGraph's actual
value — model-agnostic tool-calling, retries, streaming — only materializes once
a real LLM sits behind `StubLLMClient`'s Protocol, which is still not built
(CLAUDE.md: real LLM integration NOT BUILT). That recommendation is **knowingly
overridden here** — this phase proceeds as an explicit architecture-modernization
goal, not because the investigation changed its mind or a new functional problem
was found. Nothing below should be read as "the investigation was wrong"; it
should be read as "the investigation's caution is deliberately being paid for
now, on purpose."

**Because this phase is not closing a gap, its bar for correctness is not "does
it still work" but "is it provably identical to what already works."** The
project's own severity ordering — a confidently-wrong verdict is worse than
`unknown` (DECISIONS.md §4, D1/D3) — applies to *this migration itself*, not
just to reachability logic. Restated as this phase's actual, non-negotiable
gate criterion, repeated verbatim from the investigation because nothing below
supersedes it:

> **No cutover without a feature-flagged parallel run producing identical
> per-fixture verdicts across all 30 fixtures (22 L5 + 8 Phase 4) between the
> existing hand-rolled loop and the LangGraph-driven one. A verdict drift on
> even one fixture blocks cutover until root-caused.**

This is Unit 5's gate, and it is also the gate on Unit 6 ever starting. No
later unit reopens or loosens it.

**Design constraints carried forward from the investigation, not re-litigated
in this phase:**
- Build on raw `StateGraph`, not `create_agent`/`create_react_agent` — the
  convenience wrapper has been renamed twice in two years
  (`chat_agent_executor` → `create_react_agent`, now deprecated → `create_agent`,
  which moved to the `langchain` package) and building on it means inheriting
  future churn this project doesn't need.
- Tool-call budget enforcement stays a hand-rolled counter-in-state.
  LangGraph's `recursion_limit` raises `GraphRecursionError` rather than
  degrading gracefully; the existing contract (`agent_loop.py:178`: budget
  exceeded → `TriageFinding` with `Verdict.UNKNOWN`, never an exception reaching
  `job_runner.py`) must be preserved byte-for-byte in the new implementation.
- `sandbox_untrusted_text` (`sandbox.py`) is reused unchanged in content; only
  its call site moves into a LangGraph hook. A new test must prove no path can
  reach a tool result without passing through it — the old guarantee rested on
  `_append_tool_result` being private with no other caller (`agent_loop.py:216`),
  a guarantee LangGraph's plumbing does not give for free.
- `StubLLMClient`'s `next_action(context: list[Message]) -> AgentAction`
  Protocol (`stub_llm.py:95-98`) stays intact behind a thin adapter. This
  migration and real-LLM integration are two separate decisions; conflating
  them makes root-causing any verdict drift far harder.
- `eval_harness.py`'s gates (G1-G6) must be proven to measure the same thing
  post-migration — specifically, `DeterministicPolicyStubLLMClient`'s exact
  string-parsing/BFS behavior (it "parses stringified tool-result text from
  context to extract node IDs," per `.agent/exploration.md` §3) must survive
  the string-context → LangChain message-object plumbing change. A naive port
  could silently shift which fixtures pass G2/G3 without any real logic
  changing.

---

## 1. What this phase touches vs. leaves alone

New, parallel implementation lives in a new module,
`src/reachability/triage/langgraph_loop.py`, selected by an explicit env-var
flag (`TRIAGE_LOOP_BACKEND`, values `"stub_loop"` [default] / `"langgraph"`),
read once at dispatch time in `job_runner.py`. The existing
`src/reachability/triage/agent_loop.py` is **not modified** until Unit 6, and
even then only to delete it after cutover — no unit before Unit 6 edits it.

Untouched by this entire phase: `compute_reachability` and the L1-L4
index/query layer, `sandbox_untrusted_text`'s redaction logic itself (only its
call site changes), `job_runner.py`'s HTTP wiring beyond the one dispatch
branch, the DB/worker/reaper layer (Phase 3), and `eval_harness.py`'s gate
*semantics* (G1-G6's logic and thresholds do not change — only, eventually,
which loop implementation feeds them, and only after Unit 5 proves parity).

---

## 2. Build order

Seven units. Order is load-bearing: nothing wires the flag to a
reachable/default-on state before the sandbox guarantee and adapter parity are
independently proven, and nothing cuts over before the parallel-run gate is
clean.

1. **Unit 1 — Dependency**: `langgraph` (and its directly-imported
   `langchain-core`) pinned exact, no ranges.
2. **Unit 2 — StateGraph skeleton behind a flag**: new module, flag defaults
   to the existing loop; sanitize hook wired in from this unit's first commit
   (not bolted on later — see Unit 3).
3. **Unit 3 — Sandbox bypass-proof test**: proves Unit 2's wiring holds and
   stays held under regression.
4. **Unit 4 — StubLLMClient adapter**: plumbing-parity, independent of
   end-to-end verdict parity.
5. **Unit 5 — Parallel-run harness**: the phase's actual gate. All 30
   fixtures, both loops, full verdict diff, fails loud on any mismatch.
6. **Unit 6 — Cutover**: only after Unit 5 is clean. Flips the default,
   deletes the old loop's dead code, full docs reconciliation.
7. **Unit 7 — Merge/orchestration**: explicit final unit, per this project's
   standing convention.

Units 2-4 may be implemented in any internal order relative to each other, but
none of them may flip `TRIAGE_LOOP_BACKEND`'s default or make the LangGraph
path reachable in any deployed/CI-default environment — that only happens in
Unit 6, gated by Unit 5.

---

## 3. Units

### Unit 1 — New dependency: `langgraph`, pinned exact

Add to `requirements.txt`:
```
langgraph==1.2.11
langchain-core==<exact resolved version, captured via pip freeze at install time>
```
`1.2.11` is current stable per the investigation's research (2026-08-11,
verified against PyPI). **Do not assume this is still current at
implementation time** — CLAUDE.md's global rule applies here as much as
anywhere: re-check PyPI for the latest non-yanked stable release before
pinning, and if a newer patch exists, pin that instead and record the actual
installed version in `.agent/progress.md`, not this spec's number. Two prior
patch releases (1.2.3, 1.1.7) were yanked for regressions — do not pin a
yanked version.

No code changes in this unit.

**Gate:** `pip install -r requirements.txt` succeeds in a clean venv;
`python -c "import langgraph; print(langgraph.__version__)"` matches the pin;
a full bare `pytest` run collects and passes at the same count as before this
unit (a dependency addition alone must change zero test behavior — a
collection error here is this unit's own failure, not a pre-existing one).

### Unit 2 — StateGraph skeleton behind a feature flag

New file `src/reachability/triage/langgraph_loop.py`. A `StateGraph` (raw API,
not `create_agent`/`create_react_agent`) with:
- An agent node calling the adapted `StubLLMClient` (Unit 4's adapter; until
  Unit 4 lands, this unit may stub the node's model call minimally, but must
  not merge to a state where the flag is reachable without Unit 4 present —
  see build-order note above).
- A tool-execution node dispatching `search_symbol`/`find_callers`/
  `resolve_import`, reusing the existing schema validation
  (`agent_loop.py:83-92`'s `_is_well_formed_tool_call` and
  `agent_loop.py:95-102`'s `_dispatch_tool`, imported, not duplicated).
- A **sanitize node between tool execution and the agent node, calling the
  existing `sandbox_untrusted_text` unchanged, wired in this unit's first
  commit** — mirroring this project's own precedent for `sandbox.py` itself
  (DECISIONS.md §6.U3/U4: "existed from U3's first commit... signature and
  call site never changed"). There is no intermediate state in this unit's
  history where a tool-result edge reaches the agent node unsanitized.
- A hand-rolled budget counter in graph state (not `recursion_limit`),
  checked before the agent node's model call, with a conditional edge to
  `END` producing the same `TriageFinding`/`Verdict.UNKNOWN` /
  `"budget_exceeded: ..."` shape `agent_loop.py:178-189` produces today —
  verified byte-identical reason string, not just "also unknown."
- Same four termination shapes as `agent_loop.py:177-257`: budget exceeded,
  malformed tool call, target symbol not confirmed, normal completion via
  `compute_reachability`.

`job_runner.py` gains a single dispatch branch on `TRIAGE_LOOP_BACKEND`
(default `"stub_loop"`, unchanged behavior); no other line in `job_runner.py`
changes.

**Gate:** with the flag forced to `"langgraph"` in a test-only context, the
new graph runs at least one fixture end-to-end and produces a well-formed
`TriageFinding` (this unit does not require verdict-correctness yet — that's
Unit 5 — only that the skeleton executes and terminates through one of the
four shapes). **If `StateGraph`'s actual current API (verify against live
docs, not this spec's research snapshot) cannot cleanly support the
budget-exceeded-degrades-gracefully requirement without fighting the
framework** — e.g. if `recursion_limit`'s `GraphRecursionError` cannot be
intercepted and translated before it would otherwise propagate as an
unhandled exception to `job_runner.py`'s broad `except Exception` — **stop and
surface this for input immediately**, per this phase's explicit escalation
condition. Do not silently accept a weaker contract (e.g. "budget exceeded
becomes a caught exception converted to `failed` status" instead of a
structured `UNKNOWN` verdict) as good enough.

### Unit 3 — Sandbox bypass-proof test

New test file `tests/test_triage_langgraph_sandbox.py`. Two things, both
required, neither substitutable for the other:

1. **Structural assertion**: inspect the compiled graph's actual node/edge
   definitions (via whatever introspection `StateGraph.compile()`'s result
   exposes — verify what's actually available rather than assuming a specific
   method exists) and assert every edge carrying a tool result routes through
   the sanitize node before reaching the agent node. If the compiled graph
   object doesn't expose introspection precise enough for this, fall back to
   asserting it structurally in the graph-construction function itself (e.g.
   a single function, `build_langgraph_triage_graph()`, is the only place
   `add_node`/`add_edge` calls exist for this graph, and a code-level test
   walks its call sequence) — but prefer real graph introspection if
   available; state in `.agent/progress.md` which approach was used and why.
2. **Injection-payload regression test**: replay one of the existing
   injection-attempt strings from `tests/test_triage_sandbox.py` (or an
   adversarial fixture already exercising `sandbox_untrusted_text`) through
   the new graph end-to-end, and assert the redaction marker
   (`"[REDACTED: instruction-like text]"`, `sandbox.py:70`) appears in what
   the agent node actually sees — not just that `sandbox_untrusted_text()`
   itself still works in isolation (that's already covered by the existing
   test suite and proves nothing new about the graph's wiring).

**Proving the test actually catches a regression** (required, not optional,
per this unit's whole purpose): locally and temporarily wire a bypass path
(a second edge from the tool node straight to the agent node, skipping
sanitize), confirm both tests in this file fail, then revert the bypass —
this verification step itself is not committed, only its outcome recorded in
`.agent/progress.md`.

**Gate:** both tests exist, pass against the real (unbypassed) graph, and were
demonstrated (per above) to fail against a deliberately-reintroduced bypass.

### Unit 4 — `StubLLMClient` adapter into the model slot

Thin adapter, not a reimplementation. `DeterministicPolicyStubLLMClient`
(`stub_llm.py:101-161`) keeps its exact `next_action(context: list[Message])
-> AgentAction` signature and internal parsing logic untouched. The adapter's
only job: translate LangGraph's graph-state message list into the
`list[Message]` shape the stub already expects, call `next_action()`
unchanged, translate the returned `AgentAction` back into whatever the graph
node needs to route on.

**The one property this unit must prove, explicitly, because it's the
sharpest parity risk named in the investigation (§4):** the *string content*
of each tool result, as the stub's regex/string parsing sees it, must be
byte-identical between the old path (`agent_loop.py`'s
`_append_tool_result`, appending `sanitize_untrusted_text(str(raw_result))`
as a plain string) and the new path (however LangGraph's `ToolMessage` or
equivalent carries that same string). Do not let a serialization detail (e.g.
a LangChain message class's `str()`/`repr()` wrapping the content, or a
different quoting/escaping convention) silently change what the stub parses.

**Gate:** a new unit test (e.g. in `tests/test_triage_langgraph_loop.py`)
that, for a representative sample of tool-result payloads drawn from the
existing fixture corpus, asserts the adapter's reconstructed `list[Message]`
is content-identical (not merely equal in some looser sense) to what
`agent_loop.py`'s own context-building would have produced for the same
tool-call sequence. This is a plumbing-parity test, independent of Unit 5's
end-to-end verdict parity — it exists so that if Unit 5 ever finds a
mismatch, this test has already ruled the adapter's string-handling in or
out as the cause.

### Unit 5 — Parallel-run harness (the phase's actual gate)

New script, `scripts/run_langgraph_parallel_check.py`, structurally modeled
on `scripts/run_eval_suite.py` but doing something eval_harness deliberately
does not: running **both** implementations — `run_triage_loop` (existing,
`agent_loop.py`) and the new LangGraph graph (Unit 2, forced on regardless of
`TRIAGE_LOOP_BACKEND`'s ambient default) — across the full 30-fixture corpus
(`tests/fixtures/l5/` + `tests/fixtures/l5_phase4/`, same discovery roots
`eval_harness.py` already uses) and diffing the **full** `TriageFinding` per
fixture: verdict, `path`/`reason`, and `loop_degradation_reason` — not just
whether each passed its own gate.

**Report shape:** written to `results/langgraph_parallel_<git_sha>.json`
(mirroring the existing `results/eval_<sha>.json` /
`results/l5_<sha>.json` convention already in this repo), one row per
fixture: fixture id, old verdict/reason, new verdict/reason, `match: bool`.

**Failure mode:** any `match: false` row is a hard failure — non-zero exit,
the mismatching fixture(s) printed with both full findings inline (not just
"fixture 14 differs," the actual verdict/path/reason diff). Per this phase's
top-level gate (§0), **a single mismatched fixture blocks Unit 6 outright**;
this is not a threshold, floor, or percentage — it is exact, all-30-or-stop.

**If this run produces any mismatch, stop and surface it for input
immediately** — per this phase's explicit escalation condition. Do not
attempt to auto-root-cause and silently patch the new implementation to force
agreement without showing the actual diverging fixture and reason.

**Gate:** the script runs to completion against the current corpus, exits 0,
and `results/langgraph_parallel_<sha>.json` shows `match: true` for all 30
rows. This is the only condition under which Unit 6 may begin.

### Unit 6 — Cutover (only after Unit 5 is clean)

- Flip `TRIAGE_LOOP_BACKEND`'s default to `"langgraph"` in `job_runner.py`
  (or remove the flag entirely and make the LangGraph graph the only path —
  planner's call, recorded in `.agent/plan.md`, but either way the old
  `stub_loop` code path becomes unreachable in production after this unit,
  not merely deprioritized).
- Delete `src/reachability/triage/agent_loop.py`'s loop implementation
  (`run_triage_loop` and its private helpers) — **delete, not deprecate**.
  Anything Unit 2 already imports from it directly (e.g.
  `_is_well_formed_tool_call`, `_dispatch_tool`) must have already been moved
  to a shared location before this delete, not duplicated-then-orphaned.
- `eval_harness.py` and `scripts/run_eval_suite.py` now exercise the
  LangGraph-driven loop by construction (they call whatever `job_runner.py`
  /the shared entry point calls) — confirm this by re-running the full
  30-fixture eval suite post-cutover and recording the gate results in
  `.agent/progress.md`; they must match Unit 5's already-proven parity
  (G1-G6 all still pass, same as pre-cutover, because Unit 5 already proved
  identical verdicts).
- **Docs reconciliation, same commit:**
  - `CLAUDE.md` — new status paragraph stating the loop now runs on a
    LangGraph `StateGraph`, **explicitly framed as a goal-driven
    architecture change, not a bug fix or gap closure** — cross-reference
    `.agent/investigation.md`'s original "defer" recommendation and this
    phase's `## 0` for why it was overridden. Remove `agent_loop.py`'s
    hand-rolled-loop framing from wherever CLAUDE.md currently describes it;
    the tool-call budget, sandbox boundary, and stub/real-client boundary
    descriptions stay accurate in substance (same guarantees, new
    implementation) — update only what actually changed (module names,
    "StateGraph" vocabulary), not the guarantees themselves.
  - `DECISIONS.md` — new `## 10.` section (next number after §9), mirroring
    §8/§9's structure: what changed, why (goal-driven, not gap-driven — link
    `.agent/investigation.md`), the parallel-run parity proof and its result,
    and the three preserved guarantees (budget degrades gracefully, sandbox
    unbypassable, stub/adapter boundary unchanged) each with a one-line
    pointer to the unit that proved it (Unit 2, Unit 3, Unit 4 respectively).
  - `README.md` — update any description of the agent loop's implementation
    to name `langgraph_loop.py`/`StateGraph` instead of the old hand-rolled
    loop; do not touch unrelated sections.

**Gate:** full bare `pytest` run, zero collection errors, exact pass count
recorded (CLAUDE.md's global rule); `run_eval_suite.py` still reports
`overall_pass: true` against the same 30 fixtures; grep-level check that
`agent_loop.py`'s old loop code no longer exists (not merely unreferenced);
docs updated per above with no stale "hand-rolled loop" language remaining.

### Unit 7 — Merge/orchestration

Explicit final unit, per this project's now-standing convention. Before
merging any of Units 1-6's worktree branches into `main`:
- `git status` on `main` first — do not merge over unexamined local state.
- Resolve any conflicts by hand; do not use a mechanical
  `--strategy=theirs`/`--strategy=ours` shortcut.
- Merge units in build order (§2) — a later unit's branch may depend on an
  earlier one already being on `main`.
- After each merge, full bare `pytest` run before proceeding to the next
  unit's merge.

---

## 4. What is explicitly not in this phase

Real LLM API integration (the stub's Protocol boundary is preserved
unchanged behind the adapter — see Unit 4; this phase does not make
`next_action()` call a real model) · `create_agent`/`create_react_agent` or
any LangChain high-level convenience wrapper · any change to
`compute_reachability` or the L1-L4 index/query layer · any change to
`eval_harness.py`'s gate *logic* or thresholds (G1-G6 stay exactly as they
are; only which loop implementation feeds them changes, and only after Unit
5 proves it's a no-op change to their inputs) · any change to the DB/worker/
reaper layer (Phase 3) · retries, quotas, cost accounting, Docker, DB-stored
prompt versioning, or anything else already on CLAUDE.md's NOT BUILT list
untouched by this phase's own scope · loosening or removing Unit 5's
all-30-fixtures parity gate for convenience under any circumstance.

If a `/loop` iteration proposes any of these, the critic rejects the plan.
