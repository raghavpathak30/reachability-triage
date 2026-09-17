"""Phase LangGraph, Unit 2's own gate test: `run_triage_loop_langgraph` runs
at least one fixture end-to-end and produces a well-formed `TriageFinding`.

Calls `run_triage_loop_langgraph` directly, not through `job_runner.py`.
As of Unit 6, the old backend-selection environment flag no longer exists
at all -- `job_runner.py` calls `run_triage_loop_langgraph` unconditionally.

No verdict-correctness assertion here -- that is Unit 5's gate, not this
one's; see `agent_docs/PHASE_LANGGRAPH.md`'s Unit 2 gate wording.
"""

import sys
from pathlib import Path

from reachability.index.reachability_models import ReachabilityResult, Verdict
from reachability.triage.agent_models import Message, TriageFinding
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import _stub_llm_adapter, run_triage_loop_langgraph
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


