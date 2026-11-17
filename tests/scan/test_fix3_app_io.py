import os

import pytest

from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.pipeline import run_scan
from tests.scan.s1_support import APP_CALLS_F, simple_lib_scan

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="chmod 000 does not block root")


def _run(s):
    return run_scan(s["app"], s["snapshot"], s["sha256"], LocalDirFetcher(s["wheels"]))


def test_unreadable_app_directory_is_a_gap_and_closure_incomplete(tmp_path):
    s = simple_lib_scan(tmp_path)
    locked = s["app"] / "locked"
    locked.mkdir()
    (locked / "x.py").write_text("x = 1\n")
    locked.chmod(0o000)
    try:
        res = _run(s)
    finally:
        locked.chmod(0o755)
    gaps = [g for g in res.gaps if g.kind == "unreadable_path"]
    assert len(gaps) == 1 and gaps[0].detail == "app/locked: PermissionError"
    assert res.closure_complete is False
    assert res.findings[0].verdict == "reachable"


def test_unreadable_app_file_is_an_unparsed_gap_and_scan_continues(tmp_path):
    s = simple_lib_scan(tmp_path, app_files={"main.py": APP_CALLS_F, "secret.py": "x = 1\n"})
    (s["app"] / "secret.py").chmod(0o000)
    try:
        res = _run(s)
    finally:
        (s["app"] / "secret.py").chmod(0o644)
    gaps = [g for g in res.gaps if g.kind == "unparsed_file"]
    assert len(gaps) == 1 and gaps[0].detail.startswith("app/secret.py") and "PermissionError" in gaps[0].detail
    assert res.closure_complete is False
    assert res.findings[0].verdict == "reachable"
