from tests.scan.s15_support import link_tree, triples

CLASS_SRC = "class X:\n    @staticmethod\n    def attr():\n        return 1\n"


def test_module_class_attr_positive(tmp_path):
    files = {"m.py": CLASS_SRC, "app.py": "import m\n\n\ndef run():\n    return m.X.attr()\n"}
    idx, res = link_tree(tmp_path, files)
    assert any(e.callee_id == "m.X:attr" for e in idx.edges)
    assert triples(res) == {("app:run", "m:X.attr", 5, "module_class_attr")}


def test_module_class_attr_missing_method_no_edge(tmp_path):
    files = {"m.py": CLASS_SRC, "app.py": "import m\n\n\ndef run():\n    return m.X.nope()\n"}
    _, res = link_tree(tmp_path, files)
    assert res.edges == []


def test_module_class_attr_via_reexport(tmp_path):
    files = {
        "lib/__init__.py": "from lib.m import X\n",
        "lib/m.py": CLASS_SRC,
        "app.py": "import lib\n\n\ndef run():\n    return lib.X.attr()\n",
    }
    idx, res = link_tree(tmp_path, files)
    assert any(e.callee_id == "lib.X:attr" for e in idx.edges)
    assert triples(res) == {("app:run", "lib.m:X.attr", 5, "module_class_attr")}
