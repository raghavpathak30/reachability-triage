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


# ---- F8: huge-digit versions (Python's int-string limit makes Version() raise ValueError)

BIG = "9" * 5000


def test_requirements_huge_version_is_unsupported(scan_root):
    (scan_root / "requirements.txt").write_text(f"foo=={BIG}\n")
    e = build_inventory(scan_root).entries[0]
    assert e.status.value == "unsupported"
    assert any("not valid PEP 440" in n for n in e.notes)


def test_poetry_huge_version_is_unsupported(scan_root):
    (scan_root / "poetry.lock").write_text(f'[[package]]\nname = "foo"\nversion = "{BIG}"\ngroups = ["main"]\n')
    e = build_inventory(scan_root).entries[0]
    assert e.status.value == "unsupported"


def test_uv_huge_version_is_unsupported(scan_root):
    (scan_root / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "app"\nversion = "0.1"\nsource = {virtual = "."}\n'
        'dependencies = [{name = "foo"}]\n'
        f'[[package]]\nname = "foo"\nversion = "{BIG}"\nsource = {{registry = "https://pypi.org/simple"}}\n'
    )
    e = [x for x in build_inventory(scan_root).entries if x.dist == "foo"][0]
    assert e.status.value == "unsupported"


def test_requirements_marker_huge_version_keeps_entry_with_note(scan_root):
    (scan_root / "requirements.txt").write_text(f"foo==1.0; python_version >= '{BIG}'\n")
    e = build_inventory(scan_root).entries[0]
    assert e.status.value == "pinned"
    assert any("could not be evaluated" in n for n in e.notes)


def test_poetry_marker_huge_version_keeps_entry_with_note(scan_root):
    (scan_root / "poetry.lock").write_text(
        f'[[package]]\nname = "foo"\nversion = "1.0"\ngroups = ["main"]\nmarkers = "python_version >= \'{BIG}\'"\n'
    )
    e = build_inventory(scan_root).entries[0]
    assert any("could not be evaluated" in n for n in e.notes)


@pytest.mark.parametrize("where", ["fixed", "versions", "lkavr"])
def test_snapshot_huge_bound_matches_conservatively(scan_root, snapshot_factory, where):
    from tests.scan.test_scan_matcher_assembly import aff, rec, report_for  # noqa: PLC0415

    if where == "fixed":
        a = aff("foo", ranges=[[{"introduced": "0"}, {"fixed": BIG}]])
    elif where == "versions":
        a = aff("foo", versions=[BIG])
    else:
        a = aff("foo", database_specific={"last_known_affected_version_range": f"<= {BIG}"})
    r = report_for(snapshot_factory, scan_root, "foo==1.0\n", rec("S-1", [a]))
    if where == "versions":
        assert r.findings == ()
    else:
        assert len(r.findings) == 1


# ---- F9: deep nesting (RecursionError)

N = 3000
DEEP_MARKER = "(" * N + "python_version >= '3'" + ")" * N


def test_requirements_deep_marker_raises(scan_root, snapshot_factory):
    (scan_root / "requirements.txt").write_text(f"foo==1.0; {DEEP_MARKER}\n")
    with pytest.raises(LockfileParseError, match="requirements.txt"):
        build_inventory(scan_root)
    assert_named_exit(run_cli(scan_root, snapshot_factory))


def test_poetry_deep_marker_raises(scan_root, snapshot_factory):
    (scan_root / "poetry.lock").write_text(
        f'[[package]]\nname = "foo"\nversion = "1.0"\ngroups = ["main"]\nmarkers = "{DEEP_MARKER}"\n'
    )
    with pytest.raises(LockfileParseError, match="poetry.lock"):
        build_inventory(scan_root)
    assert_named_exit(run_cli(scan_root, snapshot_factory))


def test_uv_deep_toml_array_raises(scan_root, snapshot_factory):
    (scan_root / "uv.lock").write_text("version = 1\nx = " + "[" * N + "]" * N + "\n")
    with pytest.raises(LockfileParseError, match="uv.lock"):
        build_inventory(scan_root)
    assert_named_exit(run_cli(scan_root, snapshot_factory))


def test_poetry_deep_toml_array_raises(scan_root, snapshot_factory):
    (scan_root / "poetry.lock").write_text("x = " + "[" * N + "]" * N + "\n")
    with pytest.raises(LockfileParseError, match="poetry.lock"):
        build_inventory(scan_root)
    assert_named_exit(run_cli(scan_root, snapshot_factory))


def test_uv_deep_edge_marker_raises(scan_root):
    (scan_root / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "app"\nversion = "0.1"\nsource = {virtual = "."}\n'
        f'dependencies = [{{name = "foo", marker = "{DEEP_MARKER}"}}]\n'
        '[[package]]\nname = "foo"\nversion = "1.0"\nsource = {registry = "https://pypi.org/simple"}\n'
    )
    with pytest.raises(LockfileParseError, match="uv.lock"):
        build_inventory(scan_root)


def test_snapshot_deep_json_raises_snapshot_error(snapshot_factory):
    from reachability.scan.advisories import SnapshotError, load_snapshot  # noqa: PLC0415

    path, sha = snapshot_factory([("a.json", "[" * 100000 + "]" * 100000)])
    with pytest.raises(SnapshotError):
        load_snapshot(path, sha)


# ---- F10: odd -r operands


@pytest.mark.parametrize("operand", ["a\x00b", "x" * 5000], ids=["nul", "long_name"])
def test_odd_include_operand_is_skipped_include_missing(scan_root, operand):
    (scan_root / "requirements.txt").write_text(f"foo==1.0\n-r {operand}\n")
    inv = build_inventory(scan_root)
    assert [e.dist for e in inv.entries] == ["foo"]
    (item,) = inv.skipped
    assert item.kind == "include_missing"
    assert item.detail.startswith(("ValueError", "OSError"))


def test_long_include_operand_cli_exits_0(scan_root, snapshot_factory):
    (scan_root / "requirements.txt").write_text("foo==1.0\n-r " + "x" * 5000 + "\n")
    proc = run_cli(scan_root, snapshot_factory)
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr
