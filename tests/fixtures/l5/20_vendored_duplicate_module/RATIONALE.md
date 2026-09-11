There are two files, `pkg/sink.py` and `pkg/_vendor/sink.py`, each defining a
function named `vulnerable` with identical code. Only `pkg/sink.py`'s copy is ever
imported — `entry.py` writes `from pkg.sink import vulnerable`, never anything
mentioning `pkg._vendor`. No file in this repository imports from
`pkg/_vendor/sink.py` at all, so that copy's `vulnerable`, despite being
byte-for-byte the same code as the one that does run, is never called by anything.
Two functions that look identical are still two different functions if nothing
calls one of them.
