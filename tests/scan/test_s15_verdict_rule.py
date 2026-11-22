import json

from tests.conftest import write_tree

from reachability.scan import query as q
from reachability.scan.combined import build_combined
from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.models import Target
from reachability.scan.pipeline import run_scan
from reachability.scan.render import render_json
from tests.scan.s1_support import APP_CALLS_F, LIB_NEW, LIB_OLD, advisory, lib_files, make_scan


def _run(s):
    return run_scan(s["app"], s["snapshot"], s["sha256"], LocalDirFetcher(s["wheels"]))


def _scan(tmp_path, details, app=APP_CALLS_F, old=LIB_OLD, new=LIB_NEW):
    return make_scan(
        tmp_path,
        app_files={"main.py": app},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(old), ("lib", "2.0"): lib_files(new)},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib", details=details)],
    )


def test_black_shaped_changed_but_unnamed_is_unknown_with_partial_path(tmp_path):
    res = _run(_scan(tmp_path, "The fix lives somewhere else entirely."))
    f = res.findings[0]
    assert f.verdict == "unknown"
    assert f.reason_code == "PATH_TO_UNCONFIRMED_TARGET"
    assert f.evidence_level is None
    assert f.path is None and f.partial_path
    assert f.partial_path[-1].callee == "lib.core:f"
    assert "do not claim the vulnerable function is reached" in f.detail
    assert "`lib.core:f`" in f.detail
    d = json.loads(render_json(res))
    assert d["findings"][0]["partial_path"]
    assert d["findings"][0]["path"] is None


def test_synthetic_advisory_without_details_confident_path_is_unconfirmed(tmp_path):
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib", details="")],
    )
    f = _run(s).findings[0]
    assert f.verdict == "unknown" and f.reason_code == "PATH_TO_UNCONFIRMED_TARGET" and f.partial_path


def test_high_target_wins_over_shorter_medium_path(tmp_path):
    old = "def f():\n    return 1\n\n\ndef g():\n    return 1\n"
    new = "def f():\n    return 2\n\n\ndef g():\n    return 2\n"
    app = (
        "from lib.core import f, g\n\n\ndef helper():\n    return g()\n\n\n"
        "def main():\n    f()\n    return helper()\n\n\nif __name__ == '__main__':\n    main()\n"
    )
    res = _run(_scan(tmp_path, "The function `g()` is vulnerable.", app, old, new))
    f = res.findings[0]
    assert f.verdict == "reachable" and f.evidence_level == "path_proven"
    assert f.path[-1].callee == "lib.core:g" and len(f.path) == 3
    assert {t.node_id: t.confidence for t in f.targets} == {"lib.core:f": "medium", "lib.core:g": "high"}


def test_zero_length_path_target_not_claimed(tmp_path):
    write_tree(tmp_path / "app", {"main.py": "def go():\n    pass\n\n\nif __name__ == '__main__':\n    go()\n"})
    write_tree(tmp_path / "lib", {"lib/__init__.py": ""})
    c = build_combined(tmp_path / "app", {"lib": tmp_path / "lib"})
    try:
        ctx = q.make_context(c)
        for conf in ("high", "medium"):
            t = Target("main", "main", "main", 0, "app/main.py", 1, "changed", conf)
            assert q.exact_lookup(ctx, [t]) is None
            assert q.unconfirmed_lookup(ctx, [t]) is None
    finally:
        c.close()
