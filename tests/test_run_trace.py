"""Phase 8 U2: pure trace/metric helpers in `reachability.triage.run_trace`."""

from pathlib import Path

from reachability.triage.agent_models import ToolCallRecord
from reachability.triage.run_trace import (
    build_tool_call_trace,
    cap_reason,
    loop_metrics,
    loop_metrics_from_trace,
    normalize_tool_arguments,
    redact_org_ids,
)

ORG_ID_SEP = "org_01ab_CD-xyzq"


def _search(pattern, seq=0):
    return ToolCallRecord(seq, "search_symbol", {"pattern": pattern}, "RESULT-TEXT")


def _callers(node, seq=0):
    return ToolCallRecord(seq, "find_callers", {"node_id": node}, "RESULT-TEXT")


def _records(spec):
    """'AAABA' -> search_symbol with pattern A/B."""
    return [_search(ch, i + 1) for i, ch in enumerate(spec)]


def test_empty_trace_is_all_zeros():
    assert loop_metrics([]) == {
        "tool_call_count": 0,
        "distinct_calls": 0,
        "repeated_calls": 0,
        "longest_identical_streak": 0,
        "per_tool_counts": {},
    }


def test_three_a_then_b_then_a():
    metrics = loop_metrics(_records("AAABA"))
    assert metrics["tool_call_count"] == 5
    assert metrics["distinct_calls"] == 2
    assert metrics["repeated_calls"] == 3
    assert metrics["longest_identical_streak"] == 3
    assert metrics["per_tool_counts"] == {"search_symbol": 5}


def test_alternating_has_repeats_but_streak_one():
    metrics = loop_metrics(_records("ABAB"))
    assert metrics["repeated_calls"] == 2
    assert metrics["longest_identical_streak"] == 1


def test_single_call_streak_is_one():
    assert loop_metrics(_records("A"))["longest_identical_streak"] == 1


def test_whitespace_only_difference_is_identical():
    metrics = loop_metrics([_search("*vuln", 1), _search("  *vuln \n", 2)])
    assert metrics["repeated_calls"] == 1
    assert metrics["longest_identical_streak"] == 2


def test_difference_after_char_200_keeps_distinct_digest_and_is_not_a_repeat():
    base = "x" * 200
    trace = build_tool_call_trace([_search(base + "AAA", 1), _search(base + "BBB", 2)])
    assert trace[0]["arguments"]["pattern"] == trace[1]["arguments"]["pattern"]
    assert trace[0]["arguments"]["pattern"].endswith("...[+3 chars]")
    assert trace[0]["args_digest"] != trace[1]["args_digest"]
    assert loop_metrics_from_trace(trace)["repeated_calls"] == 0


def test_extra_keys_dropped_and_non_strings_repr():
    args = normalize_tool_arguments("resolve_import", {"name": " n ", "module": 5, "junk": "x"})
    assert args == {"module": "5", "name": "n"}
    assert list(args) == ["module", "name"]


def test_org_id_with_separators_redacted_in_trace():
    trace = build_tool_call_trace([_search(f"see {ORG_ID_SEP}, ok", 1)])
    text = str(trace)
    assert ORG_ID_SEP not in text and "xyzq" not in text
    assert "org_[REDACTED]" in text
    assert redact_org_ids({"a": ORG_ID_SEP}) == {"a": "org_[REDACTED]"}


def test_limit_truncates_metrics_view():
    trace = build_tool_call_trace(_records("AAABA"))
    metrics = loop_metrics_from_trace(trace, limit=2)
    assert metrics["tool_call_count"] == 2
    assert metrics["repeated_calls"] == 1
    assert metrics["longest_identical_streak"] == 2
    assert loop_metrics(_records("AAABA"), limit=2) == metrics


def test_result_never_in_trace_entries():
    trace = build_tool_call_trace([_search("A", 1), _callers("n", 2)])
    for entry in trace:
        assert set(entry) == {"sequence", "tool_name", "arguments", "args_digest"}
    assert "RESULT-TEXT" not in str(trace)
    assert {e["tool_name"] for e in trace} == {"search_symbol", "find_callers"}


def test_loop_and_dispatch_do_not_import_run_trace():
    root = Path(__file__).resolve().parents[1] / "src" / "reachability"
    for rel in (
        "triage/langgraph_loop.py",
        "triage/tool_dispatch.py",
        "triage/job_runner.py",
        "agent/eval_harness.py",
    ):
        assert "run_trace" not in (root / rel).read_text(), rel


def test_cap_reason_none_passes_through():
    assert cap_reason(None) is None


def test_cap_reason_short_is_unchanged():
    assert cap_reason("x" * 300) == "x" * 300


def test_cap_reason_long_gets_exact_suffix_and_keeps_prefix():
    reason = "llm_malformed_response: " + "y" * 1000
    capped = cap_reason(reason)

    assert capped == reason[:300] + f"...[+{len(reason) - 300} chars]"
    assert capped.startswith("llm_malformed_response:")
