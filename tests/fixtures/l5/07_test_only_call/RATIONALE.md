The only place `vulnerable` is ever called in this repository is inside `test_it`, a
test function. Nothing outside `tests/` imports or calls it, and there is no console
entrypoint, CLI, or route anywhere in the repo.
