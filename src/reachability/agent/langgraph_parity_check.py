"""Phase LangGraph, Unit 5 -- parallel-run parity harness (the phase's actual gate).

Runs both the old hand-rolled agent loop (`agent_loop.py::run_triage_loop`)
and the new LangGraph-driven loop
(`langgraph_loop.py::run_triage_loop_langgraph`) over the same 30-fixture
corpus `eval_harness.py` already measures the old loop against, and diffs
the full `TriageFinding` produced by each, per fixture.

Structurally mirrors `eval_harness.py` (`measure_eval_fixture` ->
`measure_parity_fixture`, `write_eval_results_json` ->
`write_parity_results_json`), and reuses its fixture discovery, target
resolution, budget, and degradation-classification helpers directly rather
than duplicating them, so this harness can never silently disagree with
`eval_harness.py`'s own G6 about what counts as a degraded finding.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from reachability.index.edges_models import CallEdge
from reachability.triage.agent_loop import run_triage_loop
from reachability.triage.agent_models import TriageFinding
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import run_triage_loop_langgraph
from reachability.triage.stub_llm import DeterministicPolicyStubLLMClient

from . import eval_harness
from .eval_harness import (
    EVAL_BUDGET,
    EVAL_FIXTURES_ROOTS,
    _loop_degradation_reason,
    _module_dotted_name,
    discover_eval_fixtures,
    git_sha,
    load_label,
    resolve_target,
)

REPO_ROOT = eval_harness.REPO_ROOT
RESULTS_DIR = eval_harness.RESULTS_DIR

_COMPARED_FIELDS = ("verdict", "path", "reason", "loop_degradation_reason")


def _serialize_call_edge(edge: CallEdge) -> dict:
    return {
        "caller_id": edge.caller_id,
        "callee_id": edge.callee_id,
        "confidence": edge.confidence.value,
        "file": str(edge.file),
        "lineno": edge.lineno,
        "resolution_rule": edge.resolution_rule.value,
    }


def _serialize_finding(finding: TriageFinding) -> dict:
    return {
        "verdict": finding.result.verdict.value,
        "path": (
            [_serialize_call_edge(e) for e in finding.result.path]
            if finding.result.path is not None
            else None
        ),
        "reason": finding.result.reason,
        "loop_degradation_reason": _loop_degradation_reason(finding.result.reason),
    }


@dataclass(frozen=True)
class ParityReport:
    fixtures: list[dict]
    summary: dict
    git_sha: str
    timestamp_utc: str


def measure_parity_fixture(fixture_dir: Path) -> dict:
    row = {
        "id": fixture_dir.name,
        "target_module": None,
        "target_symbol": None,
        "old": None,
        "old_error": None,
        "new": None,
        "new_error": None,
        "match": False,
        "mismatch_fields": [],
    }

    try:
        label = load_label(fixture_dir)
        repo_root = fixture_dir / "repo"

        target_module = _module_dotted_name(label["sink"]["file"])
        repo_index = build_repo_index(repo_root)
        target_symbol = resolve_target(
            repo_index.symbol_index, target_module, label["sink"]["line"], fixture_dir.name
        )

        row["target_module"] = target_module
        row["target_symbol"] = target_symbol

        old_client = DeterministicPolicyStubLLMClient(target_module, target_symbol)
        new_client = DeterministicPolicyStubLLMClient(target_module, target_symbol)

        old = None
        old_error = None
        try:
            old_finding = run_triage_loop(
                old_client,
                repo_index,
                target_module,
                target_symbol,
                budget=EVAL_BUDGET,
                _on_raw_tool_result=None,
            )
            old = _serialize_finding(old_finding)
        except Exception as exc:  # noqa: BLE001 -- per-loop crash is itself a mismatch to record
            old_error = f"{type(exc).__name__}: {exc}"

        new = None
        new_error = None
        try:
            new_finding = run_triage_loop_langgraph(
                new_client,
                repo_index,
                target_module,
                target_symbol,
                budget=EVAL_BUDGET,
            )
            new = _serialize_finding(new_finding)
        except Exception as exc:  # noqa: BLE001 -- per-loop crash is itself a mismatch to record
            new_error = f"{type(exc).__name__}: {exc}"

        row["old"] = old
        row["old_error"] = old_error
        row["new"] = new
        row["new_error"] = new_error

        if old_error is not None or new_error is not None:
            row["match"] = False
            row["mismatch_fields"] = ["error"]
        else:
            mismatch_fields = [f for f in _COMPARED_FIELDS if old[f] != new[f]]
            row["match"] = len(mismatch_fields) == 0
            row["mismatch_fields"] = mismatch_fields
    except Exception as exc:  # noqa: BLE001 -- a bad fixture is one failing row, not a run abort
        err = f"{type(exc).__name__}: {exc}"
        row["old_error"] = err
        row["new_error"] = err
        row["match"] = False
        row["mismatch_fields"] = ["error"]

    return row


def evaluate_parity(results: list[dict]) -> dict:
    mismatched = [r["id"] for r in results if not r["match"]]
    return {
        "all_match": len(mismatched) == 0,
        "mismatch_count": len(mismatched),
        "mismatched_fixtures": mismatched,
    }


def write_parity_results_json(report: ParityReport) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"langgraph_parallel_{report.git_sha}.json"
    payload = {
        "git_sha": report.git_sha,
        "timestamp_utc": report.timestamp_utc,
        "fixtures": report.fixtures,
        "summary": report.summary,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    return out_path


def print_parity_mismatch(row: dict) -> None:
    print(f"MISMATCH: {row['id']}")
    print(f"  target_module={row['target_module']!r} target_symbol={row['target_symbol']!r}")
    print(f"  mismatch_fields={row['mismatch_fields']}")
    if row["old_error"] is not None:
        print(f"  old_error: {row['old_error']}")
    else:
        print(f"  old: {json.dumps(row['old'], indent=4)}")
    if row["new_error"] is not None:
        print(f"  new_error: {row['new_error']}")
    else:
        print(f"  new: {json.dumps(row['new'], indent=4)}")


def run_parallel_check() -> ParityReport:
    fixture_dirs = discover_eval_fixtures(EVAL_FIXTURES_ROOTS)
    results = [measure_parity_fixture(d) for d in fixture_dirs]
    summary = evaluate_parity(results)
    sha = git_sha()
    report = ParityReport(
        fixtures=results,
        summary=summary,
        git_sha=sha,
        timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    write_parity_results_json(report)
    return report
