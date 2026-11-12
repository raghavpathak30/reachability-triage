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
