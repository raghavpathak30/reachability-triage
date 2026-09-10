from reachability.index.build import build_l1_index
from reachability.index.symbol_models import NodeKind
from reachability.index.symbols import build_symbol_index

from conftest import write_tree


def test_plain_top_level_function(tmp_path):
    write_tree(tmp_path, {"mod.py": "def foo():\n    pass\n"})

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    nodes = tables["mod"].nodes
    foo = next(n for n in nodes if n.node_id == "mod:foo")
    assert foo.kind == NodeKind.FUNCTION
    assert foo.lineno == 1
    assert foo.decorators == []


def test_plain_class_with_one_method(tmp_path):
    write_tree(
        tmp_path,
        {"mod.py": "class C:\n    def m(self):\n        pass\n"},
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    nodes = tables["mod"].nodes
    c = next(n for n in nodes if n.node_id == "mod:C")
    assert c.kind == NodeKind.CLASS
    assert c.bases == []
    assert c.lineno == 1

    m = next(n for n in nodes if n.node_id == "mod:C.m")
    assert m.kind == NodeKind.METHOD
    assert m.is_async is False
    assert m.lineno == 2


def test_async_method(tmp_path):
    write_tree(
        tmp_path,
        {"mod.py": "class C:\n    async def m(self):\n        pass\n"},
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    m = next(n for n in tables["mod"].nodes if n.node_id == "mod:C.m")
    assert m.kind == NodeKind.METHOD
    assert m.is_async is True


def test_async_top_level_function(tmp_path):
    write_tree(tmp_path, {"mod.py": "async def f():\n    pass\n"})

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    f = next(n for n in tables["mod"].nodes if n.node_id == "mod:f")
    assert f.kind == NodeKind.ASYNCFUNCTION
    assert f.is_async is True


def test_nested_function_single_level(tmp_path):
    write_tree(
        tmp_path,
        {"mod.py": "def outer():\n    def inner():\n        pass\n"},
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    node_ids = {n.node_id for n in tables["mod"].nodes}
    assert "mod:outer" in node_ids
    assert "mod:outer.inner" in node_ids

    outer = next(n for n in tables["mod"].nodes if n.node_id == "mod:outer")
    inner = next(n for n in tables["mod"].nodes if n.node_id == "mod:outer.inner")
    assert outer.kind == NodeKind.FUNCTION
    assert outer.lineno == 1
    assert inner.kind == NodeKind.FUNCTION
    assert inner.lineno == 2


def test_nested_function_duplicate_sibling_redefinition(tmp_path):
    source = (
        "def outer():\n"
        "    def inner():\n"
        "        pass\n"
        "def outer():\n"
        "    def inner():\n"
        "        pass\n"
    )
    write_tree(tmp_path, {"mod.py": source})

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    node_ids = {n.node_id for n in tables["mod"].nodes}
    assert "mod:outer@1" in node_ids
    assert "mod:outer@1.inner" in node_ids
    assert "mod:outer@4" in node_ids
    assert "mod:outer@4.inner" in node_ids
    assert "mod:outer.inner" not in node_ids
    assert "mod:outer" not in node_ids


def test_nested_function_duplicate_if_else_branches(tmp_path):
    source = (
        "if cond:\n"
        "    def outer():\n"
        "        def inner():\n"
        "            pass\n"
        "else:\n"
        "    def outer():\n"
        "        def inner():\n"
        "            pass\n"
    )
    write_tree(tmp_path, {"mod.py": source})

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    node_ids = {n.node_id for n in tables["mod"].nodes}
    assert "mod:outer@2" in node_ids
    assert "mod:outer@2.inner" in node_ids
    assert "mod:outer@6" in node_ids
    assert "mod:outer@6.inner" in node_ids
    assert "mod:outer.inner" not in node_ids


def test_class_with_first_party_base(tmp_path):
    write_tree(
        tmp_path,
        {
            "mod1.py": "class Base:\n    pass\n",
            "mod2.py": "from mod1 import Base\n\n\nclass Sub(Base):\n    pass\n",
        },
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    sub = next(n for n in tables["mod2"].nodes if n.node_id == "mod2:Sub")
    assert sub.bases == ["mod1:Base"]


def test_class_with_third_party_or_unresolved_base(tmp_path):
    write_tree(
        tmp_path,
        {"mod.py": "class Sub(SomeThirdPartyBase):\n    pass\n"},
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    sub = next(n for n in tables["mod"].nodes if n.node_id == "mod:Sub")
    assert sub.bases == ["SomeThirdPartyBase"]


def test_decorator_resolves_via_plain_import_name(tmp_path):
    write_tree(
        tmp_path,
        {
            "pkgb.py": "def deco(f):\n    return f\n",
            "pkga.py": "from pkgb import deco\n\n\n@deco\ndef f():\n    pass\n",
        },
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    f = next(n for n in tables["pkga"].nodes if n.node_id == "pkga:f")
    assert f.decorators == ["pkgb:deco"]


def test_decorator_resolves_via_aliased_attribute_chain(tmp_path):
    write_tree(
        tmp_path,
        {
            "pkgb/__init__.py": "",
            "pkgb/sub.py": "def deco(f):\n    return f\n",
            "pkga.py": "import pkgb.sub as pb\n\n\n@pb.deco\ndef f():\n    pass\n",
        },
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    f = next(n for n in tables["pkga"].nodes if n.node_id == "pkga:f")
    assert f.decorators == ["pkgb.sub:deco"]


def test_decorator_unresolvable_dynamic(tmp_path):
    write_tree(
        tmp_path,
        {"mod.py": "@some_undefined_name\ndef f():\n    pass\n"},
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    f = next(n for n in tables["mod"].nodes if n.node_id == "mod:f")
    assert f.decorators == ["?:some_undefined_name"]


def test_decorator_call_form_preserves_dotted_text(tmp_path):
    write_tree(
        tmp_path,
        {"mod.py": "@app.get(\"/x\")\ndef f():\n    pass\n"},
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    f = next(n for n in tables["mod"].nodes if n.node_id == "mod:f")
    assert f.decorators == ["?:app.get"]


def test_module_level_callable_alias_plain_assignment(tmp_path):
    write_tree(
        tmp_path,
        {"mod.py": "def do_thing():\n    pass\n\n\nhandler = do_thing\n"},
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    handler = next(n for n in tables["mod"].nodes if n.node_id == "mod:handler")
    assert handler.kind == NodeKind.ALIAS
    assert handler.target_id == "mod:do_thing"


def test_module_level_lambda_assignment(tmp_path):
    write_tree(
        tmp_path,
        {"mod.py": "handler = lambda: None\n"},
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    handler = next(n for n in tables["mod"].nodes if n.node_id == "mod:handler")
    assert handler.kind == NodeKind.FUNCTION
    assert handler.decorators == []
    assert handler.lineno == 1


def test_module_level_def_alias_collision_both_suffixed(tmp_path):
    source = (
        "def handler():\n"
        "    pass\n"
        "\n"
        "\n"
        "something_else = 1\n"
        "handler = something_else\n"
    )
    write_tree(tmp_path, {"mod.py": source})

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    node_ids = {n.node_id: n for n in tables["mod"].nodes}
    assert "mod:handler@1" in node_ids
    assert node_ids["mod:handler@1"].kind == NodeKind.FUNCTION
    assert "mod:handler@6" in node_ids
    assert node_ids["mod:handler@6"].kind == NodeKind.ALIAS
    assert node_ids["mod:handler@6"].target_id is not None
    assert "mod:handler" not in node_ids


def test_module_level_call_expression_assignment_is_not_an_alias(tmp_path):
    write_tree(
        tmp_path,
        {"mod.py": "def factory():\n    pass\n\n\nhandler = factory()\n"},
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    aliases = [n for n in tables["mod"].nodes if n.kind == NodeKind.ALIAS]
    assert not any(n.qualname == "handler" for n in aliases)


def test_every_module_gets_implicit_module_node(tmp_path):
    write_tree(tmp_path, {"mod.py": "x = 1\n"})

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    nodes = tables["mod"].nodes
    assert len(nodes) == 1
    assert nodes[0].node_id == "mod"
    assert nodes[0].kind == NodeKind.MODULE
    assert nodes[0].qualname == ""
    assert nodes[0].lineno == 1


def test_unparsed_module_skipped_from_symbol_index(tmp_path):
    write_tree(
        tmp_path,
        {
            "good.py": "import os\n",
            "bad.py": "def broken(:\n",
        },
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    assert "bad" not in tables
    assert "good" in tables


def test_l2_gate_fixture_exact_node_set(tmp_path):
    write_tree(
        tmp_path,
        {
            "pkgb.py": "def deco(f):\n    return f\n",
            "mod.py": (
                "from pkgb import deco\n"
                "\n"
                "\n"
                "def outer():\n"
                "    def inner():\n"
                "        pass\n"
                "\n"
                "\n"
                "class C:\n"
                "    def m(self):\n"
                "        pass\n"
                "\n"
                "    async def am(self):\n"
                "        pass\n"
                "\n"
                "\n"
                "@deco\n"
                "def decorated():\n"
                "    pass\n"
                "\n"
                "\n"
                "handler = decorated\n"
            ),
        },
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    nodes = {n.node_id: n for n in tables["mod"].nodes}

    expected_ids = {
        "mod",
        "mod:outer",
        "mod:outer.inner",
        "mod:C",
        "mod:C.m",
        "mod:C.am",
        "mod:decorated",
        "mod:handler",
    }
    assert set(nodes.keys()) == expected_ids

    assert nodes["mod:outer"].lineno == 4
    assert nodes["mod:outer.inner"].lineno == 5
    assert nodes["mod:C"].lineno == 9
    assert nodes["mod:C.m"].lineno == 10
    assert nodes["mod:C.am"].lineno == 13
    assert nodes["mod:C.am"].is_async is True
    assert nodes["mod:decorated"].lineno == 18
    assert nodes["mod:decorated"].decorators == ["pkgb:deco"]
    assert nodes["mod:handler"].kind == NodeKind.ALIAS
    assert nodes["mod:handler"].target_id == "mod:decorated"


def test_type_checking_guarded_def(tmp_path):
    source = (
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    def outer():\n"
        "        def inner():\n"
        "            pass\n"
        "\n"
        "\n"
        "def plain():\n"
        "    pass\n"
        "\n"
        "\n"
        "try:\n"
        "    def fast():\n"
        "        pass\n"
        "except ImportError:\n"
        "    def fast():\n"
        "        pass\n"
    )
    write_tree(tmp_path, {"mod.py": source})

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    nodes = {n.qualname: n for n in tables["mod"].nodes if n.qualname}

    outer = nodes["outer"]
    assert outer.type_checking_only is True
    inner = nodes["outer.inner"]
    assert inner.type_checking_only is True

    plain = nodes["plain"]
    assert plain.type_checking_only is False
    assert plain.conditional is False

    fast_nodes = [n for n in tables["mod"].nodes if n.qualname.startswith("fast")]
    assert len(fast_nodes) == 2
    assert all(n.conditional is True for n in fast_nodes)


def test_decorator_call_form_bare_name(tmp_path):
    write_tree(
        tmp_path,
        {"mod.py": "@task(schedule=\"*/5\", retries=3)\ndef f():\n    pass\n"},
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    f = next(n for n in tables["mod"].nodes if n.node_id == "mod:f")
    assert f.decorators == ["?:task"]


def test_nested_descendant_qualname_follows_late_discovered_collision(tmp_path):
    source = (
        "def foo():\n"
        "    def inner():\n"
        "        pass\n"
        "\n"
        "\n"
        "something_else = 1\n"
        "foo = something_else\n"
    )
    write_tree(tmp_path, {"mod.py": source})

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    node_ids = {n.node_id: n for n in tables["mod"].nodes}
    assert "mod:foo@1" in node_ids
    assert "mod:foo@7" in node_ids
    assert "mod:foo" not in node_ids
    assert "mod:foo.inner" not in node_ids
    assert "mod:foo@1.inner" in node_ids


def test_ambiguous_local_defs_block_import_fallthrough(tmp_path):
    write_tree(
        tmp_path,
        {
            "x.py": "def foo():\n    pass\n",
            "mod.py": (
                "from x import foo\n"
                "\n"
                "\n"
                "def foo():\n"
                "    pass\n"
                "\n"
                "\n"
                "def foo():\n"
                "    pass\n"
                "\n"
                "\n"
                "@foo\n"
                "def g():\n"
                "    pass\n"
            ),
        },
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    g = next(n for n in tables["mod"].nodes if n.qualname == "g")
    assert g.decorators == ["?:foo"]


def test_alias_target_id_resolution(tmp_path):
    source = (
        "def do_thing():\n"
        "    pass\n"
        "\n"
        "\n"
        "handler = do_thing\n"
        "handler2 = some_undefined_name\n"
    )
    write_tree(tmp_path, {"mod.py": source})

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    handler = next(n for n in tables["mod"].nodes if n.node_id == "mod:handler")
    assert handler.target_id == "mod:do_thing"

    handler2 = next(n for n in tables["mod"].nodes if n.node_id == "mod:handler2")
    assert handler2.target_id == "?:some_undefined_name"


def test_multiway_collision_with_deep_nesting_no_dangling_nodes(tmp_path):
    source = (
        "def foo():\n"
        "    def inner_a():\n"
        "        def deepest():\n"
        "            pass\n"
        "def foo():\n"
        "    def inner_b():\n"
        "        pass\n"
        "something_else = 1\n"
        "foo = something_else\n"
    )
    write_tree(tmp_path, {"mod.py": source})

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    node_ids = {n.node_id: n for n in tables["mod"].nodes}

    # No unsuffixed / stale-prefix variant may exist.
    assert "mod:foo" not in node_ids
    assert "mod:foo.inner_a" not in node_ids
    assert "mod:foo.inner_a.deepest" not in node_ids
    assert "mod:foo.inner_b" not in node_ids

    # Three-way collision (two defs + one alias) — every member suffixed by its own lineno.
    assert "mod:foo@1" in node_ids
    assert "mod:foo@5" in node_ids
    assert "mod:foo@9" in node_ids
    assert node_ids["mod:foo@1"].kind == NodeKind.FUNCTION
    assert node_ids["mod:foo@5"].kind == NodeKind.FUNCTION
    assert node_ids["mod:foo@9"].kind == NodeKind.ALIAS

    # Descendants two levels deep follow the suffixed parent, not the stale one.
    assert "mod:foo@1.inner_a" in node_ids
    assert "mod:foo@1.inner_a.deepest" in node_ids
    assert "mod:foo@5.inner_b" in node_ids


def test_single_conditional_def_collision_with_import_blocks_fallthrough(tmp_path):
    write_tree(
        tmp_path,
        {
            "x.py": "def foo():\n    pass\n",
            "mod.py": (
                "from typing import TYPE_CHECKING\n"
                "from x import foo\n"
                "\n"
                "\n"
                "if TYPE_CHECKING:\n"
                "    def foo():\n"
                "        pass\n"
                "\n"
                "\n"
                "@foo\n"
                "def g():\n"
                "    pass\n"
            ),
        },
    )

    report = build_l1_index(tmp_path)
    tables = build_symbol_index(report)

    g = next(n for n in tables["mod"].nodes if n.qualname == "g")
    assert g.decorators == ["?:foo"]
