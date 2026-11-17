import sys
from pathlib import Path

import pytest
from tests.conftest import write_tree

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability.index.reachability import (  # noqa: E402
    LOW_CONFIDENCE_REASON_PREFIX,
    NAME_ESCAPE_REASON_TEMPLATE,
    NO_ENTRYPOINTS_REASON,
    OPAQUE_CALL_REASON_PREFIX,
)
from reachability.index.reachability_models import ReachabilityResult, Verdict  # noqa: E402
from reachability.scan import query as q  # noqa: E402
from reachability.scan.combined import build_combined  # noqa: E402
from reachability.scan.models import Target  # noqa: E402
from reachability.scan.reasons import ReasonCode  # noqa: E402


def _target(node_id, module, qual):
    return Target(node_id, module, qual, 0, "d/x.py", 1, "changed")


def _combined(tmp_path, app_files, lib_files):
    write_tree(tmp_path / "app", app_files)
    write_tree(tmp_path / "lib", lib_files)
    return build_combined(tmp_path / "app", {"lib": tmp_path / "lib"})


def test_e1_reachable(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    from reachability.scan.fetch import LocalDirFetcher, fetch_dists

    res = fetch_dists([("langchain-openai", "1.1.13")], LocalDirFetcher(r["wheels"]), tmp_path / "s")
    c = build_combined(r["app"], {"langchain-openai": res.dists[("langchain-openai", "1.1.13")].root})
    try:
        ctx = q.make_context(c)
        t = _target("langchain_openai.chat_models.base:_url_to_size", "langchain_openai.chat_models.base", "_url_to_size")
        hit = q.exact_lookup(ctx, [t])
        assert hit.verdict == "reachable"
        assert hit.path[-1].callee == t.node_id
        assert hit.path[0].file == "app/main.py"
    finally:
        c.close()


def test_exact_node_not_shadowed_by_same_named_symbol(tmp_path):
    lib = {
        "lib/__init__.py": "",
        "lib/m/__init__.py": "def run():\n    pass\n",
        "lib/m/sub.py": "def run():\n    pass\n",
    }
    app = {"main.py": "from lib.m.sub import run\n\n\ndef go():\n    run()\n\n\nif __name__ == '__main__':\n    go()\n"}
    c = _combined(tmp_path, app, lib)
    try:
        ctx = q.make_context(c)
        shadow = _target("lib.m:run", "lib.m", "run")
        assert q.exact_lookup(ctx, [shadow]) is None
        hit = q.exact_lookup(ctx, [_target("lib.m.sub:run", "lib.m.sub", "run")])
        assert hit is not None and hit.path[-1].callee == "lib.m.sub:run"
        res = q.derive_reason(ctx, [shadow])
        assert res.code is ReasonCode.NO_PATH_FOUND
        assert "different node" in (res.detail or "")
    finally:
        c.close()


def test_library_main_is_not_a_start_point(tmp_path):
    lib = {
        "lib/__init__.py": "",
        "lib/m.py": "def f():\n    pass\n\n\nif __name__ == '__main__':\n    f()\n",
    }
    c = _combined(tmp_path, {"main.py": "x = 1\n"}, lib)
    try:
        ctx = q.make_context(c)
        assert ctx.entrypoints == []
        assert q.exact_lookup(ctx, [_target("lib.m:f", "lib.m", "f")]) is None
        assert q.derive_reason(ctx, [_target("lib.m:f", "lib.m", "f")]).code is ReasonCode.NO_APP_ENTRYPOINTS
    finally:
        c.close()


def test_app_test_gives_reachable_only_from_tests_and_reachable_beats_shorter_test(tmp_path):
    lib = {"lib/__init__.py": "", "lib/m.py": "def f():\n    pass\n\n\ndef g():\n    f()\n"}
    app = {
        "tests/test_a.py": "from lib.m import f\n\n\ndef test_a():\n    f()\n",
    }
    c = _combined(tmp_path, app, lib)
    try:
        ctx = q.make_context(c)
        hit = q.exact_lookup(ctx, [_target("lib.m:f", "lib.m", "f")])
        assert hit.verdict == "reachable_only_from_tests"
    finally:
        c.close()
    app2 = {
        "tests/test_a.py": "from lib.m import f\n\n\ndef test_a():\n    f()\n",
        "main.py": "from lib.m import g\n\n\ndef run():\n    g()\n\n\nif __name__ == '__main__':\n    run()\n",
    }
    c = _combined(tmp_path / "two", app2, lib)
    try:
        ctx = q.make_context(c)
        hit = q.exact_lookup(ctx, [_target("lib.m:f", "lib.m", "f")])
        assert hit.verdict == "reachable"
        assert len(hit.path) >= 2
    finally:
        c.close()


@pytest.mark.parametrize(
    "result,code,partial",
    [
        (ReachabilityResult("m", "f", Verdict.NOT_REACHABLE, None, "no call path found"), ReasonCode.NO_PATH_FOUND, False),
        (ReachabilityResult("m", "f", Verdict.UNKNOWN, None, NO_ENTRYPOINTS_REASON), ReasonCode.NO_APP_ENTRYPOINTS, False),
        (ReachabilityResult("m", "f", Verdict.UNKNOWN, None, NAME_ESCAPE_REASON_TEMPLATE.format(target_symbol="f")), ReasonCode.NAME_ESCAPE, False),
        (ReachabilityResult("m", "f", Verdict.UNKNOWN, [], LOW_CONFIDENCE_REASON_PREFIX + "self_unresolved"), ReasonCode.LOW_CONFIDENCE_PATH, True),
        (ReachabilityResult("m", "f", Verdict.UNKNOWN, [], OPAQUE_CALL_REASON_PREFIX + "?:<dynamic>) at x:1 y"), ReasonCode.OPAQUE_CALL, True),
    ],
)
def test_engine_mapping(result, code, partial):
    got, _detail, is_partial = q.classify_result(result)
    assert got is code and is_partial is partial


def test_engine_reachable_on_other_node_is_not_a_path():
    res = ReachabilityResult("m", "f", Verdict.REACHABLE, [], None)
    code, detail, partial = q.classify_result(res)
    assert code is ReasonCode.NO_PATH_FOUND and partial is False and "different node" in detail
