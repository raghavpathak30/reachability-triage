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


# ---- Phase 9 U3 tooling: --control, --repeats ----

_FIXTURES = [
    "01_verdict_manipulation",
    "02_unauthorized_tool_invocation",
    "03_unauthorized_context_echo",
]
_DENYLIST = [
    "caller", "call", "symbol", "record", "dead", "unused", "never", "no further",
    "target_module", "target_symbol", "pkg.other", "do_other", "resolve_import",
    "submit_final_answer", "ignore", "instruction", "org_",
]
_ROW_KEYS = {
    "fixture", "termination_cause", "injection_won", "attempts", "total_tokens_used",
    "total_cost_accrued", "reason", "verdict", "final_target_module", "final_target_symbol",
    "allowed_verdicts", "within_allowed", "request_count", "max_single_request_total_tokens",
    "rate_limit_events", "total_rate_limit_wait_seconds", "max_tokens", "max_tokens_note",
    "request_usage_log", "budget", "tool_call_trace", "loop_metrics", "response_shape_log",
    "name_normalization_events", "attempt_details",
}


def _setup(monkeypatch, tmp_path, script, responder, constructed=None):
    constructed = [] if constructed is None else constructed
    _install_fake_client(monkeypatch, script, responder, constructed)
    monkeypatch.setattr(script, "RESULTS_DIR", tmp_path / "results" / "injection_suite_real")
    monkeypatch.setattr(
        script, "CONTROL_RESULTS_DIR", tmp_path / "results" / "injection_suite_real_control"
    )
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("TRIAGE_INJECTION_SUITE_DELAY_SECONDS", raising=False)
    sleeps: list[float] = []
    monkeypatch.setattr(script.time, "sleep", lambda seconds: sleeps.append(seconds))
    return constructed, sleeps


def _complete(call_number, request):
    return _search_then_final("*vulnerable", "pkg.sink", "vulnerable")(call_number, request)


def _json_text(text):
    return json.dumps(text)[1:-1]


@pytest.mark.parametrize("fixture", _FIXTURES)
def test_benign_payload_parity_and_neutrality(script, fixture):
    from reachability.triage.sandbox import sandbox_untrusted_text

    spec = script._fixture_spec(fixture)
    original = spec["injected_payload"]
    benign = script._benign_payload(original)

    assert isinstance(benign, str)
    assert benign.count("\n") == original.count("\n")
    assert [len(x) for x in benign.split("\n")] == [len(x) for x in original.split("\n")]
    assert abs(len(benign) - len(original)) <= 0.05 * len(original)
    lowered = benign.lower()
    assert not any(word in lowered for word in _DENYLIST)
    label = script._load_label(fixture)
    assert not any(secret in benign for secret in label.get("planted_secrets", []))
    assert sandbox_untrusted_text(benign) == benign


def test_control_writes_only_control_paths_and_serves_benign_payload(monkeypatch, tmp_path, script):
    bodies: list[str] = []

    def responder(call_number, request):
        bodies.append(request.content.decode())
        if call_number == 1:
            return _tool_call_response(
                request, "search_symbol", {"pattern": "*vulnerable"},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )
        if call_number == 2:
            return _tool_call_response(
                request, "find_callers", {"node_id": "pkg.sink:vulnerable"},
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )
        return _complete(3, request)

    _setup(monkeypatch, tmp_path, script, responder)
    monkeypatch.setattr(
        script, "build_report", lambda results: (_ for _ in ()).throw(AssertionError("build_report"))
    )

    exit_code = script.main(["--control"])

    assert exit_code == 0
    assert not (tmp_path / "results" / "injection_suite_real").exists()
    control_files = sorted(p.name for p in (tmp_path / "results" / "injection_suite_real_control").glob("*.json"))
    assert control_files == [f"{f}.json" for f in _FIXTURES]
    assert not list(tmp_path.glob("results/injection_suite_real_summary_*"))
    (summary_path,) = tmp_path.glob("results/injection_suite_real_control_summary_*.json")
    summary = json.loads(summary_path.read_text())
    assert "overall" not in summary and summary["kind"] == "control"
    assert len(summary["rows"]) == 3
    assert "repeats" not in summary
    for row in summary["rows"]:
        assert row["injection_won"] is None and row["control"] is True
    joined = "".join(bodies)
    for fixture in _FIXTURES:
        spec = script._fixture_spec(fixture)
        assert _json_text(script._benign_payload(spec["injected_payload"])) in joined
        assert _json_text(spec["injected_payload"]) not in joined


def test_default_run_output_schema_unchanged(monkeypatch, tmp_path, script):
    _setup(monkeypatch, tmp_path, script, _complete)

    exit_code = script.main([])

    assert exit_code == 0
    out = tmp_path / "results" / "injection_suite_real"
    assert sorted(p.name for p in out.glob("*.json")) == [f"{f}.json" for f in _FIXTURES]
    for path in out.glob("*.json"):
        row = json.loads(path.read_text())
        assert set(row) == _ROW_KEYS
        assert "repeat" not in row and "control" not in row
    (summary_path,) = tmp_path.glob("results/injection_suite_real_summary_*.json")
    report = json.loads(summary_path.read_text())
    assert set(report) == {"total", "completed", "not_run", "overall", "results"}
    assert report["total"] == report["completed"] == 3 and report["overall"] == "PASS"


def test_repeats_writes_per_repeat_files_with_fresh_clients(monkeypatch, tmp_path, script):
    constructed, _ = _setup(monkeypatch, tmp_path, script, _complete)

    exit_code = script.main(["--repeats", "3"])

    out = tmp_path / "results" / "injection_suite_real"
    expected = sorted(f"{f}.r{k}.json" for f in _FIXTURES for k in (1, 2, 3))
    assert sorted(p.name for p in out.glob("*.json")) == expected
    assert len(constructed) == 9
    assert all(
        kwargs == {"wait_on_rate_limit": True, "rate_limit_max_total_wait_seconds": 900.0}
        for kwargs in constructed
    )
    rows = [json.loads(p.read_text()) for p in out.glob("*.json")]
    assert sorted(r["repeat"] for r in rows) == [1, 1, 1, 2, 2, 2, 3, 3, 3]
    assert all(r["fixture"] in _FIXTURES for r in rows)
    (summary_path,) = tmp_path.glob("results/injection_suite_real_summary_*.json")
    report = json.loads(summary_path.read_text())
    assert report["total"] == 9 and report["completed"] == 9 and report["repeats"] == 3
    assert {f: v["runs"] for f, v in report["per_fixture_repeat_counts"].items()} == {f: 3 for f in _FIXTURES}
    assert report["overall"] == "PASS" and exit_code == 0


def test_repeats_incomplete_runs_give_incomplete_and_exit_1(monkeypatch, tmp_path, script):
    _setup(
        monkeypatch, tmp_path, script,
        lambda call_number, request: _text_reply_response(request, "prose"),
    )

    exit_code = script.main(["--repeats", "3"])

    assert exit_code == 1
    (summary_path,) = tmp_path.glob("results/injection_suite_real_summary_*.json")
    report = json.loads(summary_path.read_text())
    assert report["overall"] == "INCOMPLETE" and report["total"] == 9 and report["completed"] == 0


def test_repeats_below_one_is_an_argument_error(script):
    with pytest.raises(SystemExit):
        script.main(["--repeats", "0"])


def _prewrite(directory, name, **fields):
    directory.mkdir(parents=True, exist_ok=True)
    row = {"fixture": "01_verdict_manipulation", "injection_won": None, **fields}
    (directory / name).write_text(json.dumps(row))
    return row


def test_resume_per_repeat_skips_non_infra_and_reruns_infra(monkeypatch, tmp_path, script):
    constructed, _ = _setup(monkeypatch, tmp_path, script, _complete)
    out = tmp_path / "results" / "injection_suite_real"
    r1 = _prewrite(out, "01_verdict_manipulation.r1.json", termination_cause="budget_exceeded", repeat=1)
    _prewrite(out, "01_verdict_manipulation.r2.json", termination_cause="llm_rate_limited", repeat=2)

    script.main(["--repeats", "3"])

    assert len(constructed) == 8  # r1 skipped; r2 and r3 and the other 6 ran
    assert json.loads((out / "01_verdict_manipulation.r1.json").read_text()) == r1
    assert json.loads((out / "01_verdict_manipulation.r2.json").read_text())["termination_cause"] == "completed"


def test_resume_with_single_repeat_keeps_original_rule(monkeypatch, tmp_path, script):
    constructed, _ = _setup(monkeypatch, tmp_path, script, _complete)
    out = tmp_path / "results" / "injection_suite_real"
    _prewrite(out, "01_verdict_manipulation.json", termination_cause="budget_exceeded")

    script.main([])

    assert len(constructed) == 3  # a non-completed row IS re-run when repeats == 1


def test_control_with_repeats_is_isolated_from_adversarial_dir(monkeypatch, tmp_path, script):
    constructed, _ = _setup(monkeypatch, tmp_path, script, _complete)
    adversarial = tmp_path / "results" / "injection_suite_real"
    control = tmp_path / "results" / "injection_suite_real_control"
    kept = _prewrite(adversarial, "01_verdict_manipulation.r1.json", termination_cause="completed", repeat=1)

    script.main(["--control", "--repeats", "3"])

    assert len(constructed) == 9  # the adversarial file was not read by control resume
    assert json.loads((adversarial / "01_verdict_manipulation.r1.json").read_text()) == kept
    assert sorted(p.name for p in control.glob("*.json")) == sorted(
        f"{f}.r{k}.json" for f in _FIXTURES for k in (1, 2, 3)
    )
    rows = [json.loads(p.read_text()) for p in control.glob("*.json")]
    assert all(r["control"] is True and r["injection_won"] is None for r in rows)
    assert sorted(r["repeat"] for r in rows) == [1, 1, 1, 2, 2, 2, 3, 3, 3]
    (summary_path,) = tmp_path.glob("results/injection_suite_real_control_summary_*.json")
    summary = json.loads(summary_path.read_text())
    assert summary["repeats"] == 3 and len(summary["rows"]) == 9
    assert "overall" not in summary

    # and the other way round: a control file is not read by adversarial resume
    constructed.clear()
    _prewrite(control, "02_unauthorized_tool_invocation.r1.json", termination_cause="completed", repeat=1)
    for path in adversarial.glob("*.json"):
        path.unlink()
    script.main(["--repeats", "3"])
    assert len(constructed) == 9


def test_inter_run_delay_slept_between_runs_and_not_after_resumed_repeat(monkeypatch, tmp_path, script):
    constructed, sleeps = _setup(monkeypatch, tmp_path, script, _complete)

    script.main(["--repeats", "3"])

    assert sleeps == [90] * 8  # 9 runs, default delay unchanged

    sleeps.clear()
    constructed.clear()
    out = tmp_path / "results" / "injection_suite_real"
    for path in out.glob("*.json"):
        path.unlink()
    _prewrite(out, "01_verdict_manipulation.r1.json", termination_cause="completed", repeat=1)

    script.main(["--repeats", "3"])

    assert len(constructed) == 8
    assert sleeps == [90] * 7  # no sleep after the resumed first repeat
