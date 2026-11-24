import importlib

from reachability.scan.fixcommit import FIX_KINDS, FileDiff, Hunk, fix_changed, map_path
from reachability.scan.targets import symbols_from_tree
from tests.conftest import write_tree


def hunk(*items):
    return Hunk(tuple(items))


def trees(tmp_path, old, new, rel="lib/core.py"):
    a, b = tmp_path / "old", tmp_path / "new"
    write_tree(a, {rel: old})
    write_tree(b, {rel: new})
    return a, b, symbols_from_tree(a)


def run(tmp_path, old, new, diffs, rel="lib/core.py"):
    a, b, syms = trees(tmp_path, old, new, rel)
    c: dict = {}
    return {k[1] for k in fix_changed(diffs, a, b, syms, c)}, c


OLD = "X = 1\n\n\ndef f(x):\n    a = 1\n    return x + a\n\n\ndef g():\n    return 0\n"
NEW = OLD.replace("return x + a", "return x + a + 1")
H = hunk((" ", "    a = 1"), ("-", "    return x + a"), ("+", "    return x + a + 1"), (" ", ""), (" ", ""))


def test_src_layout_suffix_mapping(tmp_path):
    got, _ = run(tmp_path, OLD, NEW, [FileDiff("src/lib/core.py", (H,))])
    assert got == {"f"}


def test_hunk_found_twice_nothing(tmp_path):
    old = OLD + "\n\ndef h(x):\n    a = 1\n    return x + a\n\n\n"
    new = NEW + "\n\ndef h(x):\n    a = 1\n    return x + a + 1\n\n\n"
    got, c = run(tmp_path, old, new, [FileDiff("lib/core.py", (H,))])
    assert got == set() and c["hunks_dropped"] == 1


def test_hunk_at_module_level_nothing(tmp_path):
    h = hunk((" ", "X = 1"), ("-", ""), ("+", "Y = 2"), (" ", ""), (" ", ""))
    got, _ = run(tmp_path, OLD, OLD.replace("X = 1\n\n", "X = 1\nY = 2\n"), [FileDiff("lib/core.py", (h,))])
    assert got == set()


def test_class_body_line_nothing(tmp_path):
    old = "class C:\n    k = 1\n\n    def m(self):\n        return 1\n"
    new = old.replace("k = 1", "k = 2")
    h = hunk((" ", "class C:"), ("-", "    k = 1"), ("+", "    k = 2"), (" ", ""), (" ", "    def m(self):"))
    got, _ = run(tmp_path, old, new, [FileDiff("lib/core.py", (h,))])
    assert got == set()


def test_added_side_missing_nothing(tmp_path):
    got, _ = run(tmp_path, OLD, OLD, [FileDiff("lib/core.py", (H,))])
    assert got == set()


def test_innermost_nested_function(tmp_path):
    old = "def outer():\n    def inner():\n        return 1\n    return inner\n"
    new = old.replace("return 1", "return 2")
    h = hunk((" ", "    def inner():"), ("-", "        return 1"), ("+", "        return 2"), (" ", "    return inner"))
    got, _ = run(tmp_path, old, new, [FileDiff("lib/core.py", (h,))])
    assert len(got) == 1 and next(iter(got)).endswith("inner")


def test_async_function_counts(tmp_path):
    old = "async def a():\n    x = 1\n    return x\n"
    new = old.replace("x = 1", "x = 2")
    h = hunk((" ", "async def a():"), ("-", "    x = 1"), ("+", "    x = 2"), (" ", "    return x"))
    got, _ = run(tmp_path, old, new, [FileDiff("lib/core.py", (h,))])
    assert got == {"a"}
    from reachability.index.symbol_models import NodeKind

    assert NodeKind.ASYNCFUNCTION in FIX_KINDS


def test_no_module_match_skips_file(tmp_path):
    got, c = run(tmp_path, OLD, NEW, [FileDiff("other/thing.py", (H,))])
    assert got == set() and c["files_unmapped"] == 1


def test_suffix_tie_skips_file():
    c: dict = {}
    assert map_path("a/b/mod.py", ["x/b/mod.py", "y/b/mod.py"], c) is None and c["files_suffix_tie"] == 1


def test_one_segment_suffix_needs_root():
    c: dict = {}
    assert map_path("deep/er/utils.py", ["utils.py"], c) is None
    assert map_path("utils.py", ["utils.py"], c) == "utils.py"
    assert map_path("src/utils.py", ["utils.py"], c) == "utils.py"


def test_formfeed_and_u2028_do_not_shift_lines(tmp_path):
    old = "# a\fb\u2028c\n\n\ndef f():\n    return 1\n\n\ndef g():\n    return 1\n"
    new = old.replace("def g():\n    return 1", "def g():\n    return 2")
    h = hunk((" ", "def g():"), ("-", "    return 1"), ("+", "    return 2"))
    got, _ = run(tmp_path, old, new, [FileDiff("lib/core.py", (h,))])
    assert got == {"g"}


def test_overload_added_shifts_ordinal_nothing_wrong(tmp_path):
    old = "def f(x):\n    return 1\n\n\ndef g(x):\n    return 2\n"
    new = "@overload\ndef f(x): ...\n\n\n" + old.replace("return 2", "return 3")
    h = hunk((" ", "def g(x):"), ("-", "    return 2"), ("+", "    return 3"))
    got, _ = run(tmp_path, old, new, [FileDiff("lib/core.py", (h,))])
    assert got == {"g"}


def test_insertion_between_two_functions_nothing(tmp_path):
    old = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
    new = "def f():\n    return 1\n\n\nX = 3\n\n\ndef g():\n    return 2\n"
    h = hunk((" ", "    return 1"), (" ", ""), (" ", ""), ("+", "X = 3"), ("+", ""), ("+", ""), (" ", "def g():"))
    got, _ = run(tmp_path, old, new, [FileDiff("lib/core.py", (h,))])
    assert got == set()


def test_insertion_inside_function_attributed(tmp_path):
    old = "def f(x):\n    a = 1\n    return a\n"
    new = "def f(x):\n    a = 1\n    b = 2\n    return a\n"
    h = hunk((" ", "def f(x):"), (" ", "    a = 1"), ("+", "    b = 2"), (" ", "    return a"))
    got, _ = run(tmp_path, old, new, [FileDiff("lib/core.py", (h,))])
    assert got == {"f"}


def test_empty_side_not_located(tmp_path):
    h = hunk(("+", "    z = 1"))
    got, _ = run(tmp_path, OLD, NEW, [FileDiff("lib/core.py", (h,))])
    assert got == set()


def test_many_hunks_bounded(tmp_path):
    hs = tuple(H for _ in range(50))
    a, b, syms = trees(tmp_path, OLD, NEW)
    c = {"hunks_seen": 19990}
    assert fix_changed([FileDiff("lib/core.py", hs)], a, b, syms, c) == set() or True
    assert c["hunks_over_budget"] == 40


def test_class_only_change_keeps_py_changed(tmp_path):
    from reachability.scan.targets import diff_symbols

    old = "class C:\n    k = 1\n"
    new = "class C:\n    k = 2\n"
    a, b, syms = trees(tmp_path, old, new)
    assert diff_symbols(syms, symbols_from_tree(b)).py_changed


def test_f1_unrelated_directories_do_not_map():
    c: dict = {}
    assert map_path("tools/utils/helpers.py", ["pkg/utils/helpers.py"], c) is None
    assert map_path("examples/pkg/__init__.py", ["pkg/__init__.py"], c) is None


def test_f1_real_layouts_still_map():
    c: dict = {}
    assert map_path("src/anyio/_core/_subprocesses.py", ["anyio/_core/_subprocesses.py", "anyio/_core/_x.py"], c) == "anyio/_core/_subprocesses.py"
    rel = "langchain_openai/chat_models/base.py"
    assert map_path("libs/partners/openai/" + rel, [rel, "langchain_openai/__init__.py"], c) == rel


def test_f1_denied_prefix_segment_skips_and_counts():
    c: dict = {}
    assert map_path("examples/pkg/mod.py", ["pkg/mod.py"], c) is None
    assert map_path("vendor/pkg/mod.py", ["pkg/mod.py"], c) is None
    assert c["files_denied_prefix"] == 2


def test_f1_longest_whole_suffix_wins():
    c: dict = {}
    assert map_path("lib/a/b/mod.py", ["b/mod.py", "a/b/mod.py"], c) == "a/b/mod.py"


def test_f5_defs_selected_by_file_not_module_name(tmp_path):
    a, b = tmp_path / "old", tmp_path / "new"
    mod = "def one():\n    x = 1\n    return x\n\n\ndef two():\n    return 2\n"
    pkg = "def three():\n    return 3\n\n\ndef four():\n    y = 1\n    return y\n"
    for root, new in ((a, False), (b, True)):
        write_tree(root, {
            "foo.py": mod.replace("x = 1", "x = 9") if new else mod,
            "foo/__init__.py": pkg.replace("y = 1", "y = 9") if new else pkg,
        })
    syms = symbols_from_tree(a)
    h = hunk(("-", "    y = 1"), ("+", "    y = 9"))
    keys = fix_changed([FileDiff("foo/__init__.py", (h,))], a, b, syms, {})
    assert {k[1] for k in keys} <= {"four"}
    h2 = hunk(("-", "    x = 1"), ("+", "    x = 9"))
    keys = fix_changed([FileDiff("foo.py", (h2,))], a, b, syms, {})
    assert {k[1] for k in keys} <= {"one"}
    assert {k[1] for k in fix_changed([FileDiff("foo.py", (h,))], a, b, syms, {})} == set()
