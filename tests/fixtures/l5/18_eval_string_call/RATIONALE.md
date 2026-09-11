`run` calls `eval` on the literal string `"vulnerable()"`. `eval` executes that
string as Python code, which calls `vulnerable`. The call target here exists only
inside a string, not as ordinary Python call syntax — reading the source we can see
the string literally says `vulnerable()`, but we cannot mechanically verify that this
string can never be constructed or altered differently without actually running it.
