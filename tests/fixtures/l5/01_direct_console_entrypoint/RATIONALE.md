`mycli` is declared as a console script pointing at `app.cli:main`. `main` imports
`vulnerable` from `app.sink` by name and calls it directly, unconditionally, the
first time `main` runs. There is no branching, indirection, or runtime condition
between the entrypoint and the sink.
