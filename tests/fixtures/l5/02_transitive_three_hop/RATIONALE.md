Running `pkg/entry.py` directly as a script calls `step_a`, which calls `step_b`,
which calls `vulnerable`. Each hop is a plain, unconditional, by-name function call
across three modules — no branching, no indirection.
