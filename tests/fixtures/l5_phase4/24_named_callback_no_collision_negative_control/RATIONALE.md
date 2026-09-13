`pkg/orphan2.py::orphan_target` is dead: nothing calls or imports it, and its
name is globally unique in this repository -- no other symbol, string, or
identifier anywhere is named `orphan_target`.

`other_handler`, a completely different, differently-named function, is called
directly from the entrypoint and also passed by reference to
`app.on_event("startup", other_handler)` -- the same "framework callback
reference" shape as fixture 17 in the L5 corpus and fixtures 23/26 here. This
adds only the name `other_handler` to `_LoadOutsideCallCollector`'s escape set,
never `orphan_target`.

This is the negative control for fixtures 23/26: it confirms D3's name-based
escape mechanism is scoped to names that are actually referenced, not a
blanket "some callback exists somewhere in this repo, so give up on
`not_reachable` for everything" degradation. `orphan_target` should resolve
`not_reachable` here exactly as confidently as any of the original L5 corpus's
dead-function fixtures (e.g. `09_never_imported`).
