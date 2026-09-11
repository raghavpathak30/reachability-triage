import dataclasses

import pytest

from reachability.index.edges_models import CallEdge, Confidence, ResolutionRule
from reachability.index.reachability_models import ReachabilityResult, Verdict
from reachability.triage.agent_models import Message, ToolCallRecord, TriageFinding


def test_message_fields_and_frozen():
    msg = Message(role="tool", content="hello")
    assert msg.role == "tool"
    assert msg.content == "hello"
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.role = "system"


def test_tool_call_record_fields_and_frozen():
    record = ToolCallRecord(
        sequence=0,
        tool_name="search_symbol",
        arguments={"pattern": "*foo"},
        result="[]",
    )
    assert record.sequence == 0
    assert record.tool_name == "search_symbol"
    assert record.arguments == {"pattern": "*foo"}
    assert record.result == "[]"
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.sequence = 1


def test_triage_finding_fields_and_frozen():
    edge = CallEdge(
        caller_id="pkg.mod:caller",
        callee_id="pkg.mod:callee",
        confidence=Confidence.HIGH,
        file="pkg/mod.py",
        lineno=3,
        resolution_rule=ResolutionRule.MODULE_SCOPE,
    )
    result = ReachabilityResult(
        target_module="pkg.mod",
        target_symbol="callee",
        verdict=Verdict.REACHABLE,
        path=[edge],
        reason=None,
    )
    record = ToolCallRecord(sequence=0, tool_name="find_callers", arguments={"node_id": "pkg.mod:callee"}, result=str([edge]))
    finding = TriageFinding(result=result, rationale="found a direct call", tool_calls=[record])

    assert finding.result is result
    assert finding.rationale == "found a direct call"
    assert finding.tool_calls == [record]
    with pytest.raises(dataclasses.FrozenInstanceError):
        finding.rationale = "changed"


def test_triage_finding_tool_calls_defaults_to_empty_list():
    result = ReachabilityResult(
        target_module="pkg.mod",
        target_symbol=None,
        verdict=Verdict.NOT_REACHABLE,
        path=None,
        reason="no entrypoints",
    )
    finding = TriageFinding(result=result, rationale="")
    assert finding.tool_calls == []
