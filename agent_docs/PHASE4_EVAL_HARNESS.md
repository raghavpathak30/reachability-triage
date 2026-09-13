# Phase 4 — Eval Harness Completion + CI Accuracy Gate

Drop this in `agent_docs/PHASE4_EVAL_HARNESS.md`. It is the spec the `/loop` planner reads.

---

## 0. Scope resolution (read this first)

**Open question, resolved:** exploration (`.agent/exploration.md`) found no reference
to "30 fixtures" or a named "Phase 4" roadmap anywhere in this repo — not in
`README.md`, `DECISIONS.md`, `CLAUDE.md`, `agent_docs/L5_HANDOFF.md`, or either prior
phase spec. The only place "30" and "Phase 4" appear together is this task's own
prompt. Given that, and given the task's own "Expected shape" section describes
corpus growth (22 → 30) and CI wiring of the *existing* harness as two of four units
— which only makes sense if Phase 4 is completing U6's already-built harness, not
inventing a second, parallel eval surface — **Phase 4 is resolved as: grow the
existing 22-fixture L5-shaped corpus to 30 fixtures, and CI-gate the existing
`run_eval_suite()` harness (`src/reachability/agent/eval_harness.py`). This is not a
new/separate eval surface, a new harness implementation, or a live-LLM integration.**

**Fixture disposition, stated with zero ambiguity:**
- The 22 existing fixtures stay exactly where they are, untouched:
  `tests/fixtures/l5/` (the same 20 numbered fixtures plus `14b_registry_dict_no_bare_name`
  and `16b_pep562_no_bare_name`).
- 8 new fixtures (`21`-`28`, see §3 Unit 1) go in a **new sibling directory**,
  `tests/fixtures/l5_phase4/`, same `label.json`/`RATIONALE.md`/`repo/` shape as
  their L5 counterparts.
- **Why a new directory, not the same one — this is load-bearing, not
  stylistic.** `scripts/measure_l5.py` (`agent_docs/L5_PROTOCOL.md`, frozen since
  Phase 1) walks `tests/fixtures/l5/` directly (`FIXTURES_ROOT`,
  `measure_l5.py:29`) and computes its own G2 pool dynamically — by filtering
  `allowed_verdicts == ["not_reachable"]` across whatever is in that directory
  (`measure_l5.py:148`), not against a hardcoded fixture-ID list. If the 8 new
  fixtures landed in `tests/fixtures/l5/` itself, `make measure-l5` would
  silently start computing G2 over a different pool size the next time anyone
  runs it — reopening L5_PROTOCOL.md's own frozen gate ("no threshold may
  change after seeing a result... a threshold change is a separate commit with
  written justification") without that separate, justified commit ever having
  been written. Putting the new fixtures in a sibling directory means
  `scripts/measure_l5.py` and `agent_docs/L5_PROTOCOL.md` are mechanically
  unaffected by Phase 4 — exactly as they should be, since growing the *eval
  harness's* corpus is this phase's job, not reopening L5's.
- Which harness CI actually runs: **the existing `run_eval_suite()` in
  `src/reachability/agent/eval_harness.py`**, unforked, changed only to discover
  fixtures from the union of `tests/fixtures/l5/` (22, reused as-is) and
  `tests/fixtures/l5_phase4/` (8, new) instead of a single root. `scripts/measure_l5.py`
  is not touched by this phase at all.

---

## 1. Fixture corpus layout after this phase

```
tests/fixtures/l5/            <- 22 fixtures, UNCHANGED (Phase 1/L5's own corpus)
tests/fixtures/l5_phase4/      <- 8 NEW fixtures (21-28), this phase's own corpus
```

`src/reachability/agent/eval_harness.py`'s `EVAL_FIXTURES_ROOT: Path` becomes
`EVAL_FIXTURES_ROOTS: list[Path]` (both directories above); `discover_eval_fixtures()`
iterates both roots and returns one combined, sorted list of fixture directories.
`measure_eval_fixture()`, `evaluate_eval_gates()`'s shape, and `run_eval_suite()`'s
overall structure are otherwise unchanged — this is a discovery-root change, not a
harness rewrite.

---

## 2. Build order

Four units. Order matters and deliberately does **not** match the task prompt's own
enumeration order (corpus, CI, gate semantics, docs) — CI must not be wired to gate
on a threshold that hasn't been finalized yet for the grown corpus, so gate-semantics
lands before CI wiring:

1. **Unit 1 — Corpus growth + harness discovery** (8 new fixtures, discovery-root change)
2. **Unit 2 — Accuracy gate semantics** (new thresholds for the 30-fixture corpus, new frozen protocol doc)
3. **Unit 3 — CI wiring** (new required `eval` job, now gating the correct, final corpus+thresholds)
4. **Unit 4 — Docs reconciliation** (CLAUDE.md, DECISIONS.md, README.md brought into agreement with what's actually on `main`)

Do not start Unit 3 before Unit 2's gate passes — a CI job wired against Unit 1's
interim state (30 fixtures, still-old 4-of-5 G2 threshold) would either gate on a
stale number or require a second CI-wiring commit. Each unit's own docs-sync is
committed in the same commit as that unit's code (per CLAUDE.md's global rule and
this phase's own non-negotiable) — Unit 4 is the final cross-file consistency pass,
not the only place docs get touched.

---

## 3. Units

### Unit 1 — Corpus growth: 8 new adversarial fixtures (22 → 30)

New directory `tests/fixtures/l5_phase4/`, same `label.json`/`RATIONALE.md`/`repo/`
shape as `tests/fixtures/l5/`. Each fixture probes a gap or trade-off DECISIONS.md
§4/§5 already documents as accepted-but-unfixed — not a repeat of an 01-20 shape.
Per fixture: scenario, target module/symbol shape, expected/allowed verdicts (marked
where they must be **empirically confirmed by running the fixture through the real
engine before `label.json` is finalized** — this project's own precedent,
DECISIONS.md §4's fixture 14, shows a hand-predicted mechanism can be wrong; do not
guess the label into place), and which gap it probes.

1. **`21_attribute_chain_segment_escape`** — probes the code-review-documented gap
   (DECISIONS.md §4, D3 section, "Status note (code review...)"): whether an
   intermediate attribute-chain segment of an *unrelated* call (`toolbox.probe.execute()`,
   where `probe` is an unrelated attribute, not the target function) still leaks into
   `collect_load_referenced_names`'s escape set after the §5.3 narrowing fix. Target:
   `pkg/target.py::probe()` — never called, never imported, no collision anywhere
   except the unrelated attribute name. `target_module=pkg.target`,
   `target_symbol=probe`. **Verdict must be empirically confirmed**: if the
   narrowing already excludes attribute-chain receiver segments, expect
   `not_reachable` (meaning CLAUDE.md's "not yet fixed" framing for this exact AST
   shape is stale and must be corrected in Unit 4, not silently left as-is); if the
   gap is still live, expect `unknown`. `decidable: false` either way; `forbidden`
   always includes `reachable`. If observed `not_reachable`, this fixture also
   becomes a 7th candidate for the G2 pool (see Unit 2) — do not silently drop that
   possibility.
2. **`22_opaque_dispatch_collateral_unknown`** — probes D1's stated repo-wide
   breadth (DECISIONS.md §4, D1 trade-off paragraph) on a symbol *completely
   unrelated* to the opaque call. Repo contains a `getattr(sys.modules[__name__],
   name)()`-style nameless dispatch, reachable from one entrypoint, plus a wholly
   separate, never-called, never-referenced `pkg/orphan.py::orphan()` with no name
   collision with anything in the dispatch subsystem. `target_module=pkg.orphan`,
   `target_symbol=orphan`. Expected (per D1's documented mechanism):
   `unknown`, not `not_reachable` — allowed=`["unknown"]`, forbidden=`["not_reachable",
   "reachable"]`, `decidable: false`.
3. **`23_cross_module_name_collision_escape`** — probes D3's documented
   cross-module trade-off ("a common name referenced anywhere outside a call
   position anywhere in the repo will suppress `not_reachable` for that name
   repo-wide") and CLAUDE.md's "regardless of `target_module`" framing, directly.
   Two modules each define `run()`: `pkg/mod_a.py::run` (dead — target) and
   `pkg/mod_b.py::run` (different function, passed by reference to a framework
   registration call elsewhere, reachable). `target_module=pkg.mod_a`,
   `target_symbol=run`. Expected: `unknown` (Step 5's name-only match fires for
   `mod_a.run` even though the *reference* is to `mod_b.run`) — allowed=`["unknown"]`,
   forbidden=`["not_reachable", "reachable"]`, `decidable: false`.
4. **`24_named_callback_no_collision_negative_control`** — a negative control:
   confirms the escape mechanism is name-scoped, not repo-scoped, and does not
   over-fire absent a name collision. `pkg/orphan2.py::orphan_target()` — dead,
   globally unique name. Elsewhere, an unrelated, differently-named function
   (`other_handler`) is passed by reference to a framework registration call,
   reachable. `target_module=pkg.orphan2`, `target_symbol=orphan_target`. Expected:
   `not_reachable` — allowed=`["not_reachable"]`, forbidden=`["unknown", "reachable"]`,
   `decidable: true`. This is the only one of the 8 that joins the G2 pool (see
   Unit 2).
5. **`25_opaque_dispatch_does_not_override_confident_reachable`** — regression-safety
   check: confirms D1's repo-wide opaque-call check never fires once Step 1
   (confident BFS) has already matched. `pkg/target2.py::vulnerable()` is called
   directly from the entrypoint (high-confidence edge); an unrelated
   getattr-dispatch subsystem, also reachable, exists elsewhere in the same repo.
   `target_module=pkg.target2`, `target_symbol=vulnerable`. Expected: `reachable`
   — allowed=`["reachable"]`, forbidden=`["not_reachable", "unknown"]`,
   `decidable: true`.
6. **`26_name_escape_does_not_override_confident_reachable`** — same regression-safety
   check for D3's Step 5: `pkg/target3.py::handler()` is called directly from the
   entrypoint (high-confidence edge) *and* separately passed by reference to a
   framework registration call elsewhere (so its name is also in the escape set).
   `target_module=pkg.target3`, `target_symbol=handler`. Expected: `reachable` (Step
   1 short-circuits before Step 5 ever runs) — allowed=`["reachable"]`,
   forbidden=`["not_reachable", "unknown"]`, `decidable: true`.
7. **`27_tuple_literal_container_reference`** — probes the §5.3 narrowing's
   explicitly-covered "direct element of a list/tuple/set/dict literal" case with
   an AST shape none of 01-20/14b/16b exercise: a **tuple** literal
   (`AVAILABLE = (vulnerable,)`), not a dict. `pkg/handlers3.py::vulnerable()` — never
   called. `target_module=pkg.handlers3`, `target_symbol=vulnerable`. Expected:
   `unknown` (value-bound at an `Assign`, per §5.3) — allowed=`["unknown",
   "reachable"]` (matching the existing 14/17-style labeling convention for this
   class), forbidden=`["not_reachable"]`, `decidable: false`.
8. **`28_augassign_container_reference`** — probes the `AugAssign` case §5.3's
   narrowing explicitly names as covered (`HANDLERS += [vulnerable]`), also
   untested by any existing fixture. `pkg/handlers4.py::vulnerable()` — never
   called. `target_module=pkg.handlers4`, `target_symbol=vulnerable`. Expected:
   `unknown` — allowed=`["unknown", "reachable"]`, forbidden=`["not_reachable"]`,
   `decidable: false`.

**Severity check (non-negotiable, per task):** none of the 8 is labeled or reviewed
as a coverage-count exercise. Every one is checked against this project's severity
ordering — a confident-but-wrong verdict is worse than `unknown` — before its
`allowed_verdicts`/`forbidden_verdicts` are finalized: fixtures 1-3 and 7-8 all
forbid `not_reachable` (the worst-class error) even where they cannot be fully
decided; fixtures 5-6 pin that the accepted trade-offs never *suppress* a genuinely
confident `reachable` verdict either.

**Code changes bundled with this unit:**
- `src/reachability/agent/eval_harness.py` — `EVAL_FIXTURES_ROOT: Path` →
  `EVAL_FIXTURES_ROOTS: list[Path] = [REPO_ROOT / "tests/fixtures/l5", REPO_ROOT /
  "tests/fixtures/l5_phase4"]`; `discover_eval_fixtures()` takes `roots:
  list[Path]` and returns the sorted union; `run_eval_suite()`'s call site updated
  accordingly. No other function in this file changes in this unit.
- `tests/test_eval_harness.py` — `test_run_eval_suite_shape_and_gates`'s
  `assert len(report.fixtures) == 22` → `== 30`.

**Docs-sync, same commit:** `README.md`'s U6 eval-harness section and `CLAUDE.md`'s
U6 paragraph both get their fixture-count mentions ("22") corrected to describe the
30-fixture, two-directory layout (full cross-file reconciliation completes in Unit 4;
this is the fixture-count fact specifically, landing where the fact changes).

**Gate:** all 8 new fixtures exist with the shape above; `run_eval_suite()` discovers
exactly 30 fixture rows; every new fixture's `pass` field matches its own
`allowed_verdicts` (i.e., the harness doesn't crash on any of them and each behaves
as empirically confirmed, not merely as predicted); bare `pytest` collects and
passes with zero collection errors.

### Unit 2 — Accuracy gate semantics for the 30-fixture corpus

Carries forward U6's existing hard/soft split (G1/G2/G3/G5/G6 hard, G4 reported-only)
unchanged in *kind* — only the numbers that must change because the corpus grew are
touched, per the task's own instruction not to invent a new policy.

- **G1-eval, G5-eval, G6-eval:** rule unchanged, now measured corpus-wide over 30
  fixtures instead of 22. No threshold to update — these were never fixture-count-
  dependent.
- **G2-eval:** pool grows from 5 (`09,10,11,12,20`) to 6
  (`09,10,11,12,20,24_named_callback_no_collision_negative_control`), assuming
  fixture 21 is *not* also `not_reachable` (see Unit 1's empirical-confirmation
  note — if it is, the pool is 7 and the floor below must be recomputed before this
  unit's gate is considered closed, not silently left at 6). Floor updated from
  "4 of 5" (80%) to **"5 of 6"** (~83%, preserving the same one-unit-of-slack,
  anti-degenerate intent as the original). `evaluate_eval_gates()`'s hardcoded
  `>= 4` / `"threshold": 4` becomes a named module-level constant,
  `G2_MIN_CORRECT = 5`, referenced from the gate check (matching the existing
  convention of `EVAL_BUDGET`/`WALL_CLOCK_BUDGET_SECONDS` as named constants, not
  another bare literal).
- **G3-eval:** unchanged — no new fixture targets `reachable_only_from_tests`;
  pool stays `07, 08`, floor stays 2/2.
- **G4-eval (reported-only):** decidable-fixture pool grows from 13 (`01-12,20`) to
  16 (`01-12,20,24,25,26`) — the three new `decidable: true` fixtures. Target
  stays 0; still never auto-fails.

**New frozen doc:** `agent_docs/PHASE4_EVAL_PROTOCOL.md`, pre-registering the above
numbers before Unit 3 wires CI against them, mirroring `U6_EVAL_PROTOCOL.md`'s own
"no threshold may change after first real run" structure. `agent_docs/
U6_EVAL_PROTOCOL.md` itself is **not edited in place** — it gains one appended
status-note paragraph (not a threshold change) stating it now describes the
historical 22-fixture run only, and that `PHASE4_EVAL_PROTOCOL.md` is what
`run_eval_suite()` is gated against going forward. This mirrors DECISIONS.md's own
repeated convention (§1, §7, §8) of marking a superseded decision with an appended
note rather than rewriting history.

**Code changes:** `src/reachability/agent/eval_harness.py`'s `evaluate_eval_gates()`
— only the `G2` block's hardcoded `4` becomes `G2_MIN_CORRECT`. `G1`/`G3`/`G4`/`G5`/
`G6` blocks are unchanged code (their thresholds were never fixture-count literals
to begin with — they recompute their own pools from `results`, which naturally
widens to 30 once Unit 1 lands).

**Docs-sync, same commit:** `tests/test_eval_harness.py` gains explicit regression-pin
assertions on the new pool sizes (`gates["G2"]["total"] == 6`, `gates["G2"]["threshold"]
== 5`, `len(gates["G4"]["fixtures"]) <= 16` is implicit via the `decidable` filter,
but assert the underlying decidable-pool size directly, e.g. via a helper count) —
not just `overall_pass is True`, per this project's own D2/D6 lesson (DECISIONS.md
§5.1): a verdict-only assertion can pass "by coincidence" through the wrong
mechanism, so pin the mechanism (pool size / threshold value), not just the outcome.

**Gate:** `run_eval_suite()` against the full 30-fixture corpus reports
`gates["G2"]["total"] == 6` (or the corrected number per fixture 21's empirical
result), `gates["G2"]["threshold"] == 5`, `gates["G4"]["fixtures"]` drawn from a
16-fixture decidable pool, and `overall_pass` reflects the updated hard-gate set.
`agent_docs/PHASE4_EVAL_PROTOCOL.md` exists and is internally consistent with what
the code actually computes (verified by running the harness, not by inspection
alone).

### Unit 3 — CI wiring: a new required `eval` job

New job in `.github/workflows/tests.yml`, alongside the existing `test` and
`concurrency` jobs (parallel, all required, no job depends on another):

```yaml
eval:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.13"
    - run: pip install -r requirements.txt
    - run: python scripts/run_eval_suite.py
```

**Why a new job, not a step inside `test`:** the eval harness never touches
`DATABASE_URL`/Postgres/`session.py` (`src/reachability/agent/eval_harness.py`'s
imports are index/triage-only) — bundling it into the `test` job would mean paying
for a `services: postgres:` container that `run_eval_suite()` never uses. A parallel
job with no Postgres service is strictly cheaper and keeps this check's pass/fail
independent of the DB-dependent suite's, mirroring the existing `test`/`concurrency`
split's own rationale (Phase 3, DECISIONS.md §8 item 5).

**Why this satisfies "collection error is a failure, no silent skip" (CLAUDE.md's
global rule) even though this isn't a `pytest` invocation:** `scripts/
run_eval_suite.py`'s `main()` (unchanged by this phase) returns `1` whenever
`report.gates["overall_pass"]` is `False` — and `overall_pass` includes `G5`, which
is `False` if *any* fixture's `measure_eval_fixture()` caught an exception
(`row["error"] is not None`). A fixture that crashes the harness therefore already
fails the CI job's exit code via G5 — it is measured and reported as a failure, not
silently absorbed as a data point. A crash *outside* any single fixture's own
try/except (e.g. `discover_eval_fixtures()`, `git_sha()`, or
`write_eval_results_json()` raising) propagates as an uncaught Python exception,
which also exits non-zero. No `continue-on-error: true`, no `|| true`, no soft/
informational-only job — this step blocks merge like `test`/`concurrency` do.

**Docs-sync, same commit:** `README.md`'s CI section (if one exists describing job
names) and `CLAUDE.md`'s "Lint/typecheck command" / CI-adjacent notes, if any name
specific job lists, are updated to include `eval` alongside `test`/`concurrency`.

**Gate:** the workflow YAML parses (validated locally via `yaml.safe_load` against
the file, since a live GitHub Actions run cannot be exercised from this
environment); a local dry run of the exact command
(`pip install -r requirements.txt && python scripts/run_eval_suite.py` from a clean
checkout) exits 0 against the current, passing 30-fixture corpus; deliberately
breaking one fixture's `label.json` (e.g. flipping `allowed_verdicts` to
`["reachable"]` for a known-`unknown` fixture) and re-running confirms a non-zero
exit, proving the job would actually fail CI, not just print a warning.

### Unit 4 — Docs reconciliation

Exact edits, file by file:

- **`CLAUDE.md`** — U6 paragraph (the block starting "Phase 2 U6
  (`src/reachability/agent/eval_harness.py`) is built..."): update "all 22
  fixtures in `tests/fixtures/l5/`" to describe the 30-fixture, two-directory
  corpus (22 in `tests/fixtures/l5/`, 8 new in `tests/fixtures/l5_phase4/`); remove
  or correct the "not wired into any CI pipeline" clause, replacing it with a
  description of the new required `eval` CI job; update the gate list description
  to name `agent_docs/PHASE4_EVAL_PROTOCOL.md`'s updated G2 floor (5 of 6) instead
  of repeating U6's "4 of 5" verbatim. Add a new status paragraph, in the same
  style as the existing Phase 2/Phase 3 paragraphs, summarizing Phase 4 as shipped.
- **`DECISIONS.md`** — two edits:
  1. Append (do not rewrite) a status note to the opening "Status note (U6,
     shipped, partial)" paragraph (currently lines 6-14) stating the "no CI wiring
     yet" clause is now stale as of Phase 4 (§9 below), while the rest of that
     paragraph (inert file-based prompts, deterministic stub, no live-LLM gating)
     remains accurate and unchanged.
  2. Add a new `## 9. Phase 4 — eval harness completion + CI gate` section
     (mirroring §7/§8's structure) recording: the 8 new fixtures and what each
     probes (cross-reference `agent_docs/PHASE4_EVAL_HARNESS.md` rather than
     duplicating the per-fixture detail); the new-sibling-directory decision and
     why (protecting L5_PROTOCOL.md's freeze, per §0 above); the updated G2/G4
     numbers; the new required `eval` CI job and why it needs no Postgres service;
     and the explicit statement that `U6_EVAL_PROTOCOL.md` stays frozen/historical
     while `PHASE4_EVAL_PROTOCOL.md` is now what CI actually gates against.
- **`README.md`** — the U6 eval-harness section: fixture count 22 → 30, mention of
  the new `tests/fixtures/l5_phase4/` directory, mention that `run_eval_suite()` is
  now a required CI check (not just a local/manual command), and (if `Makefile`
  gains a `run-eval-suite` target per the optional convenience step below) document
  it alongside the existing `make measure-l5` entry. The L5 measurement section
  itself (describing `scripts/measure_l5.py` / `L5_PROTOCOL.md`) is **not** edited
  — that corpus and its docs are untouched by this phase, and README should not
  imply otherwise.

**Gate:** grep-level cross-check that no file still claims 22 fixtures for the eval
harness, that no file still claims the eval harness is "not wired into any CI
pipeline," and that `CLAUDE.md`/`DECISIONS.md`/`README.md` name the same G2 floor
number (5 of 6, or the corrected number if fixture 21 changed the pool). Full bare
`pytest` run, zero collection errors, exact pass count recorded (see §4 below).

---

## 4. What is explicitly not in Phase 4

Live LLM API integration (the harness still runs `DeterministicPolicyStubLLMClient`,
unchanged) · DB-stored prompt versioning · any change to `scripts/measure_l5.py` or
`agent_docs/L5_PROTOCOL.md` (that corpus and its frozen gates are untouched, see §0) ·
any change to `agent_loop.py`, `stub_llm.py`, `sandbox.py`, or the L1-L4 index/query
layer's own resolution logic (Unit 1's fixtures probe existing, already-shipped
trade-offs; they do not motivate new `src/reachability/index/` capability in this
phase) · a new/forked eval harness implementation · CI-gating "every prompt change"
(DECISIONS.md's opening-paragraph vision) — prompts remain inert scaffolding, this
phase gates a stub loop's verdicts against fixture labels, nothing more · retries,
quotas, cost accounting, Docker, or any other item already on CLAUDE.md's NOT BUILT
list untouched by this phase's own scope.

If a `/loop` iteration proposes any of these, the critic rejects the plan.
