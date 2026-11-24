from tests.scan.s15_support import link_tree, triples

CLS = "class C:\n    def m(self, a):\n        return a\n\n\n"


def test_rule_e_star_import_in_caller_module_no_edge(tmp_path):
    files = {
        "other.py": "z = 1\n",
        "main.py": CLS + "from other import *\n\nx = C()\n\n\ndef use(a):\n    return x.m(a)\n",
    }
    _, res = link_tree(tmp_path, files)
    assert not any(e.resolution_rule.value == "module_singleton" for e in res.edges)


def test_rule_e_without_star_import_still_edges(tmp_path):
    files = {"main.py": CLS + "x = C()\n\n\ndef use(a):\n    return x.m(a)\n"}
    _, res = link_tree(tmp_path, files)
    assert any(e.resolution_rule.value == "module_singleton" for e in res.edges)


def _b_files(mid):
    return {
        "inner.py": "def real():\n    return 1\n",
        "other.py": "z = 1\n",
        "mid.py": mid,
        "app.py": "from mid import real\n\n\ndef run():\n    return real()\n",
    }


def test_rule_b_positive_control(tmp_path):
    _, res = link_tree(tmp_path, _b_files("from inner import real\n"))
    assert triples(res) == {("app:run", "inner:real", 5, "reexport_alias")}


def test_rule_b_star_import_in_reexporting_module_no_retarget(tmp_path):
    _, res = link_tree(tmp_path, _b_files("from inner import real\nfrom other import *\n"))
    assert not any(e.resolution_rule.value == "reexport_alias" for e in res.edges)


def test_rule_d_star_import_in_caller_module_no_edge(tmp_path):
    files = {
        "P/__init__.py": "",
        "P/sub.py": "def f(a):\n    return a\n",
        "other.py": "z = 1\n",
        "app.py": "from P import sub\nfrom other import *\n\n\ndef run(a):\n    return sub.f(a)\n",
    }
    _, res = link_tree(tmp_path, files)
    assert triples(res) == set()
