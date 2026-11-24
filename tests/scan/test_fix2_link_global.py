from tests.scan.s15_support import link_tree


def _rules(res):
    return {e.resolution_rule.value for e in res.edges}


def _b(mid):
    return {
        "lib.py": "def f(x):\n    return x\n",
        "mid.py": mid,
        "app.py": "from mid import f\n\n\ndef go():\n    return f(1)\n",
    }


def test_rule_b_global_rebind_no_edge(tmp_path):
    mid = "from lib import f\n\n\ndef setup():\n    global f\n    f = wrap(f)\n"
    _, res = link_tree(tmp_path, _b(mid))
    assert "reexport_alias" not in _rules(res)


def test_rule_b_positive_control(tmp_path):
    _, res = link_tree(tmp_path, _b("from lib import f\n"))
    assert "reexport_alias" in _rules(res)


_D_SUB = "def g():\n    return 1\n"


def _d(caller_extra, init_extra=""):
    return {
        "P/__init__.py": init_extra,
        "P/sub.py": _D_SUB,
        "app.py": "from P import sub\n" + caller_extra + "\n\ndef go():\n    return sub.g()\n",
    }


def test_rule_d_positive_control(tmp_path):
    _, res = link_tree(tmp_path, _d(""))
    assert "submodule_attr" in _rules(res)


def test_rule_d_caller_global_sub_no_edge(tmp_path):
    extra = "\n\ndef setup():\n    global sub\n    sub = other()\n"
    _, res = link_tree(tmp_path, _d(extra))
    assert "submodule_attr" not in _rules(res)


def test_rule_d_pkg_init_global_sub_no_edge(tmp_path):
    init = "def setup():\n    global sub\n    sub = other()\n"
    _, res = link_tree(tmp_path, _d("", init))
    assert "submodule_attr" not in _rules(res)
