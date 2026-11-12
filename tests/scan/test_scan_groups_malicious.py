from reachability.scan.advisories import load_snapshot


def rec(id_, aliases=(), **extra):
    return {"id": id_, "aliases": list(aliases), "affected": [], **extra}


def snap_of(snapshot_factory, *records):
    path, sha = snapshot_factory(list(records))
    return load_snapshot(path, sha)


def test_alias_links_two_records_into_one_group(snapshot_factory):
    s = snap_of(snapshot_factory, rec("GHSA-aaaa-aaaa-aaaa", ["CVE-1", "PYSEC-1"]), rec("PYSEC-1", ["CVE-1"]))
    assert len(s.groups) == 1
    g = next(iter(s.groups.values()))
    assert g.ids == ("GHSA-aaaa-aaaa-aaaa", "PYSEC-1")
    assert s.group_of["PYSEC-1"] == s.group_of["GHSA-aaaa-aaaa-aaaa"] == g.group_id


def test_transitive_alias_chain_one_group(snapshot_factory):
    s = snap_of(snapshot_factory, rec("A-1", ["B-1"]), rec("B-1", ["C-1"]), rec("C-1", ["D-1"]), rec("D-1"))
    assert len(s.groups) == 1


def test_alias_to_id_not_in_snapshot_still_links(snapshot_factory):
    # X and Y share the CVE alias; the CVE itself is not a record
    s = snap_of(snapshot_factory, rec("X-1", ["CVE-9"]), rec("Y-1", ["CVE-9"]), rec("Z-1", ["CVE-8"]))
    assert s.group_of["X-1"] == s.group_of["Y-1"]
    assert s.group_of["Z-1"] != s.group_of["X-1"]
    assert len(s.groups) == 2


def test_related_field_is_ignored(snapshot_factory):
    s = snap_of(snapshot_factory, rec("A-1", related=["B-1"]), rec("B-1"))
    assert len(s.groups) == 2


def test_representative_prefers_lowest_ghsa(snapshot_factory):
    s = snap_of(snapshot_factory, rec("PYSEC-1", ["GHSA-zzzz-zzzz-zzzz", "GHSA-bbbb-bbbb-bbbb", "CVE-1"]))
    assert next(iter(s.groups)) == "GHSA-bbbb-bbbb-bbbb"


def test_representative_without_ghsa_is_lowest_id(snapshot_factory):
    s = snap_of(snapshot_factory, rec("PYSEC-2", ["CVE-5"]), rec("PYSEC-1", ["CVE-5"]))
    assert next(iter(s.groups)) == "CVE-5"


def test_group_ids_sorted_and_stable(snapshot_factory):
    s1 = snap_of(snapshot_factory, rec("B-1", ["A-1"]), rec("C-1", ["A-1"]), rec("A-1"))
    assert next(iter(s1.groups.values())).ids == ("A-1", "B-1", "C-1")
    s2 = snap_of(snapshot_factory, rec("A-1"), rec("C-1", ["A-1"]), rec("B-1", ["A-1"]))
    assert s1.groups == s2.groups


def test_withdrawn_record_does_not_link_groups(snapshot_factory):
    s = snap_of(
        snapshot_factory,
        rec("A-1", ["CVE-1"]),
        rec("B-1", ["CVE-2"]),
        rec("W-1", ["CVE-1", "CVE-2"], withdrawn="2025-01-01T00:00:00Z"),
    )
    assert len(s.groups) == 2
    assert "W-1" not in s.group_of
