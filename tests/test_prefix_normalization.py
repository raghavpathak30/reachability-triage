"""Phase 9 U1: `GroqLLMClient._parse_response` accepts exactly the literal
`functions.` prefix when the remainder is a registered tool, logs each strip
as a distinct event, and rejects every other name shape. Real client over a
fake transport; no network, no key."""

import json
import logging
from pathlib import Path

import httpx
import pytest

from reachability.index.reachability_models import Verdict
from reachability.triage.agent_models import Message
from reachability.triage.groq_llm import GroqLLMClient, _action_to_cached
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import run_triage_loop_langgraph
from reachability.triage.llm_errors import LLMMalformedResponseError
from reachability.triage.stub_llm import FinalAnswerAction, ToolCallAction
from reachability.triage.termination_cause import TerminationCause, compute_termination_cause
from test_llm_failure_semantics import FakeGroqTransport, _chat_completion_body

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "l5" / "01_direct_console_entrypoint" / "repo"
VALID_FINAL = {"target_module": "app.sink", "target_symbol": "vulnerable", "rationale": "ok"}
CONTEXT = [Message(role="user", content="investigate")]


def _tool_call_response(request, name, arguments):
    body = _chat_completion_body(
        finish_reason="tool_calls",
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        },
    )
    return httpx.Response(200, json=body, request=request)


def _client(name, arguments):
    transport = FakeGroqTransport(lambda n, request: _tool_call_response(request, name, arguments))
    client = GroqLLMClient(
        api_key="fake-key-not-real", http_client=httpx.Client(transport=transport)
    )
    return client, transport


@pytest.fixture(autouse=True)
def _no_real_sleep_no_cache(monkeypatch):
    from reachability.triage import groq_llm

    monkeypatch.setattr(groq_llm.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("TRIAGE_LLM_CACHE_DISABLED", "1")


def test_prefixed_final_answer_accepted_and_logged():
    client, _ = _client("functions.submit_final_answer", VALID_FINAL)

    action = client.next_action(CONTEXT)

    assert isinstance(action, FinalAnswerAction)
    (event,) = client.name_normalization_events
    assert event["from"] == "functions.submit_final_answer"
    assert event["to"] == "submit_final_answer"
    assert event["sequence"] == 0
    assert client.response_shape_log[-1]["name_normalized"] is True
    assert client.response_shape_log[-1]["tool_names"] == ["functions.submit_final_answer"]


def test_prefixed_evidence_tool_accepted_as_tool_call():
    client, _ = _client("functions.search_symbol", {"pattern": "*vulnerable"})

    action = client.next_action(CONTEXT)

    assert isinstance(action, ToolCallAction)
    assert action.tool_name == "search_symbol"
    (event,) = client.name_normalization_events
    assert event["to"] == "search_symbol"


def test_full_loop_finishes_with_prefixed_names():
    def responder(call_number, request):
        if call_number == 1:
            return _tool_call_response(request, "functions.search_symbol", {"pattern": "*vulnerable"})
        return _tool_call_response(request, "functions.submit_final_answer", VALID_FINAL)

    transport = FakeGroqTransport(responder)
    client = GroqLLMClient(
        api_key="fake-key-not-real", http_client=httpx.Client(transport=transport)
    )
    finding = run_triage_loop_langgraph(
        client, build_repo_index(FIXTURE_REPO), "app.sink", "vulnerable", 5
    )

    assert compute_termination_cause(finding) != TerminationCause.LLM_MALFORMED_RESPONSE
    assert not finding.result.reason or not finding.result.reason.startswith("llm_malformed_response")
    assert len(client.name_normalization_events) == 2


def test_unregistered_prefixed_name_rejected_with_no_event():
    client, _ = _client("functions.not_a_tool", {"pattern": "x"})

    with pytest.raises(LLMMalformedResponseError, match="^tool call failed schema validation"):
        client.next_action(CONTEXT)

    assert client.name_normalization_events == []
    assert client.response_shape_log[-1]["name_normalized"] is False


def test_unregistered_prefixed_name_in_loop_is_malformed_unknown():
    transport = FakeGroqTransport(
        lambda n, request: _tool_call_response(request, "functions.not_a_tool", {"pattern": "x"})
    )
    client = GroqLLMClient(
        api_key="fake-key-not-real", http_client=httpx.Client(transport=transport)
    )
    finding = run_triage_loop_langgraph(
        client, build_repo_index(FIXTURE_REPO), "app.sink", "vulnerable", 5
    )

    assert finding.result.verdict == Verdict.UNKNOWN
    assert finding.result.reason.startswith("llm_malformed_response")


@pytest.mark.parametrize(
    "name",
    [
        "function.submit_final_answer",
        "fns.submit_final_answer",
        "functions.functions.submit_final_answer",
        "functions.",
        "Functions.submit_final_answer",
        " functions.submit_final_answer",
    ],
)
def test_non_exact_prefixes_rejected(name):
    client, _ = _client(name, VALID_FINAL)

    with pytest.raises(LLMMalformedResponseError):
        client.next_action(CONTEXT)

    assert client.name_normalization_events == []
    assert client.response_shape_log[-1]["name_normalized"] is False


@pytest.mark.parametrize(
    "name,arguments",
    [("submit_final_answer", VALID_FINAL), ("search_symbol", {"pattern": "*x"})],
)
def test_bare_names_have_no_event(name, arguments):
    client, _ = _client(name, arguments)

    client.next_action(CONTEXT)

    assert client.name_normalization_events == []
    assert client.response_shape_log[-1]["name_normalized"] is False


def test_prefixed_name_with_bad_args_logs_event_then_rejects_with_raw_name():
    client, _ = _client("functions.submit_final_answer", {"target_symbol": "vulnerable", "rationale": "ok"})

    with pytest.raises(LLMMalformedResponseError) as excinfo:
        client.next_action(CONTEXT)

    assert "'functions.submit_final_answer'" in str(excinfo.value)
    assert len(client.name_normalization_events) == 1


def test_one_info_log_record_per_strip_and_none_for_rejects(caplog):
    caplog.set_level(logging.INFO, logger="reachability.triage.groq_llm")
    accepted, _ = _client("functions.search_symbol", {"pattern": "*x"})
    rejected, _ = _client("functions.not_a_tool", {"pattern": "*x"})

    accepted.next_action(CONTEXT)
    with pytest.raises(LLMMalformedResponseError):
        rejected.next_action(CONTEXT)

    records = [r for r in caplog.records if "tool_name_prefix_stripped" in r.getMessage()]
    assert len(records) == 1
    assert "from=functions.search_symbol to=search_symbol" in records[0].getMessage()


def test_cache_round_trip_stores_stripped_name():
    client, _ = _client("functions.search_symbol", {"pattern": "*x"})

    action = client.next_action(CONTEXT)

    assert _action_to_cached(action)["tool_name"] == "search_symbol"
