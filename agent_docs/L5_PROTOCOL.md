# L5 Protocol — fixture corpus + measurement harness

Pre-registered before any fixture, script, or measurement run exists. No threshold
in this document may change after seeing a result; a threshold change is a
separate, later commit with written justification.

## GATES

```
G1 (hard, no waiver): false not_reachable count == 0, corpus-wide.
  Operationalized: a fixture where actual verdict == not_reachable AND
  "not_reachable" not in label.allowed_verdicts.

G2 (hard, anti-degenerate): of the 5 fixtures whose allowed_verdicts == ["not_reachable"]
  exactly (09, 10, 11, 12, 20), at least 4 of 5 must actually return not_reachable.

G3 (hard): fixtures 07 and 08 (allowed_verdicts == ["reachable_only_from_tests"])
  must both return exactly that verdict. 0 tolerance.

G4 (REPORTED, NOT AUTO-FAIL): count of "unknown" verdicts among the 13 decidable:true
  fixtures (01-12, 20). Target 0. Reported as a count + named fixture list. A
  nonzero count does NOT change the script's exit code; it requires a written
  sign-off paragraph in DECISIONS.md naming each fixture and why unknown is being
  accepted. Rationale (record verbatim): every other gate rewards conservatism and a
  hard G4 would create pressure in Stage D to guess a resolution just to turn an
  honest unknown into a confident answer — the exact failure mode this phase exists
  to prevent. Capability pressure must not be purchasable with a confident-but-wrong
  resolution.

G5 (hard): no fixture crashes, raises, or exceeds a 5-second per-fixture
  wall-clock budget. (Per critique: the earlier draft named this check but never
  pre-registered a number; 5s is generous for hand-written <60-line fixtures —
  exists only to catch an infinite loop, not real compute — and is fixed here,
  in Stage A, before any fixture or the measurement script exists, so it cannot
  be tuned after seeing a slow run.)

Output: COUNTS and a per-fixture table ONLY. Percentages, recall, precision are
FORBIDDEN in L5 output (n=20 cannot support them).

No threshold may change after seeing a result. A threshold change is a separate
commit, made after the failing run is already committed, with written justification.
```

## PREDICTIONS

```
Predicted first-run G1 failures:
  13 getattr with computed name, 18 eval/exec  — nameless dynamic dispatch
  14 registry dict, 17 framework callback, 19 monkeypatch — NAMED symbol referenced
     outside a call position (distinct mechanism from 13/18; not merged)
  16 module-level __getattr__ (PEP 562) — expected CONFIDENTLY WRONG, not a dead
     end; more severe than 13/14/17/18/19.
Also predicted: 10 or 12 may return unknown rather than not_reachable, consuming the
single unit of G2 slack.
```
