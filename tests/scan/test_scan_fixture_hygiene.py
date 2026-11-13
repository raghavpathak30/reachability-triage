import os
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan"


def _walk():
    for dirpath, dirnames, filenames in os.walk(FIXTURES):
        for name in dirnames + filenames:
            yield Path(dirpath) / name


def test_no_python_files_under_app_scan_fixtures():
    bad = [p for p in _walk() if p.suffix == ".py" or p.name == "conftest.py"]
    assert bad == []


def test_no_symlinks_under_app_scan_fixtures():
    assert [p for p in _walk() if p.is_symlink()] == []
