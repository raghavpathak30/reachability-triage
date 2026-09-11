from reachability.index.build import build_l1_index
from reachability.index.edges import build_edge_index
from reachability.index.edges_models import Confidence, ResolutionRule
from reachability.index.symbols import build_symbol_index

from conftest import write_tree


def _build_edges(tmp_path, files):
    write_tree(tmp_path, files)
    report = build_l1_index(tmp_path)
    symbol_index = build_symbol_index(report)
    return build_edge_index(report, symbol_index)


def test_obj_load_does_not_resolve_to_same_named_first_party_function(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def load():\n"
                "    return 1\n"
                "\n"
                "class Widget:\n"
                "    def use(self, obj):\n"
                "        obj.load()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:Widget.use")
    assert edge.callee_id == "?:load"
    assert edge.confidence == Confidence.LOW
    assert edge.resolution_rule == ResolutionRule.UNRESOLVED_ATTRIBUTE
    assert not any(e.callee_id == "mod:load" for e in edges)


def test_name_call_resolves_local_scope(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def outer():\n"
                "    def helper():\n"
                "        return 1\n"
                "    return helper()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:outer")
    assert edge.callee_id == "mod:outer.helper"
    assert edge.resolution_rule == ResolutionRule.LOCAL_SCOPE
    assert edge.confidence == Confidence.HIGH


def test_name_call_resolves_enclosing_scope(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def outer():\n"
                "    def helper():\n"
                "        return 1\n"
                "    def middle():\n"
                "        def inner():\n"
                "            return helper()\n"
                "        return inner()\n"
                "    return middle()\n"
            )
        },
    )

    edge = next(e for e in edges if e.resolution_rule == ResolutionRule.ENCLOSING_SCOPE)
    assert edge.callee_id == "mod:outer.helper"
    assert edge.caller_id == "mod:outer.middle.inner"


def test_name_call_resolves_module_scope(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                "def caller():\n"
                "    return helper()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:caller")
    assert edge.callee_id == "mod:helper"
    assert edge.resolution_rule == ResolutionRule.MODULE_SCOPE


def test_name_call_resolves_module_scope_via_alias_one_hop(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def do_thing():\n"
                "    return 1\n"
                "\n"
                "handler = do_thing\n"
                "\n"
                "def caller():\n"
                "    return handler()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:caller")
    assert edge.callee_id == "mod:do_thing"
    assert edge.resolution_rule == ResolutionRule.MODULE_SCOPE


def test_name_call_resolves_import_alias_first_party(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "a.py": "def helper():\n    return 1\n",
            "b.py": (
                "from a import helper\n"
                "\n"
                "def caller():\n"
                "    return helper()\n"
            ),
        },
    )

    edge = next(e for e in edges if e.caller_id == "b:caller")
    assert edge.callee_id == "a:helper"
    assert edge.resolution_rule == ResolutionRule.IMPORT_ALIAS
    assert edge.confidence == Confidence.HIGH


def test_name_call_resolves_import_alias_third_party(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "from os import getcwd\n"
                "\n"
                "def caller():\n"
                "    return getcwd()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:caller")
    assert edge.callee_id == "ext:os.getcwd"
    assert edge.resolution_rule == ResolutionRule.IMPORT_ALIAS
    assert edge.confidence == Confidence.HIGH


def test_name_call_resolves_builtin(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def caller(items):\n"
                "    return len(items)\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:caller")
    assert edge.callee_id == "ext:builtins.len"
    assert edge.resolution_rule == ResolutionRule.BUILTIN
    assert edge.confidence == Confidence.HIGH


def test_name_call_unresolved_fallback(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def caller(callback):\n"
                "    return callback()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:caller")
    assert edge.callee_id == "?:callback"
    assert edge.resolution_rule == ResolutionRule.UNRESOLVED_NAME
    assert edge.confidence == Confidence.LOW


def test_attribute_call_resolves_import_alias_module_first_party(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "a.py": "def helper():\n    return 1\n",
            "b.py": (
                "import a\n"
                "\n"
                "def caller():\n"
                "    return a.helper()\n"
            ),
        },
    )

    edge = next(e for e in edges if e.caller_id == "b:caller")
    assert edge.callee_id == "a:helper"
    assert edge.resolution_rule == ResolutionRule.MODULE_ATTRIBUTE
    assert edge.confidence == Confidence.HIGH


def test_attribute_call_resolves_import_alias_third_party(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "import yaml\n"
                "\n"
                "def caller(data):\n"
                "    return yaml.load(data)\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:caller")
    assert edge.callee_id == "ext:yaml.load"
    assert edge.resolution_rule == ResolutionRule.MODULE_ATTRIBUTE
    assert edge.confidence == Confidence.HIGH


def test_self_method_resolves_within_own_class(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "class Widget:\n"
                "    def helper(self):\n"
                "        return 1\n"
                "    def use(self):\n"
                "        return self.helper()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:Widget.use")
    assert edge.callee_id == "mod:Widget.helper"
    assert edge.resolution_rule == ResolutionRule.SELF_MRO
    assert edge.confidence == Confidence.MEDIUM


def test_self_method_resolves_via_first_party_mro(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "base.py": (
                "class Base:\n"
                "    def helper(self):\n"
                "        return 1\n"
            ),
            "sub.py": (
                "from base import Base\n"
                "\n"
                "class Sub(Base):\n"
                "    def use(self):\n"
                "        return self.helper()\n"
            ),
        },
    )

    edge = next(e for e in edges if e.caller_id == "sub:Sub.use")
    assert edge.callee_id == "base:Base.helper"
    assert edge.resolution_rule == ResolutionRule.SELF_MRO
    assert edge.confidence == Confidence.MEDIUM


def test_self_method_unresolved_when_not_found(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "class Widget:\n"
                "    def use(self):\n"
                "        return self.missing()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:Widget.use")
    assert edge.callee_id == "?:missing"
    assert edge.resolution_rule == ResolutionRule.SELF_UNRESOLVED
    assert edge.confidence == Confidence.LOW


def test_attribute_call_on_arbitrary_expression_unresolved(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "class Widget:\n"
                "    def use(self, obj):\n"
                "        return obj.process()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:Widget.use")
    assert edge.callee_id == "?:process"
    assert edge.resolution_rule == ResolutionRule.UNRESOLVED_ATTRIBUTE
    assert edge.confidence == Confidence.LOW


def test_dynamic_dispatch_getattr(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def caller(obj, name):\n"
                "    return getattr(obj, name)()\n"
            )
        },
    )

    caller_edges = [e for e in edges if e.caller_id == "mod:caller"]
    dynamic_edge = next(e for e in caller_edges if e.resolution_rule == ResolutionRule.DYNAMIC_DISPATCH)
    assert dynamic_edge.callee_id == "?:<dynamic>"
    assert dynamic_edge.confidence == Confidence.LOW

    builtin_edge = next(e for e in caller_edges if e.resolution_rule == ResolutionRule.BUILTIN)
    assert builtin_edge.callee_id == "ext:builtins.getattr"


def test_dynamic_dispatch_dict_lookup(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def caller(handlers, key):\n"
                "    return handlers[key]()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:caller")
    assert edge.callee_id == "?:<dynamic>"
    assert edge.resolution_rule == ResolutionRule.DYNAMIC_DISPATCH
    assert edge.confidence == Confidence.LOW


def test_call_extracted_inside_type_checking_guarded_function(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "from typing import TYPE_CHECKING\n"
                "\n"
                "def helper():\n"
                "    return 1\n"
                "\n"
                "if TYPE_CHECKING:\n"
                "    def guarded():\n"
                "        return helper()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:guarded")
    assert edge.callee_id == "mod:helper"
    assert edge.resolution_rule == ResolutionRule.MODULE_SCOPE


def test_call_extracted_inside_try_except_conditional_function(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                "try:\n"
                "    def maybe():\n"
                "        return helper()\n"
                "except ImportError:\n"
                "    pass\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:maybe")
    assert edge.callee_id == "mod:helper"
    assert edge.resolution_rule == ResolutionRule.MODULE_SCOPE


def test_call_extracted_at_module_top_level(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                "result = helper()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod")
    assert edge.callee_id == "mod:helper"
    assert edge.resolution_rule == ResolutionRule.MODULE_SCOPE


def test_call_extracted_under_dunder_main_guard(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    helper()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod")
    assert edge.callee_id == "mod:helper"
    assert edge.resolution_rule == ResolutionRule.MODULE_SCOPE


def test_decorator_call_attributed_to_enclosing_scope_not_decorated_function(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "def route(path):\n"
                "    def wrapper(fn):\n"
                "        return fn\n"
                "    return wrapper\n"
                "\n"
                '@route("/x")\n'
                "def handler():\n"
                "    return 1\n"
            )
        },
    )

    edge = next(e for e in edges if e.callee_id == "mod:route")
    assert edge.caller_id == "mod"
    assert edge.resolution_rule == ResolutionRule.MODULE_SCOPE
    assert not any(e.caller_id == "mod:handler" and e.callee_id == "mod:route" for e in edges)


def test_alias_with_unresolved_target_does_not_produce_false_high_confidence_edge(tmp_path):
    edges = _build_edges(
        tmp_path,
        {
            "mod.py": (
                "handler = totally_unknown_name\n"
                "\n"
                "def caller():\n"
                "    return handler()\n"
            )
        },
    )

    edge = next(e for e in edges if e.caller_id == "mod:caller")
    assert edge.callee_id == "?:totally_unknown_name"
    assert edge.resolution_rule == ResolutionRule.UNRESOLVED_NAME
    assert edge.confidence == Confidence.LOW


def test_l5_fixture16_module_attribute_not_found_falls_back_to_unresolved(tmp_path):
    # Pins L5 fixture 16 (tests/fixtures/l5/16_module_getattr_pep562): a module
    # with a PEP 562 __getattr__ that synthesizes an attribute at access time --
    # one that is never actually defined as a real symbol in the target module's
    # own symbol table -- must not be confidently resolved via MODULE_ATTRIBUTE.
    # Before this fix, `_resolve_attribute_callee` constructed a node id from the
    # attribute name without checking it exists, producing a confidently wrong
    # HIGH-confidence edge pointing at a symbol that was never defined there.
    edges = _build_edges(
        tmp_path,
        {
            "sink.py": "def target_func():\n    return 1\n",
            "lazy.py": (
                "def __getattr__(name):\n"
                '    if name == "target_func":\n'
                "        from sink import target_func\n"
                "        return target_func\n"
                "    raise AttributeError(name)\n"
            ),
            "entry.py": ("import lazy\n\nlazy.target_func()\n"),
        },
    )

    edge = next(e for e in edges if e.caller_id == "entry")
    assert edge.callee_id == "?:target_func"
    assert edge.resolution_rule == ResolutionRule.UNRESOLVED_ATTRIBUTE
    assert edge.confidence == Confidence.LOW
