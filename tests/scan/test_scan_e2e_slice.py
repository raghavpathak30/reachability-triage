from reachability.scan.inventory import Status, build_inventory


def test_slice_inventory_one_pinned_line(scan_root):
    (scan_root / "requirements.txt").write_text("Jinja2==3.1.2\n")
    inv = build_inventory(scan_root)
    assert inv.lockfile == "requirements.txt"
    assert len(inv.entries) == 1
    e = inv.entries[0]
    assert (e.dist, e.version, e.status) == ("jinja2", "3.1.2", Status.PINNED)
    assert e.source_file == "requirements.txt"
    assert e.line == 1
    assert inv.hint is None


def test_slice_snapshot_loader_hash_and_date(snapshot_factory):
    import datetime
    import hashlib

    from reachability.scan.advisories import load_snapshot

    path, sha = snapshot_factory(
        [
            {"id": "SYNTH-1", "aliases": [], "affected": []},
            {"id": "SYNTH-2", "aliases": [], "affected": []},
        ]
    )
    snap = load_snapshot(path, sha)
    assert snap.date == datetime.date(2026, 11, 12)
    assert snap.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert snap.records_loaded == 2


def test_slice_match_real_record_versions_only(snapshot_factory, scan_root):
    from reachability.scan.advisories import NO_FIX, build_report, load_snapshot

    # P1: GHSA-9p73-x86v-jw57, lollms, versions ["9.6"], no ranges.
    path, sha = snapshot_factory(["GHSA-9p73-x86v-jw57"])
    snap = load_snapshot(path, sha)

    (scan_root / "requirements.txt").write_text("lollms==9.6\n")
    report = build_report(build_inventory(scan_root), snap)
    assert len(report.findings) == 1
    f = report.findings[0]
    assert "GHSA-9p73-x86v-jw57" in f.ids
    assert (f.dist, f.version, f.fixed_in) == ("lollms", "9.6", NO_FIX)

    (scan_root / "requirements.txt").write_text("lollms==9.6.99\n")
    report = build_report(build_inventory(scan_root), snap)
    assert report.findings == ()


def test_slice_cli_section_order(snapshot_factory, scan_root):
    import subprocess
    import sys
    from pathlib import Path

    path, sha = snapshot_factory(["GHSA-9p73-x86v-jw57"])
    (scan_root / "requirements.txt").write_text("lollms==9.6\n")
    script = Path(__file__).resolve().parents[2] / "scripts" / "scan_inventory.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(scan_root), "--snapshot", str(path), "--snapshot-sha256", sha],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.splitlines()
    idx = [
        next(i for i, line in enumerate(out) if line.startswith(h))
        for h in ("advisories as of", "== Malicious packages ==", "== Inventory ==", "== Skipped and ignored ==")
    ]
    assert idx == sorted(idx)
    assert "GHSA-9p73-x86v-jw57" in "\n".join(out[idx[2] :])
