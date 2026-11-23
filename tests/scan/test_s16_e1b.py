import hashlib
import json
import shutil
import zipfile

from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.pipeline import run_scan
from tests.scan.s1_support import REPO_ROOT, mat

SHA = "eb562e6462ee46b1904e50b02ce00a858cdeb200"
E1B = REPO_ROOT / "tests" / "fixtures" / "app_scan" / "eval" / "E1b"
LABEL = json.loads((E1B / "label.json").read_text())


def _scan(r):
    return run_scan(r["app"], r["snapshot"], r["sha256"], LocalDirFetcher(r["wheels"]))


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_e1b_path_proven_to_open_process(tmp_path):
    res = _scan(mat.materialize_e1b(tmp_path))
    (f,) = res.findings
    assert f.verdict == "reachable" and f.evidence_level == "path_proven" and f.reason_code is None
    assert f.path[-1].callee == LABEL["true_symbols"][0]
    top = next(t for t in f.targets if t.node_id == LABEL["true_symbols"][0])
    assert top.confidence == "high" and top.in_fix_commit and list(top.fix_commits) == [SHA]
    assert (f.version, f.fixed_in) == ("4.14.0", "4.14.2")
    assert res.gaps == []


def _with_record(tmp_path, mutate):
    r = mat.materialize_e1b(tmp_path)
    rec = json.loads((E1B / "advisories" / "GHSA-3w57-8xmc-8v26.json").read_text())
    mutate(rec)
    snap = tmp_path / "osv-pypi-2026-11-12.zip"
    snap.unlink()
    with zipfile.ZipFile(snap, "w") as z:
        z.writestr("GHSA-3w57-8xmc-8v26.json", json.dumps(rec))
    r["sha256"] = _sha(snap)
    return r


def test_e1b_finds_diff_only_through_record_url(tmp_path):
    def drop(rec):
        rec["references"] = [x for x in rec["references"] if "/commit/" not in x["url"]]

    r = _with_record(tmp_path, drop)
    assert (r["wheels"] / "fix-commits" / "agronholm" / "anyio" / f"{SHA}.diff").is_file()
    (f,) = _scan(r).findings
    assert f.verdict == "unknown" and f.reason_code == "PATH_TO_UNCONFIRMED_TARGET"
    assert all(t.confidence == "medium" for t in f.targets)

    def other(rec):
        for x in rec["references"]:
            x["url"] = x["url"].replace(SHA, "0" * 40)

    shutil.rmtree(tmp_path / "again", ignore_errors=True)
    (f2,) = _scan(_with_record(tmp_path / "again", other)).findings
    assert f2.evidence_level is None and all(t.confidence == "medium" for t in f2.targets)


def test_e1b_fixture_hashes():
    for name, digest in LABEL["wheel_sha256"].items():
        assert _sha(E1B / "wheels" / name) == digest
    for rel, digest in LABEL["diff_sha256"].items():
        assert _sha(E1B / "wheels" / "fix-commits" / rel) == digest
    assert LABEL["wheel_sha256"]["anyio-4.14.0-py3-none-any.whl"] in (E1B / "requirements.txt").read_text()


def test_e1b_record_is_verbatim_osv_member():
    import pytest

    snap = __import__("pathlib").Path.home() / "osv-snapshots" / "osv-pypi-2026-11-12.zip"
    if not snap.is_file():
        pytest.skip("local OSV snapshot not present")
    with zipfile.ZipFile(snap) as z:
        assert z.read("GHSA-3w57-8xmc-8v26.json") == (E1B / "advisories" / "GHSA-3w57-8xmc-8v26.json").read_bytes()
