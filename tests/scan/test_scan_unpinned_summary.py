import functools
import json
import os
from pathlib import Path

import pytest

from reachability.scan.advisories import build_report, load_snapshot
from reachability.scan.inventory import build_inventory

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan"
REFERENCE = Path(os.environ.get("OSV_REFERENCE_SNAPSHOT", "~/osv-snapshots/osv-pypi-2026-11-12.zip")).expanduser()
REFERENCE_SHA = "c7ea4e15b83912960fc5cbd42c6003dd630e422ec4dfbfc4ed19445573e1b08c"


@functools.cache
def reference_snapshot():
    return load_snapshot(REFERENCE, REFERENCE_SHA)


def aff(name, versions=("1.0",)):
    return {"package": {"name": name, "ecosystem": "PyPI"}, "versions": list(versions)}


def rec(id_, affected, aliases=(), **extra):
    return {"id": id_, "aliases": list(aliases), "affected": affected, **extra}


def report(snapshot_factory, scan_root, requirements, *records):
    path, sha = snapshot_factory(list(records))
    (scan_root / "requirements.txt").write_text(requirements)
    return build_report(build_inventory(scan_root), load_snapshot(path, sha))


def label(name):
    return json.loads((FIX / "eval" / name / "label.json").read_text())


def all_fixture_records():
    return sorted(p.stem for p in (FIX / "advisories").glob("*.json"))


def test_unpinned_dist_gets_no_findings(snapshot_factory, scan_root):
    r = report(snapshot_factory, scan_root, "foo>=0\n", rec("A-1", [aff("foo", ["1.0", "2.0"])]))
    assert r.findings == () and r.malicious == ()
    assert r.unpinned.packages == 1 and r.unpinned.advisories_any_version == 1


def test_lead_none_when_all_pinned():
    path_inv = build_inventory(FIX / "lockfiles" / "req_basic")
    assert path_inv.hint is None


def test_lead_none_when_all_pinned_in_report(snapshot_factory, scan_root):
    r = report(snapshot_factory, scan_root, "foo==1.0\n", rec("A-1", [aff("foo")]))
    assert r.lead is None
    assert r.unpinned.packages == 0 and r.unpinned.advisories_any_version == 0


def test_lead_template_verbatim(snapshot_factory, scan_root):
    r = report(
        snapshot_factory,
        scan_root,
        "foo>=1\nbar\n",
        rec("A-1", [aff("foo")]),
        rec("A-2", [aff("foo")]),
        rec("A-3", [aff("bar")]),
    )
    assert r.lead == "2 packages are not pinned; they have 3 known advisories (any version). Pin them to check."


def test_lead_has_no_pluralisation_logic(snapshot_factory, scan_root):
    r = report(snapshot_factory, scan_root, "foo\n", rec("A-1", [aff("foo")]))
    assert r.lead == "1 packages are not pinned; they have 1 known advisories (any version). Pin them to check."


def test_m_counts_distinct_groups_not_records(snapshot_factory, scan_root):
    r = report(
        snapshot_factory,
        scan_root,
        "foo\n",
        rec("A-1", [aff("foo")], ["CVE-1"]),
        rec("B-1", [aff("foo")], ["CVE-1"]),
        rec("C-1", [aff("foo")]),
    )
    assert r.unpinned.advisories_any_version == 2


def test_m_excludes_withdrawn_and_malicious_groups(snapshot_factory, scan_root):
    r = report(
        snapshot_factory,
        scan_root,
        "foo\n",
        rec("A-1", [aff("foo")]),
        rec("W-1", [aff("foo")], withdrawn="2025-01-01T00:00:00Z"),
        rec("MAL-1", [aff("foo")]),
        rec("C-1", [aff("foo")], database_specific={"cwe_ids": ["CWE-506"]}),
    )
    assert r.unpinned.advisories_any_version == 1


def test_m_counts_group_once_across_unpinned_dists(snapshot_factory, scan_root):
    r = report(
        snapshot_factory,
        scan_root,
        "foo\nbar\n",
        rec("A-1", [aff("foo"), aff("bar")]),
        rec("B-1", [aff("foo")], ["CVE-1"]),
        rec("C-1", [aff("bar")], ["CVE-1"]),
    )
    assert r.unpinned.packages == 2
    assert r.unpinned.advisories_any_version == 2
    assert r.unpinned.per_dist == {"foo": 2, "bar": 2}


def test_m_ignores_non_pypi_and_pinned_dists(snapshot_factory, scan_root):
    npm = {"package": {"name": "foo", "ecosystem": "npm"}, "versions": ["1.0"]}
    r = report(snapshot_factory, scan_root, "foo\nbar==1.0\n", rec("A-1", [npm]), rec("B-1", [aff("bar")]))
    assert r.unpinned.advisories_any_version == 0


def test_m_counts_entries_that_can_never_match(snapshot_factory, scan_root):
    git_only = {"package": {"name": "foo", "ecosystem": "PyPI"}, "ranges": [{"type": "GIT", "events": [{"introduced": "0"}]}]}
    r = report(snapshot_factory, scan_root, "foo\n", rec("A-1", [git_only]))
    assert r.unpinned.advisories_any_version == 1


def test_unsupported_and_marker_skipped_rows_are_not_counted_as_unpinned(snapshot_factory, scan_root):
    r = report(snapshot_factory, scan_root, "a===1.0\nb>=1; sys_platform == 'win32'\n", rec("A-1", [aff("a"), aff("b")]))
    assert r.unpinned.packages == 0 and r.lead is None


def test_t9_lead_against_mini_snapshot(snapshot_factory):
    # accelerate has exactly one non-malicious group in the fixture records: CVE-2026-69112,
    # members GHSA-4j2p-28q2-5m79 and PYSEC-2026-3804 (both copied, see p3b_t9_pick.out.txt).
    path, sha = snapshot_factory(all_fixture_records())
    r = build_report(build_inventory(FIX / "lockfiles" / "T9"), load_snapshot(path, sha))
    assert r.unpinned.advisories_any_version == 1
    assert r.lead == label("T9")["summary_lead"]
    assert r.findings == ()


@pytest.mark.skipif(not REFERENCE.is_file(), reason="reference snapshot not present")
def test_t9_lead_equals_label_on_reference_snapshot():
    r = build_report(build_inventory(FIX / "lockfiles" / "T9"), reference_snapshot())
    assert r.lead == label("T9")["summary_lead"]


@pytest.mark.skipif(not REFERENCE.is_file(), reason="reference snapshot not present")
def test_t10_lead_equals_label_on_reference_snapshot():
    r = build_report(build_inventory(FIX / "lockfiles" / "T10"), reference_snapshot())
    assert r.lead == label("T10")["summary_lead"]


@pytest.mark.skipif(not REFERENCE.is_file(), reason="reference snapshot not present")
def test_t10_summary_unpinned_packages_is_2_and_no_findings_for_unpinned():
    r = build_report(build_inventory(FIX / "lockfiles" / "T10"), reference_snapshot())
    assert r.unpinned.packages == 2
    assert {f.dist for f in r.findings}.isdisjoint({"jinja2", "werkzeug"})
    assert r.malicious == ()


@pytest.mark.skipif(not REFERENCE.is_file(), reason="reference snapshot not present")
def test_m1_label_on_reference_snapshot():
    r = build_report(build_inventory(FIX / "lockfiles" / "M1"), reference_snapshot())
    assert [m.id for m in r.malicious] == label("M1")["malicious"]
    assert r.findings == () and r.lead is None
