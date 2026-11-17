from pathlib import Path

import pytest

from reachability.scan.inventory import build_inventory

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan" / "lockfiles"


@pytest.mark.parametrize("name", ["req_basic", "poetry_basic", "uv_basic"])
def test_requirements_poetry_uv_dot_set_app_root_dot(name):
    inv = build_inventory(FIX / name)
    assert inv.app_root == "."
    assert inv.member is None


def test_no_lockfile_leaves_app_root_none(scan_root):
    inv = build_inventory(scan_root)
    assert inv.lockfile is None
    assert inv.app_root is None
    assert inv.member is None


from reachability.scan.inventory import MemberOptionError  # noqa: E402


def test_member_with_requirements_raises_member_option_error():
    with pytest.raises(MemberOptionError, match="requirements.txt"):
        build_inventory(FIX / "req_basic", member="x")


def test_member_with_poetry_raises_member_option_error():
    with pytest.raises(MemberOptionError, match="poetry.lock"):
        build_inventory(FIX / "poetry_basic", member="x")


def test_member_with_no_lockfile_raises_member_option_error(scan_root):
    with pytest.raises(MemberOptionError, match="no lockfile"):
        build_inventory(scan_root, member="x")
