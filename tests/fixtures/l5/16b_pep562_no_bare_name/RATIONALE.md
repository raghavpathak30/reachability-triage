Same underlying bug as fixture 16: `pkg/lazy.py` has no attribute named `vulnerable`
written anywhere in its own source, only a module-level `__getattr__` (PEP 562) that
intercepts the access and hands back the real function from `pkg.sink`. A
`MODULE_ATTRIBUTE` resolver that doesn't verify the attribute actually exists in the
target module's own symbol table will confidently (and wrongly) resolve
`lazy.vulnerable` to a symbol that was never defined there.

This variant exists because fixture 16 turned out to be an imperfect adversarial
test for that exact bug: its `__getattr__` body contains `return target_func` (or,
here, the equivalent `return vulnerable`), a bare name binding that
`_LoadOutsideCallCollector` (the D3 fix) independently catches as "referenced
outside a call position" -- masking the MODULE_ATTRIBUTE bug behind an unrelated
mechanism. Reverting the MODULE_ATTRIBUTE existence check (D5) no longer changes
fixture 16's measured verdict, because D3 already pins it to `unknown` for a
different reason. See DECISIONS.md for the "revert-attribution" finding that
surfaced this.

`16b` removes the coincidence: `lazy.py` fetches the target via
`getattr(importlib.import_module("pkg.sink"), "vulnerable")`. The string
`"vulnerable"` is a literal, never a `Name` or `Attribute` AST node, so
`_LoadOutsideCallCollector` has nothing to catch here. The only way this fixture can
avoid a false `not_reachable` is via the MODULE_ATTRIBUTE existence check itself.
