"""Phase 5 U2 gate — every `LLMError` subclass degrades the loop to
`Verdict.UNKNOWN`, with the correct retry-attempt count.

`FakeGroqTransport` is an `httpx.BaseTransport` injected via
`GroqLLMClient(http_client=httpx.Client(transport=...))` -- the exact
mechanism verified against the installed `groq==1.7.0`/`httpx==0.28.1` at
implementation time (see `.agent/progress.md`'s Phase 5 section for the
direct probe). No real network call is made by any test in this file.
"""

from pathlib import Path

import httpx
import pytest

from reachability.index.reachability_models import Verdict
from reachability.triage.groq_llm import GroqLLMClient
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import run_triage_loop_langgraph

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "l5" / "01_direct_console_entrypoint" / "repo"
TARGET_MODULE = "app.sink"
TARGET_SYMBOL = "vulnerable"
BUDGET = 5


class FakeGroqTransport(httpx.BaseTransport):
    """Counts every `handle_request` call and delegates the actual
    response/exception to a caller-supplied `responder(call_number, request)`.
    """

    def __init__(self, responder):
        self.calls = 0
        self._responder = responder

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return self._responder(self.calls, request)


def _chat_completion_body(**choice_overrides) -> dict:
    choice = {
        "index": 0,
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": None},
    }
    choice.update(choice_overrides)
    return {
        "id": "fake",
        "object": "chat.completion",
        "created": 0,
        "model": "openai/gpt-oss-20b",
        "choices": [choice],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _client_with_transport(responder) -> tuple[GroqLLMClient, FakeGroqTransport]:
    transport = FakeGroqTransport(responder)
    http_client = httpx.Client(transport=transport)
    client = GroqLLMClient(api_key="fake-key-not-real", http_client=http_client)
    return client, transport


def _run(client: GroqLLMClient) -> "TriageFinding":  # noqa: F821 - typing only
    repo_index = build_repo_index(FIXTURE_REPO)
    return run_triage_loop_langgraph(client, repo_index, TARGET_MODULE, TARGET_SYMBOL, BUDGET)


@pytest.fixture(autouse=True)
def _no_real_sleep_no_cache(monkeypatch):
    from reachability.triage import groq_llm

    monkeypatch.setattr(groq_llm.time, "sleep", lambda seconds: None)
    # This file is U2's own gate and must stay independent of Postgres/U3's
    # caching (per-call fault injection must always reach the fake
    # transport, never short-circuit via a cache hit) -- explicit, not
    # relying on some other test file having already set DATABASE_URL via
    # the session-scoped postgres_cluster fixture.
    monkeypatch.setenv("TRIAGE_LLM_CACHE_DISABLED", "1")


def test_timeout_degrades_to_unknown_with_three_attempts():
    def responder(call_number, request):
        raise httpx.TimeoutException("simulated timeout", request=request)

    client, transport = _client_with_transport(responder)
    finding = _run(client)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert finding.result.reason.startswith("llm_timeout:")
    assert transport.calls == 3


def test_rate_limited_degrades_to_unknown_with_three_attempts():
    def responder(call_number, request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}}, request=request)

    client, transport = _client_with_transport(responder)
    finding = _run(client)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert finding.result.reason.startswith("llm_rate_limited:")
    assert transport.calls == 3


def test_transport_error_degrades_to_unknown_with_three_attempts():
    def responder(call_number, request):
        raise httpx.ConnectError("simulated connect error", request=request)

    client, transport = _client_with_transport(responder)
    finding = _run(client)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert finding.result.reason.startswith("llm_transport_error:")
    assert transport.calls == 3


def test_refusal_degrades_to_unknown_with_one_attempt():
    def responder(call_number, request):
        body = _chat_completion_body(finish_reason="content_filter")
        return httpx.Response(200, json=body, request=request)

    client, transport = _client_with_transport(responder)
    finding = _run(client)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert finding.result.reason.startswith("llm_refusal:")
    assert transport.calls == 1


def test_truncated_degrades_to_unknown_with_one_attempt():
    def responder(call_number, request):
        body = _chat_completion_body(finish_reason="length")
        return httpx.Response(200, json=body, request=request)

    client, transport = _client_with_transport(responder)
    finding = _run(client)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert finding.result.reason.startswith("llm_truncated:")
    assert transport.calls == 1


def test_malformed_response_degrades_to_unknown_with_one_attempt():
    def responder(call_number, request):
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

    client, transport = _client_with_transport(responder)
    finding = _run(client)

    assert finding.result.verdict == Verdict.UNKNOWN
    assert finding.result.reason.startswith("llm_malformed_response:")
    assert transport.calls == 1


@pytest.mark.parametrize(
    "responder_factory",
    [
        lambda: (lambda call_number, request: (_ for _ in ()).throw(
            httpx.TimeoutException("simulated timeout", request=request)
        )),
        lambda: (
            lambda call_number, request: httpx.Response(
                429, json={"error": {"message": "rate limited"}}, request=request
            )
        ),
        lambda: (lambda call_number, request: (_ for _ in ()).throw(
            httpx.ConnectError("simulated connect error", request=request)
        )),
        lambda: (
            lambda call_number, request: httpx.Response(
                200, json=_chat_completion_body(finish_reason="content_filter"), request=request
            )
        ),
        lambda: (
            lambda call_number, request: httpx.Response(
                200, json=_chat_completion_body(finish_reason="length"), request=request
            )
        ),
        lambda: (
            lambda call_number, request: httpx.Response(
                200,
                json=_chat_completion_body(
                    finish_reason="tool_calls",
                    message={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "search_symbol", "arguments": "{bad json"},
                            }
                        ],
                    },
                ),
                request=request,
            )
        ),
    ],
)
def test_no_injected_failure_ever_produces_a_confident_verdict(responder_factory):
    client, _transport = _client_with_transport(responder_factory())
    finding = _run(client)

    assert finding.result.verdict not in (
        Verdict.REACHABLE,
        Verdict.REACHABLE_ONLY_FROM_TESTS,
        Verdict.NOT_REACHABLE,
    )
    assert finding.result.verdict == Verdict.UNKNOWN
