"""Helpers for the S1.5 link-step tests: index a synthetic tree, run the link step."""

from tests.conftest import write_tree
from reachability.scan.link import compute_link_edges
from reachability.triage.index_adapter import build_repo_index


def index_tree(tmp_path, files):
    write_tree(tmp_path, files)
    return build_repo_index(tmp_path)


def link_tree(tmp_path, files):
    idx = index_tree(tmp_path, files)
    return idx, compute_link_edges(idx.report, idx.symbol_index, idx.edges)


def triples(result):
    return {(e.caller_id, e.callee_id, e.lineno, e.resolution_rule.value) for e in result.edges}
