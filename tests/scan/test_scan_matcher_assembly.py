"""Record- and group-level assembly. Records are SYNTHETIC unless an id from the fixture dir is used."""

from reachability.scan.advisories import AffectedEntry, build_report, entry_matches, load_snapshot
from reachability.scan.inventory import build_inventory


def aff(name, versions=(), ranges=None, ecosystem="PyPI", **extra):
    a = {"package": {"name": name, "ecosystem": ecosystem}, "versions": list(versions), **extra}
    if ranges is not None:
        a["ranges"] = [{"type": "ECOSYSTEM", "events": r} for r in ranges]
    return a


def rec(id_, affected, aliases=()):
    return {"id": id_, "aliases": list(aliases), "affected": affected}


def report_for(snapshot_factory, scan_root, requirements, *records):
    path, sha = snapshot_factory(list(records))
    (scan_root / "requirements.txt").write_text(requirements)
    return build_report(build_inventory(scan_root), load_snapshot(path, sha))


def test_versions_list_with_non_pep440_item_does_not_break_matching():
    e = AffectedEntry(dist="x", versions=("1.0-custom", "2.0"))
    assert entry_matches(e, "2.0").matched is True
    assert entry_matches(e, "1.0").matched is False


def test_versions_compared_with_pep440_equality():
    e = AffectedEntry(dist="x", versions=("1.0.0",))
    assert entry_matches(e, "1.0").matched is True


def test_non_pypi_ecosystem_entry_ignored(snapshot_factory, scan_root):
    r = report_for(snapshot_factory, scan_root, "foo==1.0\n", rec("S-1", [aff("foo", ["1.0"], ecosystem="npm")]))
    assert r.findings == ()


def test_package_name_compared_after_canonicalisation(snapshot_factory, scan_root):
    r = report_for(snapshot_factory, scan_root, "Foo.Bar==1.0\n", rec("S-1", [aff("foo_bar", ["1.0"])]))
    assert [(f.dist, f.ids) for f in r.findings] == [("foo-bar", ("S-1",))]


def test_one_group_two_matching_records_gives_one_result(snapshot_factory, scan_root):
    r = report_for(
        snapshot_factory,
        scan_root,
        "foo==1.0\n",
        rec("GHSA-bbbb-bbbb-bbbb", [aff("foo", ["1.0"])], ["CVE-1"]),
        rec("PYSEC-1", [aff("foo", ["1.0"])], ["CVE-1"]),
    )
    assert len(r.findings) == 1
    f = r.findings[0]
    assert f.ids == ("GHSA-bbbb-bbbb-bbbb", "PYSEC-1")
    assert f.group_id == "GHSA-bbbb-bbbb-bbbb"


def test_group_matching_only_one_member_lists_only_that_member(snapshot_factory, scan_root):
    r = report_for(
        snapshot_factory,
        scan_root,
        "foo==1.0\n",
        rec("A-1", [aff("foo", ["1.0"])], ["CVE-1"]),
        rec("B-1", [aff("foo", ["9.9"])], ["CVE-1"]),
    )
    assert [f.ids for f in r.findings] == [("A-1",)]


def test_same_dist_two_versions_give_two_results(snapshot_factory, scan_root):
    r = report_for(
        snapshot_factory,
        scan_root,
        'foo==1.0; python_version >= "3.8"\nfoo==2.0; sys_platform == "linux"\n',
        rec("A-1", [aff("foo", ["1.0", "2.0"])]),
    )
    assert sorted((f.dist, f.version) for f in r.findings) == [("foo", "1.0"), ("foo", "2.0")]


def test_duplicate_pinned_rows_are_matched_once(snapshot_factory, scan_root):
    r = report_for(snapshot_factory, scan_root, "foo==1.0\nfoo==1.0\n", rec("A-1", [aff("foo", ["1.0"])]))
    assert len(r.findings) == 1


def test_only_pinned_entries_are_matched(snapshot_factory, scan_root):
    reqs = "a>=1\nb===1.0\nc==1.0; sys_platform == 'win32'\nd==1.0-custom\ne==1.0\n"
    recs = [rec(f"S-{n}", [aff(n, ["1.0", "1"])]) for n in "abcde"]
    r = report_for(snapshot_factory, scan_root, reqs, *recs)
    assert [f.dist for f in r.findings] == ["e"]


def test_fixed_in_is_minimum_across_group_members(snapshot_factory, scan_root):
    r = report_for(
        snapshot_factory,
        scan_root,
        "foo==1.0\n",
        rec("A-1", [aff("foo", [], [[{"introduced": "0"}, {"fixed": "3.0"}]])], ["CVE-1"]),
        rec("B-1", [aff("foo", [], [[{"introduced": "0"}, {"fixed": "2.0"}]])], ["CVE-1"]),
    )
    assert r.findings[0].fixed_in == "2.0"


def test_fixed_in_ignores_group_members_without_a_fix(snapshot_factory, scan_root):
    r = report_for(
        snapshot_factory,
        scan_root,
        "foo==1.0\n",
        rec("A-1", [aff("foo", ["1.0"])], ["CVE-1"]),
        rec("B-1", [aff("foo", [], [[{"introduced": "0"}, {"fixed": "2.0"}]])], ["CVE-1"]),
    )
    assert r.findings[0].fixed_in == "2.0"


def test_notes_are_unique_and_attached(snapshot_factory, scan_root):
    r = report_for(
        snapshot_factory,
        scan_root,
        "foo==1.0+cpu\n",
        rec("A-1", [aff("foo", ["1.0"])]),
    )
    assert "local version segment +cpu ignored for matching" in r.findings[0].notes
