import ast

import pytest

from reachability.scan import wheel_extract as we
from reachability.scan.fetch import FetchBudget, LocalDirFetcher, fetch_dists
from reachability.scan.pipeline import run_scan
from tests.scan.s1_support import simple_lib_scan


def _run(s, fetcher=None, **kw):
    return run_scan(s["app"], s["snapshot"], s["sha256"], fetcher or LocalDirFetcher(s["wheels"]), **kw)


def _kinds(res):
    return [(g.kind, g.dist, g.detail) for g in res.gaps]


def test_baseline_is_path_proven(tmp_path):
    res = _run(simple_lib_scan(tmp_path))
    assert res.findings[0].verdict == "reachable" and res.gaps == [] and res.closure_complete


def test_recursion_error_file_is_a_gap_and_scan_continues(tmp_path):
    bad = "x = " + "+".join(["1"] * 100000) + "\n"
    s = simple_lib_scan(tmp_path, extra_old={"lib/deep.py": bad}, extra_new={"lib/deep.py": bad})
    res = _run(s)
    gaps = [g for g in res.gaps if g.kind == "unparsed_file"]
    assert gaps and "lib/deep.py" in gaps[0].detail and "RecursionError" in gaps[0].detail
    assert res.closure_complete is False
    assert res.findings[0].verdict == "reachable"


def test_nul_byte_file_is_a_gap(tmp_path):
    s = simple_lib_scan(tmp_path, extra_old={"lib/nul.py": "x = 1\x00\n"})
    res = _run(s)
    gaps = [g for g in res.gaps if g.kind == "unparsed_file"]
    assert gaps and ("ValueError" in gaps[0].detail or "SyntaxError" in gaps[0].detail)
    assert res.findings[0].verdict == "reachable"


def test_memory_error_file_is_a_gap(tmp_path, monkeypatch):
    real = ast.parse

    def parse(source, filename="<unknown>", *a, **k):
        if str(filename).endswith("mem.py"):
            raise MemoryError
        return real(source, filename, *a, **k)

    monkeypatch.setattr(we.ast, "parse", parse)
    s = simple_lib_scan(tmp_path, extra_old={"lib/mem.py": "x = 1\n"})
    res = _run(s)
    assert any("MemoryError" in g.detail for g in res.gaps if g.kind == "unparsed_file")


def test_app_file_that_cannot_parse_is_a_gap(tmp_path):
    s = simple_lib_scan(tmp_path, app_files={"main.py": "from lib.core import f\n\n\ndef main():\n    return f(1)\n", "bad.py": "def (:\n"})
    res = _run(s)
    assert any(g.kind == "unparsed_file" and g.detail.startswith("app/bad.py") for g in res.gaps)


class RaisingFetcher(LocalDirFetcher):
    def get_json(self, url):
        if "/lib/" in url:
            raise RuntimeError("boom")
        return super().get_json(url)


def test_raising_fetcher_is_dist_error_gap(tmp_path):
    s = simple_lib_scan(tmp_path)
    res = _run(s, RaisingFetcher(s["wheels"]))
    assert any(g.kind == "dist_error" and g.dist == "lib" and "RuntimeError" in g.detail for g in res.gaps)
    assert res.closure_complete is False
    assert res.findings[0].verdict == "unknown" and res.findings[0].reason_code == "NO_CANDIDATE"


def test_fixed_wheel_fetch_error_does_not_fail_scan(tmp_path):
    s = simple_lib_scan(tmp_path)

    class F(LocalDirFetcher):
        def get_json(self, url):
            if "/lib/2.0/" in url:
                raise OSError("net")
            return super().get_json(url)

    res = _run(s, F(s["wheels"]))
    assert any(g.kind == "dist_error" for g in res.gaps)
    assert res.findings[0].reason_code == "NO_CANDIDATE"


def test_dist_count_cap_cuts_the_rest_by_name(tmp_path):
    s = simple_lib_scan(tmp_path)
    pairs = [("lib", "1.0"), ("lib", "2.0"), ("zzz", "1.0")]
    res = fetch_dists(pairs, LocalDirFetcher(s["wheels"]), tmp_path / "st", budget=FetchBudget(max_dists=2))
    assert res.dists[("zzz", "1.0")].status == "budget"
    cut = [g for g in res.gaps if g.kind == "budget"]
    assert len(cut) == 1 and cut[0].dist == "zzz"


def test_total_fetch_seconds_cap_with_injected_clock(tmp_path):
    s = simple_lib_scan(tmp_path)
    now = [0.0]

    class SlowFetcher(LocalDirFetcher):
        def download(self, *a, **k):
            now[0] += 400.0
            super().download(*a, **k)

    budget = FetchBudget(total_seconds=600.0, clock=lambda: now[0])
    pairs = [("lib", "1.0"), ("lib", "2.0"), ("zzz", "1.0")]
    res = fetch_dists(pairs, SlowFetcher(s["wheels"]), tmp_path / "st", budget=budget)
    assert res.dists[("lib", "1.0")].status == "fetched" and res.dists[("lib", "2.0")].status == "fetched"
    assert res.dists[("zzz", "1.0")].status == "budget"
    assert "600" in res.dists[("zzz", "1.0")].reason
