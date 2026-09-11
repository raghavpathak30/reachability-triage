`dispatch` looks up an attribute on the `sink` module by name at run time, via
`getattr`, using whatever string is passed in as `name`. The one call site we can see
in this repository passes the literal string `"vulnerable"`, so at run time this does
call `vulnerable` — but `dispatch` takes `name` as a parameter, so nothing in the
source guarantees it is never called with some other string too. Reading the source
alone cannot rule out that `vulnerable` is reached this way, nor can it prove with
certainty that it always is.
