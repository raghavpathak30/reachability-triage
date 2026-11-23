from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.pipeline import ANALYSIS_SECONDS, MAX_ADVISORIES, run_scan
from reachability.scan.reasons import ReasonCode
from tests.scan.s1_support import LIB_NEW, LIB_OLD, advisory, lib_files, make_scan

APP_NO_PATH = "from lib.core import helper\n\n\ndef main():\n    return helper()\n\n\nif __name__ == '__main__':\n    main()\n"
APP_CALLS_F = "from lib.core import f\n\n\ndef main():\n    return f(1)\n\n\nif __name__ == '__main__':\n    main()\n"


def _scan(tmp_path, advs, app=APP_NO_PATH, extra=None):
    return make_scan(
        tmp_path,
        app_files={"main.py": app},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=advs,
    )


def _run(s, **kw):
    return run_scan(s["app"], s["snapshot"], s["sha256"], LocalDirFetcher(s["wheels"]), **kw)


def test_constants():
    assert ANALYSIS_SECONDS == 600.0 and MAX_ADVISORIES == 200


def test_201st_advisory_is_scan_budget_and_named(tmp_path):
    advs = [advisory(f"GHSA-aaaa-bbbb-{i:04d}", "lib") for i in range(201)]
    res = _run(_scan(tmp_path, advs))
    by_id = {f.advisory_id: f for f in res.findings}
    assert len(res.findings) == 201
    assert by_id["GHSA-aaaa-bbbb-0200"].reason_code == "SCAN_BUDGET"
    assert by_id["GHSA-aaaa-bbbb-0200"].evidence_level is None
    assert "advisory cap" in by_id["GHSA-aaaa-bbbb-0200"].detail
    assert all(f.reason_code != "SCAN_BUDGET" for i, f in by_id.items() if i != "GHSA-aaaa-bbbb-0200")


def test_clock_past_limit_mid_phase_b_cuts_the_rest(tmp_path):
    advs = [advisory(f"GHSA-aaaa-bbbb-{i:04d}", "lib") for i in range(5)]
    now = [0.0]
    queries = []

    def clock():
        return now[0]

    import reachability.scan.query as q

    real = q.ReachabilityBatch.query

    def counting(*a, **k):
        queries.append(1)
        if len(queries) == 2:
            now[0] += 601.0
        return real(*a, **k)

    q.ReachabilityBatch.query = counting
    try:
        res = _run(_scan(tmp_path, advs), clock=clock)
    finally:
        q.ReachabilityBatch.query = real
    codes = [f.reason_code for f in res.findings]
    assert codes.count("SCAN_BUDGET") == 3 and codes[:2] == ["NO_PATH_FOUND"] * 2
    assert all("analysis time cap" in f.detail for f in res.findings if f.reason_code == "SCAN_BUDGET")


def test_path_proven_survives_budget_exhaustion(tmp_path, monkeypatch):
    advs = [advisory("GHSA-aaaa-bbbb-0001", "lib")]
    s = _scan(tmp_path, advs, app=APP_CALLS_F)
    import reachability.scan.pipeline as pl

    now = [0.0]
    real = pl.exact_lookup

    def spend_after_lookup(*a, **k):
        out = real(*a, **k)
        now[0] += 601.0  # budget spent after phase A, before phase B
        return out

    monkeypatch.setattr(pl, "exact_lookup", spend_after_lookup)
    res = _run(s, clock=lambda: now[0])
    f = res.findings[0]
    assert f.verdict == "reachable" and f.evidence_level == "path_proven" and f.path


def test_proven_advisory_beyond_the_cap_is_kept(tmp_path):
    advs = [advisory(f"GHSA-aaaa-bbbb-{i:04d}", "lib") for i in range(3)]
    res = _run(_scan(tmp_path, advs, app=APP_CALLS_F), max_advisories=1)
    assert [f.verdict for f in res.findings] == ["reachable"] * 3


def test_budget_cut_is_stated_in_text_output(tmp_path):
    from reachability.scan.render import render_text

    advs = [advisory(f"GHSA-aaaa-bbbb-{i:04d}", "lib") for i in range(3)]
    res = _run(_scan(tmp_path, advs), max_advisories=1)
    text = render_text(res)
    assert "analysis budget reached" in text and "GHSA-aaaa-bbbb-0002" in text


def test_reason_aggregation_priority():
    from reachability.scan.reasons import aggregate_reasons

    assert aggregate_reasons([ReasonCode.NO_PATH_FOUND, ReasonCode.LOW_CONFIDENCE_PATH]) is ReasonCode.LOW_CONFIDENCE_PATH
