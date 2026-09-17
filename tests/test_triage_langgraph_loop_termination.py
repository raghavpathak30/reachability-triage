"""Gate (i) — termination/degradation — plus the tool-role-only harness
invariant, for `run_triage_loop_langgraph`.

Ported from the deleted hand-rolled-loop termination test file (Phase LangGraph
Unit 6 cutover): same four client classes and same seven degradation
assertions, driving `run_triage_loop_langgraph` instead of the deleted
`run_triage_loop`. `AgentLoopError` is now imported from `.tool_dispatch`.
The tool-role-only invariant is ported using
`build_langgraph_triage_graph(...).invoke(initial_state)` directly and
asserting on `result["messages"]` -- no spy needed, since (like the old
loop) only `sanitize_node` ever appends to `messages`, so every message
after the first user message is, structurally, a tool result.
"""

from pathlib import Path

import pytest

from reachability.index.reachability_models import Verdict
from reachability.triage.agent_models import Message
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import (
    LangGraphTriageState,
    build_langgraph_triage_graph,
    run_triage_loop_langgraph,
)
from reachability.triage.stub_llm import DeterministicPolicyStubLLMClient, FinalAnswerAction, ToolCallAction
from reachability.triage.tool_dispatch import AgentLoopError

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "l5" / "01_direct_console_entrypoint" / "repo"


class AlwaysExceedsBudgetStubLLMClient:
    """Always requests another (well-formed) tool call; never answers.

    Used to deterministically drive the loop into case (a), budget
    exhaustion, regardless of the configured budget.
    """

    def next_action(self, context):
        return ToolCallAction("search_symbol", {"pattern": "*"})


class NamesMissingSymbolStubLLMClient:
    """Confirms nothing is found, then answers with the (absent) target.

    Used to deterministically drive the loop into case (b): a final
    answer targeting a symbol that was never confirmed present by any
    `search_symbol` call made during the run.
    """

    def __init__(self, target_module: str, target_symbol: str):
        self.target_module = target_module
        self.target_symbol = target_symbol
        self._searched = False

    def next_action(self, context):
        if not self._searched:
            self._searched = True
            return ToolCallAction("search_symbol", {"pattern": self.target_symbol})
        return FinalAnswerAction(self.target_module, self.target_symbol, "nothing found")


class MalformedArgumentStubLLMClient:
    """Emits a single malformed tool call, of a caller-chosen shape.

    Used to deterministically drive the loop into case (c).
    """

    def __init__(self, tool_name: str, arguments: dict[str, object]):
        self.tool_name = tool_name
        self.arguments = arguments

    def next_action(self, context):
        return ToolCallAction(self.tool_name, self.arguments)


def test_budget_exceeded_degrades_to_unknown():
    client = AlwaysExceedsBudgetStubLLMClient()
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=2)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "budget_exceeded" in finding.result.reason
    assert len(finding.tool_calls) == 2


def test_budget_zero_degrades_immediately():
    client = AlwaysExceedsBudgetStubLLMClient()
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=0)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "budget_exceeded" in finding.result.reason
    assert finding.tool_calls == []


def test_missing_symbol_degrades_to_unknown():
    client = NamesMissingSymbolStubLLMClient("app.sink", "definitely_not_a_real_symbol_xyz")
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop_langgraph(
        client, repo_index, "app.sink", "definitely_not_a_real_symbol_xyz", budget=10
    )

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "target_symbol_not_found_in_index" in finding.result.reason


def test_malformed_tool_argument_degrades_to_unknown_missing_key():
    client = MalformedArgumentStubLLMClient("find_callers", {"nod_id": "typo"})
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=10)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "malformed_tool_call_argument" in finding.result.reason
    assert finding.tool_calls == []


def test_malformed_tool_argument_degrades_to_unknown_wrong_type():
    client = MalformedArgumentStubLLMClient("resolve_import", {"module": 123, "name": "x"})
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=10)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "malformed_tool_call_argument" in finding.result.reason
    assert finding.tool_calls == []


def test_malformed_tool_argument_degrades_to_unknown_non_dict_arguments():
    # Review (.agent/review.md) Warning #6: a non-dict `arguments` must
    # degrade through the malformed-argument path, not raise `TypeError`.
    client = MalformedArgumentStubLLMClient("find_callers", None)
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=10)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "malformed_tool_call_argument" in finding.result.reason
    assert finding.tool_calls == []


class ReturnsUnrecognizedActionStubLLMClient:
    """Returns a value that is neither a `ToolCallAction` nor a
    `FinalAnswerAction`, to exercise the explicit `AgentLoopError` guard
    for an `llm_client` that violates its own contract (review .agent/
    review.md Warning #5).
    """

    def next_action(self, context):
        return object()


def test_unrecognized_action_raises_agentlooperror():
    client = ReturnsUnrecognizedActionStubLLMClient()
    repo_index = build_repo_index(FIXTURE_REPO)

    with pytest.raises(AgentLoopError):
        run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=10)


def test_tool_results_are_tool_role_only():
    repo_index = build_repo_index(FIXTURE_REPO)
    client = DeterministicPolicyStubLLMClient("app.sink", "vulnerable")

    graph = build_langgraph_triage_graph(client, repo_index)
    initial_state: LangGraphTriageState = {
        "messages": [
            Message(role="user", content="target_module='app.sink' target_symbol='vulnerable'")
        ],
        "tool_calls": [],
        "confirmed_symbol_ids": set(),
        "calls_made": 0,
        "budget": 30,
        "target_module": "app.sink",
        "target_symbol": "vulnerable",
        "pending_action_kind": None,
        "pending_tool_name": None,
        "pending_tool_arguments": None,
        "pending_final_target_module": None,
        "pending_final_target_symbol": None,
        "pending_final_rationale": None,
        "raw_tool_result": None,
        "finding": None,
    }
    result = graph.invoke(initial_state)

    assert result["finding"].result.verdict == Verdict.REACHABLE
    assert result["messages"][0].role == "user"
    assert result["messages"], "expected at least one tool result to have been appended during this run"
    assert all(m.role == "tool" for m in result["messages"][1:])
