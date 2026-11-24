import pytest

from tests.scan.s15_support import link_tree

CLS = "class C:\n    def m(self, a):\n        return a\n\n\n"
PKG = {"P/__init__.py": "", "P/sub.py": "def f(a):\n    return a\n"}

E_BODIES = [
    "[x.m(1) for x in range(3)]\n",
    "def use(a):\n    return [x.m(1) for x in range(3)]\n",
    "def use(a):\n    return list(x.m(1) for x in range(3))\n",
    "def use(a):\n    return {x.m(1) for x in range(3)}\n",
    "def use(a):\n    return {k: x.m(1) for k, x in a}\n",
    "def use(a):\n    return (lambda x: x.m(1))(a)\n",
    "def use(a):\n    return [(lambda x: x.m(1)) for _ in a]\n",
]


@pytest.mark.parametrize("body", E_BODIES)
def test_rule_e_shadowed_by_nested_scope_no_edge(tmp_path, body):
    _, res = link_tree(tmp_path, {"main.py": CLS + "x = C()\n\n" + body})
    assert not any(e.resolution_rule.value == "module_singleton" for e in res.edges)


D_BODIES = [
    "[sub.f(1) for sub in range(3)]\n",
    "def run(a):\n    return [sub.f(1) for sub in a]\n",
    "def run(a):\n    return list(sub.f(1) for sub in a)\n",
    "def run(a):\n    return (lambda sub: sub.f(1))(a)\n",
]


@pytest.mark.parametrize("body", D_BODIES)
def test_rule_d_shadowed_by_nested_scope_no_edge(tmp_path, body):
    files = dict(PKG)
    files["app.py"] = "from P import sub\n\n\n" + body
    _, res = link_tree(tmp_path, files)
    assert not any(e.resolution_rule.value == "submodule_attr" for e in res.edges)
