"""Phase 9 U2a/U2b: `GroqLLMClient(tool_choice=...)` plumbing. The default is
`"required"` (U2b); `"auto"` stays selectable and any non-`"auto"` value is
folded into the cache fingerprint.
Fake transport; no network, no key."""

import json
from pathlib import Path

import httpx
import pytest

import reachability.triage.groq_llm as groq_llm_module
from reachability.index.reachability_models import Verdict
from reachability.triage.agent_models import Message
from reachability.triage.groq_llm import GroqLLMClient, _build_tool_defs, _prompt_fingerprint
from reachability.triage.llm_cache import compute_cache_key
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import run_triage_loop_langgraph
from reachability.triage.llm_errors import LLMMalformedResponseError
from reachability.triage.termination_cause import TerminationCause, compute_termination_cause
from test_llm_failure_semantics import FakeGroqTransport, _chat_completion_body

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "l5" / "01_direct_console_entrypoint" / "repo"


def _final_answer(call_number, request):
    body = _chat_completion_body(
        finish_reason="tool_calls",
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "submit_final_answer",
                        "arguments": json.dumps(
                            {
                                "target_module": "app.sink",
                                "target_symbol": "vulnerable",
                                "rationale": "ok",
                            }
                        ),
                    },
                }
            ],
        },
    )
    return httpx.Response(200, json=body, request=request)


def _bodies_and_client(**kwargs):
    bodies: list[dict] = []

    def responder(call_number, request):
        bodies.append(json.loads(request.content))
        return _final_answer(call_number, request)

    transport = FakeGroqTransport(responder)
    client = GroqLLMClient(
        api_key="fake-key-not-real", http_client=httpx.Client(transport=transport), **kwargs
    )
    return bodies, client


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setenv("TRIAGE_LLM_CACHE_DISABLED", "1")


def test_default_client_sends_required():
    bodies, client = _bodies_and_client()

    client.next_action([Message(role="user", content="x")])

    assert client.tool_choice == "required"
    assert bodies[0]["tool_choice"] == "required"


def test_explicit_auto_client_sends_auto():
    bodies, client = _bodies_and_client(tool_choice="auto")

    client.next_action([Message(role="user", content="x")])

    assert client.tool_choice == "auto"
    assert bodies[0]["tool_choice"] == "auto"


def test_text_reply_under_required_is_malformed_never_completed():
    requests: list[int] = []

    def responder(call_number, request):
        requests.append(call_number)
        body = _chat_completion_body(
            finish_reason="stop", message={"role": "assistant", "content": "I think it is dead code."}
        )
        return httpx.Response(200, json=body, request=request)

    transport = FakeGroqTransport(responder)
    client = GroqLLMClient(api_key="fake-key-not-real", http_client=httpx.Client(transport=transport))

    with pytest.raises(LLMMalformedResponseError):
        client.next_action([Message(role="user", content="x")])

    client = GroqLLMClient(api_key="fake-key-not-real", http_client=httpx.Client(transport=transport))
    transport.calls = 0
    finding = run_triage_loop_langgraph(
        client, build_repo_index(FIXTURE_REPO), "app.sink", "vulnerable", 5
    )

    assert client.tool_choice == "required"
    assert finding.result.verdict == Verdict.UNKNOWN
    assert finding.result.reason.startswith("llm_malformed_response")
    assert compute_termination_cause(finding) == TerminationCause.LLM_MALFORMED_RESPONSE
    assert compute_termination_cause(finding) != TerminationCause.COMPLETED
    assert transport.calls == 1  # one turn, one request: no hidden retry


@pytest.mark.parametrize("value", ["none", "foo", ""])
def test_invalid_tool_choice_rejected(value):
    with pytest.raises(ValueError):
        GroqLLMClient(api_key="fake-key-not-real", tool_choice=value)


def test_auto_fingerprint_is_the_pre_phase9_string():
    expected = groq_llm_module._SYSTEM_PROMPT + json.dumps(_build_tool_defs(), sort_keys=True)

    assert _prompt_fingerprint("auto") == expected
    assert _prompt_fingerprint() == expected
    assert _prompt_fingerprint("required") == expected + "|tool_choice=required"


def test_cache_key_differs_between_auto_and_required():
    context = [Message(role="user", content="same context")]

    auto_key = compute_cache_key("v1", "m", _prompt_fingerprint("auto"), context)
    required_key = compute_cache_key("v1", "m", _prompt_fingerprint("required"), context)

    assert auto_key != required_key


def test_cached_auto_action_is_not_served_to_required_client(db_session, monkeypatch):
    monkeypatch.delenv("TRIAGE_LLM_CACHE_DISABLED", raising=False)
    repo_index = build_repo_index(FIXTURE_REPO)

    def run(**kwargs):
        bodies, client = _bodies_and_client(**kwargs)
        run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", 5)
        return bodies

    assert len(run(tool_choice="auto")) == 1
    assert len(run(tool_choice="auto")) == 0  # auto client: cache hit, no request
    required_bodies = run()  # default is now "required": a distinct cache key
    assert len(required_bodies) == 1
    assert required_bodies[0]["tool_choice"] == "required"


def test_tool_choice_env_vars_not_referenced_under_src():
    root = Path(__file__).resolve().parents[1] / "src" / "reachability"
    for path in root.rglob("*.py"):
        text = path.read_text()
        for name in ("TRIAGE_REAL_RUN_TOOL_CHOICE", "TRIAGE_REAL_RUN_FIXTURES", "TRIAGE_REAL_RUN_LABEL"):
            assert name not in text, f"{name} referenced in {path}"
