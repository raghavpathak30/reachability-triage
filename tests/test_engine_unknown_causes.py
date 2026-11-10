"""v0.3.4 -- engine-produced UNKNOWN reasons (reachability.py steps 3/4/5) get
their own `TerminationCause`. Literals below are copied from the strings
`compute_reachability` returned at 19a3b59, so they pin the reason text
independently of the constants."""

from pathlib import Path

import pytest

from reachability.index.build import build_l1_index
from reachability.index.edges import build_edge_index
from reachability.index.edges_models import CallEdge, Confidence, ResolutionRule
from reachability.index.entrypoint_models import Entrypoint, EntrypointSource
from reachability.index.entrypoints import build_entrypoint_index
from reachability.index.models import DiscoveryReport
from reachability.index.reachability import compute_reachability
from reachability.index.reachability_models import Verdict
from reachability.index.symbols import build_symbol_index
from reachability.triage.agent_models import TriageFinding
from reachability.triage.termination_cause import TerminationCause, compute_termination_cause

from conftest import write_tree

S3_REASON_19A3B59 = "reachable only via low-confidence edge(s): unresolved_attribute"


def _build(tmp_path, files):
    write_tree(tmp_path, files)
    report = build_l1_index(tmp_path)
    symbol_index = build_symbol_index(report)
    edges = build_edge_index(report, symbol_index)
    entrypoints = build_entrypoint_index(report, symbol_index, tmp_path)
    return entrypoints, edges, report


def _finding(result):
    return TriageFinding(result=result, rationale="", tool_calls=[])


def _s3_result(tmp_path):
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
    return compute_reachability("somepkg", "load", entrypoints, edges, report)


def test_s3_reason_is_byte_identical_to_19a3b59(tmp_path):
    result = _s3_result(tmp_path)
    assert result.verdict == Verdict.UNKNOWN
    assert result.reason == S3_REASON_19A3B59


def test_s3_multi_rule_reason_is_byte_identical_to_19a3b59():
    entry = Entrypoint(EntrypointSource.DUNDER_MAIN, "m:entry", "m", Path("m.py"), 1, False)
    edges = [
        CallEdge("m:entry", "m:a", Confidence.LOW, Path("m.py"), 2, ResolutionRule.SELF_UNRESOLVED),
        CallEdge("m:a", "?:load", Confidence.LOW, Path("m.py"), 5, ResolutionRule.UNRESOLVED_ATTRIBUTE),
    ]
    report = DiscoveryReport(modules=[], import_tables={}, unparsed=[], star_import_count=0)
    result = compute_reachability("somepkg", "load", [entry], edges, report)
    assert result.verdict == Verdict.UNKNOWN
    assert result.reason == "reachable only via low-confidence edge(s): self_unresolved, unresolved_attribute"


def test_low_confidence_reason_prefix_constant_is_pinned():
    from reachability.index.reachability import LOW_CONFIDENCE_REASON_PREFIX

    assert LOW_CONFIDENCE_REASON_PREFIX == "reachable only via low-confidence edge(s): "


def test_s3_unknown_classifies_as_low_confidence_path(tmp_path):
    cause = compute_termination_cause(_finding(_s3_result(tmp_path)))
    assert cause == TerminationCause.LOW_CONFIDENCE_PATH
    assert cause != TerminationCause.UNCLASSIFIED
    assert cause != TerminationCause.COMPLETED


_S4_TAIL = " carries no literal target name, so a not_reachable verdict cannot be trusted"


def _s4_result(tmp_path):
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
    return compute_reachability("sink", "target_func", entrypoints, edges, report)


def test_s4_reason_is_byte_identical_to_19a3b59(tmp_path):
    result = _s4_result(tmp_path)
    assert result.verdict == Verdict.UNKNOWN
    assert result.reason == f"a reachable opaque call (?:<dynamic>) at {tmp_path / 'entry.py'}:4{_S4_TAIL}"


@pytest.mark.parametrize("builtin", ["eval", "exec"])
def test_s4_eval_exec_reason_is_byte_identical_to_19a3b59(tmp_path, builtin):
    arg = '"1"' if builtin == "eval" else '"pass"'
    entrypoints, edges, report = _build(
        tmp_path,
        {
            "sink.py": "def target_func():\n    return 1\n",
            "entry.py": (
                "def go():\n"
                f"    {builtin}({arg})\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    go()\n"
            ),
        },
    )
    result = compute_reachability("sink", "target_func", entrypoints, edges, report)
    assert result.verdict == Verdict.UNKNOWN
    assert result.reason == f"a reachable opaque call (ext:builtins.{builtin}) at {tmp_path / 'entry.py'}:2{_S4_TAIL}"


def test_opaque_call_reason_prefix_constant_is_pinned():
    from reachability.index.reachability import OPAQUE_CALL_REASON_PREFIX

    assert OPAQUE_CALL_REASON_PREFIX == "a reachable opaque call ("


def test_s4_unknown_classifies_as_opaque_call(tmp_path):
    cause = compute_termination_cause(_finding(_s4_result(tmp_path)))
    assert cause == TerminationCause.OPAQUE_CALL
    assert cause != TerminationCause.UNCLASSIFIED
    assert cause != TerminationCause.COMPLETED
