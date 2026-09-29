"""Phase 6b U1 gate -- `GroqLLMClient.request_usage_log` records one entry
per successful (usage-bearing) response, without changing the existing
running totals. Fake transport only, no network, cache disabled."""

import json

import httpx
import pytest

from reachability.triage.agent_models import Message
from reachability.triage.llm_config import estimate_cost
from test_llm_failure_semantics import _chat_completion_body, _client_with_transport

USAGES = [(100, 10, 110), (250, 20, 270), (700, 5, 705)]


@pytest.fixture(autouse=True)
def _no_real_sleep_no_cache(monkeypatch):
    from reachability.triage import groq_llm

    monkeypatch.setattr(groq_llm.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("TRIAGE_LLM_CACHE_DISABLED", "1")


def _tool_call_body(prompt, completion, total) -> dict:
    body = _chat_completion_body(
        finish_reason="tool_calls",
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_symbol", "arguments": json.dumps({"pattern": "*x"})},
                }
            ],
        },
    )
    body["usage"] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }
    return body


CONTEXT = [Message(role="user", content="hello")]


def test_usage_log_has_one_entry_per_response_matching_totals():
    def responder(call_number, request):
        p, c, t = USAGES[call_number - 1]
        return httpx.Response(200, json=_tool_call_body(p, c, t), request=request)

    client, transport = _client_with_transport(responder)
    for _ in USAGES:
        client.next_action(CONTEXT)

    assert transport.calls == 3
    assert len(client.request_usage_log) == 3
    for entry, (p, c, t) in zip(client.request_usage_log, USAGES):
        assert entry == {"prompt_tokens": p, "completion_tokens": c, "total_tokens": t}
    assert sum(e["total_tokens"] for e in client.request_usage_log) == client.total_tokens_used
    expected_cost = sum(estimate_cost(client.model, p, c) for p, c, _ in USAGES)
    assert client.total_cost_accrued == pytest.approx(expected_cost)


def test_failed_request_adds_no_usage_entry():
    def responder(call_number, request):
        if call_number == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}}, request=request)
        return httpx.Response(200, json=_tool_call_body(50, 5, 55), request=request)

    client, transport = _client_with_transport(responder)
    client.next_action(CONTEXT)

    assert transport.calls == 2
    assert client.request_usage_log == [
        {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55}
    ]
    assert client.total_tokens_used == 55
