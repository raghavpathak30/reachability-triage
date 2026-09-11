`pkg/__init__.py` re-exports `vulnerable` from `sinkmod`, so importing `vulnerable`
from the package `pkg` and importing it from `pkg.sinkmod` refer to the identical
function. `entry.py` imports it the package-level way and calls it unconditionally
at script run time.
