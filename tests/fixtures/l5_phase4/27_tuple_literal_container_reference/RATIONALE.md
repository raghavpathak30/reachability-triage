`pkg/handlers3.py::vulnerable` is never called anywhere in this repository. It
is, however, stored as the sole element of a tuple literal assigned at module
level: `AVAILABLE = (vulnerable,)`.

`_LoadOutsideCallCollector._record_value_bound` explicitly recurses into
`ast.Tuple`/`ast.List`/`ast.Set` elements when the container itself is the
value-bound expression of an `Assign` (DECISIONS.md §5.3's narrowing
explicitly names "a direct element of a list/tuple/set/dict literal" as still
covered, to keep "stored in a container" catching this shape). None of L5's
existing fixtures (14/14b use a dict, not a tuple) exercise a bare tuple
literal specifically -- this fixture closes that gap in AST-shape coverage.
Expect `unknown`: `vulnerable`'s name is in the escape set, so `not_reachable`
cannot be confidently returned, matching the labeling convention already used
for fixtures 14/17 (`allowed_verdicts: ["unknown", "reachable"]`).
