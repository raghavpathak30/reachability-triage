from tests.scan.s15_support import link_tree, triples

BASE = {
    "inner.py": "def real():\n    return 1\n\n\nclass K:\n    def __init__(self):\n        pass\n",
    "app.py": "from mid import real\n\n\ndef run():\n    return real()\n",
}


def _files(mid, **extra):
    files = dict(BASE)
    files["mid.py"] = mid
    files.update(extra)
    return files


def test_reexport_alias_positive(tmp_path):
    idx, res = link_tree(tmp_path, _files("from inner import real\n"))
    assert any(e.callee_id == "mid:real" for e in idx.edges)
    assert triples(res) == {("app:run", "inner:real", 5, "reexport_alias")}


def test_reexport_class_chains_to_constructor(tmp_path):
    files = _files("from inner import K\n")
    files["app.py"] = "from mid import K\n\n\ndef run():\n    return K()\n"
    _, res = link_tree(tmp_path, files)
    assert triples(res) == {
        ("app:run", "inner:K", 5, "reexport_alias"),
        ("app:run", "inner:K.__init__", 5, "constructor_init"),
    }


def test_reexport_two_aliases_ambiguous_no_edge(tmp_path):
    _, res = link_tree(tmp_path, _files("from inner import real\nfrom other import real\n", **{"other.py": "def real():\n    pass\n"}))
    assert res.edges == []


def test_reexport_cycle_no_edge(tmp_path):
    files = {
        "a.py": "from b import x\n",
        "b.py": "from a import x\n",
        "app.py": "from a import x\n\n\ndef run():\n    return x()\n",
    }
    _, res = link_tree(tmp_path, files)
    assert res.edges == []


def test_reexport_conditional_alias_no_edge(tmp_path):
    _, res = link_tree(tmp_path, _files("try:\n    from inner import real\nexcept ImportError:\n    pass\n"))
    assert res.edges == []


def test_reexport_rebound_name_no_edge(tmp_path):
    _, res = link_tree(tmp_path, _files("from inner import real\nreal = wrap(real)\n"))
    assert res.edges == []


def test_reexport_callee_with_alias_symbol_node_not_followed(tmp_path):
    _, res = link_tree(tmp_path, _files("import inner\nreal = inner.real\n"))
    assert res.edges == []


def test_reexport_two_hops(tmp_path):
    files = _files("from mid2 import real\n", **{"mid2.py": "from inner import real\n"})
    _, res = link_tree(tmp_path, files)
    assert triples(res) == {("app:run", "inner:real", 5, "reexport_alias")}
