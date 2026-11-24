from tests.scan.s15_support import link_tree, triples

PKG = {"P/__init__.py": "", "P/sub.py": "def f(a):\n    return a\n\n\ndef g(a):\n    return a\n"}


def _app(body, imp="from P import sub\n"):
    files = dict(PKG)
    files["app.py"] = imp + "\n\ndef run(a, b):\n" + body
    return files


def test_submodule_attr_positive(tmp_path):
    idx, res = link_tree(tmp_path, _app("    return sub.f(a)\n"))
    assert any(e.callee_id == "?:f" for e in idx.edges)
    assert triples(res) == {("app:run", "P.sub:f", 5, "submodule_attr")}


def test_submodule_shadowed_in_package_init_no_edge(tmp_path):
    files = _app("    return sub.f(a)\n")
    files["P/__init__.py"] = "sub = object()\n"
    _, res = link_tree(tmp_path, files)
    assert res.edges == []


def test_submodule_star_import_in_package_init_no_edge(tmp_path):
    files = _app("    return sub.f(a)\n")
    files["P/__init__.py"] = "from other import *\n"
    files["other.py"] = "x = 1\n"
    _, res = link_tree(tmp_path, files)
    assert res.edges == []


def test_submodule_two_calls_on_one_line_no_edge(tmp_path):
    files = _app("    return sub.f(a) + other.f(b)\n")
    files["app.py"] = "import other\n" + files["app.py"]
    files["other.py"] = "def f(a):\n    return a\n"
    _, res = link_tree(tmp_path, files)
    assert not any(e.resolution_rule.value == "submodule_attr" for e in res.edges)


def test_submodule_conditional_import_no_edge(tmp_path):
    _, res = link_tree(tmp_path, _app("    return sub.f(a)\n", "try:\n    from P import sub\nexcept ImportError:\n    sub = None\n"))
    assert res.edges == []


def test_submodule_parameter_shadow_no_edge(tmp_path):
    files = _app("    return sub.f(a)\n")
    files["app.py"] = files["app.py"].replace("def run(a, b):", "def run(a, sub):")
    _, res = link_tree(tmp_path, files)
    assert res.edges == []


def test_submodule_missing_function_no_edge(tmp_path):
    _, res = link_tree(tmp_path, _app("    return sub.nothere(a)\n"))
    assert res.edges == []
