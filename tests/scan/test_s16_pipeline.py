import json
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


def _cli(setup, capsys, *extra):
    rc = main(["scan", str(setup["app"]), "--snapshot", str(setup["snapshot"]), "--snapshot-sha256", setup["sha256"],
               "--wheel-dir", str(setup["wheels"]), "--format", "json", *extra])
    return rc, json.loads(capsys.readouterr().out)


def test_fetch_failure_gap_no_high_exit_0(tmp_path, capsys):
    setup = simple_lib_scan(tmp_path)
    shutil.rmtree(setup["wheels"] / "fix-commits")
    rc, d = _cli(setup, capsys)
    assert rc == 0
    (f,) = d["findings"]
    assert f["verdict"] == "unknown" and f["reason_code"] == "PATH_TO_UNCONFIRMED_TARGET"
    assert all(t["confidence"] == "medium" for t in f["targets"])
    assert any(g["kind"] == "fix_commit_unavailable" and g["detail"].startswith(f"owner/repo@{SHA}: ")
               for g in d["gaps"])


def test_failure_only_removes_high(tmp_path):
    ok = scan(simple_lib_scan(tmp_path / "a"))
    bad = scan(simple_lib_scan(tmp_path / "b", fix=False))
    high = lambda r: {t.node_id for f in r.findings for t in f.targets if t.confidence == "high"}  # noqa: E731
    assert high(bad) == set() and high(ok) != set()


def test_diff_budget_exceeded_is_gap(tmp_path, monkeypatch):
    import reachability.scan.fixcommit as fc

    monkeypatch.setattr(fc, "MAX_DIFFS", 0)
    setup = simple_lib_scan(tmp_path)
    res = run_scan(setup["app"], setup["snapshot"], setup["sha256"], LocalDirFetcher(setup["wheels"]),
                   diff_fetcher=LocalDiffDir(setup["wheels"] / "fix-commits"))
    assert res.stats["fix_diffs_unavailable"] == {"diff budget: 0": 1}
    (g,) = [g for g in res.gaps if g.kind == "fix_commit_unavailable"]
    assert g.detail == f"owner/repo@{SHA}: diff budget: 0"
    assert res.findings[0].verdict == "unknown"


def test_one_bad_commit_does_not_hide_good_one(tmp_path):
    adv = advisory("GHSA-aaaa-bbbb-cccc", "lib")
    adv["references"].append({"type": "WEB", "url": f"https://github.com/owner/repo/commit/{SHA_B}"})
    setup = make_scan(
        tmp_path, app_files={"main.py": APP_CALLS_F}, pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=[adv],
    )
    res = scan(setup)
    (f,) = res.findings
    assert f.evidence_level == "path_proven"
    assert [g.detail for g in res.gaps if g.kind == "fix_commit_unavailable"] == [
        f"owner/repo@{SHA_B}: no local diff"
    ]


def test_diff_fetch_outside_analysis_clock(tmp_path):
    ticks = []

    class Fetcher:
        def fetch_diff(self, owner, repo, sha):
            ticks.append("fetch")
            return LIB_F_DIFF

    t = [0.0]

    def clock():
        t[0] += 1.0
        return t[0]

    setup = simple_lib_scan(tmp_path)
    res = run_scan(setup["app"], setup["snapshot"], setup["sha256"], LocalDirFetcher(setup["wheels"]),
                   diff_fetcher=Fetcher(), clock=clock, analysis_seconds=1e9)
    assert ticks == ["fetch"] and res.timings["fix_commit_seconds"] > 0
    assert res.findings[0].evidence_level == "path_proven"
