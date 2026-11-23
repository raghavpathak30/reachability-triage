import re
from pathlib import Path

import pytest

from reachability.index.edges_models import ResolutionRule
from reachability.scan.link import LinkRule, compute_link_edges
from reachability.scan.pipeline import run_scan
from reachability.triage.index_adapter import build_repo_index
from tests.conftest import write_tree
from tests.scan.test_s1_invariants import SCENARIOS

SRC = Path(__file__).resolve().parents[2] / "src" / "reachability"


def test_link_imported_only_inside_scan():
    for path in SRC.rglob("*.py"):
        if SRC / "scan" in path.parents:
            continue
        text = path.read_text()
        assert "scan.link" not in text and "from .link" not in text, path
    assert "scan.link" not in (SRC.parent.parent / "main.py").read_text()


def test_engine_does_not_import_scan():
    text = (SRC / "index" / "reachability.py").read_text()
    assert not re.search(r"^\s*(from|import)\s+reachability\.scan", text, re.M)
    assert "scan" not in "".join(re.findall(r"^(?:from|import) .*$", text, re.M))


def test_resolution_rule_unchanged():
    schema = (SRC.parent.parent / "web" / "src" / "api" / "schema.d.ts").read_text()
    line = next(l for l in schema.splitlines() if l.strip().startswith("ResolutionRule:"))
    assert set(re.findall(r'"(\w+)"', line)) == {r.value for r in ResolutionRule}


def test_link_rules_are_not_engine_rules():
    assert {r.value for r in LinkRule}.isdisjoint({r.value for r in ResolutionRule})


ALL_RULES_TREE = {
    "lib/__init__.py": "from lib.m import X\n",
    "lib/m.py": "class X:\n    def __init__(self):\n        pass\n\n    @staticmethod\n    def attr():\n        pass\n\n    def m(self):\n        pass\n\n\ndef real():\n    pass\n",
    "P/__init__.py": "",
    "P/sub.py": "def f():\n    pass\n",
    "mid.py": "from lib.m import real\n",
    "app.py": (
        "import lib\nfrom P import sub\nfrom mid import real\nfrom lib.m import X\n\nx = X()\n\n\n"
        "def run():\n    X()\n    real()\n    lib.X.attr()\n    sub.f()\n    x.m()\n"
    ),
}


def test_every_link_edge_targets_an_existing_node(tmp_path):
    write_tree(tmp_path, ALL_RULES_TREE)
    idx = build_repo_index(tmp_path)
    res = compute_link_edges(idx.report, idx.symbol_index, idx.edges)
    assert {e.resolution_rule for e in res.edges} == set(LinkRule)
    ids = {n.node_id for t in idx.symbol_index.values() for n in t.nodes}
    for e in res.edges:
        assert e.callee_id in ids and e.confidence.value == "medium", e


@pytest.mark.filterwarnings("ignore:Duplicate name")
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_findings_respect_s15_invariants(tmp_path, name):
    setup, fetcher, kw = SCENARIOS[name](tmp_path)
    res = run_scan(setup["app"], setup["snapshot"], setup["sha256"], fetcher, **kw)
    for f in res.findings:
        if f.reason_code == "PATH_TO_UNCONFIRMED_TARGET":
            assert f.verdict == "unknown" and f.evidence_level is None
            assert f.partial_path and f.path is None
            assert f.partial_path[-1].callee in {t.node_id for t in f.targets if t.confidence == "medium"}
        if f.evidence_level == "path_proven":
            confs = {t.node_id: t.confidence for t in f.targets}
            assert confs[f.path[-1].callee] == "high"
        assert f.verdict != "not_reachable"
    assert all(g.kind != "link_error" for g in res.gaps)


def test_not_reachable_enum_member_only_on_mapping_line():
    hits = [
        (p, i)
        for p in (SRC / "scan").rglob("*.py")
        for i, line in enumerate(p.read_text().splitlines())
        if "NOT_REACHABLE" in line
    ]
    assert len(hits) == 1 and hits[0][0].name == "query.py"
