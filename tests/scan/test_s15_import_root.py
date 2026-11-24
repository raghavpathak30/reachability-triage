import os

import pytest

from reachability.scan.combined import find_app_files
from tests.conftest import write_tree

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="chmod 000 does not block root")


def _scan_tree(app, locked_name):
    write_tree(app, {"lib/zpkg/__init__.py": "", "lib/zpkg/x.py": "y = 1\n"})
    locked = app / "lib" / locked_name
    locked.mkdir()
    (locked / "hidden.py").write_text("z = 1\n")
    locked.chmod(0o000)
    gaps = []
    try:
        files = find_app_files(app, gaps)
    finally:
        locked.chmod(0o755)
    return files, gaps


def test_locked_dir_sorted_before_packaged_sibling_keeps_sibling_dotted_names(tmp_path):
    files, gaps = _scan_tree(tmp_path / "app", "aaa_locked")
    assert {f.rel_in_root for f in files} == {"zpkg/__init__.py", "zpkg/x.py"}
    unreadable = [g for g in gaps if g.kind == "unreadable_path"]
    assert len(unreadable) == 1 and unreadable[0].detail == "app/lib/aaa_locked: PermissionError"


def test_locked_dir_sorted_after_packaged_sibling_unchanged(tmp_path):
    files, gaps = _scan_tree(tmp_path / "app", "zzz_locked")
    assert {f.rel_in_root for f in files} == {"zpkg/__init__.py", "zpkg/x.py"}
    assert len([g for g in gaps if g.kind == "unreadable_path"]) == 1
