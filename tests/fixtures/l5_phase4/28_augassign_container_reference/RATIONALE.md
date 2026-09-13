`pkg/handlers4.py::vulnerable` is never called anywhere in this repository. It
is, however, appended to a list via an augmented assignment:
`HANDLERS += [vulnerable]` (after `HANDLERS = []`).

`_LoadOutsideCallCollector.visit_AugAssign` calls `_record_value_bound` on the
RHS of the `AugAssign` node, which recurses into the list literal's elements
just as it would for a plain `Assign` -- DECISIONS.md §5.3's narrowing
explicitly names `AugAssign` as one of the value-bound positions the collector
still covers. No existing L5 fixture exercises an `AugAssign` specifically;
this fixture closes that AST-shape gap. Expect `unknown`: `vulnerable`'s name
is in the escape set, so `not_reachable` cannot be confidently returned.
