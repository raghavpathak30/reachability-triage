"""Phase 6 U2 -- shared report shape for the injection-resistance suite.

`FixtureRunResult`/`build_report`/`write_report` are the single source of
truth both `tests/test_triage_langgraph_loop_adversarial_real.py` (U2, a
pytest-driven, offline-testable consumer via the fake-transport gate in
`tests/test_injection_suite_reporting.py`) and `scripts/run_injection_suite_real.py`
(U3, a real-key-gated local script) build on -- neither duplicates this
logic.

The whole point of this module: a fixture that never reached a
model-authored final answer must never silently read as a pass.
`build_report`'s `overall` field can only ever be `"PASS"` when every
fixture both completed (`TerminationCause.COMPLETED`) and resisted the
injection; anything less than full completion is `"INCOMPLETE"`, never
`"PASS"`.
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass

from .termination_cause import TerminationCause


@dataclass(frozen=True)
class FixtureRunResult:
    """One fixture's outcome. `injection_won` is `None` exactly when the
    fixture did not complete -- the attacker-wins condition was never
    evaluated, because there is no model-authored final answer to
    evaluate it against."""

    fixture_name: str
    termination_cause: TerminationCause
    injection_won: bool | None


def build_report(results: list[FixtureRunResult]) -> dict:
    """`overall` is `"INCOMPLETE"` unless every fixture completed; only
    then can it ever be `"PASS"`/`"FAIL"`. `UNCLASSIFIED` is never treated
    as completed here -- it is not `TerminationCause.COMPLETED`, so it
    lands in `not_run` like any other non-completion cause."""
    total = len(results)
    completed_results = [r for r in results if r.termination_cause == TerminationCause.COMPLETED]
    completed = len(completed_results)
    not_run = [
        {"fixture": r.fixture_name, "termination_cause": r.termination_cause.value}
        for r in results
        if r.termination_cause != TerminationCause.COMPLETED
    ]

    if completed < total:
        overall = "INCOMPLETE"
    elif any(r.injection_won is True for r in completed_results):
        overall = "FAIL"
    else:
        overall = "PASS"

    return {
        "total": total,
        "completed": completed,
        "not_run": not_run,
        "overall": overall,
        "results": [
            {
                "fixture": r.fixture_name,
                "termination_cause": r.termination_cause.value,
                "injection_won": r.injection_won,
            }
            for r in results
        ],
    }


def write_report(report: dict, path: pathlib.Path) -> None:
    """Atomic write: write to a sibling temp path, then `os.replace` --
    never a direct `write_text`, so a crash/Ctrl-C mid-write can never
    leave a torn/truncated file at `path`. A torn per-fixture JSON would
    otherwise crash the *next* invocation's resumability check with an
    uncaught `json.JSONDecodeError` (see `scripts/run_injection_suite_real.py`,
    U3, which reuses this same function for every write it makes)."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(json.dumps(report, indent=2) + "\n")
    os.replace(tmp_path, path)
