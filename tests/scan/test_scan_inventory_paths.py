import os

import pytest

from reachability.scan.inventory import ScanRootError, Status, build_inventory


def run_guarded(root, seconds=5):
    """Run build_inventory in a daemon thread so a FIFO open cannot hang the suite."""
    import threading

    box = {}

    def target():
        box["inv"] = build_inventory(root)

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(seconds)
    assert not th.is_alive(), "build_inventory blocked (opened a FIFO?)"
    return box["inv"]


def kinds(inv):
    return [s.kind for s in inv.skipped]


def test_include_outside_root_is_skipped_and_recorded(tmp_path, scan_root):
    (tmp_path / "outside.txt").write_text("evil==9.9\n")
    (scan_root / "requirements.txt").write_text("-r ../outside.txt\nfoo==1.0\n")
    inv = build_inventory(scan_root)
    assert [e.dist for e in inv.entries] == ["foo"]
    assert kinds(inv) == ["include_outside_root"]
    assert inv.skipped[0].path == "../outside.txt"


def test_absolute_include_outside_root_skipped(tmp_path, scan_root):
    (tmp_path / "outside.txt").write_text("evil==9.9\n")
    (scan_root / "requirements.txt").write_text(f"-r {tmp_path / 'outside.txt'}\n")
    inv = build_inventory(scan_root)
    assert inv.entries == ()
    assert kinds(inv) == ["include_outside_root"]


def test_symlinked_requirements_file_is_skipped_and_recorded(tmp_path, scan_root):
    (tmp_path / "real.txt").write_text("evil==9.9\n")
    os.symlink(tmp_path / "real.txt", scan_root / "requirements.txt")
    inv = build_inventory(scan_root)
    assert inv.entries == ()
    assert inv.lockfile is None
    assert kinds(inv) == ["symlink"]
    assert inv.hint is not None


def test_symlinked_include_target_skipped(tmp_path, scan_root):
    (scan_root / "real.txt").write_text("evil==9.9\n")
    os.symlink(scan_root / "real.txt", scan_root / "link.txt")
    (scan_root / "requirements.txt").write_text("-r link.txt\nfoo==1.0\n")
    inv = build_inventory(scan_root)
    assert [e.dist for e in inv.entries] == ["foo"]
    assert kinds(inv) == ["symlink"]


def test_symlinked_directory_component_skipped(tmp_path, scan_root):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "x.txt").write_text("evil==9.9\n")
    os.symlink(elsewhere, scan_root / "sub")
    (scan_root / "requirements.txt").write_text("-r sub/x.txt\n")
    inv = build_inventory(scan_root)
    assert inv.entries == ()
    assert kinds(inv) == ["symlink"]


def test_symlink_inside_root_to_inside_root_still_skipped(scan_root):
    (scan_root / "d").mkdir()
    (scan_root / "d" / "x.txt").write_text("a==1\n")
    os.symlink(scan_root / "d", scan_root / "dl")
    (scan_root / "requirements.txt").write_text("-r dl/x.txt\n")
    assert kinds(build_inventory(scan_root)) == ["symlink"]


def test_scan_root_must_exist_and_be_directory(tmp_path):
    with pytest.raises(ScanRootError):
        build_inventory(tmp_path / "missing")
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(ScanRootError):
        build_inventory(f)


def test_scan_root_itself_symlink_resolved_to_real_boundary(tmp_path, scan_root):
    (scan_root / "requirements.txt").write_text("foo==1.0\n")
    link = tmp_path / "rootlink"
    os.symlink(scan_root, link)
    inv = build_inventory(link)
    assert inv.root == str(scan_root.resolve())
    assert [e.dist for e in inv.entries] == ["foo"]


def test_fifo_named_requirements_is_skipped_not_opened(scan_root):
    os.mkfifo(scan_root / "requirements.txt")
    inv = run_guarded(scan_root)
    assert kinds(inv) == ["not_regular_file"]
    assert inv.entries == ()


def test_fifo_include_is_skipped_not_opened(scan_root):
    os.mkfifo(scan_root / "pipe.txt")
    (scan_root / "requirements.txt").write_text("-r pipe.txt\nfoo==1.0\n")
    inv = run_guarded(scan_root)
    assert kinds(inv) == ["not_regular_file"]
    assert [e.dist for e in inv.entries] == ["foo"]


def test_oversized_lockfile_skipped(scan_root):
    with open(scan_root / "requirements.txt", "wb") as f:
        f.write(b"# " + b"x" * (5 * 1024 * 1024 + 1) + b"\nfoo==1.0\n")
    inv = build_inventory(scan_root)
    assert kinds(inv) == ["too_large"]
    assert inv.entries == ()


def test_oversized_include_skipped(scan_root):
    with open(scan_root / "big.txt", "wb") as f:
        f.write(b"# " + b"x" * (5 * 1024 * 1024 + 1) + b"\n")
    (scan_root / "requirements.txt").write_text("-r big.txt\nfoo==1.0\n")
    inv = build_inventory(scan_root)
    assert kinds(inv) == ["too_large"]
    assert [e.status for e in inv.entries] == [Status.PINNED]
