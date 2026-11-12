import json
from pathlib import Path

import pytest

from reachability.scan import inventory
from reachability.scan.inventory import build_inventory

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan"
LOCKFILES = FIXTURES / "lockfiles"
LABELLED = {"M1", "T9", "T10"}  # expectation lives in eval/<name>/label.json
DIRS = sorted(p.name for p in LOCKFILES.iterdir() if p.is_dir())


def expectation(name):
    path = FIXTURES / "eval" / name / "label.json" if name in LABELLED else LOCKFILES / name / "expected.json"
    return json.loads(path.read_text())


def key_of(entry):
    return entry.dist if entry.dist is not None else f"(unnamed line {entry.line})"


def expected_rows(inv_expect):
    rows = []
    for key, val in inv_expect.items():
        for v in val if isinstance(val, list) else [val]:
            rows.append((key, v["version"], v["status"]))
    return sorted(rows, key=repr)


def test_every_lockfile_fixture_has_an_expectation():
    for name in DIRS:
        assert expectation(name), name


def test_expected_fixture_set_is_complete():
    assert {
        "req_basic", "req_unpinned_kinds", "req_markers", "req_include", "req_index_url", "req_dup_versions",
        "T9", "T10", "M1", "poetry_basic", "poetry_markers", "uv_basic", "uv_extras_markers", "uv_cycle",
        "uv_no_root", "uv_two_roots", "uv_root_subdir",
    } <= set(DIRS)


@pytest.mark.parametrize("name", DIRS)
def test_g5_exact_dist_version_status_on_every_lockfile_fixture(name):
    exp = expectation(name)
    if "raises" in exp:
        with pytest.raises(getattr(inventory, exp["raises"])):
            build_inventory(LOCKFILES / name)
        return
    inv = build_inventory(LOCKFILES / name)
    actual = sorted(((key_of(e), e.version, e.status.value) for e in inv.entries), key=repr)
    assert actual == expected_rows(exp["inventory"])


def test_names_canonicalised_pep503(scan_root):
    (scan_root / "requirements.txt").write_text("Jinja2==3.0\nPyYAML==6.0\nzope.interface==6.0\nTyping_Extensions==4.0\n")
    assert [e.dist for e in build_inventory(scan_root).entries] == ["jinja2", "pyyaml", "zope-interface", "typing-extensions"]
