import pytest

from reachability.scan import combined as cmb
from reachability.scan.combined import CombinedIndexError, build_combined
from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.pipeline import run_scan
from reachability.triage.index_adapter import IndexBuildError
from tests.conftest import write_tree
from tests.scan.s1_support import APP_CALLS_F, LIB_NEW, LIB_OLD, advisory, make_scan


def _poison(monkeypatch, markers, exc=IndexBuildError):
    real = cmb.build_repo_index
    calls = []

    def fake(root):
        calls.append(sorted(p.name for p in root.iterdir()))
        if any((root / m).exists() for m in markers):
            raise exc(f"L1 failed in {markers}") if exc is IndexBuildError else exc()
        return real(root)

    monkeypatch.setattr(cmb, "build_repo_index", fake)
    return calls


def _dists(tmp_path, names):
    out = {}
    for n in names:
        write_tree(tmp_path / "d" / n, {f"{n}/__init__.py": "", f"{n}/m.py": "x = 1\n"})
        out[n] = tmp_path / "d" / n
    return out


def test_failing_dist_is_bisected_out(tmp_path, monkeypatch):
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    dists = _dists(tmp_path, ["aa", "bb", "badpkg", "dd", "ee"])
    calls = _poison(monkeypatch, ["badpkg"])
    c = build_combined(tmp_path / "app", dists)
    try:
        dropped = [g for g in c.gaps if g.kind == "dist_error"]
        assert [g.dist for g in dropped] == ["badpkg"] and "IndexBuildError" in dropped[0].detail
        assert {"aa", "bb", "dd", "ee"} <= {m.dotted_name.split(".")[0] for m in c.index.report.modules}
        assert len(calls) - 1 <= cmb.MAX_REBUILDS + 1
    finally:
        c.close()


@pytest.mark.parametrize("exc", [RecursionError, MemoryError])
def test_other_failure_types_are_caught(tmp_path, monkeypatch, exc):
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    dists = _dists(tmp_path, ["aa", "badpkg"])
    _poison(monkeypatch, ["badpkg"], exc)
    c = build_combined(tmp_path / "app", dists)
    try:
        assert [g.dist for g in c.gaps if g.kind == "dist_error"] == ["badpkg"]
    finally:
        c.close()


def test_rebuild_limit_marks_undecided_dists(tmp_path, monkeypatch):
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})
    names = [f"d{i:02d}" for i in range(40)] + ["badpkg"]
    dists = _dists(tmp_path, names)
    # every dist poisons, so the search can never succeed and must stop at the limit
    calls = _poison(monkeypatch, names)
    c = build_combined(tmp_path / "app", dists)
    try:
        assert len(calls) <= cmb.MAX_REBUILDS + 2
        assert len([g for g in c.gaps if g.kind == "dist_error"]) == len(names)
        assert any("undecided" in g.detail for g in c.gaps)
    finally:
        c.close()


def test_app_that_cannot_be_indexed_is_tool_error(tmp_path, monkeypatch):
    write_tree(tmp_path / "app", {"main.py": "x = 1\n"})

    def fake(root):
        raise IndexBuildError("always")

    monkeypatch.setattr(cmb, "build_repo_index", fake)
    with pytest.raises(CombinedIndexError, match="app cannot be indexed"):
        build_combined(tmp_path / "app", _dists(tmp_path, ["aa"]))


def test_scan_continues_when_one_dist_breaks_the_index(tmp_path, monkeypatch):
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins={"lib": "1.0", "badpkg": "1.0"},
        wheels={
            ("lib", "1.0"): {"lib/__init__.py": "", "lib/core.py": LIB_OLD},
            ("lib", "2.0"): {"lib/__init__.py": "", "lib/core.py": LIB_NEW},
            ("badpkg", "1.0"): {"badpkg/__init__.py": ""},
        },
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    _poison(monkeypatch, ["badpkg"])
    res = run_scan(s["app"], s["snapshot"], s["sha256"], LocalDirFetcher(s["wheels"]))
    assert res.findings[0].verdict == "reachable"
    assert any(g.kind == "dist_error" and g.dist == "badpkg" for g in res.gaps)
    assert res.closure_complete is False
