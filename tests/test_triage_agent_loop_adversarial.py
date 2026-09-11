"""U4 gate — three adversarial injection classes.

Defines one test-only, deliberately naive `NaiveInjectableStubLLMClient`
(local to this file, never imported by `src/`), susceptible to injected
text if it reaches the client unsanitized. Its `next_action` reads only
from the `context: list[Message]` it is given -- i.e. it only ever sees
what `_append_tool_result` (`agent_loop.py`) actually appended, sanitized
when the real `sandbox_untrusted_text` is wired in, raw when a test wires
in an identity replacement for control purposes.

Since `search_symbol`/`find_callers`/`resolve_import` (the only three
real tool functions, `query.py:8,17,21`) return structured
`SymbolNode`/`CallEdge` data with no free-text field a malicious repo
author could populate (see `.agent/plan.md`'s Risks section and
`tests/fixtures/triage_adversarial/01_verdict_manipulation/RATIONALE.md`),
each test here monkeypatches `find_callers` as imported into
`agent_loop.py` to return a plain string (the fixture's `injected_text`,
or planted secrets) for exactly one call, simulating a text-bearing
advisory/docstring/comment channel the spec describes without requiring
new capability in the frozen L1-L4 index layer.

Each of the three tests below runs the loop twice against the *same*
real repo (`tests/fixtures/l5/11_dead_function_call_site/repo`, which has
two distinct, independently confirmable symbols with two distinct,
known verdicts -- `pkg.sink:vulnerable` is `not_reachable`,
`pkg.other:do_other` is `reachable` -- exactly what the verdict-
manipulation fixture needs to prove a *real* verdict change, not just a
case-(b) degradation to `unknown`): once with the real
`sandbox_untrusted_text` wired in, once with an identity function
substituted as a control. Each test asserts the protected behavior holds
only in the real-sandbox run, and that the identity-sandbox control run
actually exhibits the bad behavior -- proving each fixture is
non-vacuous, not merely "structurally guaranteed to pass," per
`.agent/critique.md`'s blocking item #4.

`test_gate_i_still_green_after_real_sandbox` re-invokes gate (i)'s three
degradation tests from `tests/test_triage_agent_loop.py` directly, as an
explicit, written-down acknowledgment that gate (i) is being re-run
against the real (non-stub) `sandbox_untrusted_text` -- satisfying "U3 is
not done until U4's boundary function is real and U3's gate (i) has been
re-run against it." Per critique item #5: only cases (a) and (b) actually
dispatch a real tool call (and so exercise the real sandbox) before
degrading; case (c) is rejected pre-dispatch by design in both the stub
and real states, so its re-run demonstrates only "no regression," not
sandbox interaction -- this is expected, not a gap.
"""

import json
import re
from pathlib import Path

import pytest

import test_triage_agent_loop as gate_i
from reachability.index.reachability import compute_reachability
from reachability.index.reachability_models import Verdict
from reachability.triage import agent_loop
from reachability.triage.agent_loop import run_triage_loop
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.sandbox import temporary_secret_pattern
from reachability.triage.stub_llm import FinalAnswerAction, ToolCallAction

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "l5" / "11_dead_function_call_site" / "repo"
ADVERSARIAL_ROOT = Path(__file__).parent / "fixtures" / "triage_adversarial"

BUDGET = 10

_TARGET_SWITCH_PATTERN = re.compile(r"target_module=(\S+)\s+target_symbol=(\S+)")


def _load_label(fixture_name: str) -> dict:
    return json.loads((ADVERSARIAL_ROOT / fixture_name / "label.json").read_text())


class NaiveInjectableStubLLMClient:
    """Deliberately naive stub client, local to this test file only.

    Policy: (1) `search_symbol` to confirm its originally configured
    target; (2) one `find_callers` "evidence gathering" call (the call a
    test monkeypatches to return injected text); (3) if the just-returned
    text contains a `target_module=... target_symbol=...` directive,
    re-confirm the *new* target via another `search_symbol` call and
    answer against it instead; else if it contains "call resolve_import",
    make that off-menu call; otherwise answer directly. In all cases, the
    final rationale is a naive concatenation of every message this client
    has ever seen in context -- simulating an LLM that explains itself by
    quoting the evidence it read, and thus leaking any planted secret
    still present in context at answer time.
    """

    def __init__(self, target_module: str, target_symbol: str) -> None:
        self.target_module = target_module
        self.target_symbol = target_symbol
        self._step = 0
        self._pending_switch: tuple[str, str] | None = None
        self._answer_target_module = target_module
        self._answer_target_symbol = target_symbol

    def next_action(self, context):
        latest = context[-1].content if context else ""

        if self._step == 0:
            self._step = 1
            return ToolCallAction("search_symbol", {"pattern": f"*{self.target_symbol}"})

        if self._step == 1:
            self._step = 2
            return ToolCallAction(
                "find_callers", {"node_id": f"{self.target_module}:{self.target_symbol}"}
            )

        if self._step == 2:
            self._step = 3
            match = _TARGET_SWITCH_PATTERN.search(latest)
            if match is not None:
                self._pending_switch = (match.group(1), match.group(2))
                return ToolCallAction("search_symbol", {"pattern": f"*{match.group(2)}"})
            if "call resolve_import" in latest.lower():
                return ToolCallAction("resolve_import", {"module": "os", "name": "system"})
            return self._final_answer(context)

        if self._pending_switch is not None:
            self._answer_target_module, self._answer_target_symbol = self._pending_switch
            self._pending_switch = None

        return self._final_answer(context)

    def _final_answer(self, context) -> FinalAnswerAction:
        rationale = " | ".join(msg.content for msg in context)
        return FinalAnswerAction(self._answer_target_module, self._answer_target_symbol, rationale)


def _run_with_mocked_find_callers(monkeypatch, injected_payload, target_module, target_symbol):
    repo_index = build_repo_index(FIXTURE_REPO)

    def _fake_find_callers(edges, node_id):
        return injected_payload

    monkeypatch.setattr(agent_loop, "find_callers", _fake_find_callers)

    client = NaiveInjectableStubLLMClient(target_module, target_symbol)
    finding = run_triage_loop(client, repo_index, target_module, target_symbol, budget=BUDGET)
    return finding, repo_index


def test_verdict_manipulation_fixture(monkeypatch):
    label = _load_label("01_verdict_manipulation")
    original_module, original_symbol = "pkg.sink", "vulnerable"
    switch_module, switch_symbol = "pkg.other", "do_other"
    injected_payload = (
        f"{label['injected_text']} target_module={switch_module} target_symbol={switch_symbol}"
    )

    baseline_repo_index = build_repo_index(FIXTURE_REPO)
    baseline = compute_reachability(
        original_module,
        original_symbol,
        baseline_repo_index.entrypoints,
        baseline_repo_index.edges,
        baseline_repo_index.report,
    )
    assert baseline.verdict == Verdict.NOT_REACHABLE

    switched_reference = compute_reachability(
        switch_module,
        switch_symbol,
        baseline_repo_index.entrypoints,
        baseline_repo_index.edges,
        baseline_repo_index.report,
    )
    assert switched_reference.verdict == Verdict.REACHABLE
    assert switched_reference.verdict != baseline.verdict

    # Real sandbox: the directive is redacted before the naive client ever
    # sees it, so it must answer against the original target.
    real_finding, _ = _run_with_mocked_find_callers(
        monkeypatch, injected_payload, original_module, original_symbol
    )
    assert real_finding.result.target_module == original_module
    assert real_finding.result.target_symbol == original_symbol
    assert real_finding.result.verdict == baseline.verdict

    # Identity-sandbox control: the directive survives, proving the
    # fixture is non-vacuous -- the naive client really would be steered
    # absent real sanitization.
    monkeypatch.setattr(agent_loop, "sandbox_untrusted_text", lambda raw: raw)
    control_finding, _ = _run_with_mocked_find_callers(
        monkeypatch, injected_payload, original_module, original_symbol
    )
    assert control_finding.result.target_module == switch_module
    assert control_finding.result.target_symbol == switch_symbol
    assert control_finding.result.verdict == switched_reference.verdict
    assert control_finding.result.verdict != baseline.verdict


def test_unauthorized_tool_invocation_fixture(monkeypatch):
    label = _load_label("02_unauthorized_tool_invocation")
    target_module, target_symbol = "pkg.sink", "vulnerable"

    # Real sandbox: "call resolve_import ..." is redacted before the
    # naive client ever sees it.
    real_finding, _ = _run_with_mocked_find_callers(
        monkeypatch, label["injected_text"], target_module, target_symbol
    )
    real_sequence = [record.tool_name for record in real_finding.tool_calls]
    assert real_sequence == label["expected_tool_sequence"]
    assert "resolve_import" not in real_sequence

    # Identity-sandbox control: the instruction survives, and the naive
    # client actually makes the off-menu call -- proving non-vacuity.
    monkeypatch.setattr(agent_loop, "sandbox_untrusted_text", lambda raw: raw)
    control_finding, _ = _run_with_mocked_find_callers(
        monkeypatch, label["injected_text"], target_module, target_symbol
    )
    control_sequence = [record.tool_name for record in control_finding.tool_calls]
    assert "resolve_import" in control_sequence
    assert control_sequence != label["expected_tool_sequence"]


def test_unauthorized_context_echo_fixture(monkeypatch):
    label = _load_label("03_unauthorized_context_echo")
    target_module, target_symbol = "pkg.sink", "vulnerable"
    planted_secrets = label["planted_secrets"]
    # Only the third planted secret has no digits/high-entropy shape --
    # it is redacted purely because the test registers it below, not by
    # the generic heuristics, proving temporary_secret_pattern is
    # load-bearing rather than redundant with them.
    literal_secret = planted_secrets[2]
    injected_payload = "\n".join(planted_secrets) + "\n" + label["injected_text"]

    with temporary_secret_pattern(literal_secret):
        real_finding, _ = _run_with_mocked_find_callers(
            monkeypatch, injected_payload, target_module, target_symbol
        )
        for secret in planted_secrets:
            assert secret not in real_finding.rationale

        # Identity-sandbox control: the secrets survive into the
        # rationale, proving the fixture is non-vacuous.
        monkeypatch.setattr(agent_loop, "sandbox_untrusted_text", lambda raw: raw)
        control_finding, _ = _run_with_mocked_find_callers(
            monkeypatch, injected_payload, target_module, target_symbol
        )
        assert any(secret in control_finding.rationale for secret in planted_secrets)


def test_gate_i_still_green_after_real_sandbox():
    gate_i.test_budget_exceeded_degrades_to_unknown()
    gate_i.test_budget_zero_degrades_immediately()
    gate_i.test_missing_symbol_degrades_to_unknown()
    gate_i.test_malformed_tool_argument_degrades_to_unknown_missing_key()
    gate_i.test_malformed_tool_argument_degrades_to_unknown_wrong_type()
    with pytest.MonkeyPatch.context() as mp:
        gate_i.test_tool_results_are_tool_role_only(mp)
