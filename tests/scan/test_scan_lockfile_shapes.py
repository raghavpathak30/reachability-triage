"""Fix round 1, F3: valid TOML of the wrong shape raises LockfileParseError, not a raw TypeError."""

import subprocess
import sys

import pytest

from reachability.scan.inventory import LockfileParseError, build_inventory

UV_OK_ROOT = 'version = 1\n[[package]]\nname = "app"\nversion = "0.1"\nsource = {virtual = "."}\n'

UV_BAD = {
    "package_int": "version = 1\npackage = 3\n",
    "package_member_int": "version = 1\npackage = [1]\n",
    "name_int": UV_OK_ROOT + '[[package]]\nname = 5\nversion = "1"\n',
    "version_int": UV_OK_ROOT + '[[package]]\nname = "a"\nversion = 1\n',
    "source_str": UV_OK_ROOT + '[[package]]\nname = "a"\nversion = "1"\nsource = "registry"\n',
    "dependencies_int": 'version = 1\n[[package]]\nname = "app"\nversion = "0.1"\ndependencies = 3\nsource = {virtual = "."}\n',
    "dependency_str": 'version = 1\n[[package]]\nname = "app"\nversion = "0.1"\ndependencies = ["a"]\nsource = {virtual = "."}\n',
    "optional_dependencies_list": 'version = 1\n[[package]]\nname = "app"\nversion = "0.1"\noptional-dependencies = [1]\nsource = {virtual = "."}\n',
    "marker_int": 'version = 1\n[[package]]\nname = "app"\nversion = "0.1"\ndependencies = [{name = "a", marker = 3}]\nsource = {virtual = "."}\n'
    + '[[package]]\nname = "a"\nversion = "1"\n',
    "extra_string": 'version = 1\n[[package]]\nname = "app"\nversion = "0.1"\ndependencies = [{name = "a", extra = "dev"}]\nsource = {virtual = "."}\n'
    + '[[package]]\nname = "a"\nversion = "1"\n',
}

POETRY_BAD = {
    "package_int": "package = 3\n",
    "package_member_int": "package = [1]\n",
    "name_int": "[[package]]\nname = 5\nversion = '1'\n",
    "version_int": "[[package]]\nname = 'a'\nversion = 1\n",
    "source_str": "[[package]]\nname = 'a'\nversion = '1'\nsource = 'git'\n",
    "groups_str": "[[package]]\nname = 'a'\nversion = '1'\ngroups = 'main'\n",
    "markers_int": "[[package]]\nname = 'a'\nversion = '1'\nmarkers = 3\n",
    "markers_table_int": "[[package]]\nname = 'a'\nversion = '1'\nmarkers = {main = 3}\n",
}


@pytest.mark.parametrize("key", sorted(UV_BAD))
def test_uv_lock_wrong_shape_raises_lockfile_parse_error(scan_root, key):
    (scan_root / "uv.lock").write_text(UV_BAD[key])
    with pytest.raises(LockfileParseError, match="uv.lock"):
        build_inventory(scan_root)


@pytest.mark.parametrize("key", sorted(POETRY_BAD))
def test_poetry_lock_wrong_shape_raises_lockfile_parse_error(scan_root, key):
    (scan_root / "poetry.lock").write_text(POETRY_BAD[key])
    with pytest.raises(LockfileParseError, match="poetry.lock"):
        build_inventory(scan_root)


def test_error_names_the_field(scan_root):
    (scan_root / "uv.lock").write_text(UV_BAD["extra_string"])
    with pytest.raises(LockfileParseError, match="'extra'"):
        build_inventory(scan_root)


def test_cli_wrong_shape_exits_1_with_named_error(scan_root, snapshot_factory):
    from tests.scan.test_scan_cli import SCRIPT  # noqa: PLC0415

    (scan_root / "uv.lock").write_text(UV_BAD["package_int"])
    path, sha = snapshot_factory([])
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(scan_root), "--snapshot", str(path), "--snapshot-sha256", sha],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert proc.stderr.startswith("error: LockfileParseError: ")
    assert "Traceback" not in proc.stderr
