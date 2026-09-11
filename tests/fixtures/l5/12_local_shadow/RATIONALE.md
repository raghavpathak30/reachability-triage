`run` defines its own local function, also named `vulnerable`, before calling
`vulnerable()`. Python resolves names in a function body against the nearest
enclosing scope first: inside `run`, the local `def vulnerable():` shadows the
module-level import, so `vulnerable()` on the last line of `run` always invokes the
local shadow, never the `vulnerable` imported from `pkg.sink`, regardless of what the
module-level import statement looks like.
