`vulnerable` is stored, by name, as a value in the `HANDLERS` dict at import time
(`HANDLERS = {"go": vulnerable}`). `entry.py` looks a key up in that dict and calls
whatever function it finds there. The one call site we can see passes the key
`"go"`, which does map to `vulnerable` in `HANDLERS` — but the lookup key comes from
a function argument, so we cannot rule out other keys being used elsewhere, and
either way `vulnerable` is genuinely present and callable through this dict, not
dead code sitting unused.
