`entry.py` imports the whole `sinkmod` module under the alias `sm` and, when run as a
script, calls `sm.vulnerable()`. Renaming the module on import does not change which
function is called — it is the same `vulnerable` defined in `sinkmod.py`.
