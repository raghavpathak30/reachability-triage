import json

from reachability import cli
from reachability.scan import combined as combined_mod
from reachability.scan.combined import build_combined
from reachability.scan.query import make_context
from tests.scan.s1_support import advisory, lib_files, make_scan

LIB = {
    "lib/__init__.py": "",
    "lib/core.py": "def deep():\n    return 1\n\n\nclass C:\n    def __init__(self):\n        deep()\n",
}
APP = "from lib.core import C\n\n\ndef main():\n    return C()\n\n\nif __name__ == '__main__':\n    main()\n"


def _combined(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text(APP)
    root = tmp_path / "libroot"
    for rel, text in LIB.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return build_combined(tmp_path / "app", {"lib": root}, tmp_path)


def test_link_edges_on_combined_not_in_repo_index_edges(tmp_path):
    c = _combined(tmp_path)
    try:
        assert [e.callee_id for e in c.link_edges] == ["lib.core:C.__init__"]
        assert not any(type(e).__name__ == "LinkEdge" for e in c.index.edges)
        assert c.link_counts == {"constructor_init": 1}
    finally:
        c.close()


def test_make_context_bfs_uses_link_edges(tmp_path):
    c = _combined(tmp_path)
    try:
        ctx = make_context(c)
        assert "lib.core:C.__init__" in ctx.paths_non_test
        assert "lib.core:deep" in ctx.paths_non_test
        assert "lib.core:deep" not in make_context_without_links(c).paths_non_test
    finally:
        c.close()


def make_context_without_links(c):
    saved = c.link_edges
    c.link_edges = []
    try:
        return make_context(c)
    finally:
        c.link_edges = saved


def test_link_step_exception_becomes_link_error_gap_and_scan_continues(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("boom\x1b[0m")

    monkeypatch.setattr(combined_mod, "compute_link_edges", boom)
    r = make_scan(
        tmp_path,
        app_files={"main.py": APP},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(), ("lib", "2.0"): lib_files()},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    rc = cli.main([
        "scan", str(r["app"]), "--snapshot", str(r["snapshot"]), "--snapshot-sha256", r["sha256"],
        "--wheel-dir", str(r["wheels"]), "--format", "json",
    ])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    gaps = [g for g in d["gaps"] if g["kind"] == "link_error"]
    assert len(gaps) == 1
    assert gaps[0]["dist"] is None
    assert gaps[0]["detail"].startswith("link step: RuntimeError: ")
    assert "\x1b" not in gaps[0]["detail"]
    assert d["closure_complete"] is False
    assert d["stats"]["link_edges_total"] == 0


def test_stats_report_link_edges_per_rule(tmp_path, capsys):
    r = make_scan(
        tmp_path,
        app_files={"main.py": APP},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): LIB, ("lib", "2.0"): LIB},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    rc = cli.main([
        "scan", str(r["app"]), "--snapshot", str(r["snapshot"]), "--snapshot-sha256", r["sha256"],
        "--wheel-dir", str(r["wheels"]), "--format", "json",
    ])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["stats"]["link_edges"] == {"constructor_init": 1}
    assert d["stats"]["link_edges_total"] == 1
    assert d["stats"]["combined_edges"] > 0
