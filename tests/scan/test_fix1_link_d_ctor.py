from tests.scan.s15_support import link_tree, triples

PKG = {"P/__init__.py": "", "P/sub.py": "class K:\n    def __init__(self, a):\n        self.a = a\n"}
APP = "from P import sub\n\n\ndef run(a):\n    return sub.K(a)\n"


def test_rule_d_class_init_edge_is_constructor_init(tmp_path):
    _, res = link_tree(tmp_path, {**PKG, "app.py": APP})
    assert triples(res) == {
        ("app:run", "P.sub:K", 5, "submodule_attr"),
        ("app:run", "P.sub:K.__init__", 5, "constructor_init"),
    }


def test_rule_d_class_without_init_has_no_second_edge(tmp_path):
    files = {**PKG, "P/sub.py": "class K:\n    pass\n", "app.py": APP}
    _, res = link_tree(tmp_path, files)
    assert triples(res) == {("app:run", "P.sub:K", 5, "submodule_attr")}
