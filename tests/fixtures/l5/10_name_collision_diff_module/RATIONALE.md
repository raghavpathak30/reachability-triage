There are two different functions, both named `vulnerable`, defined in two different
files: `real.py` and `decoy.py`. `entry.py` imports and calls only the one from
`decoy.py` (`from pkg.decoy import vulnerable`) — it never imports anything from
`real.py`. A function sharing a name with a called function is not the same function;
`real.py`'s `vulnerable` is never referenced by any import or call anywhere in this
repository, so it cannot be reached, no matter how similar its name and body look to
the one that actually runs.
