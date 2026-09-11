import sys
from pathlib import Path

import pytest

from reachability.index.edges_models import ResolutionRule
from reachability.index.reachability import compute_reachability
from reachability.index.reachability_models import Verdict
from reachability.triage.index_adapter import IndexBuildError, build_repo_index

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import measure_l5  # noqa: E402


def test_build_repo_index_reproduces_fixture06_subclass_mro_reachable():
    fixture_dir = Path(__file__).parent / "fixtures" / "l5" / "06_subclass_method_call"
    repo_root = fixture_dir / "repo"

    idx = build_repo_index(repo_root)

    target_module = measure_l5._module_dotted_name("repo/pkg/base.py")
    target_symbol = measure_l5.resolve_target(idx.symbol_index, target_module, 2, "06_subclass_method_call")
    assert target_module == "pkg.base"
    assert target_symbol == "vulnerable"

    wrapped_result = compute_reachability(target_module, target_symbol, idx.entrypoints, idx.edges, idx.report)

    from reachability.index.build import build_l1_index
    from reachability.index.edges import build_edge_index
    from reachability.index.entrypoints import build_entrypoint_index
    from reachability.index.symbols import build_symbol_index

    report = build_l1_index(repo_root)
    symbol_index = build_symbol_index(report)
    edges = build_edge_index(report, symbol_index)
    entrypoints = build_entrypoint_index(report, symbol_index, repo_root)
    direct_result = compute_reachability(target_module, target_symbol, entrypoints, edges, report)

    assert wrapped_result == direct_result

    assert wrapped_result.verdict == Verdict.REACHABLE
    assert wrapped_result.path is not None
    assert wrapped_result.path[-1].resolution_rule == ResolutionRule.SELF_MRO
    assert wrapped_result.path[-1].callee_id == "pkg.base:Base.vulnerable"


def test_build_repo_index_raises_index_build_error_on_missing_repo_root(tmp_path):
    missing = tmp_path / "does_not_exist"

    with pytest.raises(IndexBuildError, match="does not exist"):
        build_repo_index(missing)


def test_build_repo_index_raises_index_build_error_on_l2_failure(tmp_path, monkeypatch):
    def _boom(report):
        raise ValueError("boom")

    monkeypatch.setattr("reachability.triage.index_adapter.build_symbol_index", _boom)

    with pytest.raises(IndexBuildError, match="L2 symbol table construction failed") as exc_info:
        build_repo_index(tmp_path)
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_build_repo_index_raises_index_build_error_on_l3_failure(tmp_path, monkeypatch):
    def _boom(report, symbol_index):
        raise ValueError("boom")

    monkeypatch.setattr("reachability.triage.index_adapter.build_edge_index", _boom)

    with pytest.raises(IndexBuildError, match="L3 call-edge extraction failed") as exc_info:
        build_repo_index(tmp_path)
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_build_repo_index_raises_index_build_error_on_l4_failure(tmp_path, monkeypatch):
    def _boom(report, symbol_index, repo_root):
        raise ValueError("boom")

    monkeypatch.setattr("reachability.triage.index_adapter.build_entrypoint_index", _boom)

    with pytest.raises(IndexBuildError, match="L4 entrypoint detection failed") as exc_info:
        build_repo_index(tmp_path)
    assert isinstance(exc_info.value.__cause__, ValueError)
