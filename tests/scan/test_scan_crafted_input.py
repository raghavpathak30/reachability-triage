"""Fix round 3: crafted input gives a named ScanError (CLI exit 1), never a raw exception."""

import subprocess
import sys

import pytest

from reachability.scan.inventory import MAX_INCLUDE_DEPTH, LockfileParseError, build_inventory


def run_cli(scan_root, snapshot_factory):
    from tests.scan.test_scan_cli import SCRIPT  # noqa: PLC0415

    path, sha = snapshot_factory([])
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(scan_root), "--snapshot", str(path), "--snapshot-sha256", sha],
        capture_output=True,
        text=True,
    )


def assert_named_exit(proc, name="LockfileParseError"):
    assert proc.returncode == 1
    assert proc.stderr.startswith(f"error: {name}: ")
    assert "Traceback" not in proc.stderr


def write_chain(root, n):
    """requirements.txt includes r1.txt ... includes r<n>.txt; the last has a pin."""
    (root / "requirements.txt").write_text("-r r1.txt\n" if n else "foo==1.0\n")
    for i in range(1, n + 1):
        body = f"-r r{i + 1}.txt\n" if i < n else "foo==1.0\n"
        (root / f"r{i}.txt").write_text(body)


def test_include_chain_of_max_depth_parses(scan_root):
    write_chain(scan_root, MAX_INCLUDE_DEPTH)
    inv = build_inventory(scan_root)
    assert [e.dist for e in inv.entries] == ["foo"]


def test_include_chain_one_past_max_depth_raises_naming_file(scan_root):
    write_chain(scan_root, MAX_INCLUDE_DEPTH + 1)
    with pytest.raises(LockfileParseError, match=rf"r{MAX_INCLUDE_DEPTH + 1}\.txt"):
        build_inventory(scan_root)


def test_600_file_include_chain_raises_and_cli_exits_1(scan_root, snapshot_factory):
    write_chain(scan_root, 600)
    with pytest.raises(LockfileParseError):
        build_inventory(scan_root)
    assert_named_exit(run_cli(scan_root, snapshot_factory))
