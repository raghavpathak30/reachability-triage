"""Phase 5 U3 gate — the Postgres-backed real-LLM response cache.

Reuses `test_llm_failure_semantics.py`'s `FakeGroqTransport` (cross-test-file
import, same established precedent as
`test_triage_langgraph_loop_adversarial.py`'s `import
test_triage_langgraph_loop_termination as gate_i`) rather than duplicating
a fake transport. Both tests here need a real Postgres DB (`db_session`,
`tests/conftest.py`) -- unlike `test_llm_failure_semantics.py`, which
explicitly disables the cache to stay DB-independent.
"""

import json
from pathlib import Path

from reachability.db.repository import serialize_finding
from reachability.triage.groq_llm import GroqLLMClient
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import run_triage_loop_langgraph
from test_llm_failure_semantics import FakeGroqTransport, _chat_completion_body

import httpx

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "l5" / "01_direct_console_entrypoint" / "repo"
TARGET_MODULE = "app.sink"
TARGET_SYMBOL = "vulnerable"
BUDGET = 5


def _final_answer_responder(call_number, request):
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
                                "target_module": TARGET_MODULE,
                                "target_symbol": TARGET_SYMBOL,
                                "rationale": "fixed cache-test response",
                            }
                        ),
                    },
                }
            ],
        },
    )
    return httpx.Response(200, json=body, request=request)


def _finding_json(finding) -> str:
    return json.dumps(serialize_finding(finding), sort_keys=True)


def test_repeated_run_is_a_cache_hit_and_makes_zero_additional_calls(db_session, monkeypatch):
    monkeypatch.delenv("TRIAGE_LLM_CACHE_DISABLED", raising=False)
    transport = FakeGroqTransport(_final_answer_responder)
    repo_index = build_repo_index(FIXTURE_REPO)

    client1 = GroqLLMClient(
        api_key="fake-key-not-real", http_client=httpx.Client(transport=transport)
    )
    finding1 = run_triage_loop_langgraph(
        client1, repo_index, TARGET_MODULE, TARGET_SYMBOL, BUDGET
    )
    assert transport.calls == 1

    # A second, independent client instance -- same fake transport object --
    # simulating a second job run. If this were an in-process cache on the
    # client instance, using a fresh instance here would not prove
    # anything; using a fresh instance against the *same* transport proves
    # the hit came from the Postgres-backed cache, not client-local memory.
    client2 = GroqLLMClient(
        api_key="fake-key-not-real", http_client=httpx.Client(transport=transport)
    )
    finding2 = run_triage_loop_langgraph(
        client2, repo_index, TARGET_MODULE, TARGET_SYMBOL, BUDGET
    )

    assert transport.calls == 1, "second run must not have touched the transport at all"
    assert _finding_json(finding1) == _finding_json(finding2)


def test_cache_disabled_flag_defeats_the_cache(db_session, monkeypatch):
    monkeypatch.setenv("TRIAGE_LLM_CACHE_DISABLED", "1")
    transport = FakeGroqTransport(_final_answer_responder)
    repo_index = build_repo_index(FIXTURE_REPO)

    client1 = GroqLLMClient(
        api_key="fake-key-not-real", http_client=httpx.Client(transport=transport)
    )
    run_triage_loop_langgraph(client1, repo_index, TARGET_MODULE, TARGET_SYMBOL, BUDGET)
    assert transport.calls == 1

    client2 = GroqLLMClient(
        api_key="fake-key-not-real", http_client=httpx.Client(transport=transport)
    )
    run_triage_loop_langgraph(client2, repo_index, TARGET_MODULE, TARGET_SYMBOL, BUDGET)

    assert transport.calls == 2, "cache disabled -- second run must hit the transport again"
