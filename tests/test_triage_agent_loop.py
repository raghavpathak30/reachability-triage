"""Gate (i) — termination/degradation — plus the tool-role-only harness
invariant, for `run_triage_loop`.

This file is written once in the U3 commit and re-run unmodified after
U4's real `sandbox_untrusted_text` body lands (`sandbox.py` step 5) — the
plan's stated success criterion for "gate (i) re-run against the real
implementation."
"""

from pathlib import Path

from reachability.index.reachability_models import Verdict
from reachability.triage import agent_loop
from reachability.triage.agent_loop import run_triage_loop
from reachability.triage.agent_models import Message
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.stub_llm import DeterministicPolicyStubLLMClient, FinalAnswerAction, ToolCallAction

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

    finding = run_triage_loop(client, repo_index, "app.sink", "vulnerable", budget=2)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "budget_exceeded" in finding.result.reason
    assert len(finding.tool_calls) == 2


def test_budget_zero_degrades_immediately():
    client = AlwaysExceedsBudgetStubLLMClient()
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop(client, repo_index, "app.sink", "vulnerable", budget=0)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "budget_exceeded" in finding.result.reason
    assert finding.tool_calls == []


def test_missing_symbol_degrades_to_unknown():
    client = NamesMissingSymbolStubLLMClient("app.sink", "definitely_not_a_real_symbol_xyz")
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop(client, repo_index, "app.sink", "definitely_not_a_real_symbol_xyz", budget=10)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "target_symbol_not_found_in_index" in finding.result.reason


def test_malformed_tool_argument_degrades_to_unknown_missing_key():
    client = MalformedArgumentStubLLMClient("find_callers", {"nod_id": "typo"})
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop(client, repo_index, "app.sink", "vulnerable", budget=10)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "malformed_tool_call_argument" in finding.result.reason
    assert finding.tool_calls == []


def test_malformed_tool_argument_degrades_to_unknown_wrong_type():
    client = MalformedArgumentStubLLMClient("resolve_import", {"module": 123, "name": "x"})
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop(client, repo_index, "app.sink", "vulnerable", budget=10)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert "malformed_tool_call_argument" in finding.result.reason
    assert finding.tool_calls == []


def test_tool_results_are_tool_role_only(monkeypatch):
    recorded: list[Message] = []
    original = agent_loop._append_tool_result

    def _spy(context, tool_name, sanitized):
        original(context, tool_name, sanitized)
        recorded.append(context[-1])

    monkeypatch.setattr(agent_loop, "_append_tool_result", _spy)

    repo_index = build_repo_index(FIXTURE_REPO)
    client = DeterministicPolicyStubLLMClient("app.sink", "vulnerable")
    finding = run_triage_loop(client, repo_index, "app.sink", "vulnerable", budget=30)

    assert finding.result.verdict == Verdict.REACHABLE
    assert recorded, "expected at least one tool result to have been appended during this run"
    assert all(msg.role == "tool" for msg in recorded)
    assert all(msg.role not in {"system", "developer"} for msg in recorded)
