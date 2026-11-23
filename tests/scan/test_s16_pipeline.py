import shutil

from reachability.cli import main
from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.fixcommit import LocalDiffDir
from reachability.scan.pipeline import run_scan
from tests.scan.s1_support import (
    APP_CALLS_F, FIX_KEY, FIXCOMMITS, LIB_F_DIFF, LIB_NEW, LIB_OLD, SHA, advisory, lib_files, make_scan,
    simple_lib_scan, with_fix_commit,
)

SHA_B = "b" * 40
NEW_BOTH = LIB_NEW.replace("return 0", "return 1")
HELPER_DIFF = (FIXCOMMITS / "lib_helper.diff").read_text()


def scan(setup, **kw):
    return run_scan(
        setup["app"], setup["snapshot"], setup["sha256"], LocalDirFetcher(setup["wheels"]),
        diff_fetcher=LocalDiffDir(setup["wheels"] / "fix-commits"), **kw,
    )


def sibling_setup(tmp_path):
    a = with_fix_commit(advisory("GHSA-aaaa-aaaa-aaaa", "lib"), SHA)
    b = with_fix_commit(advisory("GHSA-bbbb-bbbb-bbbb", "lib"), SHA_B)
    return make_scan(
        tmp_path, app_files={"main.py": APP_CALLS_F}, pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(NEW_BOTH)},
        advisories=[a, b],
        fix_diffs={FIX_KEY: LIB_F_DIFF, f"owner/repo/{SHA_B}": HELPER_DIFF},
    )


def test_sibling_case(tmp_path):
    res = scan(sibling_setup(tmp_path))
    by = {f.advisory_id: f for f in res.findings}
    a, b = by["GHSA-aaaa-aaaa-aaaa"], by["GHSA-bbbb-bbbb-bbbb"]
    assert a.verdict == "reachable" and a.evidence_level == "path_proven"
    f_a = next(t for t in a.targets if t.node_id.endswith(":f") or "f@" in t.node_id)
    assert f_a.confidence == "high" and f_a.in_fix_commit and f_a.fix_commits == (SHA,)
    assert b.verdict == "unknown" and b.reason_code == "PATH_TO_UNCONFIRMED_TARGET"
    f_b = next(t for t in b.targets if "f@" in t.node_id or t.node_id.endswith(":f"))
    assert f_b.confidence == "medium" and not f_b.in_fix_commit and b.partial_path
    d = f_a.to_dict()
    assert d["fix_commits"] == [SHA] and d["in_fix_commit"] is True


def test_stats_counters_present(tmp_path):
    res = scan(sibling_setup(tmp_path))
    s = res.stats
    assert s["fix_commit_advisories"] == 2 and s["fix_diffs_fetched"] == 2
    assert s["fix_hunks_attributed"] == 2 and s["fix_advisories_attributed"] == 2
    assert "fix_commit_seconds" in res.timings


def test_stub_fix_changed_end_to_end(tmp_path):
    res = scan(simple_lib_scan(tmp_path))
    (f,) = res.findings
    assert f.evidence_level == "path_proven"


def test_commit_failure_never_raises(tmp_path):
    setup = simple_lib_scan(tmp_path)
    shutil.rmtree(setup["wheels"] / "fix-commits")
    res = run_scan(setup["app"], setup["snapshot"], setup["sha256"], LocalDirFetcher(setup["wheels"]),
                   diff_fetcher=None)
    (f,) = res.findings
    assert f.verdict == "unknown" and f.reason_code == "PATH_TO_UNCONFIRMED_TARGET"
    gaps = [g for g in res.gaps if g.kind == "fix_commit_unavailable"]
    assert len(gaps) == 1 and gaps[0].detail == f"owner/repo@{SHA}: no local diff"
    assert all(t.confidence == "medium" for t in f.targets)
