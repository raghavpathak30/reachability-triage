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


import json  # noqa: E402
from pathlib import Path  # noqa: E402

from reachability.scan.advisories import build_report  # noqa: E402
from reachability.scan.inventory import build_inventory  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan"


def aff(name, versions):
    return {"package": {"name": name, "ecosystem": "PyPI"}, "versions": versions}


def signal(snapshot_factory, *records):
    s = snap_of(snapshot_factory, *records)
    return {g.group_id: g.malicious_signal for g in s.groups.values()}


def test_mal_prefix_on_id(snapshot_factory):
    assert signal(snapshot_factory, rec("MAL-2024-1")) == {"MAL-2024-1": "mal_prefix"}


def test_mal_prefix_on_alias_only(snapshot_factory):
    assert list(signal(snapshot_factory, rec("GHSA-aaaa-aaaa-aaaa", ["MAL-2024-9"])).values()) == ["mal_prefix"]


def test_cwe_506_when_no_mal_signal(snapshot_factory):
    r = rec("GHSA-aaaa-aaaa-aaaa", database_specific={"cwe_ids": ["CWE-79", "CWE-506"]})
    assert list(signal(snapshot_factory, r).values()) == ["cwe_506"]


def test_mal_prefix_wins_over_cwe_506(snapshot_factory):
    r = rec("MAL-2024-1", database_specific={"cwe_ids": ["CWE-506"]})
    assert list(signal(snapshot_factory, r).values()) == ["mal_prefix"]


def test_no_signal_gives_none(snapshot_factory):
    assert list(signal(snapshot_factory, rec("GHSA-aaaa-aaaa-aaaa", ["CVE-1"], database_specific={"cwe_ids": ["CWE-79"]})).values()) == [None]


def test_no_summary_text_matching(snapshot_factory):
    r = rec("GHSA-aaaa-aaaa-aaaa", summary="Malicious code in foo", details="malware, backdoor, trojan")
    assert list(signal(snapshot_factory, r).values()) == [None]


def test_signal_is_group_level_a_malicious_member_taints_the_group(snapshot_factory):
    sig = signal(
        snapshot_factory,
        rec("GHSA-aaaa-aaaa-aaaa", ["CVE-1"], database_specific={"cwe_ids": ["CWE-506"]}),
        rec("PYSEC-1", ["CVE-1"]),
    )
    assert sig == {"GHSA-aaaa-aaaa-aaaa": "cwe_506"}


def run(snapshot_factory, scan_root, requirements, *records):
    path, sha = snapshot_factory(list(records))
    (scan_root / "requirements.txt").write_text(requirements)
    return build_report(build_inventory(scan_root), load_snapshot(path, sha))


def test_unrelated_record_in_malicious_group_does_not_leak_into_findings(snapshot_factory, scan_root):
    r = run(
        snapshot_factory,
        scan_root,
        "foo==1.0\n",
        rec("GHSA-aaaa-aaaa-aaaa", ["CVE-1"], database_specific={"cwe_ids": ["CWE-506"]}, affected=[aff("bar", ["1"])]),
        {"id": "PYSEC-1", "aliases": ["CVE-1"], "affected": [aff("foo", ["1.0"])]},
    )
    assert r.findings == ()
    assert [(m.id, m.dist, m.signal) for m in r.malicious] == [("PYSEC-1", "foo", "cwe_506")]


def test_malicious_entry_prefers_the_mal_id(snapshot_factory, scan_root):
    r = run(
        snapshot_factory,
        scan_root,
        "foo==1.0\n",
        {"id": "GHSA-aaaa-aaaa-aaaa", "aliases": ["MAL-2024-1"], "affected": [aff("foo", ["1.0"])]},
        {"id": "MAL-2024-1", "aliases": [], "affected": [aff("foo", ["1.0"])]},
    )
    assert len(r.malicious) == 1 and r.malicious[0].id == "MAL-2024-1"
    assert r.malicious[0].group_id == "GHSA-aaaa-aaaa-aaaa"


def test_malicious_never_in_findings_over_real_records(snapshot_factory, scan_root):
    cases = [("GHSA-jxr6-qrxx-2ph2", "num2words==0.5.15"), ("GHSA-4f84-67cv-qrv3", "dydx-v4-client==1.1.6"),
             ("MAL-2023-10", "security-util-py==0.0.6")]
    for rid, req in cases:
        r = run(snapshot_factory, scan_root, req + "\n", rid)
        assert r.findings == (), rid
        assert len(r.malicious) == 1, rid


def test_m1_label_record_is_malicious_with_label_dist_and_version(snapshot_factory):
    label = json.loads((FIX / "eval" / "M1" / "label.json").read_text())
    path, sha = snapshot_factory(label["malicious"])
    inv = build_inventory(FIX / "lockfiles" / "M1")
    report = build_report(inv, load_snapshot(path, sha))
    (dist, spec), = label["inventory"].items()
    assert [(m.id, m.dist, m.version, m.signal) for m in report.malicious] == [
        (label["malicious"][0], dist, spec["version"], "mal_prefix")
    ]
    assert report.findings == ()


def test_malicious_entry_is_per_dist_version_group(snapshot_factory, scan_root):
    r = run(
        snapshot_factory,
        scan_root,
        "foo==1.0\nfoo==2.0\n",
        {"id": "MAL-2024-1", "aliases": [], "affected": [aff("foo", ["1.0", "2.0"])]},
    )
    assert sorted((m.version, m.signal) for m in r.malicious) == [("1.0", "mal_prefix"), ("2.0", "mal_prefix")]
