"""Phase 6b U2 gate -- opt-in `wait_on_rate_limit` on `GroqLLMClient`.

Gates: (a) default OFF is exactly the pre-existing behaviour; (b) ON sleeps
out a 429 (`retry-after`) and re-sends the identical request; (c) the total
wait cap is respected; (d) an oversized reserved request (429 or 413) fails
at once as `llm_request_too_large` with no sleep; (d4) the 413 path.
Fake transport, injected sleep, cache disabled, no network.
"""

import json
import math
from pathlib import Path

import httpx
import pytest

from reachability.triage.groq_llm import GroqLLMClient
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import run_triage_loop_langgraph
from reachability.triage.llm_errors import LLMRequestTooLargeError
from reachability.triage.termination_cause import TerminationCause, compute_termination_cause
from test_llm_failure_semantics import FakeGroqTransport, _chat_completion_body

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "l5" / "01_direct_console_entrypoint" / "repo"
BUDGET = 5

SEARCH = ("tool", "search_symbol", {"pattern": "*vulnerable"})
FINAL = (
    "tool",
    "submit_final_answer",
    {"target_module": "app.sink", "target_symbol": "vulnerable", "rationale": "done"},
)


def rl(headers=None, body_message="rate limited"):
    return ("429", headers or {}, {"error": {"message": body_message}})


def too_large_413(message="Request too large: Limit 8000, Requested 9500"):
    return ("413", {}, {"error": {"message": message}})


TIMEOUT = ("timeout",)


@pytest.fixture(autouse=True)
def _cache_off(monkeypatch):
    monkeypatch.setenv("TRIAGE_LLM_CACHE_DISABLED", "1")


class Script:
    """Serves a fixed sequence of steps, one per transport call, recording
    request bodies and whether a `submit_final_answer` response was served."""

    def __init__(self, steps, repeat_last=False):
        self._steps = list(steps)
        self._repeat_last = repeat_last
        self.bodies: list[bytes] = []
        self.final_served = 0
        self.transport = FakeGroqTransport(self._respond)

    def _respond(self, call_number, request):
        self.bodies.append(request.content)
        index = call_number - 1
        if index >= len(self._steps):
            assert self._repeat_last, f"unexpected extra transport call #{call_number}"
            index = len(self._steps) - 1
        step = self._steps[index]
        kind = step[0]
        if kind == "timeout":
            raise httpx.TimeoutException("simulated timeout", request=request)
        if kind in ("429", "413"):
            return httpx.Response(int(kind), headers=step[1], json=step[2], request=request)
        _, name, arguments = step
        if name == "submit_final_answer":
            self.final_served += 1
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

    def client(self, sleeps, **kwargs):
        kwargs.setdefault("sleep_fn", sleeps.append)
        return GroqLLMClient(
            api_key="fake-key-not-real",
            http_client=httpx.Client(transport=self.transport),
            **kwargs,
        )


def _run(client):
    repo_index = build_repo_index(FIXTURE_REPO)
    return run_triage_loop_langgraph(client, repo_index, "app.sink", "vulnerable", BUDGET)


def _assert_no_false_pass(script, cause):
    """A COMPLETED cause requires a served `submit_final_answer`; a
    non-completed cause must not have served one."""
    if cause == TerminationCause.COMPLETED:
        assert script.final_served >= 1
    else:
        assert script.final_served == 0


# ---- (a) default OFF is unchanged ----


def test_a_default_off_three_attempts_and_original_backoff_even_with_retry_after():
    script = Script([rl({"retry-after": "7"})], repeat_last=True)
    sleeps: list[float] = []
    client = script.client(sleeps)

    finding = _run(client)

    cause = compute_termination_cause(finding)
    assert cause == TerminationCause.LLM_RATE_LIMITED
    assert script.transport.calls == 3
    assert sleeps == [1.0, 2.0]
    assert client.rate_limit_events == []
    _assert_no_false_pass(script, cause)


def test_a_off_with_oversized_body_is_plain_rate_limited():
    script = Script([rl(body_message="Limit 8000, Used 0, Requested 9500")], repeat_last=True)
    sleeps: list[float] = []

    finding = _run(script.client(sleeps))

    assert compute_termination_cause(finding) == TerminationCause.LLM_RATE_LIMITED
    assert script.transport.calls == 3
    assert sleeps == [1.0, 2.0]


def test_a_off_413_is_unchanged_transport_error_with_three_attempts():
    script = Script([too_large_413()], repeat_last=True)
    sleeps: list[float] = []

    finding = _run(script.client(sleeps))

    assert compute_termination_cause(finding) == TerminationCause.LLM_TRANSPORT_ERROR
    assert script.transport.calls == 3
    assert sleeps == [1.0, 2.0]


# ---- (b) ON waits and resends ----


def test_b_on_sleeps_retry_after_and_resends_identical_request():
    script = Script([rl({"retry-after": "7"}), SEARCH, FINAL])
    sleeps: list[float] = []
    client = script.client(sleeps, wait_on_rate_limit=True)

    finding = _run(client)

    cause = compute_termination_cause(finding)
    assert cause == TerminationCause.COMPLETED
    assert sleeps == [7.5]
    assert script.bodies[0] == script.bodies[1]
    assert script.transport.calls == 3
    assert len(client.rate_limit_events) == 1
    assert client.rate_limit_events[0]["waited"] == 7.5
    assert client.rate_limit_events[0]["status_code"] == 429
    assert client.total_rate_limit_wait_seconds == 7.5
    _assert_no_false_pass(script, cause)


def test_b2_on_without_retry_after_header_uses_fallback():
    script = Script([rl(), SEARCH, FINAL])
    sleeps: list[float] = []

    finding = _run(script.client(sleeps, wait_on_rate_limit=True))

    cause = compute_termination_cause(finding)
    assert cause == TerminationCause.COMPLETED
    assert sleeps == [20.5]
    _assert_no_false_pass(script, cause)


@pytest.mark.parametrize(
    "header_value", ["nan", "inf", "-5", "Wed, 21 Oct 2026 07:28:00 GMT", "garbage"]
)
def test_b3_unusable_retry_after_falls_back_to_default(header_value):
    script = Script([rl({"retry-after": header_value}), SEARCH, FINAL])
    sleeps: list[float] = []

    finding = _run(script.client(sleeps, wait_on_rate_limit=True))

    assert compute_termination_cause(finding) == TerminationCause.COMPLETED
    assert sleeps == [20.5]


def test_b4_tiny_retry_after_is_floored_to_one_second():
    script = Script([rl({"retry-after": "0"}), SEARCH, FINAL])
    sleeps: list[float] = []

    _run(script.client(sleeps, wait_on_rate_limit=True))

    assert sleeps == [1.5]


# ---- (c) cap ----


def test_c_single_wait_over_cap_fails_without_sleeping():
    script = Script([rl({"retry-after": "100"})], repeat_last=True)
    sleeps: list[float] = []

    finding = _run(script.client(sleeps, wait_on_rate_limit=True, rate_limit_max_total_wait_seconds=50))

    cause = compute_termination_cause(finding)
    assert cause == TerminationCause.LLM_RATE_LIMITED
    assert sleeps == []
    assert script.transport.calls == 1
    _assert_no_false_pass(script, cause)


def test_c_accumulated_waits_past_cap_end_rate_limited():
    script = Script([rl({"retry-after": "10"})], repeat_last=True)
    sleeps: list[float] = []

    finding = _run(script.client(sleeps, wait_on_rate_limit=True, rate_limit_max_total_wait_seconds=25))

    cause = compute_termination_cause(finding)
    assert cause == TerminationCause.LLM_RATE_LIMITED
    assert sleeps == [10.5, 10.5]
    assert sum(sleeps) <= 25
    assert script.transport.calls == 3
    _assert_no_false_pass(script, cause)


@pytest.mark.parametrize("bad_cap", [math.nan, math.inf, -1.0])
def test_c_invalid_cap_rejected(bad_cap):
    with pytest.raises(ValueError):
        GroqLLMClient(api_key="fake-key-not-real", rate_limit_max_total_wait_seconds=bad_cap)


# ---- (d) oversized reserved request ----


def test_d_on_oversized_429_fails_once_with_distinct_cause():
    script = Script(
        [rl(body_message="Rate limit reached on tokens per minute (TPM): Limit 8000, Used 0, Requested 9500")],
        repeat_last=True,
    )
    sleeps: list[float] = []
    client = script.client(sleeps, wait_on_rate_limit=True)

    finding = _run(client)

    cause = compute_termination_cause(finding)
    assert cause == TerminationCause.LLM_REQUEST_TOO_LARGE
    assert finding.result.reason.startswith("llm_request_too_large")
    assert "requested=9500" in finding.result.reason and "limit=8000" in finding.result.reason
    assert "prompt tokens alone" not in finding.result.reason
    assert script.transport.calls == 1
    assert sleeps == []
    _assert_no_false_pass(script, cause)


def test_d2_no_numbers_in_body_takes_wait_path():
    script = Script([rl(body_message="slow down"), SEARCH, FINAL])
    sleeps: list[float] = []

    finding = _run(script.client(sleeps, wait_on_rate_limit=True))

    assert compute_termination_cause(finding) == TerminationCause.COMPLETED
    assert sleeps == [20.5]


def test_d3_limit_only_in_header_still_detects_oversized():
    script = Script(
        [rl({"x-ratelimit-limit-tokens": "8000"}, body_message="Requested 9500")], repeat_last=True
    )
    sleeps: list[float] = []

    finding = _run(script.client(sleeps, wait_on_rate_limit=True))

    assert compute_termination_cause(finding) == TerminationCause.LLM_REQUEST_TOO_LARGE
    assert script.transport.calls == 1
    assert sleeps == []


def test_d_requested_within_limit_is_not_oversized():
    script = Script(
        [rl(body_message="Limit 8000, Used 6000, Requested 3000"), SEARCH, FINAL]
    )
    sleeps: list[float] = []

    finding = _run(script.client(sleeps, wait_on_rate_limit=True))

    assert compute_termination_cause(finding) == TerminationCause.COMPLETED


def test_d4_on_413_fails_once_with_no_sleep():
    script = Script([too_large_413()], repeat_last=True)
    sleeps: list[float] = []
    client = script.client(sleeps, wait_on_rate_limit=True)

    finding = _run(client)

    cause = compute_termination_cause(finding)
    assert cause == TerminationCause.LLM_REQUEST_TOO_LARGE
    assert script.transport.calls == 1
    assert sleeps == []
    _assert_no_false_pass(script, cause)


def test_d4_on_413_signal_carries_status_and_raw_body():
    script = Script([too_large_413()], repeat_last=True)
    client = script.client([], wait_on_rate_limit=True)

    with pytest.raises(LLMRequestTooLargeError) as info:
        client.next_action([])

    assert info.value.signal["status_code"] == 413
    assert "Requested 9500" in info.value.signal["raw_body"]
    assert info.value.signal["limit"] == 8000
    assert info.value.signal["requested"] == 9500


# ---- terminal rate-limit events (Phase 8) ----


def test_terminal_event_recorded_when_wait_cap_exceeded():
    script = Script([rl({"retry-after": "100"})], repeat_last=True)
    client = script.client([], wait_on_rate_limit=True, rate_limit_max_total_wait_seconds=50)

    _run(client)

    last = client.rate_limit_events[-1]
    assert last["terminal"] == "wait_cap_exceeded"
    assert last["retry_after"] == 100
    assert last["waited"] == 0.0
    assert client.total_rate_limit_wait_seconds == 0.0


def test_terminal_event_recorded_for_oversized_429():
    script = Script(
        [rl(body_message="Rate limit reached on tokens per minute (TPM): Limit 8000, Used 0, Requested 9500")],
        repeat_last=True,
    )
    client = script.client([], wait_on_rate_limit=True)

    _run(client)

    (event,) = client.rate_limit_events
    assert event["terminal"] == "request_too_large"
    assert event["waited"] == 0.0


def test_terminal_event_recorded_for_413():
    script = Script([too_large_413()], repeat_last=True)
    client = script.client([], wait_on_rate_limit=True)

    _run(client)

    (event,) = client.rate_limit_events
    assert event["terminal"] == "request_too_large"
    assert event["status_code"] == 413


def test_waited_then_completed_has_no_terminal_event():
    script = Script([rl({"retry-after": "7"}), SEARCH, FINAL])
    client = script.client([], wait_on_rate_limit=True)

    _run(client)

    assert client.rate_limit_events
    assert all("terminal" not in event for event in client.rate_limit_events)


@pytest.mark.parametrize("wait", [False, True])
def test_max_tokens_is_never_sent(wait):
    script = Script([SEARCH, FINAL])
    client = script.client([], wait_on_rate_limit=wait)

    _run(client)

    assert script.bodies
    for raw in script.bodies:
        body = json.loads(raw)
        assert "max_tokens" not in body
        assert "max_completion_tokens" not in body
    assert client.max_tokens_sent is None


# ---- R7: mixed 429 / timeout ----


def test_mixed_429_timeout_429_success_completes_with_separate_counters():
    script = Script([rl({"retry-after": "3"}), TIMEOUT, rl({"retry-after": "4"}), SEARCH, FINAL])
    sleeps: list[float] = []
    client = script.client(sleeps, wait_on_rate_limit=True)

    finding = _run(client)

    cause = compute_termination_cause(finding)
    assert cause == TerminationCause.COMPLETED
    assert sleeps == [3.5, 1.0, 4.5]
    assert client.total_rate_limit_wait_seconds == 8.0
    _assert_no_false_pass(script, cause)


def test_on_timeouts_still_capped_at_three_attempts_regardless_of_wait_budget():
    script = Script([TIMEOUT], repeat_last=True)
    sleeps: list[float] = []

    finding = _run(script.client(sleeps, wait_on_rate_limit=True))

    assert compute_termination_cause(finding) == TerminationCause.LLM_TIMEOUT
    assert script.transport.calls == 3
    assert sleeps == [1.0, 2.0]


# ---- static isolation ----


def test_wait_option_not_referenced_by_production_or_eval_paths():
    root = Path(__file__).resolve().parents[1] / "src" / "reachability"
    for rel in ("triage/job_runner.py", "agent/eval_harness.py"):
        assert "wait_on_rate_limit" not in (root / rel).read_text()
