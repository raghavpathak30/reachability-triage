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


S5_REASON_19A3B59 = (
    "'target_func' is referenced by name outside any call position elsewhere in the repo "
    "(e.g. passed as a callback, stored in a container, or reassigned); a confident "
    "not_reachable verdict cannot be given"
)


def _s5_result(tmp_path):
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
    return compute_reachability("sink", "target_func", entrypoints, edges, report)


def _unknown_finding(reason, verdict=Verdict.UNKNOWN):
    from reachability.index.reachability_models import ReachabilityResult

    return _finding(
        ReachabilityResult(target_module="m", target_symbol="f", verdict=verdict, path=None, reason=reason)
    )


def _name_escape_reason(symbol):
    from reachability.index.reachability import NAME_ESCAPE_REASON_TEMPLATE

    return NAME_ESCAPE_REASON_TEMPLATE.format(target_symbol=symbol)


def test_s5_reason_is_byte_identical_to_19a3b59(tmp_path):
    result = _s5_result(tmp_path)
    assert result.verdict == Verdict.UNKNOWN
    assert result.reason == S5_REASON_19A3B59


def test_name_escape_template_constant_is_pinned():
    from reachability.index.reachability import NAME_ESCAPE_REASON_TEMPLATE

    assert NAME_ESCAPE_REASON_TEMPLATE == (
        "'{target_symbol}' is referenced by name outside any call position elsewhere in the repo "
        "(e.g. passed as a callback, stored in a container, or reassigned); a confident "
        "not_reachable verdict cannot be given"
    )
    assert NAME_ESCAPE_REASON_TEMPLATE.count("{target_symbol}") == 1
    assert NAME_ESCAPE_REASON_TEMPLATE.count("{") == 1


def test_s5_unknown_classifies_as_name_escape(tmp_path):
    cause = compute_termination_cause(_finding(_s5_result(tmp_path)))
    assert cause == TerminationCause.NAME_ESCAPE
    assert cause != TerminationCause.UNCLASSIFIED
    assert cause != TerminationCause.COMPLETED


def test_name_escape_fullmatch_rejects_empty_symbol_and_extra_text():
    good = _name_escape_reason("f")
    assert compute_termination_cause(_unknown_finding(good)) == TerminationCause.NAME_ESCAPE
    for bad in (_name_escape_reason(""), "x" + good, good + " extra"):
        assert compute_termination_cause(_unknown_finding(bad)) == TerminationCause.UNCLASSIFIED


@pytest.mark.parametrize("symbol", ["a.b+c", "x'y", "f(1)"])
def test_name_escape_symbol_with_regex_metacharacters_still_matches(symbol):
    reason = _name_escape_reason(symbol)
    assert compute_termination_cause(_unknown_finding(reason)) == TerminationCause.NAME_ESCAPE


def test_name_escape_symbol_with_newline_classifies_as_name_escape():
    # main.py's TriageRequest.target_symbol is an unvalidated `str | None`, so a
    # newline-bearing symbol can reach the engine; (.+) must match it (re.DOTALL).
    reason = _name_escape_reason("a\nb")
    assert compute_termination_cause(_unknown_finding(reason)) == TerminationCause.NAME_ESCAPE


def test_s3_s4_match_is_startswith_not_contains():
    from reachability.index.reachability import LOW_CONFIDENCE_REASON_PREFIX, OPAQUE_CALL_REASON_PREFIX

    for prefix, expected in (
        (LOW_CONFIDENCE_REASON_PREFIX, TerminationCause.LOW_CONFIDENCE_PATH),
        (OPAQUE_CALL_REASON_PREFIX, TerminationCause.OPAQUE_CALL),
    ):
        assert compute_termination_cause(_unknown_finding(prefix + "anything")) == expected
        assert compute_termination_cause(_unknown_finding("xx" + prefix + "anything")) == TerminationCause.UNCLASSIFIED


def test_engine_reason_on_non_unknown_verdict_is_completed():
    from reachability.index.reachability import LOW_CONFIDENCE_REASON_PREFIX, OPAQUE_CALL_REASON_PREFIX

    for reason in (
        LOW_CONFIDENCE_REASON_PREFIX + "unresolved_attribute",
        OPAQUE_CALL_REASON_PREFIX + "?:<dynamic>) at x.py:1 tail",
        _name_escape_reason("f"),
    ):
        finding = _unknown_finding(reason, verdict=Verdict.NOT_REACHABLE)
        assert compute_termination_cause(finding) == TerminationCause.COMPLETED


def test_engine_prefixes_are_disjoint_from_loop_prefixes():
    from reachability.index.reachability import (
        LOW_CONFIDENCE_REASON_PREFIX,
        NAME_ESCAPE_REASON_TEMPLATE,
        NO_ENTRYPOINTS_REASON,
        OPAQUE_CALL_REASON_PREFIX,
    )
    from reachability.triage.termination_cause import (
        TARGET_NOT_FOUND_REASON,
        _UNKNOWN_VERDICT_REASON_PREFIXES,
    )

    loop_strings = list(_UNKNOWN_VERDICT_REASON_PREFIXES) + [TARGET_NOT_FOUND_REASON, NO_ENTRYPOINTS_REASON]
    engine_heads = [
        LOW_CONFIDENCE_REASON_PREFIX,
        OPAQUE_CALL_REASON_PREFIX,
        NAME_ESCAPE_REASON_TEMPLATE.split("{target_symbol}")[0],
    ]
    for engine in engine_heads:
        for loop in loop_strings:
            assert not engine.startswith(loop)
            assert not loop.startswith(engine)
    for i, a in enumerate(engine_heads):
        for b in engine_heads[i + 1 :]:
            assert not a.startswith(b)
            assert not b.startswith(a)


def _zero_entrypoint_result(tmp_path):
    report = DiscoveryReport(modules=[], import_tables={}, unparsed=[], star_import_count=0)
    return compute_reachability("m", "f", [], [], report)


@pytest.mark.parametrize(
    ("site", "expected"),
    [
        ("zero_entrypoints", TerminationCause.NO_ENTRYPOINTS),
        ("low_confidence", TerminationCause.LOW_CONFIDENCE_PATH),
        ("opaque_call", TerminationCause.OPAQUE_CALL),
        ("name_escape", TerminationCause.NAME_ESCAPE),
    ],
)
def test_every_engine_unknown_return_classifies_not_unclassified(tmp_path, site, expected):
    builders = {
        "zero_entrypoints": _zero_entrypoint_result,
        "low_confidence": _s3_result,
        "opaque_call": _s4_result,
        "name_escape": _s5_result,
    }
    result = builders[site](tmp_path)
    assert result.verdict == Verdict.UNKNOWN
    cause = compute_termination_cause(_finding(result))
    assert cause == expected
    assert cause != TerminationCause.UNCLASSIFIED


def test_compute_reachability_has_exactly_four_unknown_returns():
    import inspect

    assert inspect.getsource(compute_reachability).count("Verdict.UNKNOWN") == 4


def test_index_package_does_not_import_triage():
    index_dir = Path(__file__).resolve().parents[1] / "src" / "reachability" / "index"
    for source_file in index_dir.glob("*.py"):
        text = source_file.read_text()
        assert "reachability.triage" not in text, source_file
        assert "from ..triage" not in text, source_file
