from reachability.index.build import build_l1_index
from reachability.index.edges import build_edge_index
from reachability.index.query import find_callers, resolve_import, search_symbol
from reachability.index.symbols import build_symbol_index

from conftest import write_tree


def test_search_symbol_glob_match(tmp_path):
    write_tree(
        tmp_path,
        {
            "mod.py": (
                "class Widget:\n"
                "    def use(self):\n"
                "        return 1\n"
                "\n"
                "def helper():\n"
                "    return 1\n"
            )
        },
    )
    report = build_l1_index(tmp_path)
    symbol_index = build_symbol_index(report)

    matches = search_symbol(symbol_index, "*.use")
    assert {n.node_id for n in matches} == {"mod:Widget.use"}
    assert not any(n.node_id == "mod:helper" for n in matches)


def test_find_callers_returns_matching_edges(tmp_path):
    write_tree(
        tmp_path,
        {
            "mod.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                "def caller_a():\n"
                "    return helper()\n"
                "\n"
                "def caller_b():\n"
                "    return helper()\n"
                "\n"
                "def unrelated():\n"
                "    return 1\n"
            )
        },
    )
    report = build_l1_index(tmp_path)
    symbol_index = build_symbol_index(report)
    edges = build_edge_index(report, symbol_index)

    callers = find_callers(edges, "mod:helper")
    assert {e.caller_id for e in callers} == {"mod:caller_a", "mod:caller_b"}
    assert all(e.callee_id == "mod:helper" for e in callers)


def test_resolve_import_first_party(tmp_path):
    write_tree(
        tmp_path,
        {
            "a.py": "def helper():\n    return 1\n",
            "b.py": "from a import helper\n",
        },
    )
    report = build_l1_index(tmp_path)

    assert resolve_import(report, "b", "helper") == "a:helper"


def test_resolve_import_third_party(tmp_path):
    write_tree(tmp_path, {"mod.py": "from os import getcwd\n"})
    report = build_l1_index(tmp_path)

    assert resolve_import(report, "mod", "getcwd") == "ext:os.getcwd"


def test_resolve_import_returns_none_for_unimported_name(tmp_path):
    write_tree(tmp_path, {"mod.py": "from os import getcwd\n"})
    report = build_l1_index(tmp_path)

    assert resolve_import(report, "mod", "nonexistent") is None


def test_resolve_import_returns_none_for_bare_module_import(tmp_path):
    # Critique blocking item 1's exact repro: a bare "import os" alias has
    # target_symbol=None. Without the target_symbol-is-not-None filter this would
    # build a malformed id ("os:None"/"ext:os.None") instead of correctly returning
    # None for a plain-module import.
    write_tree(tmp_path, {"mod.py": "import os\n"})
    report = build_l1_index(tmp_path)

    assert resolve_import(report, "mod", "os") is None
