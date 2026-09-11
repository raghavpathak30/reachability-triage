`vulnerable` is decorated with `@audit`, whose body is `return func` — it hands the
original function back unchanged. `entry.py` imports and calls `vulnerable` directly;
the decorator does not intercept, wrap, or block the call, only relabels the object
without behavior change.
