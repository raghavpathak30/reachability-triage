import sys
from pathlib import Path

import pytest

from tests.conftest import write_tree  # noqa: F401  (shared helper)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability.scan.combined import CombinedIndexError, build_combined  # noqa: E402
from reachability.scan.fetch import LocalDirFetcher, fetch_dists  # noqa: E402


def test_e1_combined_has_app_and_dist_modules(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    res = fetch_dists([("langchain-openai", "1.1.13")], LocalDirFetcher(r["wheels"]), tmp_path / "store")
    root = res.dists[("langchain-openai", "1.1.13")].root
    c = build_combined(r["app"], {"langchain-openai": root})
    try:
        names = {m.dotted_name for m in c.index.report.modules}
        assert "main" in names and "langchain_openai.chat_models.base" in names
        assert c.app_modules == {"main"}
        assert c.module_origins()["langchain_openai.chat_models.base"] == "langchain-openai"
    finally:
        c.close()


def test_open_webui_shaped_tree(tmp_path):
    app = tmp_path / "app"
    write_tree(
        app,
        {
            "src/app.html": "<html/>",
            "src/app.css": "x",
            "backend/open_webui/__init__.py": "",
            "backend/open_webui/models.py": "x = 1\n",
            "backend/open_webui/utils/__init__.py": "",
            "backend/open_webui/utils/auth.py": "from open_webui.models import x\n",
            "scripts/x.py": "y = 2\n",
        },
    )
    c = build_combined(app, {})
    try:
        names = c.app_modules
        assert "open_webui.utils.auth" in names and "scripts.x" in names
        assert not any(n.startswith("backend") or n.startswith("src") for n in names)
        assert not (c.root / "src").exists()
    finally:
        c.close()


def test_flaskr_shaped_tree(tmp_path):
    app = tmp_path / "app"
    write_tree(
        app,
        {
            "flaskr/__init__.py": "",
            "flaskr/auth.py": "def login(): pass\n",
            "tests/conftest.py": "",
            "tests/test_auth.py": "def test_x(): pass\n",
        },
    )
    c = build_combined(app, {})
    try:
        assert {"flaskr", "flaskr.auth", "tests.test_auth"} <= c.app_modules
    finally:
        c.close()


def test_src_layout(tmp_path):
    app = tmp_path / "app"
    write_tree(app, {"src/mypkg/__init__.py": "", "src/mypkg/core.py": "a = 1\n"})
    c = build_combined(app, {})
    try:
        assert "mypkg.core" in c.app_modules
    finally:
        c.close()


def test_temp_root_with_test_component_is_tool_error(tmp_path):
    base = tmp_path / "tests"
    base.mkdir()
    app = tmp_path / "app"
    write_tree(app, {"a.py": "x = 1\n"})
    with pytest.raises(CombinedIndexError, match="TMPDIR"):
        build_combined(app, {}, tmp_parent=base)


# ---- step 16a: collisions, symlinks, excluded dirs, path mapping ----

import json  # noqa: E402
import os  # noqa: E402

from reachability.scan.fetch import LocalDirFetcher as _Local  # noqa: E402
from reachability.scan.pipeline import run_scan  # noqa: E402
from reachability.scan.render import render_json, render_text  # noqa: E402
from tests.scan.s1_support import APP_CALLS_F, LIB_NEW, LIB_OLD, advisory, lib_files, make_scan  # noqa: E402


def test_same_path_in_two_dists_is_a_collision_gap_first_wins(tmp_path):
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    write_tree(tmp_path / "a", {"ns/__init__.py": "", "ns/x.py": "A = 1\n"})
    write_tree(tmp_path / "b", {"ns/__init__.py": "", "ns/x.py": "B = 1\n"})
    c = build_combined(tmp_path / "app", {"b-dist": tmp_path / "b", "a-dist": tmp_path / "a"})
    try:
        coll = [g for g in c.gaps if g.kind == "collision"]
        assert len(coll) == 2  # ns/__init__.py and ns/x.py
        assert any("b-dist/ns/x.py" in g.detail and "a-dist/ns/x.py" in g.detail for g in coll)
        assert c.origin["ns/x.py"] == "a-dist"
        assert (c.root / "ns" / "x.py").read_text() == "A = 1\n"
    finally:
        c.close()


def test_app_wins_over_dist_on_collision(tmp_path):
    write_tree(tmp_path / "app", {"ns/__init__.py": "", "ns/x.py": "APP = 1\n"})
    write_tree(tmp_path / "a", {"ns/__init__.py": "", "ns/x.py": "A = 1\n"})
    c = build_combined(tmp_path / "app", {"a-dist": tmp_path / "a"})
    try:
        assert c.origin["ns/x.py"] == "app"
        assert any(g.kind == "collision" for g in c.gaps)
    finally:
        c.close()


def test_symlinked_app_file_and_dir_are_gaps(tmp_path):
    app = tmp_path / "app"
    write_tree(app, {"real.py": "x = 1\n", "pkg/__init__.py": ""})
    os.symlink(tmp_path / "app" / "real.py", app / "link.py")
    os.symlink(tmp_path / "app" / "pkg", app / "linkdir")
    c = build_combined(app, {})
    try:
        skipped = sorted(g.detail for g in c.gaps if g.kind == "skipped_symlink")
        assert skipped == ["app/link.py", "app/linkdir"]
        assert "link" not in c.app_modules and not any(m.startswith("linkdir") for m in c.app_modules)
    finally:
        c.close()


def test_excluded_dirs_are_gaps_when_they_hold_python(tmp_path):
    write_tree(tmp_path / "app", {"main.py": "x = 1\n", "build/gen.py": "y = 1\n", "dist/empty.txt": "", "venv/lib.py": "z = 1\n"})
    write_tree(tmp_path / "w", {"pkg/__init__.py": "", "build/b.py": "q = 1\n", "src/s.py": "r = 1\n"})
    c = build_combined(tmp_path / "app", {"wheel": tmp_path / "w"})
    try:
        ex = sorted((g.dist or "", g.detail.split(":")[0]) for g in c.gaps if g.kind == "excluded_dir")
        assert ex == [("", "app/build"), ("wheel", "wheel/build"), ("wheel", "wheel/src")]
        assert not (c.root / "src").exists()
    finally:
        c.close()


def test_app_file_under_src_without_package_cannot_become_the_index_root(tmp_path):
    write_tree(tmp_path / "app", {"main.py": "x = 1\n", "src/script.py": "y = 1\n"})
    c = build_combined(tmp_path / "app", {})
    try:
        assert not (c.root / "src").exists() and "main" in c.app_modules
        assert any(g.kind == "excluded_dir" for g in c.gaps)
    finally:
        c.close()


def test_paths_are_mapped_and_no_temp_root_leaks(tmp_path):
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    res = run_scan(s["app"], s["snapshot"], s["sha256"], _Local(s["wheels"]))
    f = res.findings[0]
    assert f.path[0].file == "app/main.py"
    assert f.targets[0].file == "lib/lib/core.py"
    for out in (render_json(res), render_text(res)):
        assert "rscan-" not in out and str(tmp_path) not in out
    assert json.loads(render_json(res))["findings"][0]["path"][0]["file"] == "app/main.py"


def test_opaque_case_leaks_no_temp_root(tmp_path):
    app = (
        "from lib.core import helper\n\n\ndef main(obj, name):\n    getattr(obj, name)()\n    return helper()\n\n\n"
        "if __name__ == '__main__':\n    main(None, 'x')\n"
    )
    s = make_scan(
        tmp_path,
        app_files={"main.py": app},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    res = run_scan(s["app"], s["snapshot"], s["sha256"], _Local(s["wheels"]))
    f = res.findings[0]
    assert f.reason_code == "OPAQUE_CALL" and f.verdict == "unknown"
    for out in (render_json(res), render_text(res)):
        assert "rscan-" not in out


def test_unmapped_engine_reason_detail_is_scrubbed(tmp_path, monkeypatch):
    from reachability.index.reachability_models import ReachabilityResult, Verdict
    from reachability.scan import query as q

    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F.replace("f(1)", "helper()").replace("import f", "import helper")},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )

    def fake(module, symbol, eps, edges, report):
        root = str(next(iter(report.modules)).file_path.parents[0])
        return ReachabilityResult(module, symbol, Verdict.UNKNOWN, None, f"odd reason in {root}/lib/core.py:3")

    monkeypatch.setattr(q, "compute_reachability", fake)
    res = run_scan(s["app"], s["snapshot"], s["sha256"], _Local(s["wheels"]))
    d = res.findings[0].detail
    assert d.startswith("unmapped engine reason") and "rscan-" not in d and "lib/lib/core.py:3" in d
