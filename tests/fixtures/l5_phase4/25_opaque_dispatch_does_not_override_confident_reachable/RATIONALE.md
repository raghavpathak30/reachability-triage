`pkg/entry.py` calls `target2.vulnerable()` directly -- a plain, high-confidence
attribute call that `compute_reachability`'s Step 1 (confident BFS from
non-test entrypoints) matches and returns `REACHABLE` on immediately.

The entrypoint also calls `dispatcher.dispatch("something")`, which internally
does `getattr(sys.modules[__name__], name)()` -- the same nameless, opaque
dispatch shape as fixture 22 and L5's fixture 13, also reachable from the same
entrypoint. This subsystem has nothing to do with `vulnerable`.

This fixture exists to confirm a specific ordering property of
`compute_reachability`, not just document it: D1's Step 4 opaque-call check
only runs if Step 1's confident BFS found no match. Since `vulnerable` is
matched at Step 1, Step 4 never executes for this query, and the unrelated
opaque dispatch subsystem's mere presence in the same repo must not degrade
this verdict from `reachable` to `unknown`.
