# Phase 4 Eval Protocol — gate semantics for the 30-fixture corpus

Supersedes `agent_docs/U6_EVAL_PROTOCOL.md` for CI-gating purposes as of Phase 4.
`U6_EVAL_PROTOCOL.md` itself stays frozen/historical, describing only the original
22-fixture run — see its appended status note. This document is what
`run_eval_suite()` is gated against going forward. No threshold in this document may
change after being pre-registered here; a threshold change is a separate, later
commit with written justification (same rule `U6_EVAL_PROTOCOL.md` and
`L5_PROTOCOL.md` both state).

**Fixture 21 reconciliation (resolved, not left as an "or"):** `agent_docs/
PHASE4_EVAL_HARNESS.md`'s fixture 21
(`21_attribute_chain_segment_escape`) was empirically run through
`measure_eval_fixture()` before any number below was finalized. It resolved
`not_reachable` — confirming the `DECISIONS.md` §5.3 narrowing already closed the
code-review-found gap it probes (an intermediate attribute-chain segment of an
unrelated call no longer leaks into `_LoadOutsideCallCollector`'s escape set).
Per `agent_docs/PHASE4_EVAL_HARNESS.md`'s own instruction, this means fixture 21 is
corrected to `decidable: true` and folds into both the G2 pool and the G4 decidable
pool below. The numbers in this document are the resulting, single, final numbers —
not a range or an "or."

## GATES

```
Eval corpus: 30 fixtures across two directories — the original 22 under
  tests/fixtures/l5/ (reused as-is, untouched by this phase) plus 8 new fixtures
  (21-28) under tests/fixtures/l5_phase4/ (agent_docs/PHASE4_EVAL_HARNESS.md).
  EVAL_BUDGET = 50 tool calls per fixture (unchanged from U6). WALL_CLOCK_BUDGET_SECONDS
  = 10 per fixture (unchanged from U6).

G1-eval (hard, no waiver): false not_reachable count == 0, corpus-wide over all 30
  fixtures. Operationalized: a fixture where verdict == "not_reachable" AND
  "not_reachable" not in label.allowed_verdicts. Unchanged in kind from U6; now
  measured over 30 fixtures instead of 22.

G2-eval (hard, anti-degenerate): of the 7 fixtures whose allowed_verdicts ==
  ["not_reachable"] exactly (09, 10, 11, 12, 20, 21, 24), at least 6 of 7 must
  return not_reachable through the full agent loop. Pool grew from 5 (U6) to 7:
  fixture 21 (empirically not_reachable, decidable: true per the reconciliation
  above) and fixture 24 (negative control, decidable: true) both join. Floor
  updated from "4 of 5" (U6) to "6 of 7" — preserving the same one-unit-of-slack,
  anti-degenerate intent as the original (exactly one wrong answer tolerated, no
  more). `evaluate_eval_gates()`'s G2 threshold is a named module-level constant,
  `G2_MIN_CORRECT = 6`.

G3-eval (hard): fixtures 07 and 08 (allowed_verdicts == ["reachable_only_from_tests"])
  must both return exactly that verdict through the full agent loop. 0 tolerance.
  Unchanged — no new fixture targets this verdict.

G4-eval (REPORTED, NOT AUTO-FAIL): count of "unknown" verdicts among the 17
  decidable:true fixtures (01-12, 20, 21, 24, 25, 26). Target 0. Pool grew from 13
  (U6) to 17: fixture 21 (empirically not_reachable, decidable: true), fixture 24
  (not_reachable negative control, decidable: true), fixture 25 (reachable
  regression-safety check, decidable: true), and fixture 26 (reachable
  regression-safety check, decidable: true) all join. A nonzero count does not
  change run_eval_suite()'s exit code; it would require the same written
  DECISIONS.md sign-off L5_PROTOCOL.md's/U6's G4 requires, not pre-approved here.

G5-eval (hard): no fixture crashes, raises, or exceeds the 10-second-per-fixture
  wall-clock budget. Unchanged in kind; now measured over 30 fixtures.

G6-eval (hard, loop-integrity — no L5 analogue): zero occurrences, across the full
  30-fixture eval corpus, of any loop-level degradation reason
  (TriageFinding.result.reason starting with "budget_exceeded",
  "malformed_tool_call_argument", or exactly equal to
  "target_symbol_not_found_in_index"). Unchanged in kind from U6; now measured over
  30 fixtures.

Output: counts + a per-fixture table only. No percentages/recall/precision (n too
  small, same rationale as L5_PROTOCOL.md/U6_EVAL_PROTOCOL.md).

overall_pass = G1-eval AND G2-eval AND G3-eval AND G5-eval AND G6-eval.
  (G4-eval is reported only and never enters overall_pass, matching
  U6_EVAL_PROTOCOL.md's own anti-guessing rationale for its G4.)
```
