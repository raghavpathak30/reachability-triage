import hashlib
import stat
import zipfile
from pathlib import Path

import pytest

from reachability.scan.fetch import LocalDirFetcher, fetch_dist
from reachability.scan.wheel_extract import WheelRejected, check_members, extract_wheel


def make_wheel(path: Path, members):
    """members: list of (name, data, external_attr or None)."""
    with zipfile.ZipFile(path, "w") as z:
        for name, data, attr in members:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = attr if attr is not None else 0o644 << 16
            z.writestr(info, data)
    return path


GOOD = ("pkg/__init__.py", b"x = 1\n", None)

CASES = {
    "zip-slip": [GOOD, ("../evil.py", b"x", None)],
    "nested-zip-slip": [GOOD, ("pkg/../../evil.py", b"x", None)],
    "absolute": [GOOD, ("/etc/evil.py", b"x", None)],
    "drive": [GOOD, ("C:evil.py", b"x", None)],
    "backslash": [GOOD, ("pkg\\evil.py", b"x", None)],
    "symlink": [GOOD, ("pkg/link.py", b"target.py", (stat.S_IFLNK | 0o777) << 16)],
    "hardlink-or-nonregular": [GOOD, ("pkg/dev.py", b"", (stat.S_IFCHR | 0o644) << 16)],
    "fifo": [GOOD, ("pkg/f.py", b"", (stat.S_IFIFO | 0o644) << 16)],
    "duplicate": [GOOD, GOOD],
    "file-dir-conflict": [("a.py", b"x", None), ("a.py/b.py", b"y", None)],
}


pytestmark = pytest.mark.filterwarnings("ignore:Duplicate name")


@pytest.mark.parametrize("name", sorted(CASES))
def test_member_rules_reject_whole_wheel(tmp_path, name):
    whl = make_wheel(tmp_path / "w.whl", CASES[name])
    dest = tmp_path / "out"
    with pytest.raises(WheelRejected):
        extract_wheel(whl, dest)
    assert not list(dest.rglob("*.py"))


@pytest.mark.parametrize("name", sorted(CASES))
def test_rejected_wheel_is_no_wheel_with_reason(tmp_path, name):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    whl = make_wheel(wheels / "pkg-1.0-py3-none-any.whl", CASES[name])
    (wheels / (whl.name + ".sha256")).write_text(hashlib.sha256(whl.read_bytes()).hexdigest())
    fd, gaps = fetch_dist("pkg", "1.0", LocalDirFetcher(wheels), tmp_path / "store")
    assert fd.status == "no_wheel" and fd.reason.startswith("wheel rejected:")
    assert gaps[0].kind == "no_wheel"
    assert not list((tmp_path / "store").rglob("*.py"))


def test_clean_wheel_passes(tmp_path):
    whl = make_wheel(tmp_path / "w.whl", [GOOD, ("pkg/sub/", b"", 0o40755 << 16), ("pkg/sub/m.py", b"y = 2\n", None)])
    extract_wheel(whl, tmp_path / "out")
    assert (tmp_path / "out" / "pkg" / "sub" / "m.py").is_file()


def test_nul_in_name_rejected():
    info = zipfile.ZipInfo("pkg/ok.py")
    info.filename = "pkg/ev\x00il.py"  # ZipInfo() itself truncates at NUL, so set it afterwards
    with pytest.raises(WheelRejected, match="NUL"):
        check_members([info])
