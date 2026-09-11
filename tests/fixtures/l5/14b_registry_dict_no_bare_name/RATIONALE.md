Same underlying bug as fixture 14: `vulnerable` is reachable only through a registry
dict lookup, `HANDLERS[key]()`, which L3 extracts as a nameless `ast.Subscript` call
-- the same "?:<dynamic>" marker a `getattr(obj, name)()` call produces, since
`edges.py` treats `isinstance(func, (ast.Call, ast.Subscript))` identically. A
`NOT_REACHABLE` verdict here cannot be trusted: the opaque dispatch could resolve to
the queried symbol at runtime.

This variant exists because fixture 14 turned out to be an imperfect adversarial
test for that exact bug: `registry.py` populates `HANDLERS` via
`from pkg.sink import vulnerable; HANDLERS = {"go": vulnerable}`, which binds
`vulnerable` as a bare `Name` Load inside the dict literal -- a load outside any call
position, which `_LoadOutsideCallCollector` (the D3 fix) independently catches.
Reverting D1 (the nameless-dynamic-dispatch check) does not change fixture 14's
measured verdict, because D3 already pins it to `unknown` for a different reason.
Confirmed directly via the revert-attribution check: with D1 reverted and D3 intact,
fixture 14's regression test still passed. See DECISIONS.md for this finding.

`14b` removes the coincidence: `registry.py` builds `HANDLERS` via
`getattr(importlib.import_module("pkg.sink"), "vulnerable")`. The string
`"vulnerable"` is a literal, never a `Name` or `Attribute` AST node, so
`_LoadOutsideCallCollector` has nothing to catch here. The only way this fixture can
avoid a false `not_reachable` is via the nameless-dynamic-dispatch check itself.
