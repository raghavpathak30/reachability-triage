from reachability.index.build import build_l1_index
from reachability.index.edges import build_edge_index
from reachability.index.edges_models import Confidence
from reachability.index.entrypoints import build_entrypoint_index
from reachability.index.reachability import compute_reachability
from reachability.index.reachability_models import Verdict
from reachability.index.symbols import build_symbol_index

from conftest import write_tree


def _build(tmp_path, files):
    write_tree(tmp_path, files)
    report = build_l1_index(tmp_path)
    symbol_index = build_symbol_index(report)
    edges = build_edge_index(report, symbol_index)
    entrypoints = build_entrypoint_index(report, symbol_index, tmp_path)
    return entrypoints, edges, report


def test_reachable_direct_one_hop_from_dunder_main(tmp_path):
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "mod.py": (
                "def target_func():\n"
                "    return 1\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    target_func()\n"
            )
        },
    )

    result = compute_reachability("mod", "target_func", entrypoints, edges, report)
    assert result.verdict == Verdict.REACHABLE
    assert result.path is not None
    assert len(result.path) == 1
    assert result.path[0].callee_id == "mod:target_func"
    assert result.reason is None


def test_reachable_two_hop_transitive(tmp_path):
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "mod.py": (
                "def target_func():\n"
                "    return 1\n"
                "\n"
                "def middle():\n"
                "    return target_func()\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    middle()\n"
            )
        },
    )

    result = compute_reachability("mod", "target_func", entrypoints, edges, report)
    assert result.verdict == Verdict.REACHABLE
    assert result.path is not None
    assert [e.callee_id for e in result.path] == ["mod:middle", "mod:target_func"]


def test_reachable_via_ext_target_match(tmp_path):
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "mod.py": (
                "import yaml\n"
                "\n"
                "def caller():\n"
                '    return yaml.load("x")\n'
                "\n"
                'if __name__ == "__main__":\n'
                "    caller()\n"
            )
        },
    )

    result = compute_reachability("yaml", "load", entrypoints, edges, report)
    assert result.verdict == Verdict.REACHABLE
    assert result.path is not None
    assert result.path[-1].callee_id == "ext:yaml.load"


def test_reachable_only_from_tests(tmp_path):
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "mod.py": "def target_func():\n    return 1\n",
            "test_mod.py": (
                "from mod import target_func\n"
                "\n"
                "def test_calls_target():\n"
                "    return target_func()\n"
            ),
        },
    )

    result = compute_reachability("mod", "target_func", entrypoints, edges, report)
    assert result.verdict == Verdict.REACHABLE_ONLY_FROM_TESTS
    assert result.path is not None
    assert result.path[-1].callee_id == "mod:target_func"


def test_unknown_via_named_unresolved_attribute_hop(tmp_path):
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "mod.py": (
                "def run(obj):\n"
                "    obj.load()\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    run(None)\n"
            )
        },
    )

    result = compute_reachability("somepkg", "load", entrypoints, edges, report)
    assert result.verdict == Verdict.UNKNOWN
    assert result.path is not None
    assert result.path[-1].callee_id == "?:load"
    assert result.path[-1].confidence == Confidence.LOW
    assert result.reason is not None


def test_unknown_test_only_low_confidence_ordering(tmp_path):
    # Amendment case: reachable ONLY from a test entrypoint, and only via a
    # low-confidence edge — confidence is checked before entrypoint-source, so this
    # resolves to UNKNOWN, not REACHABLE_ONLY_FROM_TESTS.
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "test_mod.py": (
                "def test_calls_something():\n"
                "    obj = None\n"
                "    obj.load()\n"
            )
        },
    )

    result = compute_reachability("somepkg", "load", entrypoints, edges, report)
    assert result.verdict == Verdict.UNKNOWN
    assert result.verdict != Verdict.REACHABLE_ONLY_FROM_TESTS


def test_not_reachable_imported_but_never_called(tmp_path):
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "mod.py": (
                "def target_func():\n"
                "    return 1\n"
                "\n"
                "def run():\n"
                "    return 1\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    run()\n"
            )
        },
    )

    result = compute_reachability("mod", "target_func", entrypoints, edges, report)
    assert result.verdict == Verdict.NOT_REACHABLE
    assert result.path is None
    assert result.reason is not None


def test_not_reachable_no_entrypoints(tmp_path):
    write_tree(tmp_path, {"mod.py": "def target_func():\n    return 1\n"})
    report = build_l1_index(tmp_path)
    symbol_index = build_symbol_index(report)
    edges = build_edge_index(report, symbol_index)

    result = compute_reachability("mod", "target_func", [], edges, report)
    assert result.verdict == Verdict.NOT_REACHABLE
    assert result.path is None
    assert result.reason == "no entrypoints detected in repository"


def test_l5_fixture13_nameless_getattr_dispatch_yields_unknown_not_not_reachable(tmp_path):
    # Pins L5 fixture 13 (tests/fixtures/l5/13_getattr_dynamic_dispatch): a call
    # target reached only through getattr(mod, name)() with no literal name at the
    # call site must not be confidently ruled not_reachable.
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "sink.py": "def target_func():\n    return 1\n",
            "entry.py": (
                "import sink\n"
                "\n"
                "def dispatch(name):\n"
                "    getattr(sink, name)()\n"
                "\n"
                'if __name__ == "__main__":\n'
                '    dispatch("target_func")\n'
            ),
        },
    )

    result = compute_reachability("sink", "target_func", entrypoints, edges, report)
    assert result.verdict == Verdict.UNKNOWN
    assert result.verdict != Verdict.NOT_REACHABLE


def test_l5_fixture18_eval_call_yields_unknown_not_not_reachable(tmp_path):
    # Pins L5 fixture 18 (tests/fixtures/l5/18_eval_string_call): a call target
    # that exists only inside a string executed by eval() must not be confidently
    # ruled not_reachable, since this engine never parses eval'd strings as code.
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "sink.py": "def target_func():\n    return 1\n",
            "entry.py": (
                "from sink import target_func\n"
                "\n"
                "def run():\n"
                '    eval("target_func()")\n'
                "\n"
                'if __name__ == "__main__":\n'
                "    run()\n"
            ),
        },
    )

    result = compute_reachability("sink", "target_func", entrypoints, edges, report)
    assert result.verdict == Verdict.UNKNOWN
    assert result.verdict != Verdict.NOT_REACHABLE


def test_l5_fixture14_registry_dict_dispatch_yields_unknown_not_not_reachable(tmp_path):
    # Pins L5 fixture 14 (tests/fixtures/l5/14_registry_dict_dispatch): a call
    # through HANDLERS[key]() hits the same nameless dynamic-dispatch marker as
    # getattr dispatch (edges.py's `isinstance(func, (ast.Call, ast.Subscript))`
    # branch), so it must also not be confidently ruled not_reachable. Fixed as a
    # side effect of D1, before D3 existed -- see DECISIONS.md.
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "sink.py": "def target_func():\n    return 1\n",
            "registry.py": ("from sink import target_func\n\nHANDLERS = {\"go\": target_func}\n"),
            "entry.py": (
                "from registry import HANDLERS\n"
                "\n"
                "def dispatch(key):\n"
                "    HANDLERS[key]()\n"
                "\n"
                'if __name__ == "__main__":\n'
                '    dispatch("go")\n'
            ),
        },
    )

    result = compute_reachability("sink", "target_func", entrypoints, edges, report)
    assert result.verdict == Verdict.UNKNOWN
    assert result.verdict != Verdict.NOT_REACHABLE


def test_l5_fixture17_bare_name_argument_yields_unknown_not_not_reachable(tmp_path):
    # Pins L5 fixture 17 (tests/fixtures/l5/17_framework_callback_reference): a
    # function passed BY REFERENCE as a call argument (never itself the `.func` of
    # a Call) produces no call edge naming it at all, since L3 only inspects
    # `call.func`, never `call.args`/`call.keywords`. A confident not_reachable
    # verdict cannot be given when the target's name is loaded anywhere outside a
    # call position in the repo.
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "sink.py": "def target_func():\n    return 1\n",
            "framework.py": (
                "class App:\n"
                "    def on_event(self, name, handler):\n"
                "        pass\n"
                "\n"
                "    def run(self):\n"
                "        pass\n"
            ),
            "entry.py": (
                "from sink import target_func\n"
                "from framework import App\n"
                "\n"
                "app = App()\n"
                'app.on_event("startup", target_func)\n'
                "\n"
                'if __name__ == "__main__":\n'
                "    app.run()\n"
            ),
        },
    )

    result = compute_reachability("sink", "target_func", entrypoints, edges, report)
    assert result.verdict == Verdict.UNKNOWN
    assert result.verdict != Verdict.NOT_REACHABLE


def test_l5_fixture19_monkeypatch_reference_yields_unknown_not_not_reachable(tmp_path):
    # Pins L5 fixture 19 (tests/fixtures/l5/19_monkeypatched_test_target): a
    # function referenced only as a bare-Name argument to monkeypatch.setattr (or
    # any other call) produces no static call edge naming it, so a confident
    # not_reachable verdict cannot be given.
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "sink.py": "def target_func():\n    return 1\n",
            "decoy.py": "def safe():\n    return 0\n",
            "entry.py": (
                "import decoy\n\ndef run():\n    decoy.safe()\n\nif __name__ == \"__main__\":\n    run()\n"
            ),
            "tests/test_monkeypatch.py": (
                "import decoy\n"
                "from sink import target_func\n"
                "from entry import run\n"
                "\n"
                "def test_it(monkeypatch):\n"
                '    monkeypatch.setattr(decoy, "safe", target_func)\n'
                "    run()\n"
            ),
        },
    )

    result = compute_reachability("sink", "target_func", entrypoints, edges, report)
    assert result.verdict == Verdict.UNKNOWN
    assert result.verdict != Verdict.NOT_REACHABLE
