
from tests.conftest import write_tree

from reachability.scan.combined import build_combined
from reachability.scan.models import Target
from reachability.scan.targets import (
    MAX_TARGETS,
    diff_symbols,
    named_names,
    resolve_targets,
    symbols_from_tables,
    symbols_from_tree,
)


def _setup(tmp_path, old, new):
    write_tree(tmp_path / "inst", {"p/__init__.py": "", "p/a.py": old})
    fixed = write_tree(tmp_path / "fix", {"p/__init__.py": "", "p/a.py": new})
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    c = build_combined(tmp_path / "app", {"d": tmp_path / "inst"})
    mods = {m for m, o in c.module_origins().items() if o == "d"}
    installed = symbols_from_tables(c.index.symbol_index, mods)
    changes = diff_symbols(installed, symbols_from_tree(fixed)).changes
    return c, installed, changes


def test_named_and_changed_is_high(tmp_path):
    c, inst, ch = _setup(tmp_path, "def f():\n    return 1\n", "def f():\n    return 2\n")
    try:
        ts, trunc = resolve_targets(ch, inst, c, "d", named_names("calls `f()` badly"))
    finally:
        c.close()
    assert [(t.qualname, t.confidence, t.named) for t in ts] == [("f", "high", True)]
    assert not trunc


def test_changed_but_unnamed_is_medium(tmp_path):
    c, inst, ch = _setup(tmp_path, "def f():\n    return 1\n", "def f():\n    return 2\n")
    try:
        ts, _ = resolve_targets(ch, inst, c, "d", named_names("calls `g()` badly"))
    finally:
        c.close()
    assert [(t.qualname, t.confidence, t.named) for t in ts] == [("f", "medium", False)]


def test_named_but_unchanged_is_medium_target(tmp_path):
    old = "def f():\n    return 1\n\n\ndef g():\n    return 1\n"
    new = "def f():\n    return 2\n\n\ndef g():\n    return 1\n"
    c, inst, ch = _setup(tmp_path, old, new)
    try:
        ts, _ = resolve_targets(ch, inst, c, "d", named_names("`g()` and `f()`"))
    finally:
        c.close()
    got = {t.qualname: (t.confidence, t.changed, t.named) for t in ts}
    assert got == {"f": ("high", "changed", True), "g": ("medium", "unchanged", True)}
    assert [t.qualname for t in ts] == ["f", "g"]


def test_cap_keeps_all_high_before_medium(tmp_path):
    names = [f"f{i:02d}" for i in range(22)] + ["zz1", "zz2", "zz3"]
    old = "".join(f"def {n}():\n    return 1\n\n\n" for n in names)
    new = old.replace("return 1", "return 2")
    c, inst, ch = _setup(tmp_path, old, new)
    try:
        ts, trunc = resolve_targets(ch, inst, c, "d", named_names("`zz1()` `zz2()` `zz3()`"))
    finally:
        c.close()
    assert len(ts) == MAX_TARGETS and trunc
    assert [t.qualname for t in ts[:3]] == ["zz1", "zz2", "zz3"]
    assert all(t.confidence == "high" for t in ts[:3]) and all(t.confidence == "medium" for t in ts[3:])


def test_confidence_defaults_unchanged_for_positional_target():
    t = Target("m:f", "m", "f", 0, "d/x.py", 1, "changed")
    assert t.confidence == "medium" and t.named is False
