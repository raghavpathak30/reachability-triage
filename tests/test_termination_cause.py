"""Phase 6 U1 gate — `compute_termination_cause` correctly classifies every
one of `run_triage_loop_langgraph`'s ten loop-level bail-out paths, plus
the genuine-completion case, without ever misclassifying a completed
`NOT_REACHABLE` run as not-completed (the B1 regression this phase exists
to fix -- see `termination_cause.py`'s own docstring).

Reuses `FakeGroqTransport`/`_chat_completion_body`/`_client_with_transport`
from `test_llm_failure_semantics.py` and
`AlwaysExceedsBudgetStubLLMClient`/`NamesMissingSymbolStubLLMClient`/
`MalformedArgumentStubLLMClient` from
`test_triage_langgraph_loop_termination.py` -- cross-test-file import, the
established convention (see `test_llm_cache.py`'s own docstring).
"""

from pathlib import Path

import pytest

from reachability.triage.groq_llm import GroqLLMClient
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import run_triage_loop_langgraph
from reachability.triage.stub_llm import DeterministicPolicyStubLLMClient
from reachability.triage.termination_cause import TerminationCause, compute_termination_cause
from test_llm_failure_semantics import _chat_completion_body, _client_with_transport
from test_triage_langgraph_loop_termination import (
    AlwaysExceedsBudgetStubLLMClient,
    MalformedArgumentStubLLMClient,
    NamesMissingSymbolStubLLMClient,
)

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "l5" / "01_direct_console_entrypoint" / "repo"
NOT_REACHABLE_FIXTURE_REPO = (
    Path(__file__).parent / "fixtures" / "l5" / "11_dead_function_call_site" / "repo"
)


@pytest.fixture(autouse=True)
def _no_real_sleep_no_cache(monkeypatch):
    from reachability.triage import groq_llm

    monkeypatch.setattr(groq_llm.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("TRIAGE_LLM_CACHE_DISABLED", "1")


def _run_llm_error(responder):
    client, transport = _client_with_transport(responder)
    repo_index = build_repo_index(FIXTURE_REPO)
    finding = run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=5)
    return finding, transport


def _timeout_responder(call_number, request):
    import httpx

    raise httpx.TimeoutException("simulated timeout", request=request)


def _rate_limited_responder(call_number, request):
    import httpx

    return httpx.Response(429, json={"error": {"message": "rate limited"}}, request=request)


def _transport_error_responder(call_number, request):
    import httpx

    raise httpx.ConnectError("simulated connect error", request=request)


def _request_too_large_responder(call_number, request):
    import httpx

    return httpx.Response(
        413, json={"error": {"message": "Request too large: Limit 8000, Requested 9500"}}, request=request
    )


def _run_wait_enabled_llm_error(responder):
    import httpx

    from test_llm_failure_semantics import FakeGroqTransport

    transport = FakeGroqTransport(responder)
    client = GroqLLMClient(
        api_key="fake-key-not-real",
        http_client=httpx.Client(transport=transport),
        wait_on_rate_limit=True,
        sleep_fn=lambda seconds: None,
    )
    repo_index = build_repo_index(FIXTURE_REPO)
    return run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=5)


def _refusal_responder(call_number, request):
    import httpx

    body = _chat_completion_body(finish_reason="content_filter")
    return httpx.Response(200, json=body, request=request)


def _truncated_responder(call_number, request):
    import httpx

    body = _chat_completion_body(finish_reason="length")
    return httpx.Response(200, json=body, request=request)


def _malformed_response_responder(call_number, request):
    import httpx

    body = _chat_completion_body(
        finish_reason="tool_calls",
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_symbol", "arguments": "{not valid json"},
                }
            ],
        },
    )
    return httpx.Response(200, json=body, request=request)


def test_completed_member_for_a_genuine_reachable_verdict():
    client = DeterministicPolicyStubLLMClient("app.sink", "vulnerable")
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=30)

    assert compute_termination_cause(finding) == TerminationCause.COMPLETED


def test_budget_exceeded_member():
    client = AlwaysExceedsBudgetStubLLMClient()
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=2)

    assert compute_termination_cause(finding) == TerminationCause.BUDGET_EXCEEDED


def test_malformed_tool_call_member():
    client = MalformedArgumentStubLLMClient("find_callers", {"nod_id": "typo"})
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", budget=10)

    assert compute_termination_cause(finding) == TerminationCause.MALFORMED_TOOL_CALL


def test_target_not_found_member():
    client = NamesMissingSymbolStubLLMClient("app.sink", "definitely_not_a_real_symbol_xyz")
    repo_index = build_repo_index(FIXTURE_REPO)

    finding = run_triage_loop_langgraph(
        client, repo_index, "app.sink", "definitely_not_a_real_symbol_xyz", budget=10
    )

    assert compute_termination_cause(finding) == TerminationCause.TARGET_NOT_FOUND


def test_llm_timeout_member():
    finding, _transport = _run_llm_error(_timeout_responder)
    assert compute_termination_cause(finding) == TerminationCause.LLM_TIMEOUT


def test_llm_rate_limited_member():
    finding, _transport = _run_llm_error(_rate_limited_responder)
    assert compute_termination_cause(finding) == TerminationCause.LLM_RATE_LIMITED


def test_llm_request_too_large_member():
    finding = _run_wait_enabled_llm_error(_request_too_large_responder)
    assert finding.result.reason.startswith("llm_request_too_large")
    assert compute_termination_cause(finding) == TerminationCause.LLM_REQUEST_TOO_LARGE


def test_llm_transport_error_member():
    finding, _transport = _run_llm_error(_transport_error_responder)
    assert compute_termination_cause(finding) == TerminationCause.LLM_TRANSPORT_ERROR


def test_llm_refusal_member():
    finding, _transport = _run_llm_error(_refusal_responder)
    assert compute_termination_cause(finding) == TerminationCause.LLM_REFUSAL


def test_llm_truncated_member():
    finding, _transport = _run_llm_error(_truncated_responder)
    assert compute_termination_cause(finding) == TerminationCause.LLM_TRUNCATED


def test_llm_malformed_response_member():
    finding, _transport = _run_llm_error(_malformed_response_responder)
    assert compute_termination_cause(finding) == TerminationCause.LLM_MALFORMED_RESPONSE


def test_unrecognized_reason_on_unknown_verdict_is_unclassified_not_completed():
    from reachability.index.reachability_models import ReachabilityResult, Verdict
    from reachability.triage.agent_models import TriageFinding

    finding = TriageFinding(
        result=ReachabilityResult(
            target_module="app.sink",
            target_symbol="vulnerable",
            verdict=Verdict.UNKNOWN,
            path=None,
            reason="some_future_bailout_reason: detail",
        ),
        rationale="",
        tool_calls=[],
    )

    assert compute_termination_cause(finding) == TerminationCause.UNCLASSIFIED


def test_not_reachable_with_non_none_reason_is_completed():
    """The direct regression test for critique.md's B1 finding: a genuinely
    completed run whose verdict is `NOT_REACHABLE` (a non-`None` reason)
    must be `COMPLETED`, not `UNCLASSIFIED`. Without this, U3's real-model
    gate against `tests/fixtures/l5/11_dead_function_call_site` (ground
    truth `NOT_REACHABLE`) would be structurally unreachable."""
    client = DeterministicPolicyStubLLMClient("pkg.sink", "vulnerable")
    repo_index = build_repo_index(NOT_REACHABLE_FIXTURE_REPO)

    finding = run_triage_loop_langgraph(client, repo_index, "pkg.sink", "vulnerable", budget=30)

    from reachability.index.reachability_models import Verdict

    assert finding.result.verdict == Verdict.NOT_REACHABLE
    assert finding.result.reason is not None
    assert compute_termination_cause(finding) == TerminationCause.COMPLETED


def test_timeout_reason_string_unchanged_by_instrumentation():
    finding, transport = _run_llm_error(_timeout_responder)

    assert finding.result.reason.startswith("llm_timeout:")
    assert transport.calls == 3


def test_rate_limited_reason_string_unchanged_by_instrumentation():
    finding, transport = _run_llm_error(_rate_limited_responder)

    assert finding.result.reason.startswith("llm_rate_limited:")
    assert transport.calls == 3


def test_every_reachable_loop_bailout_reason_maps_to_a_known_member():
    """Conformance test: every reason the loop can *actually* currently
    emit must map to a `TerminationCause` member other than `UNCLASSIFIED`.
    `UNCLASSIFIED` is reserved for reasons the loop does not emit today."""
    repo_index = build_repo_index(FIXTURE_REPO)

    findings = [
        run_triage_loop_langgraph(
            AlwaysExceedsBudgetStubLLMClient(), repo_index, "app.sink", "vulnerable", budget=2
        ),
        run_triage_loop_langgraph(
            MalformedArgumentStubLLMClient("find_callers", {"nod_id": "typo"}),
            repo_index,
            "app.sink",
            "vulnerable",
            budget=10,
        ),
        run_triage_loop_langgraph(
            NamesMissingSymbolStubLLMClient("app.sink", "definitely_not_a_real_symbol_xyz"),
            repo_index,
            "app.sink",
            "definitely_not_a_real_symbol_xyz",
            budget=10,
        ),
        _run_llm_error(_timeout_responder)[0],
        _run_llm_error(_rate_limited_responder)[0],
        _run_wait_enabled_llm_error(_request_too_large_responder),
        _run_llm_error(_transport_error_responder)[0],
        _run_llm_error(_refusal_responder)[0],
        _run_llm_error(_truncated_responder)[0],
        _run_llm_error(_malformed_response_responder)[0],
    ]

    for finding in findings:
        cause = compute_termination_cause(finding)
        assert cause != TerminationCause.UNCLASSIFIED, (
            f"reason {finding.result.reason!r} unexpectedly mapped to UNCLASSIFIED"
        )
