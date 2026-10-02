"""Phase 8 U2: `scripts/run_real_llm_fixtures.py` -- opt-in wait-on-429,
diagnosis fields on each row, org-ID redaction and the resume rule. Real
`GroqLLMClient` over a fake transport; no network, no key."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from reachability.triage.groq_llm import GroqLLMClient
from test_llm_failure_semantics import FakeGroqTransport, _chat_completion_body

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_real_llm_fixtures.py"
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "l5"
FIXTURE_11 = FIXTURES / "11_dead_function_call_site"
FIXTURE_09 = FIXTURES / "09_never_imported"
ORG_ID_SEP = "org_01ab_CD-xyzq"
USAGE = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}


@pytest.fixture
def script():
    saved_environ = dict(os.environ)
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("run_real_llm_fixtures_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        os.environ.clear()
        os.environ.update(saved_environ)
        sys.path[:] = saved_path


def _tool_call(request, name, arguments):
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
    body["usage"] = USAGE
    return httpx.Response(200, json=body, request=request)


def _repeat_then_final(pattern, repeats):
    def responder(call_number, request):
        if call_number <= repeats:
            return _tool_call(request, "search_symbol", {"pattern": pattern})
        return _tool_call(
            request,
            "submit_final_answer",
            {"target_module": "pkg.sink", "target_symbol": "vulnerable", "rationale": "ok"},
        )

    return responder


def _install(monkeypatch, script, responder, constructed, tmp_path, fixture_dirs):
    def factory(**kwargs):
        constructed.append(kwargs)
        return GroqLLMClient(
            api_key="fake-key-not-real",
            http_client=httpx.Client(transport=FakeGroqTransport(responder)),
            sleep_fn=lambda seconds: None,
            **kwargs,
        )

    monkeypatch.setattr(script, "GroqLLMClient", factory)
    monkeypatch.setattr(script, "RESULTS_DIR", tmp_path / "real_llm")
    monkeypatch.setattr(script, "discover_eval_fixtures", lambda roots: list(fixture_dirs))
    monkeypatch.setattr(script, "git_sha", lambda: "testsha")
    monkeypatch.setattr(script.time, "sleep", lambda seconds: None)
    monkeypatch.delenv("TRIAGE_REAL_RUN_MAX_TOTAL_WAIT_SECONDS", raising=False)
    monkeypatch.delenv("TRIAGE_REAL_RUN_DELAY_SECONDS", raising=False)


def test_wait_is_off_unless_env_var_set(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    _install(monkeypatch, script, _repeat_then_final("*vulnerable", 1), constructed, tmp_path, [FIXTURE_11])

    script.run_one_fixture(FIXTURE_11)
    assert constructed == [{}]

    monkeypatch.setenv("TRIAGE_REAL_RUN_MAX_TOTAL_WAIT_SECONDS", "123")
    script.run_one_fixture(FIXTURE_11)
    assert constructed[1] == {"wait_on_rate_limit": True, "rate_limit_max_total_wait_seconds": 123.0}


def test_row_carries_metrics_cause_and_no_raw_org_id(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    _install(
        monkeypatch,
        script,
        _repeat_then_final(f"look {ORG_ID_SEP}, here", 3),
        constructed,
        tmp_path,
        [FIXTURE_11],
    )

    exit_code = script.main()

    assert exit_code == 0
    path = tmp_path / "real_llm" / "testsha" / "11_dead_function_call_site.json"
    row = json.loads(path.read_text())
    assert row["termination_cause"] == "target_not_found"
    assert row["budget"] == script.EVAL_BUDGET
    assert row["loop_metrics"]["tool_call_count"] == 3
    assert row["loop_metrics"]["repeated_calls"] == 2
    assert row["loop_metrics"]["longest_identical_streak"] == 3
    assert row["allowed_verdicts"] == ["not_reachable"]
    assert isinstance(row["pass"], bool)
    assert row["response_shape_log"][0]["finish_reason"] == "tool_calls"
    assert len(row["request_usage_log"]) == 4
    assert ORG_ID_SEP not in path.read_text() and "xyzq" not in path.read_text()


def test_resume_skips_completed_and_reruns_rate_limited(monkeypatch, tmp_path, script, capsys):
    constructed: list[dict] = []
    _install(
        monkeypatch,
        script,
        _repeat_then_final("*vulnerable", 1),
        constructed,
        tmp_path,
        [FIXTURE_09, FIXTURE_11],
    )
    out_dir = tmp_path / "real_llm" / "testsha"
    out_dir.mkdir(parents=True)
    done = {"id": "09_never_imported", "error": None, "termination_cause": "completed",
            "verdict": "not_reachable", "total_tokens_used": 7, "total_cost_accrued": 0.0}
    limited = {"id": "11_dead_function_call_site", "error": None,
               "termination_cause": "llm_rate_limited", "verdict": "unknown",
               "total_tokens_used": 0, "total_cost_accrued": 0.0}
    (out_dir / "09_never_imported.json").write_text(json.dumps(done))
    (out_dir / "11_dead_function_call_site.json").write_text(json.dumps(limited))

    script.main()

    assert len(constructed) == 1  # only fixture 11 was re-run
    assert json.loads((out_dir / "09_never_imported.json").read_text()) == done
    rerun = json.loads((out_dir / "11_dead_function_call_site.json").read_text())
    assert rerun["termination_cause"] == "completed"
    output = capsys.readouterr().out
    assert "fixtures run: 2" in output
    assert "completed=2" in output


def test_prefixed_invalid_final_answer_reason_is_capped_and_events_listed(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    long_rationale = "r" * 2000

    def responder(call_number, request):
        return _tool_call(
            request,
            "functions.submit_final_answer",
            {"target_symbol": "vulnerable", "rationale": long_rationale},
        )

    _install(monkeypatch, script, responder, constructed, tmp_path, [FIXTURE_11])

    row = script.run_one_fixture(FIXTURE_11)

    assert row["termination_cause"] == "llm_malformed_response"
    assert row["reason"].startswith("llm_malformed_response")
    assert len(row["reason"]) <= 300 + len("...[+99999 chars]")
    assert isinstance(row["name_normalization_events"], list)


def test_prefixed_valid_final_answer_row_has_one_event(monkeypatch, tmp_path, script):
    constructed: list[dict] = []

    def responder(call_number, request):
        return _tool_call(
            request,
            "functions.submit_final_answer",
            {"target_module": "pkg.sink", "target_symbol": "vulnerable", "rationale": "ok"},
        )

    _install(monkeypatch, script, responder, constructed, tmp_path, [FIXTURE_11])

    row = script.run_one_fixture(FIXTURE_11)

    assert row["termination_cause"] != "llm_malformed_response"
    assert len(row["name_normalization_events"]) == 1
    assert row["name_normalization_events"][0]["to"] == "submit_final_answer"


def _clear_probe_env(monkeypatch):
    for name in ("TRIAGE_REAL_RUN_TOOL_CHOICE", "TRIAGE_REAL_RUN_FIXTURES", "TRIAGE_REAL_RUN_LABEL"):
        monkeypatch.delenv(name, raising=False)


def test_tool_choice_env_unset_passes_no_kwarg_and_row_records_auto(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    _install(monkeypatch, script, _repeat_then_final("*vulnerable", 1), constructed, tmp_path, [FIXTURE_11])
    _clear_probe_env(monkeypatch)

    row = script.run_one_fixture(FIXTURE_11)

    assert constructed == [{}]
    assert row["tool_choice"] == "auto"


def test_tool_choice_env_required_is_passed_and_recorded(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    _install(monkeypatch, script, _repeat_then_final("*vulnerable", 1), constructed, tmp_path, [FIXTURE_11])
    _clear_probe_env(monkeypatch)
    monkeypatch.setenv("TRIAGE_REAL_RUN_TOOL_CHOICE", "required")

    row = script.run_one_fixture(FIXTURE_11)

    assert constructed == [{"tool_choice": "required"}]
    assert row["tool_choice"] == "required"


def test_fixture_filter_matches_first_segment_exactly(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    dirs = [FIXTURES / "06_subclass_method_call", FIXTURES / "16_module_getattr_pep562",
            FIXTURES / "16b_pep562_no_bare_name", FIXTURE_11]
    _install(monkeypatch, script, _repeat_then_final("*vulnerable", 1), constructed, tmp_path, dirs)
    _clear_probe_env(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr(
        script, "run_one_fixture",
        lambda d: ran.append(d.name) or {"id": d.name, "error": None, "verdict": "unknown",
                                          "total_tokens_used": 0, "total_cost_accrued": 0.0},
    )

    monkeypatch.setenv("TRIAGE_REAL_RUN_FIXTURES", "06,16b")
    script.main()
    assert ran == ["06_subclass_method_call", "16b_pep562_no_bare_name"]

    ran.clear()
    monkeypatch.setenv("TRIAGE_REAL_RUN_FIXTURES", "16")
    script.main()
    assert ran == ["16_module_getattr_pep562"]


def test_unknown_fixture_token_fails_before_any_run(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    _install(monkeypatch, script, _repeat_then_final("*vulnerable", 1), constructed, tmp_path, [FIXTURE_11])
    _clear_probe_env(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr(script, "run_one_fixture", lambda d: ran.append(d.name))
    monkeypatch.setenv("TRIAGE_REAL_RUN_FIXTURES", "11,99")

    with pytest.raises(SystemExit):
        script.main()

    assert ran == [] and constructed == []
    assert not (tmp_path / "real_llm").exists()


def test_label_env_writes_to_separate_dir_and_never_touches_default(monkeypatch, tmp_path, script):
    constructed: list[dict] = []
    _install(monkeypatch, script, _repeat_then_final("*vulnerable", 1), constructed, tmp_path, [FIXTURE_11])
    _clear_probe_env(monkeypatch)
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("TRIAGE_REAL_RUN_LABEL", "probe_auto")

    script.main()

    assert (tmp_path / "results" / "real_llm_probe_auto" / "testsha" / "11_dead_function_call_site.json").exists()
    assert not (tmp_path / "real_llm").exists()
