import pytest

import reachability.scan.pipeline as pl
from reachability.scan.fetch import FetchBudget, LocalDirFetcher
from reachability.scan.pipeline import run_scan
from reachability.scan.render import render_json, render_text
from tests.scan.s1_support import LIB_NEW, LIB_OLD, advisory, lib_files, make_scan

APP_CALLS_F = "from lib.core import f\n\n\ndef main():\n    return f(1)\n\n\nif __name__ == '__main__':\n    main()\n"
APP_NO_PATH = "from lib.core import helper\n\n\ndef main():\n    return helper()\n\n\nif __name__ == '__main__':\n    main()\n"


def _setup(tmp_path, advs, app=APP_CALLS_F):
    return make_scan(
        tmp_path,
        app_files={"main.py": app},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=advs,
    )


def _run(s, **kw):
    return run_scan(s["app"], s["snapshot"], s["sha256"], LocalDirFetcher(s["wheels"]), **kw)


def _advs(n):
    return [advisory(f"GHSA-aaaa-bbbb-{i:04d}", "lib") for i in range(n)]


# ---- F1
def test_f1_exact_lookup_raising_for_one_advisory_others_analysed(tmp_path, monkeypatch):
    real = pl.exact_lookup
    calls = []

    def flaky(ctx, targets):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(ctx, targets)

    monkeypatch.setattr(pl, "exact_lookup", flaky)
    res = _run(_setup(tmp_path, _advs(3)))
    by_id = {f.advisory_id: f for f in res.findings}
    bad = by_id["GHSA-aaaa-bbbb-0001"]
    assert bad.verdict == "unknown" and bad.evidence_level is None and bad.reason_code == "NO_CANDIDATE"
    assert "RuntimeError" in bad.detail
    assert by_id["GHSA-aaaa-bbbb-0000"].verdict == "reachable" and by_id["GHSA-aaaa-bbbb-0002"].verdict == "reachable"
    assert any(g.kind == "dist_error" and "RuntimeError" in g.detail and "GHSA-aaaa-bbbb-0001" in g.detail for g in res.gaps)
    assert res.closure_complete is False


def test_f1_reason_query_raising_is_isolated(tmp_path, monkeypatch):
    real = pl.derive_reason
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("bad")
        return real(*a, **k)

    monkeypatch.setattr(pl, "derive_reason", flaky)
    res = _run(_setup(tmp_path, _advs(3), app=APP_NO_PATH))
    codes = [f.reason_code for f in res.findings]
    assert codes == ["NO_CANDIDATE", "NO_PATH_FOUND", "NO_PATH_FOUND"]
    assert res.closure_complete is False


def test_f1_targets_phase_raising_is_isolated(tmp_path, monkeypatch):
    real = pl.resolve_targets
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise KeyError("x")
        return real(*a, **k)

    monkeypatch.setattr(pl, "resolve_targets", flaky)
    res = _run(_setup(tmp_path, _advs(2)))
    assert [f.verdict for f in res.findings] == ["unknown", "reachable"]
    assert res.findings[0].reason_code == "NO_CANDIDATE"


def test_f1_shared_context_failure_is_one_gap_not_a_crash(tmp_path, monkeypatch):
    def boom(combined):
        raise RuntimeError("ctx")

    monkeypatch.setattr(pl, "make_context", boom)
    res = _run(_setup(tmp_path, _advs(3)))
    assert [f.verdict for f in res.findings] == ["unknown"] * 3
    assert all(f.evidence_level is None and f.reason_code == "NO_CANDIDATE" for f in res.findings)
    ctx_gaps = [g for g in res.gaps if g.kind == "dist_error" and "RuntimeError" in g.detail]
    assert len(ctx_gaps) == 1 and res.closure_complete is False
    assert "RuntimeError" in render_json(res)
    assert "gaps: 1" in render_text(res)


def test_f1_cli_catch_all_exits_2(tmp_path, monkeypatch, capsys):
    from reachability import cli

    s = _setup(tmp_path, _advs(1))

    def boom(*a, **k):
        raise ZeroDivisionError("\x1b[2Jsecret")

    monkeypatch.setattr(cli, "run_scan", boom)
    rc = cli.main(["scan", str(s["app"]), "--snapshot", str(s["snapshot"]), "--snapshot-sha256", s["sha256"],
                   "--wheel-dir", str(s["wheels"])])
    err = capsys.readouterr().err
    assert rc == 2 and "ZeroDivisionError" in err and "\x1b" not in err and "Traceback" not in err
    assert err.count("\n") == 1


# ---- F2
def test_g1_cut_advisories_had_no_targets_built_and_built_ones_get_exact_lookup(tmp_path, monkeypatch):
    now = [0.0]
    real = pl.resolve_targets
    calls = []

    def crossing(*a, **k):
        calls.append(1)
        out = real(*a, **k)
        if len(calls) == 1:
            now[0] += 601.0
        return out

    looked_up = []
    real_exact = pl.exact_lookup

    def spy(ctx, targets):
        looked_up.append(len(targets))
        return real_exact(ctx, targets)

    monkeypatch.setattr(pl, "resolve_targets", crossing)
    monkeypatch.setattr(pl, "exact_lookup", spy)
    res = _run(_setup(tmp_path, _advs(3)), clock=lambda: now[0])
    by_id = {f.advisory_id: f for f in res.findings}
    # targets were built for 0000 before the clock ran out: its exact lookup still ran and proved it
    assert by_id["GHSA-aaaa-bbbb-0000"].verdict == "reachable" and by_id["GHSA-aaaa-bbbb-0000"].targets
    assert len(looked_up) == 1 and looked_up[0] > 0
    for i in ("0001", "0002"):
        f = by_id[f"GHSA-aaaa-bbbb-{i}"]
        assert f.reason_code == "SCAN_BUDGET" and f.verdict == "unknown" and f.evidence_level is None
        assert "analysis time cap" in f.detail
        assert f.targets == []  # cut advisories never had targets built


# ---- F3
def test_f3_cap_counts_only_advisories_needing_analysis(tmp_path):
    no_fix = [advisory(f"GHSA-nofx-bbbb-{i:04d}", "lib", fixed=None) for i in range(150)]
    pending = [advisory(f"GHSA-pend-bbbb-{i:04d}", "lib") for i in range(60)]
    res = _run(_setup(tmp_path, no_fix + pending, app=APP_NO_PATH))
    assert len(res.findings) == 210
    assert not any(f.reason_code == "SCAN_BUDGET" for f in res.findings)
    assert sum(f.reason_code == "NO_FIX_VERSION" for f in res.findings) == 150
    assert sum(f.reason_code == "NO_PATH_FOUND" for f in res.findings) == 60


def test_f3_cap_bites_over_200_pending(tmp_path):
    no_fix = [advisory(f"GHSA-nofx-bbbb-{i:04d}", "lib", fixed=None) for i in range(5)]
    pending = [advisory(f"GHSA-pend-bbbb-{i:04d}", "lib") for i in range(201)]
    res = _run(_setup(tmp_path, no_fix + pending, app=APP_NO_PATH))
    assert sum(f.reason_code == "SCAN_BUDGET" for f in res.findings) == 1


# ---- F4
def test_f4_fix_wheel_cut_by_fetch_budget_is_scan_budget(tmp_path, monkeypatch):
    real = pl.FetchBudget if hasattr(pl, "FetchBudget") else FetchBudget
    monkeypatch.setattr(pl, "FetchBudget", lambda clock: real(max_dists=1, clock=clock))
    res = _run(_setup(tmp_path, _advs(2)))
    for f in res.findings:
        assert f.reason_code == "SCAN_BUDGET" and f.verdict == "unknown" and f.evidence_level is None
        assert "lib==2.0" in f.detail and "cap" in f.detail
    assert any(g.kind == "budget" and "lib==2.0" in g.detail for g in res.gaps)
    assert not any(f.reason_code == "NO_WHEEL_FOR_FIX" for f in res.findings)


# ---- G2
def test_g2_matched_installed_then_fix_wheels_before_unrelated_installed(tmp_path):
    pins = {f"aaa-{i:03d}": "1.0" for i in range(155)}
    pins["lib"] = "1.0"
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins=pins,
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=[advisory("GHSA-aaaa-bbbb-0000", "lib")],
    )
    res = _run(s)
    f = res.findings[0]
    assert f.verdict == "reachable" and f.evidence_level == "path_proven"
    cut = sorted(g.dist for g in res.gaps if g.kind == "budget")
    assert cut == [f"aaa-{i:03d}" for i in range(148, 155)]
    assert "lib" not in cut and "aaa-000" not in cut
    assert all(g.dist.startswith("aaa-") and "cap reached" in g.detail for g in res.gaps if g.kind == "budget")
    assert not any(g.kind == "budget" and "2.0" in g.detail for g in res.gaps)
