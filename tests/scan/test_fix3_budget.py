from reachability.scan.fetch import FetchBudget, LocalDirFetcher, fetch_dists
from reachability.scan.pipeline import run_scan
from tests.scan.s1_support import APP_CALLS_F, LIB_NEW, LIB_OLD, advisory, lib_files, make_scan, simple_lib_scan


def _run(s, fetcher, **kw):
    return run_scan(s["app"], s["snapshot"], s["sha256"], fetcher, **kw)


def test_installed_wheel_cut_by_fetch_budget_is_scan_budget(tmp_path):
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins={"aaa": "1.0", "lib": "1.0"},
        wheels={
            ("aaa", "1.0"): lib_files(LIB_OLD, pkg="aaa"),
            ("aaa", "2.0"): lib_files(LIB_NEW, pkg="aaa"),
            ("lib", "1.0"): lib_files(LIB_OLD),
            ("lib", "2.0"): lib_files(LIB_NEW),
        },
        advisories=[advisory("GHSA-aaaa-aaaa-aaaa", "aaa"), advisory("GHSA-bbbb-bbbb-bbbb", "lib")],
    )
    now = [0.0]

    class Slow(LocalDirFetcher):
        def download(self, *a, **k):
            now[0] += 700.0
            super().download(*a, **k)

    res = _run(s, Slow(s["wheels"]), clock=lambda: now[0])
    by = {f.dist: f for f in res.findings}
    assert by["lib"].reason_code == "SCAN_BUDGET"
    assert by["lib"].detail.startswith("installed wheel lib==1.0 not fetched: total fetch time cap reached")
    assert any(g.kind == "budget" and g.dist == "lib" for g in res.gaps)
