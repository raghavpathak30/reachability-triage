from tests.scan.s15_support import link_tree, triples

CLS = "class C:\n    def m(self, a):\n        return a\n\n\n"


def _mod(body, extra=""):
    return CLS + "x = C()\n" + extra + "\n\ndef use(a):\n" + body


def test_module_singleton_positive(tmp_path):
    idx, res = link_tree(tmp_path, {"main.py": _mod("    return x.m(a)\n")})
    assert any(e.callee_id == "?:m" for e in idx.edges)
    assert [t for t in triples(res) if t[3] == "module_singleton"] == [("main:use", "main:C.m", 10, "module_singleton")]


def test_module_singleton_class_imported(tmp_path):
    files = {
        "lib.py": CLS.rstrip() + "\n",
        "main.py": "from lib import C\n\nx = C()\n\n\ndef use(a):\n    return x.m(a)\n",
    }
    _, res = link_tree(tmp_path, files)
    assert ("main:use", "lib:C.m", 7, "module_singleton") in triples(res)


def test_singleton_rebound_via_global_no_edge(tmp_path):
    extra = "\n\ndef reset():\n    global x\n    x = C()\n"
    _, res = link_tree(tmp_path, {"main.py": _mod("    return x.m(a)\n", extra)})
    assert not any(e.resolution_rule.value == "module_singleton" for e in res.edges)


def test_singleton_reassigned_at_module_level_no_edge(tmp_path):
    _, res = link_tree(tmp_path, {"main.py": _mod("    return x.m(a)\n", "x = None\n")})
    assert not any(e.resolution_rule.value == "module_singleton" for e in res.edges)


def test_singleton_in_other_module_no_edge(tmp_path):
    files = {
        "lib.py": CLS + "x = C()\n",
        "main.py": "from lib import x\n\n\ndef use(a):\n    return x.m(a)\n",
    }
    _, res = link_tree(tmp_path, files)
    assert not any(e.resolution_rule.value == "module_singleton" for e in res.edges)


def test_parameter_shadowing_singleton_no_edge(tmp_path):
    files = {"main.py": _mod("    return x.m(a)\n").replace("def use(a):", "def use(a, x):")}
    _, res = link_tree(tmp_path, files)
    assert not any(e.resolution_rule.value == "module_singleton" for e in res.edges)


def test_function_local_instance_is_not_a_singleton(tmp_path):
    files = {"main.py": CLS + "def use(a):\n    x = C()\n    return x.m(a)\n"}
    _, res = link_tree(tmp_path, files)
    assert not any(e.resolution_rule.value == "module_singleton" for e in res.edges)


def test_except_as_shadow_no_edge(tmp_path):
    body = "    try:\n        pass\n    except ValueError as x:\n        return x.m(a)\n"
    _, res = link_tree(tmp_path, {"main.py": _mod(body)})
    assert not any(e.resolution_rule.value == "module_singleton" for e in res.edges)


def test_two_attribute_calls_same_line_no_edge(tmp_path):
    _, res = link_tree(tmp_path, {"main.py": _mod("    return x.m(a) + a.m(a)\n")})
    assert not any(e.resolution_rule.value == "module_singleton" for e in res.edges)
