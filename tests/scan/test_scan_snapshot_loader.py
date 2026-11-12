import datetime
import hashlib
import zipfile

import pytest

from reachability.scan.advisories import (
    SnapshotError,
    SnapshotFilenameError,
    SnapshotHashMismatchError,
    load_snapshot,
)
from reachability.scan.inventory import ScanError

REC = {"id": "SYNTH-1", "aliases": [], "affected": []}


def test_wrong_sha256_refused(snapshot_factory):
    path, sha = snapshot_factory([REC])
    with pytest.raises(SnapshotHashMismatchError):
        load_snapshot(path, "0" * 64)
    assert load_snapshot(path, sha.upper()).sha256 == sha


def test_wrong_sha256_refused_before_any_member_is_read(snapshot_factory):
    path, _ = snapshot_factory([REC, ("BAD.json", b"{ not json")])
    with pytest.raises(SnapshotHashMismatchError):
        load_snapshot(path, "0" * 64)


@pytest.mark.parametrize(
    "name",
    ["PyPI-all.zip", "osv-pypi-2026-13-01.zip", "osv-pypi-2026-11-12.zip.bak", "OSV-PYPI-2026-11-12.zip",
     "osv-pypi-2026-02-30.zip", "osv-pypi-26-11-12.zip", "osv-pypi-2026-11-12 .zip"],
)
def test_filename_must_match_osv_pypi_date_pattern(snapshot_factory, name):
    path, sha = snapshot_factory([REC], name=name)
    with pytest.raises(SnapshotFilenameError):
        load_snapshot(path, sha)


def test_date_taken_from_filename(snapshot_factory):
    path, sha = snapshot_factory([REC], name="osv-pypi-2025-01-31.zip")
    assert load_snapshot(path, sha).date == datetime.date(2025, 1, 31)


def test_errors_are_scan_errors():
    assert issubclass(SnapshotHashMismatchError, SnapshotError)
    assert issubclass(SnapshotError, ScanError)
    assert issubclass(SnapshotFilenameError, SnapshotError)


def test_withdrawn_records_excluded_and_counted(snapshot_factory):
    wd = {"id": "SYNTH-W", "aliases": ["SYNTH-1"], "withdrawn": "2025-01-01T00:00:00Z", "affected": []}
    path, sha = snapshot_factory([REC, wd])
    snap = load_snapshot(path, sha)
    assert snap.records_loaded == 1
    assert snap.withdrawn_excluded == 1
    assert "SYNTH-W" not in snap.records


def test_non_json_member_raises_snapshot_error(snapshot_factory):
    path, sha = snapshot_factory([("x.json", b"{ not json")])
    with pytest.raises(SnapshotError, match="x.json"):
        load_snapshot(path, sha)


def test_record_without_id_raises_snapshot_error(snapshot_factory):
    path, sha = snapshot_factory([("x.json", b'{"aliases": []}')])
    with pytest.raises(SnapshotError, match="x.json"):
        load_snapshot(path, sha)


def test_not_a_zip_raises_snapshot_error(tmp_path):
    p = tmp_path / "osv-pypi-2026-11-12.zip"
    p.write_bytes(b"this is not a zip")
    with pytest.raises(SnapshotError):
        load_snapshot(p, hashlib.sha256(p.read_bytes()).hexdigest())


def test_zip_members_never_extracted(tmp_path, snapshot_factory):
    path, sha = snapshot_factory([("../evil.json", b'{"id": "EVIL-1", "aliases": []}'), REC])
    before = sorted(p.name for p in tmp_path.parent.iterdir())
    snap = load_snapshot(path, sha)
    assert sorted(p.name for p in tmp_path.parent.iterdir()) == before
    assert not (tmp_path.parent / "evil.json").exists()
    assert "EVIL-1" in snap.records  # parsed in memory by id, never written


def test_non_json_named_members_are_ignored(snapshot_factory):
    path, sha = snapshot_factory([REC, ("README.txt", b"hello")])
    assert load_snapshot(path, sha).records_loaded == 1


def test_read_once_hash_and_zip_come_from_same_bytes(snapshot_factory, monkeypatch):
    path, sha = snapshot_factory([REC])
    reads = []
    real = type(path).read_bytes

    def counting(self):
        reads.append(self)
        return real(self)

    monkeypatch.setattr(type(path), "read_bytes", counting)
    load_snapshot(path, sha)
    assert len(reads) == 1


def test_zip_path_not_reopened(snapshot_factory, monkeypatch):
    path, sha = snapshot_factory([REC])
    monkeypatch.setattr(zipfile.ZipFile, "__init__", _only_bytesio(zipfile.ZipFile.__init__))
    load_snapshot(path, sha)


def _only_bytesio(real_init):
    import io

    def init(self, file, *a, **k):
        assert isinstance(file, io.BytesIO), "ZipFile must be opened from the hashed bytes"
        return real_init(self, file, *a, **k)

    return init
