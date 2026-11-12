from pathlib import Path

import pytest

from reachability.scan.inventory import (
    LockfileParseError,
    UvLockFormatError,
    UvLockRootError,
    build_inventory,
    find_uv_root,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan" / "lockfiles"


def pkg(name, source, **extra):
    return {"name": name, "version": "1.0", "source": source, **extra}


def test_root_is_editable_dot():
    root = find_uv_root([pkg("a", {"registry": "x"}), pkg("app", {"editable": "."})])
    assert root["name"] == "app"


def test_root_is_virtual_dot():
    assert find_uv_root([pkg("app", {"virtual": "."})])["name"] == "app"


def test_registry_package_never_root():
    with pytest.raises(UvLockRootError):
        find_uv_root([pkg("a", {"registry": "https://pypi.org/simple"})])


def test_directory_source_dot_is_not_root():
    with pytest.raises(UvLockRootError):
        find_uv_root([pkg("a", {"directory": "."})])


def test_workspace_member_editable_subdir_is_not_root():
    with pytest.raises(UvLockRootError, match="packages/ws"):
        find_uv_root([pkg("ws", {"editable": "packages/ws"})])


def test_no_root_raises_uvlockrooterror_naming_candidates():
    with pytest.raises(UvLockRootError) as ei:
        build_inventory(FIX / "uv_no_root")
    msg = str(ei.value)
    assert "root" in msg and "ws-member" in msg and "packages/ws-member" in msg


def test_two_roots_raises_uvlockrooterror_naming_both():
    with pytest.raises(UvLockRootError) as ei:
        build_inventory(FIX / "uv_two_roots")
    msg = str(ei.value)
    assert "root" in msg and "app-one" in msg and "app-two" in msg


def test_root_in_subdirectory_is_refused_and_named():
    with pytest.raises(UvLockRootError) as ei:
        build_inventory(FIX / "uv_root_subdir")
    assert "backend" in str(ei.value) and "app" in str(ei.value)


def write_uv(root, body):
    (root / "uv.lock").write_text(body)
    return build_inventory(root)


def test_uv_lock_unknown_version_raises(scan_root):
    with pytest.raises(UvLockFormatError, match="version"):
        write_uv(scan_root, 'version = 2\n[[package]]\nname="a"\nversion="1"\nsource={editable="."}\n')


@pytest.mark.parametrize("body", ['[[package]]\nname="a"\nversion="1"\nsource={editable="."}\n', 'version = "1"\n', "version = true\n"])
def test_uv_lock_missing_or_non_integer_version_raises(scan_root, body):
    with pytest.raises(UvLockFormatError):
        write_uv(scan_root, body)


def test_uv_lock_malformed_toml_raises_parse_error(scan_root):
    with pytest.raises(LockfileParseError):
        write_uv(scan_root, "version = [")
