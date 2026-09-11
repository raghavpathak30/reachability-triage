`vulnerable` is imported inside a `try/except ImportError` block, so whether it ends
up being a real function or `None` depends on whether `pkg.sink` is actually
importable in whatever environment runs this code. `run` only calls `vulnerable` if
it is truthy. Reading the source, we cannot be certain the import always succeeds in
every environment this package might run in — but we also cannot rule it out, since
the import commonly does succeed and both files live in the same package.
