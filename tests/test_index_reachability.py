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
    return entrypoints, edges


def test_reachable_direct_one_hop_from_dunder_main(tmp_path):
    entrypoints, edges = _build(
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

    result = compute_reachability("mod", "target_func", entrypoints, edges)
    assert result.verdict == Verdict.REACHABLE
    assert result.path is not None
    assert len(result.path) == 1
    assert result.path[0].callee_id == "mod:target_func"
    assert result.reason is None


def test_reachable_two_hop_transitive(tmp_path):
    entrypoints, edges = _build(
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

    result = compute_reachability("mod", "target_func", entrypoints, edges)
    assert result.verdict == Verdict.REACHABLE
    assert result.path is not None
    assert [e.callee_id for e in result.path] == ["mod:middle", "mod:target_func"]


def test_reachable_via_ext_target_match(tmp_path):
    entrypoints, edges = _build(
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

    result = compute_reachability("yaml", "load", entrypoints, edges)
    assert result.verdict == Verdict.REACHABLE
    assert result.path is not None
    assert result.path[-1].callee_id == "ext:yaml.load"


def test_reachable_only_from_tests(tmp_path):
    entrypoints, edges = _build(
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

    result = compute_reachability("mod", "target_func", entrypoints, edges)
    assert result.verdict == Verdict.REACHABLE_ONLY_FROM_TESTS
    assert result.path is not None
    assert result.path[-1].callee_id == "mod:target_func"


def test_unknown_via_named_unresolved_attribute_hop(tmp_path):
    entrypoints, edges = _build(
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

    result = compute_reachability("somepkg", "load", entrypoints, edges)
    assert result.verdict == Verdict.UNKNOWN
    assert result.path is not None
    assert result.path[-1].callee_id == "?:load"
    assert result.path[-1].confidence == Confidence.LOW
    assert result.reason is not None


def test_unknown_test_only_low_confidence_ordering(tmp_path):
    # Amendment case: reachable ONLY from a test entrypoint, and only via a
    # low-confidence edge — confidence is checked before entrypoint-source, so this
    # resolves to UNKNOWN, not REACHABLE_ONLY_FROM_TESTS.
    entrypoints, edges = _build(
        tmp_path,
        {
            "test_mod.py": (
                "def test_calls_something():\n"
                "    obj = None\n"
                "    obj.load()\n"
            )
        },
    )

    result = compute_reachability("somepkg", "load", entrypoints, edges)
    assert result.verdict == Verdict.UNKNOWN
    assert result.verdict != Verdict.REACHABLE_ONLY_FROM_TESTS


def test_not_reachable_imported_but_never_called(tmp_path):
    entrypoints, edges = _build(
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

    result = compute_reachability("mod", "target_func", entrypoints, edges)
    assert result.verdict == Verdict.NOT_REACHABLE
    assert result.path is None
    assert result.reason is not None


def test_not_reachable_no_entrypoints(tmp_path):
    write_tree(tmp_path, {"mod.py": "def target_func():\n    return 1\n"})
    report = build_l1_index(tmp_path)
    symbol_index = build_symbol_index(report)
    edges = build_edge_index(report, symbol_index)

    result = compute_reachability("mod", "target_func", [], edges)
    assert result.verdict == Verdict.NOT_REACHABLE
    assert result.path is None
    assert result.reason == "no entrypoints detected in repository"
