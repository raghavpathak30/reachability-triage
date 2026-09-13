`pkg/entry.py` calls `target3.handler()` directly -- a plain, high-confidence
attribute call that `compute_reachability`'s Step 1 matches and returns
`REACHABLE` on immediately.

The entrypoint separately also passes `target3.handler` by reference to
`app.on_event("startup", target3.handler)`, so `handler` is genuinely present
in `_LoadOutsideCallCollector`'s escape set (the same D3 mechanism fixtures
17/19/23/24 exercise). This fixture confirms that having a symbol's name in
the escape set does not retroactively downgrade a verdict Step 1 already
returned confidently -- Step 5 (the name-escape check) only runs when Step 1-3
found no match at all, so it never even executes for this query.
