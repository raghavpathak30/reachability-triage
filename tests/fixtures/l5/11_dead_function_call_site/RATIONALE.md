`wrapper`, defined in `dead.py`, calls `vulnerable` — but nothing in this repository
ever calls `wrapper` itself. The only code that actually runs when this package is
executed directly is `do_other` in `other.py`, which has no relationship to `wrapper`
or `vulnerable` at all. A call site that is never reached because its containing
function is never called cannot make the function it calls reachable either.
