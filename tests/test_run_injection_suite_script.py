"""Phase 6b: `scripts/run_injection_suite_real.py` row-building/writing path
-- per-request usage log, rate-limit events, `max_tokens` record, the opt-in
client kwargs, and org-ID redaction (amendments A1/A2, R3/R4/R8). Real
`GroqLLMClient` over a fake transport; no network."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from reachability.triage.groq_llm import GroqLLMClient
from test_llm_failure_semantics import FakeGroqTransport, _chat_completion_body

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_injection_suite_real.py"
ORG_ID = "org_01abcDEF23"


@pytest.fixture
def script(monkeypatch):
    """Import the script with `os.environ`/`sys.path` restored afterwards
    (it mutates both at import time)."""
    saved_environ = dict(os.environ)
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("run_injection_suite_real_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        os.environ.clear()
        os.environ.update(saved_environ)
        sys.path[:] = saved_path


def _tool_call_response(request, name, arguments, usage):
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
    body["usage"] = usage
    return httpx.Response(200, json=body, request=request)


def _install_fake_client(monkeypatch, script, responder, constructed):
    def factory(**kwargs):
        constructed.append(kwargs)
        return GroqLLMClient(
            api_key="fake-key-not-real",
            http_client=httpx.Client(transport=FakeGroqTransport(responder)),
            sleep_fn=lambda seconds: None,
            **kwargs,
        )

    monkeypatch.setattr(script, "GroqLLMClient", factory)


def _completing_responder_after_429(call_number, request):
    if call_number == 1:
        return httpx.Response(
            429,
            headers={"retry-after": "2", "x-ratelimit-limit-tokens": "8000"},
            json={"error": {"message": f"Rate limit reached for organization {ORG_ID} TPM: Limit 8000, Used 7900, Requested 500"}},
            request=request,
        )
    if call_number == 2:
        return _tool_call_response(
            request,
            "search_symbol",
            {"pattern": "*vulnerable"},
            {"prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230},
        )
    return _tool_call_response(
        request,
        "submit_final_answer",
        {"target_module": "pkg.sink", "target_symbol": "vulnerable", "rationale": "ok"},
        {"prompt_tokens": 2000, "completion_tokens": 40, "total_tokens": 2040},
    )


def test_row_contains_usage_events_max_tokens_and_client_kwargs(monkeypatch, script):
    constructed: list[dict] = []
    _install_fake_client(monkeypatch, script, _completing_responder_after_429, constructed)
    monkeypatch.setenv("TRIAGE_INJECTION_SUITE_MAX_TOTAL_WAIT_SECONDS", "123")

    row = script._run_fixture_with_retry("02_unauthorized_tool_invocation", script._fixture_spec("02_unauthorized_tool_invocation"))

    assert constructed == [
        {"wait_on_rate_limit": True, "rate_limit_max_total_wait_seconds": 123.0}
    ]
    assert row["termination_cause"] == "completed"
    assert row["attempts"] == 1
    assert row["request_count"] == 2
    assert row["max_single_request_total_tokens"] == 2040
    assert row["max_tokens"] is None
    assert row["max_tokens_note"] == "not sent; provider default applies"
    assert row["attempt_details"][0]["request_usage_log"] == [
        {"prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230},
        {"prompt_tokens": 2000, "completion_tokens": 40, "total_tokens": 2040},
    ]
    assert row["attempt_details"][0]["max_prompt_tokens"] == 2000
    (event,) = row["rate_limit_events"]
    assert event["waited"] == 2.5
    assert event["status_code"] == 429
    assert event["limit"] == 8000 and event["requested"] == 500
    assert ORG_ID in event["raw_body"]
    assert row["total_rate_limit_wait_seconds"] == 2.5


def test_redact_org_ids_is_recursive():
    spec = importlib.util.spec_from_file_location("redact_only", SCRIPT)
    saved_environ = dict(os.environ)
    saved_path = list(sys.path)
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        os.environ.clear()
        os.environ.update(saved_environ)
        sys.path[:] = saved_path

    redacted = module._redact_org_ids({"a": [f"x {ORG_ID} y", {"b": ORG_ID}], "n": 3})

    assert redacted == {"a": ["x org_[REDACTED] y", {"b": "org_[REDACTED]"}], "n": 3}


def test_written_files_never_contain_org_id_and_do_contain_marker(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    _install_fake_client(monkeypatch, script, _completing_responder_after_429, constructed)
    monkeypatch.setattr(script, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("TRIAGE_INJECTION_SUITE_DELAY_SECONDS", "0")
    monkeypatch.setattr(script.time, "sleep", lambda seconds: None)

    exit_code = script.main()

    written = sorted(tmp_path.rglob("*.json"))
    assert len(written) >= 4  # three per-fixture rows + the summary
    combined = "".join(p.read_text() for p in written)
    assert ORG_ID not in combined
    assert "org_[REDACTED]" in combined
    assert exit_code in (0,)


def test_terminal_too_large_reason_recorded_redacted_and_not_retried(monkeypatch, tmp_path, script):
    def responder(call_number, request):
        return httpx.Response(
            413,
            json={"error": {"message": f"Request too large for {ORG_ID}: Limit 8000, Requested 9500"}},
            request=request,
        )

    constructed: list[dict] = []
    _install_fake_client(monkeypatch, script, responder, constructed)
    monkeypatch.setattr(script.time, "sleep", lambda seconds: None)

    row = script._run_fixture_with_retry(
        "02_unauthorized_tool_invocation", script._fixture_spec("02_unauthorized_tool_invocation")
    )

    assert row["termination_cause"] == "llm_request_too_large"
    assert row["attempts"] == 1
    assert row["reason"].startswith("llm_request_too_large")
    assert "requested=9500" in row["reason"] and "limit=8000" in row["reason"]

    out = tmp_path / "row.json"
    script.write_report(script._redact_org_ids(row), out)
    assert ORG_ID not in out.read_text()
    assert "org_[REDACTED]" in out.read_text()
