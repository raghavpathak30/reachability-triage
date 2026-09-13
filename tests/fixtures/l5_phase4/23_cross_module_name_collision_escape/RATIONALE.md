`pkg/mod_a.py` and `pkg/mod_b.py` each define a function named `run`. Nothing
calls or imports `pkg.mod_a.run`. `pkg.mod_b.run` is called directly from the
entrypoint (`mod_b.run()`) and additionally passed by reference to
`app.on_event("startup", mod_b.run)`.

CLAUDE.md documents this exact trade-off: "A *named* unresolved call... bridges
to `unknown` for any query whose `target_symbol` matches that name, regardless
of `target_module`... an unresolved callee carries no module information to
check." `_LoadOutsideCallCollector` records only the bare name `run` (via
`self.names.add(expr.attr)` on the `mod_b.run` argument), with no module
qualifier at all -- so querying `pkg.mod_a::run` (the genuinely dead one) still
degrades to `unknown`, because from the escape set's point of view there is no
way to distinguish "a `run` was referenced" from "*this* `run` was referenced."
This is the accepted D3 trade-off working exactly as documented, probed with a
real cross-module collision rather than just asserted from the code.
