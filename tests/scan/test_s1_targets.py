import sys
from pathlib import Path

from tests.conftest import write_tree

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability.scan.combined import build_combined  # noqa: E402
from reachability.scan.targets import diff_symbols, resolve_targets, symbols_from_tables, symbols_from_tree  # noqa: E402


def _tree(tmp_path, name, files):
    return write_tree(tmp_path / name, files)


def _combined_defs(root: Path, dist="d"):
    c = build_combined(root / "app", {dist: root / "inst"})
    return c, symbols_from_tables(c.index.symbol_index, {m for m, o in c.module_origins().items() if o == dist})


def test_e1_diff(tmp_path):
    inst = _tree(tmp_path, "inst", {k: v for k, v in mat.e1_wheel_files("1.1.13").items()})
    fixed = _tree(tmp_path, "fix", {k: v for k, v in mat.e1_wheel_files("1.1.14").items()})
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    c, installed = _combined_defs(tmp_path)
    try:
        changes = dict(diff_symbols(installed, symbols_from_tree(fixed)).changes)
        key = ("langchain_openai.chat_models.base", "_url_to_size", 0)
        assert changes[key] == "changed"
        assert changes[("langchain_openai.chat_models.base", "_get_ssrf_safe_client", 0)] == "added"
        targets, _ = resolve_targets(sorted(changes.items()), installed, c, "d")
        assert "langchain_openai.chat_models.base:_url_to_size" in {t.node_id for t in targets}
        assert all(t.confidence == "medium" and t.named is False for t in targets)
        assert not any(t.changed == "added" for t in targets)
    finally:
        c.close()


def test_method_change_does_not_make_class_a_target(tmp_path):
    old = "class A:\n    x = 1\n    def m(self):\n        return 1\n"
    new = "class A:\n    x = 1\n    def m(self):\n        return 2\n"
    _tree(tmp_path, "inst", {"p/__init__.py": "", "p/a.py": old})
    fixed = _tree(tmp_path, "fix", {"p/__init__.py": "", "p/a.py": new})
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    c, installed = _combined_defs(tmp_path)
    try:
        changes = dict(diff_symbols(installed, symbols_from_tree(fixed)).changes)
        assert changes == {("p.a", "A.m", 0): "changed"}
    finally:
        c.close()


def test_class_body_change_makes_class_a_target(tmp_path):
    _tree(tmp_path, "inst", {"p/__init__.py": "", "p/a.py": "class A:\n    x = 1\n    def m(self): pass\n"})
    fixed = _tree(tmp_path, "fix", {"p/__init__.py": "", "p/a.py": "class A:\n    x = 2\n    def m(self): pass\n"})
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    c, installed = _combined_defs(tmp_path)
    try:
        assert dict(diff_symbols(installed, symbols_from_tree(fixed)).changes) == {("p.a", "A", 0): "changed"}
    finally:
        c.close()


def test_duplicate_name_pair_gives_no_phantom(tmp_path):
    old = "def f():\n    return 1\n\n\ndef f():\n    return 2\n"
    new = "# shifted\n\n\n\ndef f():\n    return 1\n\n\ndef f():\n    return 2\n"
    _tree(tmp_path, "inst", {"p.py": old})
    fixed = _tree(tmp_path, "fix", {"p.py": new})
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    c, installed = _combined_defs(tmp_path)
    try:
        assert diff_symbols(installed, symbols_from_tree(fixed)).changes == []
    finally:
        c.close()


def test_module_level_change_gives_no_target(tmp_path):
    _tree(tmp_path, "inst", {"p.py": "X = 1\ndef f(): pass\n"})
    fixed = _tree(tmp_path, "fix", {"p.py": "X = 2\ndef f(): pass\n"})
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    c, installed = _combined_defs(tmp_path)
    try:
        assert diff_symbols(installed, symbols_from_tree(fixed)).changes == []
    finally:
        c.close()


def _pair(tmp_path, old_files, new_files, dist="d"):
    write_tree(tmp_path / "inst", old_files)
    fixed = write_tree(tmp_path / "fix", new_files)
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    c = build_combined(tmp_path / "app", {dist: tmp_path / "inst"})
    installed = symbols_from_tables(c.index.symbol_index, {m for m, o in c.module_origins().items() if o == dist})
    return c, installed, symbols_from_tree(fixed)


def test_version_only_change_is_dropped(tmp_path):
    old = {"p/__init__.py": "__version__ = '1.0'\nversion_info = (1, 0)\n", "p/a.py": "def f(): pass\n"}
    new = {"p/__init__.py": "__version__ = '1.1'\nversion_info = (1, 1)\n", "p/a.py": "def f(): pass\n"}
    c, inst, fixed = _pair(tmp_path, old, new)
    try:
        d = diff_symbols(inst, fixed)
        assert d.changes == [] and d.module_level_changes == 0 and not d.py_changed
    finally:
        c.close()


def test_non_constant_version_assignment_is_a_module_level_change(tmp_path):
    old = {"p/__init__.py": "__version__ = '1.0'\n"}
    new = {"p/__init__.py": "__version__ = compute()\n"}
    c, inst, fixed = _pair(tmp_path, old, new)
    try:
        d = diff_symbols(inst, fixed)
        assert d.changes == [] and d.module_level_changes == 1 and d.py_changed
    finally:
        c.close()


def test_test_modules_are_excluded(tmp_path):
    old = {"p/__init__.py": "", "p/tests/__init__.py": "", "p/tests/test_a.py": "def t(): return 1\n",
           "p/test_b.py": "def u(): return 1\n", "p/real.py": "def r(): return 1\n"}
    new = {"p/__init__.py": "", "p/tests/__init__.py": "", "p/tests/test_a.py": "def t(): return 2\n",
           "p/test_b.py": "def u(): return 2\n", "p/real.py": "def r(): return 2\n"}
    c, inst, fixed = _pair(tmp_path, old, new)
    try:
        assert [k for k, _ in diff_symbols(inst, fixed).changes] == [("p.real", "r", 0)]
    finally:
        c.close()


def test_cap_20_with_truncated_flag(tmp_path):
    old = {"p/__init__.py": "", "p/a.py": "".join(f"def f{i:02d}(): return 1\n" for i in range(25))}
    new = {"p/__init__.py": "", "p/a.py": "".join(f"def f{i:02d}(): return 2\n" for i in range(25))}
    c, inst, fixed = _pair(tmp_path, old, new)
    try:
        targets, truncated = resolve_targets(diff_symbols(inst, fixed).changes, inst, c, "d")
        assert len(targets) == 20 and truncated is True
        assert [t.qualname for t in targets] == [f"f{i:02d}" for i in range(20)]
    finally:
        c.close()


def test_removed_symbols_count(tmp_path):
    old = {"p/__init__.py": "", "p/a.py": "def gone(): pass\ndef kept(): pass\n"}
    new = {"p/__init__.py": "", "p/a.py": "def kept(): pass\n"}
    c, inst, fixed = _pair(tmp_path, old, new)
    try:
        targets, _ = resolve_targets(diff_symbols(inst, fixed).changes, inst, c, "d")
        assert [(t.qualname, t.changed) for t in targets] == [("gone", "removed")]
    finally:
        c.close()


def test_non_py_changes_from_record(tmp_path):
    from reachability.scan.targets import non_py_changed

    def tree(name, digest):
        return write_tree(tmp_path / name, {"p-1.dist-info/RECORD": f"p/_n.so,sha256={digest},4\np/a.py,sha256=zz,3\np-1.dist-info/RECORD,,\n"})

    assert non_py_changed(tree("a", "AAA"), tree("b", "BBB")) is True
    assert non_py_changed(tree("c", "AAA"), tree("d", "AAA")) is False
