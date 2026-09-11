`pkg/lazy.py` has no attribute named `vulnerable` written anywhere in it — but it
defines a module-level `__getattr__`, a Python feature that intercepts any attribute
access on the module that isn't otherwise found, and for the specific name
`"vulnerable"` it fetches and returns the real `vulnerable` from `pkg.sink`. So
`lazy.vulnerable()` in `entry.py` really does call `pkg.sink.vulnerable` at run time,
even though `lazy.py`'s own source never defines anything literally named
`vulnerable`.
