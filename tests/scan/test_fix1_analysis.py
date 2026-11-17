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


