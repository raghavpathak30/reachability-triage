"""U6 eval harness.

Structurally mirrors `scripts/measure_l5.py`, but runs U3's full agent loop
(`triage/agent_loop.py::run_triage_loop`) instead of a direct
`compute_reachability` call, over the same frozen `tests/fixtures/l5/`
corpus (reused as-is, per `agent_docs/U6_EVAL_PROTOCOL.md`). See that
protocol document for the pre-registered gate definitions (G1-eval through
G6-eval) this module's `evaluate_eval_gates` implements.

A handful of small pure helpers below (`_module_dotted_name`,
`resolve_target`, `load_label`, `git_sha`) are duplicated from
`scripts/measure_l5.py` rather than imported from it -- `scripts/` is a
CLI-only directory that imports from `src/` in this codebase, never the
reverse.
"""

from __future__ import annotations

import json
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from reachability.index.symbol_models import NodeKind
from reachability.triage.agent_loop import run_triage_loop
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.stub_llm import DeterministicPolicyStubLLMClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
EVAL_FIXTURES_ROOTS: list[Path] = [
    REPO_ROOT / "tests" / "fixtures" / "l5",
    REPO_ROOT / "tests" / "fixtures" / "l5_phase4",
]
RESULTS_DIR = REPO_ROOT / "results"
EVAL_BUDGET = 50
WALL_CLOCK_BUDGET_SECONDS = 10

_FUNCTION_KINDS = {NodeKind.FUNCTION, NodeKind.ASYNCFUNCTION, NodeKind.METHOD}

_DEGRADATION_PREFIXES = ("budget_exceeded", "malformed_tool_call_argument")
_DEGRADATION_EXACT = "target_symbol_not_found_in_index"


class _TimeoutError(Exception):
    pass


def _alarm_handler(signum, frame):
    raise _TimeoutError(f"exceeded {WALL_CLOCK_BUDGET_SECONDS}s wall-clock budget")


def discover_eval_fixtures(roots: list[Path]) -> list[Path]:
    return sorted(p for root in roots for p in root.iterdir() if p.is_dir())


def load_label(fixture_dir: Path) -> dict:
    return json.loads((fixture_dir / "label.json").read_text())


def _module_dotted_name(sink_file: str) -> str:
    """Mirror discovery.py::discover_modules's dotted-name rule, relative to repo/."""
    parts = Path(sink_file).parts
    if parts[0] != "repo":
        raise ValueError(f"sink file {sink_file!r} is not rooted under 'repo/'")
    parts = parts[1:]
    if parts[-1] == "__init__.py":
        dotted_parts = parts[:-1]
    else:
        dotted_parts = parts[:-1] + (parts[-1][: -len(".py")],)
    return ".".join(dotted_parts)


def resolve_target(symbol_index: dict, target_module: str, sink_line: int, fixture_id: str) -> str:
    table = symbol_index.get(target_module)
    if table is None:
        raise ValueError(f"{fixture_id}: module {target_module!r} not found in symbol index")
    for node in table.nodes:
        if node.lineno == sink_line and node.kind in _FUNCTION_KINDS:
            return node.qualname.rsplit(".", 1)[-1].split("@")[0]
    raise ValueError(f"{fixture_id}: no function/method node at {target_module}:{sink_line}")


def git_sha() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _loop_degradation_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    if reason == _DEGRADATION_EXACT:
        return _DEGRADATION_EXACT
    for prefix in _DEGRADATION_PREFIXES:
        if reason.startswith(prefix):
            return prefix
    return None


@dataclass(frozen=True)
class EvalReport:
    fixtures: list[dict]
    gates: dict
    git_sha: str
    timestamp_utc: str


def measure_eval_fixture(fixture_dir: Path) -> dict:
    label = load_label(fixture_dir)
    repo_root = fixture_dir / "repo"

    row = {
        "id": fixture_dir.name,
        "scenario": label["scenario"],
        "target_module": None,
        "target_symbol": None,
        "verdict": None,
        "allowed_verdicts": label["allowed_verdicts"],
        "forbidden_verdicts": label["forbidden_verdicts"],
        "decidable": label["decidable"],
        "pass": False,
        "reason": None,
        "tool_call_count": None,
        "loop_degradation_reason": None,
        "error": None,
        "elapsed_seconds": None,
    }

    has_alarm = hasattr(signal, "SIGALRM")
    old_handler = None
    start = time.monotonic()
    try:
        if has_alarm:
            old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(WALL_CLOCK_BUDGET_SECONDS)

        target_module = _module_dotted_name(label["sink"]["file"])
        repo_index = build_repo_index(repo_root)
        target_symbol = resolve_target(
            repo_index.symbol_index, target_module, label["sink"]["line"], fixture_dir.name
        )

        row["target_module"] = target_module
        row["target_symbol"] = target_symbol

        client = DeterministicPolicyStubLLMClient(target_module, target_symbol)
        finding = run_triage_loop(
            client,
            repo_index,
            target_module,
            target_symbol,
            budget=EVAL_BUDGET,
            _on_raw_tool_result=None,
        )

        row["verdict"] = finding.result.verdict.value
        row["reason"] = finding.result.reason
        row["tool_call_count"] = len(finding.tool_calls)
        row["loop_degradation_reason"] = _loop_degradation_reason(finding.result.reason)
        row["pass"] = finding.result.verdict.value in label["allowed_verdicts"]
    except Exception as exc:  # G5-eval: no fixture may crash the harness
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["pass"] = False
    finally:
        if has_alarm:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)

    row["elapsed_seconds"] = round(time.monotonic() - start, 4)
    return row


def evaluate_eval_gates(results: list[dict]) -> dict:
    gates: dict = {}

    g1_offenders = [
        r["id"] for r in results if r["verdict"] == "not_reachable" and "not_reachable" not in r["allowed_verdicts"]
    ]
    gates["G1"] = {
        "pass": len(g1_offenders) == 0,
        "false_not_reachable_count": len(g1_offenders),
        "offending_fixtures": g1_offenders,
    }

    g2_set = [r for r in results if r["allowed_verdicts"] == ["not_reachable"]]
    g2_correct = [r["id"] for r in g2_set if r["verdict"] == "not_reachable"]
    gates["G2"] = {
        "pass": len(g2_correct) >= 4,
        "correct_count": len(g2_correct),
        "total": len(g2_set),
        "threshold": 4,
        "correct_fixtures": g2_correct,
    }

    g3_set = [r for r in results if r["allowed_verdicts"] == ["reachable_only_from_tests"]]
    g3_failing = [r["id"] for r in g3_set if r["verdict"] != "reachable_only_from_tests"]
    gates["G3"] = {
        "pass": len(g3_failing) == 0,
        "total": len(g3_set),
        "failing_fixtures": g3_failing,
    }

    g4_set = [r for r in results if r["decidable"] and r["verdict"] == "unknown"]
    gates["G4"] = {
        "reported_only": True,
        "unknown_count": len(g4_set),
        "target": 0,
        "fixtures": [r["id"] for r in g4_set],
    }

    g5_crashed = [r["id"] for r in results if r["error"] is not None]
    gates["G5"] = {
        "pass": len(g5_crashed) == 0,
        "crashed_fixtures": g5_crashed,
    }

    g6_offenders = [r["id"] for r in results if r["loop_degradation_reason"] is not None]
    gates["G6"] = {
        "pass": len(g6_offenders) == 0,
        "loop_degradation_count": len(g6_offenders),
        "offending_fixtures": g6_offenders,
    }

    gates["overall_pass"] = (
        gates["G1"]["pass"]
        and gates["G2"]["pass"]
        and gates["G3"]["pass"]
        and gates["G5"]["pass"]
        and gates["G6"]["pass"]
    )
    return gates


def write_eval_results_json(report: EvalReport) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"eval_{report.git_sha}.json"
    payload = {
        "git_sha": report.git_sha,
        "timestamp_utc": report.timestamp_utc,
        "fixtures": report.fixtures,
        "gates": report.gates,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    return out_path


def print_eval_table(results: list[dict]) -> None:
    header = (
        f'{"fixture":38} {"verdict":24} {"allowed?":9} '
        f'{"tool_calls":10} {"loop_degradation_reason":26} {"pass"}'
    )
    print(header)
    print("-" * len(header))
    for r in results:
        allowed = "yes" if r["verdict"] in r["allowed_verdicts"] else "no"
        status = "FAIL" if not r["pass"] else "pass"
        if r["error"]:
            status = "FAIL"
        print(
            f'{r["id"]:38} {str(r["verdict"]):24} {allowed:9} '
            f'{str(r["tool_call_count"]):10} {str(r["loop_degradation_reason"]):26} {status}'
        )
        if r["error"]:
            print(f'    ERROR: {r["error"]}')


def run_eval_suite() -> EvalReport:
    fixture_dirs = discover_eval_fixtures(EVAL_FIXTURES_ROOTS)
    results = [measure_eval_fixture(d) for d in fixture_dirs]
    gates = evaluate_eval_gates(results)
    sha = git_sha()
    report = EvalReport(
        fixtures=results,
        gates=gates,
        git_sha=sha,
        timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    write_eval_results_json(report)
    return report
