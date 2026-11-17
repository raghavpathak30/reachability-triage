import sys
from pathlib import Path

from tests.conftest import write_tree

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability.scan.combined import build_combined  # noqa: E402
from reachability.scan.targets import defs_from_tables, defs_from_tree, diff_defs, resolve_targets  # noqa: E402


def _tree(tmp_path, name, files):
    return write_tree(tmp_path / name, files)


def _combined_defs(root: Path, dist="d"):
    c = build_combined(root / "app", {dist: root / "inst"})
    return c, defs_from_tables(c.index.symbol_index, {m for m, o in c.module_origins().items() if o == dist})


def test_e1_diff(tmp_path):
    inst = _tree(tmp_path, "inst", {k: v for k, v in mat.e1_wheel_files("1.1.13").items()})
    fixed = _tree(tmp_path, "fix", {k: v for k, v in mat.e1_wheel_files("1.1.14").items()})
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    c, installed = _combined_defs(tmp_path)
    try:
        changes = dict(diff_defs(installed, defs_from_tree(fixed)))
        key = ("langchain_openai.chat_models.base", "_url_to_size", 0)
        assert changes[key] == "changed"
        assert changes[("langchain_openai.chat_models.base", "_get_ssrf_safe_client", 0)] == "added"
        targets = resolve_targets(sorted(changes.items()), installed, c, "d")
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
        changes = dict(diff_defs(installed, defs_from_tree(fixed)))
        assert changes == {("p.a", "A.m", 0): "changed"}
    finally:
        c.close()


def test_class_body_change_makes_class_a_target(tmp_path):
    _tree(tmp_path, "inst", {"p/__init__.py": "", "p/a.py": "class A:\n    x = 1\n    def m(self): pass\n"})
    fixed = _tree(tmp_path, "fix", {"p/__init__.py": "", "p/a.py": "class A:\n    x = 2\n    def m(self): pass\n"})
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    c, installed = _combined_defs(tmp_path)
    try:
        assert dict(diff_defs(installed, defs_from_tree(fixed))) == {("p.a", "A", 0): "changed"}
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
        assert diff_defs(installed, defs_from_tree(fixed)) == []
    finally:
        c.close()


def test_module_level_change_gives_no_target(tmp_path):
    _tree(tmp_path, "inst", {"p.py": "X = 1\ndef f(): pass\n"})
    fixed = _tree(tmp_path, "fix", {"p.py": "X = 2\ndef f(): pass\n"})
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    c, installed = _combined_defs(tmp_path)
    try:
        assert diff_defs(installed, defs_from_tree(fixed)) == []
    finally:
        c.close()
