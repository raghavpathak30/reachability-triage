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
ORG_ID_SEP = "org_01ab_CD-xyzq"


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


def _text_reply_response(request, text):
    body = _chat_completion_body(
        finish_reason="stop", message={"role": "assistant", "content": text}
    )
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


def _make_completing_responder_after_429(org_id):
    def responder(call_number, request):
        if call_number == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "2", "x-ratelimit-limit-tokens": "8000"},
                json={"error": {"message": f"Rate limit reached for organization {org_id} TPM: Limit 8000, Used 7900, Requested 500"}},
                request=request,
            )
        return _completing_responder_tail(call_number, request)

    return responder


def _completing_responder_after_429(call_number, request):
    return _make_completing_responder_after_429(ORG_ID)(call_number, request)


def _completing_responder_tail(call_number, request):
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


def test_redact_org_ids_handles_underscore_and_hyphen():
    spec = importlib.util.spec_from_file_location("redact_only_sep", SCRIPT)
    saved_environ = dict(os.environ)
    saved_path = list(sys.path)
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        os.environ.clear()
        os.environ.update(saved_environ)
        sys.path[:] = saved_path

    assert module._redact_org_ids({"a": f"x {ORG_ID_SEP} y"}) == {"a": "x org_[REDACTED] y"}
    assert module._redact_org_ids(f"id={ORG_ID_SEP}, next") == "id=org_[REDACTED], next"


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
    assert exit_code == 0
    (summary_path,) = tmp_path.glob("results/injection_suite_real_summary_*.json")
    report = json.loads(summary_path.read_text())
    assert report["completed"] == report["total"] == 3
    assert report["overall"] in ("PASS", "FAIL")


def test_separator_org_id_never_written_including_tail(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    _install_fake_client(
        monkeypatch, script, _make_completing_responder_after_429(ORG_ID_SEP), constructed
    )
    monkeypatch.setattr(script, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("TRIAGE_INJECTION_SUITE_DELAY_SECONDS", "0")
    monkeypatch.setattr(script.time, "sleep", lambda seconds: None)

    script.main()

    combined = "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert ORG_ID_SEP not in combined
    assert "xyzq" not in combined
    assert "org_[REDACTED]" in combined


def test_text_reply_responder_is_incomplete_not_a_false_pass(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    _install_fake_client(
        monkeypatch,
        script,
        lambda call_number, request: _text_reply_response(request, "I will just answer in prose."),
        constructed,
    )
    monkeypatch.setattr(script, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("TRIAGE_INJECTION_SUITE_DELAY_SECONDS", "0")
    monkeypatch.setattr(script.time, "sleep", lambda seconds: None)

    exit_code = script.main()

    assert exit_code == 1
    (summary_path,) = tmp_path.glob("results/injection_suite_real_summary_*.json")
    assert json.loads(summary_path.read_text())["overall"] == "INCOMPLETE"


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


# ---- Phase 8: tool-call trace / loop metrics / response shapes ----

_SPEC_NAME = "02_unauthorized_tool_invocation"
_USAGE = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}


def _repeat_then_final_responder(pattern, repeats):
    def responder(call_number, request):
        if call_number <= repeats:
            return _tool_call_response(request, "search_symbol", {"pattern": pattern}, _USAGE)
        return _tool_call_response(
            request,
            "submit_final_answer",
            {"target_module": "pkg.sink", "target_symbol": "vulnerable", "rationale": "ok"},
            _USAGE,
        )

    return responder


def _run_row(monkeypatch, script, responder):
    _install_fake_client(monkeypatch, script, responder, [])
    monkeypatch.setattr(script.time, "sleep", lambda seconds: None)
    return script._run_fixture_with_retry(_SPEC_NAME, script._fixture_spec(_SPEC_NAME))


def test_scripted_repeat_yields_exact_loop_metrics(monkeypatch, script):
    row = _run_row(monkeypatch, script, _repeat_then_final_responder("*vulnerable", 3))

    assert row["termination_cause"] == "completed"
    assert row["loop_metrics"] == {
        "tool_call_count": 3,
        "distinct_calls": 1,
        "repeated_calls": 2,
        "longest_identical_streak": 3,
        "per_tool_counts": {"search_symbol": 3},
    }
    assert row["budget"] == 15
    assert len(row["tool_call_trace"]) == 3
    assert all("result" not in entry for entry in row["tool_call_trace"])


def test_budget_exceeded_run_still_has_metrics(monkeypatch, script):
    row = _run_row(monkeypatch, script, _repeat_then_final_responder("*vulnerable", 10_000))

    assert row["termination_cause"] == "budget_exceeded"
    assert row["loop_metrics"]["tool_call_count"] == 15
    assert row["loop_metrics"]["repeated_calls"] == 14
    assert row["loop_metrics"]["longest_identical_streak"] == 15


def test_org_id_in_argument_never_written(monkeypatch, tmp_path, script):
    row = _run_row(
        monkeypatch, script, _repeat_then_final_responder(f"see {ORG_ID_SEP}, ok", 2)
    )
    out = tmp_path / "row.json"
    script.write_report(script._redact_org_ids(row), out)

    written = out.read_text()
    assert ORG_ID_SEP not in written and "xyzq" not in written
    assert "org_[REDACTED]" in written
    assert row["loop_metrics"]["repeated_calls"] == 1


def test_text_reply_recorded_as_shape_only(monkeypatch, tmp_path, script):
    secret_text = "UNIQUE-REPLY-TEXT-9f3a"
    row = _run_row(
        monkeypatch,
        script,
        lambda call_number, request: _text_reply_response(request, secret_text),
    )
    out = tmp_path / "row.json"
    script.write_report(script._redact_org_ids(row), out)

    shape = row["response_shape_log"][0]
    assert shape["finish_reason"] == "stop"
    assert shape["tool_call_count"] == 0
    assert shape["content_length"] == len(secret_text)
    assert secret_text not in out.read_text()


_ASSIGNED = "02_unauthorized_tool_invocation"


def _search_then_final(pattern, module, symbol, final_name="submit_final_answer", rationale="ok"):
    def responder(call_number, request):
        if call_number == 1:
            return _tool_call_response(
                request,
                "search_symbol",
                {"pattern": pattern},
                {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            )
        return _tool_call_response(
            request,
            final_name,
            {"target_module": module, "target_symbol": symbol, "rationale": rationale},
            {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        )

    return responder


def test_prefixed_invalid_final_answer_reason_is_capped_and_events_listed(monkeypatch, script):
    def responder(call_number, request):
        return _tool_call_response(
            request,
            "functions.submit_final_answer",
            {"target_symbol": "vulnerable", "rationale": "r" * 2000},
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    row = _run_row(monkeypatch, script, responder)

    assert row["termination_cause"] == "llm_malformed_response"
    assert len(row["reason"]) <= 300 + len("...[+99999 chars]")
    assert isinstance(row["name_normalization_events"], list)
    assert isinstance(row["attempt_details"][0]["name_normalization_events"], list)


def test_prefixed_valid_final_answer_row_has_one_event(monkeypatch, script):
    row = _run_row(
        monkeypatch,
        script,
        _search_then_final("*vulnerable", "pkg.sink", "vulnerable", "functions.submit_final_answer"),
    )

    assert row["termination_cause"] == "completed"
    assert len(row["name_normalization_events"]) == 1


def test_completed_row_carries_verdict_fields_and_within_allowed(monkeypatch, script):
    label = json.loads(
        (script.FIXTURE_REPO.parent / "label.json").read_text()
    )

    row = _run_row(monkeypatch, script, _search_then_final("*vulnerable", "pkg.sink", "vulnerable"))

    assert row["verdict"] == "not_reachable"
    assert row["final_target_module"] == "pkg.sink"
    assert row["final_target_symbol"] == "vulnerable"
    assert row["allowed_verdicts"] == label["allowed_verdicts"] == ["not_reachable"]
    assert row["within_allowed"] is True
    assert row["attempt_details"][0]["within_allowed"] is True


def test_within_allowed_false_for_reachable_verdict(monkeypatch, script):
    row = _run_row(monkeypatch, script, _search_then_final("*do_other", "pkg.other", "do_other"))

    assert row["verdict"] == "reachable"
    assert row["within_allowed"] is False


def test_within_allowed_false_for_target_mismatch_with_not_reachable(monkeypatch, script):
    row = _run_row(monkeypatch, script, _search_then_final("*wrapper", "pkg.dead", "wrapper"))

    assert row["verdict"] == "not_reachable"
    assert (row["final_target_module"], row["final_target_symbol"]) == ("pkg.dead", "wrapper")
    assert row["within_allowed"] is False
