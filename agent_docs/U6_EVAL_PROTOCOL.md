# U6 Eval Protocol — full agent loop evaluation harness

Pre-registered before `src/reachability/agent/eval_harness.py` exists. No threshold
in this document may change after `run_eval_suite()` is first run against real
results; a threshold change is a separate, later commit with written justification.

## GATES

```
Eval corpus: all fixtures under tests/fixtures/l5/ (currently 22: 01-20 plus the
  two no-bare-name variants 14b_registry_dict_no_bare_name and
  16b_pep562_no_bare_name), reused as-is —
  no new fixture files are written for U6. EVAL_BUDGET = 50 tool calls per
  fixture (same value already exercised by test_triage_agent_loop_l5.py's gate
  (ii) check). WALL_CLOCK_BUDGET_SECONDS = 10 per fixture (a fresh number for
  this harness, not a change to L5_PROTOCOL.md's own frozen 5s — loop dispatch
  overhead justifies a wider margin).

G1-eval (hard, no waiver): false not_reachable count == 0, corpus-wide, measured
  from the full agent loop's TriageFinding.result.verdict (never a direct
  compute_reachability call). Operationalized: a fixture where verdict ==
  "not_reachable" AND "not_reachable" not in label.allowed_verdicts.

G2-eval (hard, anti-degenerate): of the 5 fixtures whose allowed_verdicts ==
  ["not_reachable"] exactly (09, 10, 11, 12, 20), at least 4 of 5 must return
  not_reachable through the full agent loop.

G3-eval (hard): fixtures 07 and 08 (allowed_verdicts == ["reachable_only_from_tests"])
  must both return exactly that verdict through the full agent loop. 0 tolerance.

G4-eval (REPORTED, NOT AUTO-FAIL): count of "unknown" verdicts among the 13
  decidable:true fixtures (01-12, 20). Target 0. A nonzero count does not change
  run_eval_suite()'s exit code; it would require the same written DECISIONS.md
  sign-off L5_PROTOCOL.md's G4 requires, which is not pre-approved by this plan.

G5-eval (hard): no fixture crashes, raises, or exceeds the 10-second-per-fixture
  wall-clock budget.

G6-eval (hard, loop-integrity — no L5 analogue): zero occurrences, across the
  full eval corpus, of any loop-level degradation reason
  (TriageFinding.result.reason starting with "budget_exceeded",
  "malformed_tool_call_argument", or exactly equal to
  "target_symbol_not_found_in_index"). Any occurrence is an unconditional hard
  fail regardless of whether the resulting verdict happens to still be an
  allowed one — it signals the loop's own plumbing broke down (wrong budget, a
  stub confirmation failure), not a genuine index-level unknown. This is the
  gate specifically exercising "the full agent loop, not just the index"
  (PHASE2_TRIAGE_AGENT.md line 209-210).

Output: counts + a per-fixture table only. No percentages/recall/precision
  (n too small, same rationale as L5_PROTOCOL.md line 38).

Known risk (documented, not a reason to pre-tune EVAL_BUDGET): the 50-call
  budget is carried over from test_triage_agent_loop_l5.py's precedent, which
  only exercises 4 of the 22 fixtures (02, 07, 09, 17) at that budget. If some
  other fixture's backward-BFS policy needs more than 50 calls on its first
  real run under the full loop, G6-eval could fire on a budget artifact rather
  than a genuine loop-plumbing bug. Per this same protocol's "no threshold
  changes after first real run" rule, EVAL_BUDGET may not be silently raised
  post-hoc to paper over that outcome — a budget-exhaustion-caused G6-eval
  failure must be diagnosed and, if it is in fact a budget-sizing issue rather
  than a real bug, raised only via an explicit, justified follow-up commit
  that amends this protocol document, not a quiet code change.

overall_pass = G1-eval AND G2-eval AND G3-eval AND G5-eval AND G6-eval.
  (G4-eval is reported only and never enters overall_pass, matching
  L5_PROTOCOL.md's explicit anti-guessing rationale for its own G4.)
```

**Status note (Phase 4):** this document describes the historical 22-fixture run
only and is now frozen/superseded for CI-gating purposes. As of Phase 4, the eval
corpus grew to 30 fixtures (22 here plus 8 new in `tests/fixtures/l5_phase4/`) and
`run_eval_suite()` is gated against `agent_docs/PHASE4_EVAL_PROTOCOL.md`'s updated
G2/G4 numbers, not this document's. This document's own thresholds are not edited
in place, per its own "no threshold may change" rule above.
