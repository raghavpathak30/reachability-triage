"""Phase LangGraph, Unit 2's own gate test: `run_triage_loop_langgraph` runs
at least one fixture end-to-end and produces a well-formed `TriageFinding`.

Calls `run_triage_loop_langgraph` directly, not through `job_runner.py`, so
`TRIAGE_LOOP_BACKEND` is not in play here -- the flag's job-runner wiring is
exercised separately, in `tests/test_triage_job_runner.py`.

No verdict-correctness assertion here -- that is Unit 5's gate, not this
one's; see `agent_docs/PHASE_LANGGRAPH.md`'s Unit 2 gate wording.
"""

import sys
from pathlib import Path

from reachability.index.reachability_models import ReachabilityResult, Verdict
from reachability.triage import agent_loop
from reachability.triage.agent_loop import _dispatch_tool
from reachability.triage.agent_models import Message, TriageFinding
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import (
    _stub_llm_adapter,
    build_langgraph_triage_graph,
    run_triage_loop_langgraph,
)
from reachability.triage.sandbox import sandbox_untrusted_text
from reachability.triage.stub_llm import DeterministicPolicyStubLLMClient, FinalAnswerAction, ToolCallAction

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import measure_l5  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "l5" / "09_never_imported"
FIXTURE_DIR_3HOP = REPO_ROOT / "tests" / "fixtures" / "l5" / "02_transitive_three_hop"

BUDGET = 50


def test_langgraph_loop_runs_fixture_end_to_end():
    repo_root = FIXTURE_DIR / "repo"
    repo_index = build_repo_index(repo_root)

    label = measure_l5.load_label(FIXTURE_DIR)
    target_module = measure_l5._module_dotted_name(label["sink"]["file"])
    target_symbol = measure_l5.resolve_target(
        repo_index.symbol_index, target_module, label["sink"]["line"], FIXTURE_DIR.name
    )

    client = DeterministicPolicyStubLLMClient(target_module, target_symbol)

    finding = run_triage_loop_langgraph(
        client,
        repo_index,
        target_module,
        target_symbol,
        budget=BUDGET,
    )

    assert isinstance(finding, TriageFinding)
    assert isinstance(finding.result, ReachabilityResult)
    assert isinstance(finding.result.verdict, Verdict)
    assert isinstance(finding.tool_calls, list)


class _ScriptedStubLLMClient:
    """Test-local scripted client: a `ToolCallAction` then a
    `FinalAnswerAction`, the same shape `stub_llm.py`'s docstring says
    belongs in test files, not `src/`."""

    def __init__(self) -> None:
        self._asked = False

    def next_action(self, messages: list[Message]):
        if not self._asked:
            self._asked = True
            return ToolCallAction(tool_name="find_callers", arguments={"node_id": "m:s"})
        return FinalAnswerAction(target_module="m", target_symbol="s", rationale="done")


def test_stub_llm_adapter_matches_inline_agent_node_behavior():
    client = _ScriptedStubLLMClient()
    messages = [Message(role="user", content="target_module='m' target_symbol='s'")]

    tool_call_result = _stub_llm_adapter(client, messages)
    assert tool_call_result == {
        "pending_action_kind": "tool_call",
        "pending_tool_name": "find_callers",
        "pending_tool_arguments": {"node_id": "m:s"},
    }

    final_answer_result = _stub_llm_adapter(client, messages)
    assert final_answer_result == {
        "pending_action_kind": "final_answer",
        "pending_final_target_module": "m",
        "pending_final_target_symbol": "s",
        "pending_final_rationale": "done",
    }


class _SingleToolCallStubLLMClient:
    """Test-local scripted client: issues exactly one named `ToolCallAction`
    with fixed `arguments`, then always answers -- used to drive one
    tool-result payload through the real, shipped graph."""

    def __init__(self, tool_name: str, arguments: dict[str, object]) -> None:
        self._tool_name = tool_name
        self._arguments = arguments
        self._asked = False

    def next_action(self, messages: list[Message]):
        if not self._asked:
            self._asked = True
            return ToolCallAction(tool_name=self._tool_name, arguments=self._arguments)
        return FinalAnswerAction(target_module="m", target_symbol="s", rationale="done")


def _old_path_message(tool_name: str, arguments: dict[str, object], repo_index) -> Message:
    raw_result = _dispatch_tool(tool_name, arguments, repo_index)
    sanitized = sandbox_untrusted_text(str(raw_result))
    old_context: list[Message] = []
    agent_loop._append_tool_result(old_context, tool_name, sanitized)
    return old_context[0]


def _new_path_message(tool_name: str, arguments: dict[str, object], repo_index) -> Message:
    client = _SingleToolCallStubLLMClient(tool_name, arguments)
    initial_state = {
        "messages": [Message(role="user", content="target_module='m' target_symbol='s'")],
        "tool_calls": [],
        "confirmed_symbol_ids": set(),
        "calls_made": 0,
        "budget": 10,
        "target_module": "m",
        "target_symbol": "s",
        "pending_action_kind": None,
        "pending_tool_name": None,
        "pending_tool_arguments": None,
        "pending_final_target_module": None,
        "pending_final_target_symbol": None,
        "pending_final_rationale": None,
        "raw_tool_result": None,
        "finding": None,
    }
    graph = build_langgraph_triage_graph(client, repo_index)
    result = graph.invoke(initial_state)
    return result["messages"][1]


def test_langgraph_message_content_matches_append_tool_result():
    """Unit 4's real gate: the `Message` `sanitize_node` appends must be
    byte-identical (`role` and `content`) to what `agent_loop.py`'s real
    `_append_tool_result` produces for the same tool-result payload, for
    one representative sample of each of the three L4 query tools."""
    repo_index_1 = build_repo_index(FIXTURE_DIR / "repo")
    label_1 = measure_l5.load_label(FIXTURE_DIR)
    target_module_1 = measure_l5._module_dotted_name(label_1["sink"]["file"])
    target_symbol_1 = measure_l5.resolve_target(
        repo_index_1.symbol_index, target_module_1, label_1["sink"]["line"], FIXTURE_DIR.name
    )
    search_symbol_args = {"pattern": f"*{target_symbol_1}"}

    repo_index_2 = build_repo_index(FIXTURE_DIR_3HOP / "repo")
    label_2 = measure_l5.load_label(FIXTURE_DIR_3HOP)
    target_module_2 = measure_l5._module_dotted_name(label_2["sink"]["file"])
    target_symbol_2 = measure_l5.resolve_target(
        repo_index_2.symbol_index, target_module_2, label_2["sink"]["line"], FIXTURE_DIR_3HOP.name
    )
    confirmed = _dispatch_tool("search_symbol", {"pattern": f"*{target_symbol_2}"}, repo_index_2)
    find_callers_args = {"node_id": confirmed[0].node_id}

    resolve_import_args = {"module": "pkg.entry", "name": "step_a"}

    payloads = [
        ("search_symbol", search_symbol_args, repo_index_1),
        ("find_callers", find_callers_args, repo_index_2),
        ("resolve_import", resolve_import_args, repo_index_2),
    ]

    for tool_name, arguments, repo_index in payloads:
        old_message = _old_path_message(tool_name, arguments, repo_index)
        new_message = _new_path_message(tool_name, arguments, repo_index)
        assert old_message == new_message
