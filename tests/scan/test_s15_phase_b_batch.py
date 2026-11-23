import reachability.index.reachability as engine
import reachability.scan.query as q
from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.pipeline import run_scan
from tests.scan.s1_support import advisory, lib_files, make_scan

OLD = "def f():\n    return 1\n\n\ndef g():\n    return 1\n\n\ndef helper():\n    return 0\n"
NEW = OLD.replace("return 1", "return 2")
APP_NO_PATH = "from lib.core import helper\n\n\ndef main():\n    return helper()\n\n\nif __name__ == '__main__':\n    main()\n"


def _scan(tmp_path, n):
    return make_scan(
        tmp_path,
        app_files={"main.py": APP_NO_PATH},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(OLD), ("lib", "2.0"): lib_files(NEW)},
        advisories=[advisory(f"GHSA-aaaa-bbbb-{i:04d}", "lib") for i in range(n)],
    )


def _run(s, **kw):
    return run_scan(s["app"], s["snapshot"], s["sha256"], LocalDirFetcher(s["wheels"]), **kw)


def test_phase_b_builds_batch_once_for_many_advisories(tmp_path, monkeypatch):
    counts = {"init": 0, "d3": 0}
    real_init = engine.ReachabilityBatch.__init__
    real_d3 = engine.collect_load_referenced_names

    def init(self, *a, **k):
        counts["init"] += 1
        return real_init(self, *a, **k)

    def d3(*a, **k):
        counts["d3"] += 1
        return real_d3(*a, **k)

    monkeypatch.setattr(engine.ReachabilityBatch, "__init__", init)
    monkeypatch.setattr(engine, "collect_load_referenced_names", d3)
    res = _run(_scan(tmp_path, 6))
    assert [f.reason_code for f in res.findings] == ["NO_PATH_FOUND"] * 6
    assert res.stats["queries"] == 12  # two changed targets per advisory
    assert counts == {"init": 1, "d3": 1}
    assert "batch_setup_seconds" in res.stats


def test_budget_between_targets_gives_scan_budget_and_partial_advisory_too(tmp_path, monkeypatch):
    now = [0.0]
    seen = []
    real = q.ReachabilityBatch.query

    def counting(self, *a, **k):
        seen.append(a)
        now[0] += 601.0
        return real(self, *a, **k)

    monkeypatch.setattr(q.ReachabilityBatch, "query", counting)
    res = _run(_scan(tmp_path, 2), clock=lambda: now[0])
    # first advisory: one of its two targets ran, then the clock ran out mid-advisory
    assert len(seen) == 1
    assert [f.reason_code for f in res.findings] == ["SCAN_BUDGET", "SCAN_BUDGET"]
    assert all("analysis time cap" in f.detail for f in res.findings)


def test_advisory_cap_still_applies(tmp_path):
    res = _run(_scan(tmp_path, 5), max_advisories=2)
    codes = [f.reason_code for f in res.findings]
    assert codes.count("NO_PATH_FOUND") == 2 and codes.count("SCAN_BUDGET") == 3
    assert all("advisory cap" in f.detail for f in res.findings if f.reason_code == "SCAN_BUDGET")
