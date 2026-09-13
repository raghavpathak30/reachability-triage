`pkg/entry.py`'s `dispatch(name)` calls `getattr(sys.modules[__name__], name)()` --
a nameless dispatch with no literal target string at the call site itself (the one
call site passes `"something"`, but `dispatch` takes `name` as a parameter, so
nothing in the source rules out another string reaching it at runtime). This
opaque call is reachable directly from the `if __name__ == "__main__":` entrypoint.

`pkg/orphan.py::orphan` is a completely unrelated function: nothing imports
`pkg.orphan`, nothing calls `orphan`, and no other symbol in the repository is
even named `orphan`. DECISIONS.md's D1 finding states this class of opaque call
degrades *any* would-be `not_reachable` verdict to `unknown`, repo-wide, not just
for symbols whose name could plausibly be the dispatch target -- this fixture
exists to confirm that trade-off actually behaves that broadly (rather than, say,
only firing when the query target's name matches something the opaque call could
plausibly resolve to).
