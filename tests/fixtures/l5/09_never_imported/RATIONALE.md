No file in this repository imports `pkg.sink` or names `vulnerable`. `pkg/other.py`
is the only file that runs anything when the repository is executed directly, and it
calls `do_stuff`, which has no relationship to `vulnerable` at all. A function that
is defined but never imported anywhere cannot be called by any code path in this
repository.
