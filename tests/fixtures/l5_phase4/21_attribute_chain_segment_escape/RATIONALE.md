`pkg/target.py::probe` is never called or imported anywhere in this repository.
The only other place the identifier `probe` appears is in `entry.py`:
`toolbox.probe.execute()` (an intermediate attribute-chain segment on the way to
calling `.execute()`, not `probe` itself) and `self.probe = Toolbox.Probe()` (an
assignment *target*, not a value-bound RHS).

This fixture exists to empirically re-probe a gap a code review once flagged
against an *earlier* version of `_LoadOutsideCallCollector`: that an
intermediate attribute-chain segment of an unrelated call could leak into the
escape set `compute_reachability`'s Step 5 checks, turning a genuinely dead
function into a false `unknown` merely because some unrelated object happens to
have an attribute with the same name. `DECISIONS.md` §5.3 already narrowed
the collector to only record a `Name`/`Attribute` when it is itself the
value-bound expression at an assignment or call-argument position -- an
attribute-chain *receiver* inside `call.func` (like `probe` in
`toolbox.probe.execute()`) is walked by plain `generic_visit`, with no
`visit_Attribute` override to record it. Tracing the collector's code confirms
this fixture should resolve `not_reachable`; running it through the real
engine (not just reading the code) is what actually finalizes this fixture's
label.
