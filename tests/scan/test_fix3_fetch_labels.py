from urllib.error import URLError

from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.pipeline import run_scan
from tests.scan.s1_support import (
    APP_CALLS_F,
    LIB_OLD,
    advisory,
    lib_files,
    make_scan,
    simple_lib_scan,
)


def _run(s, fetcher, **kw):
    return run_scan(s["app"], s["snapshot"], s["sha256"], fetcher, **kw)


def _failing(marker):
    class F(LocalDirFetcher):
        def get_json(self, url):
            if marker in url:
                raise URLError("unreachable")
            return super().get_json(url)

    return F


def test_installed_wheel_transport_error_is_no_candidate_naming_the_exception(tmp_path):
    s = simple_lib_scan(tmp_path)
    res = _run(s, _failing("/lib/1.0/")(s["wheels"]))
    f = res.findings[0]
    assert f.reason_code == "NO_CANDIDATE"
    assert f.detail == "fetch failed: URLError during installed wheel fetch"
    assert any(g.kind == "dist_error" and g.dist == "lib" and "URLError" in g.detail for g in res.gaps)


def test_fix_wheel_transport_error_is_no_candidate_naming_the_exception(tmp_path):
    s = simple_lib_scan(tmp_path)
    res = _run(s, _failing("/lib/2.0/")(s["wheels"]))
    f = res.findings[0]
    assert f.reason_code == "NO_CANDIDATE"
    assert f.detail == "fetch failed: URLError during fix wheel fetch (lib==2.0)"
    assert any(g.kind == "dist_error" and g.dist == "lib" and "URLError" in g.detail for g in res.gaps)


def test_genuine_no_matching_fix_wheel_stays_no_wheel_for_fix(tmp_path):
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD)},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    res = _run(s, LocalDirFetcher(s["wheels"]))
    assert res.findings[0].reason_code == "NO_WHEEL_FOR_FIX"
