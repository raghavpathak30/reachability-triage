from reachability.index.edges_models import Confidence, CallEdge
from reachability.scan.link import LinkEdge, LinkRule

from tests.scan.s15_support import link_tree, triples

BASE = {
    "lib/__init__.py": "",
    "lib/m.py": "class C:\n    def __init__(self, x):\n        self.x = x\n\n\nclass D:\n    pass\n\n\nclass E(C):\n    pass\n",
}


def test_constructor_init_edge_positive(tmp_path):
    files = dict(BASE)
    files["app.py"] = "from lib.m import C\n\n\ndef run():\n    return C(1)\n"
    idx, res = link_tree(tmp_path, files)
    assert ("app:run", "lib.m:C.__init__", 5, "constructor_init") in triples(res)
    assert any(e.callee_id == "lib.m:C" and e.caller_id == "app:run" for e in idx.edges)
    link = next(e for e in res.edges if e.callee_id == "lib.m:C.__init__")
    assert link.file.name == "app.py" and link.lineno == 5
    assert res.per_rule_counts == {"constructor_init": 1}


def test_constructor_init_inherited_found_through_mro(tmp_path):
    files = dict(BASE)
    files["app.py"] = "from lib.m import E\n\n\ndef run():\n    return E()\n"
    _, res = link_tree(tmp_path, files)
    assert ("app:run", "lib.m:C.__init__", 5, "constructor_init") in triples(res)


def test_constructor_init_no_init_in_mro_no_edge(tmp_path):
    files = dict(BASE)
    files["app.py"] = "from lib.m import D\n\n\ndef run():\n    return D()\n"
    _, res = link_tree(tmp_path, files)
    assert res.edges == []


def test_link_edge_is_not_call_edge_and_medium():
    assert not issubclass(LinkEdge, CallEdge)
    e = LinkEdge("a:b", "c:d", __import__("pathlib").Path("x.py"), 1, LinkRule.CONSTRUCTOR_INIT)
    assert e.confidence is Confidence.MEDIUM
