"""Matcher table: >= 12 rows from real OSV records.

The expectations live in tests/fixtures/app_scan/eval/prereg/matcher_rows.json. They were
written by hand from the raw record data and committed before the matcher existed.
They are never edited to make a test pass.
"""

import json
from pathlib import Path

import pytest

from reachability.scan.advisories import build_report, load_snapshot
from reachability.scan.inventory import build_inventory

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan"
TABLE = json.loads((FIX / "eval" / "prereg" / "matcher_rows.json").read_text())
ROWS = TABLE["rows"]


def run_row(row, snapshot_factory, scan_root):
    path, sha = snapshot_factory([row["record_id"], *row["extra_record_ids"]])
    (scan_root / "requirements.txt").write_text(row["requirement"] + "\n")
    inv = build_inventory(scan_root)
    return inv, build_report(inv, load_snapshot(path, sha))


def test_table_has_at_least_12_rows_and_each_names_a_record_id():
    assert len(ROWS) >= 12
    assert all(r["record_id"] for r in ROWS)
    assert len({r["row"] for r in ROWS}) == len(ROWS)


def test_every_row_record_id_exists_in_fixture_dir():
    for r in ROWS:
        for rid in [r["record_id"], *r["extra_record_ids"]]:
            assert (FIX / "advisories" / f"{rid}.json").is_file(), rid


def test_fixture_records_are_byte_identical_to_recorded_hashes():
    import hashlib

    recorded = {}
    for name in ("p7_copy_step4", "p7_copy_step7", "p7_copy_matcher"):
        for line in (FIX / "eval" / "prereg" / f"{name}.out.txt").read_text().splitlines():
            if " | " in line:
                rid, sha = line.split(" | ")
                recorded[rid] = sha
    for rid, sha in recorded.items():
        assert hashlib.sha256((FIX / "advisories" / f"{rid}.json").read_bytes()).hexdigest() == sha, rid


@pytest.mark.parametrize("row", ROWS, ids=[r["row"] for r in ROWS])
def test_matcher_row(row, snapshot_factory, scan_root):
    inv, report = run_row(row, snapshot_factory, scan_root)
    exp = row["expect"]
    entry = inv.entries[0]
    assert entry.status.value == exp["inventory_status"]
    if exp["malicious_signal"]:
        assert report.findings == ()
        assert len(report.malicious) == 1
        m = report.malicious[0]
        assert m.signal == exp["malicious_signal"]
        assert m.id == row["record_id"]
        assert (m.dist, m.version) == (row["dist"], row["installed"])
        return
    assert report.malicious == ()
    if not exp["matched"]:
        assert report.findings == ()
        return
    assert len(report.findings) == 1
    f = report.findings[0]
    assert list(f.ids) == exp["finding_ids"]
    assert f.fixed_in == exp["fixed_in"]
    assert (f.dist, f.version) == (row["dist"], row["installed"])
    for needle in exp["notes_contain"]:
        assert any(needle in n for n in f.notes), (needle, f.notes)


def test_row_r8_withdrawn_record_is_counted_and_has_no_group(snapshot_factory):
    row = next(r for r in ROWS if r["row"] == "R8")
    path, sha = snapshot_factory([row["record_id"]])
    snap = load_snapshot(path, sha)
    assert snap.withdrawn_excluded == 1 and snap.records == {} and snap.groups == {}


def test_row_r9_collapses_to_one_group(snapshot_factory):
    row = next(r for r in ROWS if r["row"] == "R9")
    path, sha = snapshot_factory([row["record_id"], *row["extra_record_ids"]])
    snap = load_snapshot(path, sha)
    assert len(snap.groups) == 1


def test_row_r15_counts_ignored_range_types(snapshot_factory):
    row = next(r for r in ROWS if r["row"] == "R15")
    path, sha = snapshot_factory([row["record_id"]])
    assert load_snapshot(path, sha).ignored_range_types == {"GIT": 1}
