"""U6 eval harness tests.

Covers `evaluate_eval_gates` against synthetic rows (fast, no real corpus
run needed), `run_eval_suite()` against the real reused `tests/fixtures/l5/`
corpus, `prompt_registry.py`'s `load_prompt`, and a static guard that the
prompt-versioning scaffolding stays uncomsumed by `triage/stub_llm.py` and
`triage/agent_loop.py`.
"""

from pathlib import Path

from reachability.agent.eval_harness import evaluate_eval_gates, run_eval_suite
from reachability.agent.prompt_registry import PROMPT_VERSION, load_prompt

REPO_ROOT = Path(__file__).resolve().parent.parent


def _base_row(**overrides) -> dict:
    row = {
        "id": "00_placeholder",
        "scenario": "placeholder",
        "target_module": "pkg.mod",
        "target_symbol": "func",
        "verdict": "reachable",
        "allowed_verdicts": ["reachable"],
        "forbidden_verdicts": ["not_reachable"],
        "decidable": True,
        "pass": True,
        "reason": None,
        "tool_call_count": 3,
        "loop_degradation_reason": None,
        "error": None,
        "elapsed_seconds": 0.01,
    }
    row.update(overrides)
    return row


def test_evaluate_eval_gates_fails_on_false_not_reachable():
    results = [
        _base_row(
            id="09_never_imported",
            verdict="not_reachable",
            allowed_verdicts=["reachable"],
        ),
    ]
    gates = evaluate_eval_gates(results)
    assert gates["G1"]["pass"] is False
    assert gates["overall_pass"] is False


def test_evaluate_eval_gates_passes_on_clean_synthetic_rows():
    results = [_base_row(id="01_direct_console_entrypoint")]
    for fid in ("09_never_imported", "10_name_collision_diff_module", "11_dead_function_call_site", "12_local_shadow", "20_vendored_duplicate_module", "21_attribute_chain_segment_escape", "24_named_callback_no_collision_negative_control"):
        results.append(
            _base_row(id=fid, verdict="not_reachable", allowed_verdicts=["not_reachable"], forbidden_verdicts=["reachable"])
        )
    for fid in ("07_test_only_call", "08_conftest_fixture_only"):
        results.append(
            _base_row(
                id=fid,
                verdict="reachable_only_from_tests",
                allowed_verdicts=["reachable_only_from_tests"],
                forbidden_verdicts=["not_reachable"],
            )
        )

    gates = evaluate_eval_gates(results)
    assert gates["overall_pass"] is True


def test_evaluate_eval_gates_fails_on_loop_degradation():
    results = [
        _base_row(
            id="01_direct_console_entrypoint",
            verdict="reachable",
            loop_degradation_reason="budget_exceeded",
        ),
    ]
    gates = evaluate_eval_gates(results)
    assert gates["G6"]["pass"] is False
    assert gates["overall_pass"] is False


def test_run_eval_suite_shape_and_gates():
    report = run_eval_suite()

    assert len(report.fixtures) == 30

    # Regression-pin: G2's pool/threshold and G4's decidable-pool size are pinned
    # to the exact numbers agent_docs/PHASE4_EVAL_PROTOCOL.md pre-registers, not
    # just the pass/fail outcome (DECISIONS.md §5.1's D2/D6 lesson: a
    # verdict-only assertion can pass "by coincidence" through the wrong
    # mechanism).
    assert report.gates["G2"]["total"] == 7
    assert report.gates["G2"]["threshold"] == 6
    decidable_pool_size = len([row for row in report.fixtures if row["decidable"]])
    assert decidable_pool_size == 17

    required_keys = {
        "id",
        "scenario",
        "target_module",
        "target_symbol",
        "verdict",
        "allowed_verdicts",
        "forbidden_verdicts",
        "decidable",
        "pass",
        "reason",
        "tool_call_count",
        "loop_degradation_reason",
        "error",
        "elapsed_seconds",
    }
    for row in report.fixtures:
        assert required_keys <= set(row.keys())

    assert report.gates["G1"]["false_not_reachable_count"] == 0
    assert report.gates["G6"]["loop_degradation_count"] == 0
    assert report.gates["overall_pass"] is True


def test_prompt_registry_loads_v1_system_prompt():
    assert PROMPT_VERSION == "v1"

    system_prompt = load_prompt("system")
    assert system_prompt.strip() != ""

    try:
        load_prompt("does-not-exist")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError for a missing prompt name")


def test_stub_llm_and_agent_loop_do_not_import_prompt_registry():
    stub_llm_source = (REPO_ROOT / "src" / "reachability" / "triage" / "stub_llm.py").read_text()
    agent_loop_source = (REPO_ROOT / "src" / "reachability" / "triage" / "agent_loop.py").read_text()

    for source in (stub_llm_source, agent_loop_source):
        assert "prompt_registry" not in source
        assert "prompts/v1" not in source
