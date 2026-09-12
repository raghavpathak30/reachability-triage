<!-- Not read by any code path today; see `prompt_registry.py`'s docstring. -->

# Tool usage guidance (draft, v1)

## `search_symbol`

Call this tool with a `pattern` string to look up symbols in the
repository's symbol table by name or glob-style pattern. Use it first, to
confirm that a symbol matching your target actually exists in the index
before you rely on its node id for any other tool call. A no-match result
is not an error; it means the symbol was not found, and you should not
invent a node id in its place.

## `find_callers`

Call this tool with a `node_id` string (the fully-qualified id of a symbol,
as returned by `search_symbol` or a previous `find_callers` call) to list
every caller of that symbol currently known to the call graph. Use it
repeatedly, walking backward one hop at a time from your target's confirmed
node id toward its entrypoints, to build the evidence for whether the
target is reachable. Each new caller id you discover is itself a valid
`node_id` argument for a further `find_callers` call.

## `resolve_import`

Call this tool with a `module` string and a `name` string to resolve what a
given import name inside a given module actually refers to. Use it when a
caller's identity is ambiguous because of aliasing or re-export, and you
need to confirm which underlying module or symbol an imported name actually
points to before treating it as a caller in your evidence chain.

## Budget

You have a fixed, per-run integer budget on the total number of tool calls
you may dispatch across all three tools combined, checked before you are
asked for your next action. Once that budget is exhausted, you must submit
your best final answer with whatever evidence you have already gathered;
you cannot make an additional tool call to gather more.
