# reachability-triage — Session Handoff, 2026-09-11
# Phase 1 complete. L5 shipped. Three open items before Phase 2 proceeds.

## What shipped today
L5 (fixture corpus + measurement + gates) — the last unit of Phase 1 — went from spec
to a green, committed run:
  - agent_docs/L5_PROTOCOL.md: 5 gates (G1 false-not_reachable hard-zero, G2 anti-
    degenerate floor, G3 test-path discrimination, G4 unknown-ceiling REPORTED not
    auto-fail, G5 no crashes) plus pre-registered failure predictions, written before
    any fixture existed.
  - tests/fixtures/l5/: 20 frozen, hand-labeled fixtures (label.json + RATIONALE.md
    per fixture), covering reachable / test-only / not-reachable / must-degrade
    scenarios. Labels derived from source only, not from running the engine.
  - scripts/measure_l5.py + Makefile target measure-l5.
  - Four Stage-D fixes, each its own commit with regression tests:
      D1: nameless dynamic dispatch (getattr/eval with computed names) -> unknown
      D3: _LoadOutsideCallCollector — named symbols referenced outside a call position
          (registry dicts, callbacks, monkeypatch targets) -> unknown
      D5: MODULE_ATTRIBUTE resolution now verifies the attribute exists before
          resolving confidently (was a confidently-wrong-resolution bug, most severe
          class); reopened the L3 freeze to do this, logged in DECISIONS.md
      D7: DECISIONS.md addendum
  - Final run: results/l5_d3a6ba968a23d0dd31249a0c141987a8f99690ba.json.
    G1=0, G2=5/5, G3=2/2, G4=0, G5=0. Bare pytest: 110/110 (baseline was 104).
  - Commit order verified strictly A -> B -> C -> D1 -> D3 -> D5(alone) -> D7.

## Plan corrections made mid-loop (kept on record, not smoothed over)
  - D1/D3 were planned as fixing disjoint fixture sets (13/18 vs 14/17/19). Wrong:
    edges.py collapses ast.Call and ast.Subscript callees to the same "?:<dynamic>"
    marker, so D1 alone fixed 14 as a side effect.
  - Fixture 16 (PEP 562 __getattr__) went green from D3's side effect before D5 was
    written — pkg/lazy.py has a bare `return target_func` that D3's collector catches.
    Verified via resolution_rule (module_attribute -> unresolved_attribute only after
    D5), not via the verdict alone. D5 was implemented anyway as the real root-cause
    fix, since a variant without that bare-name binding would trigger the original bug
    undetected.
  - Two real bugs found while building fixtures, fixed pre-commit: fixture 06 had an
    undefined name (NameError on import); fixture 19's original monkeypatch design was
    a no-op due to Python import-binding semantics, verified with pytest before/after.
  - pytest.ini norecursedirs added — the fixture corpus's own test_*.py/conftest.py
    files were being swept into the project's bare pytest collection.
  - CLAUDE.md's L4-gaps paragraph had gone stale (described the D1 gap as still
    current); corrected during review. README's "dynamic dispatch permanently out of
    scope" claim was corrected in Stage A, before any fixture existed.

## OPEN — must be resolved before Phase 2 proceeds
1. Revert-attribution check on D2/D4/D6 (regression tests): revert each src/ fix
   independently, confirm the matching test goes red, restore. Predicted outcome: D6's
   test still passes with D5 reverted (same bare-name-binding coincidence as fixture
   16). If so, D6 needs to be rewritten against a variant with no bare-name binding —
   the module-attribute path must be the only route to the sink. STATUS AS OF END OF
   SESSION: not confirmed as run. Treat as unresolved until explicitly reported.
2. Fixture 16 is now permanently masked by D3 — it will pass regardless of whether D5
   exists, so current coverage of the PEP-562-confidently-wrong scenario is gone.
   Preferred fix: add tests/fixtures/l5/16b_pep562_no_bare_name/ (same shape, no bare
   return of the target anywhere). This is also fixture #1's fix — do both together.
3. Code review found _LoadOutsideCallCollector leaks intermediate attribute-chain
   segments into the escape set, not just names passed by value. Safe direction (more
   unknown, never a false not_reachable) but broader than documented, and its severity
   scales with real-repo size in a way the 60-line fixtures can't reveal — an escape
   set that grows with chain segments risks degrading toward "unknown for everything,"
   which is the degenerate failure G2 exists to catch and the corpus is too small to
   catch it happening. BLOCKING for Phase 2, not a fast-follow: before Phase 2 indexes
   any real codebase, run the index build against one mid-sized third-party package and
   report len(escape_set) vs total symbol count. High ratio -> narrow the collector to
   value-bound names before trusting it outside the corpus.

## Phase 2 backlog (capability gaps, not bugs — do not fix inside Phase 1/2 gate work)
  - CALLBACK_REFERENCE edge type: functions passed by reference to a framework and
    invoked indirectly currently resolve to unknown; a named edge type could recover
    some of these as reachable.
  - Registry-dispatch (HANDLERS[key]()) discards the statically-present key name at the
    ast.Subscript collapse point (edges.py:346-349). Same shape as CALLBACK_REFERENCE —
    the name is in the source and currently thrown away.
