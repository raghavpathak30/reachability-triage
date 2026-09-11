#!/usr/bin/env python3
"""L5 measurement harness.

Walks tests/fixtures/l5/*, builds the real L1-L4 index for each fixture's repo/
via the public API in src/reachability/index/, queries the labeled sink, and
checks the resulting verdict against the fixture's pre-registered label.json.

Ground truth comes ONLY from label.json. Nothing here writes inside
tests/fixtures/l5/. See agent_docs/L5_PROTOCOL.md for the gate definitions.
"""

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from reachability.index.build import build_l1_index  # noqa: E402
from reachability.index.edges import build_edge_index  # noqa: E402
from reachability.index.entrypoints import build_entrypoint_index  # noqa: E402
from reachability.index.reachability import compute_reachability  # noqa: E402
from reachability.index.symbol_models import NodeKind  # noqa: E402
from reachability.index.symbols import build_symbol_index  # noqa: E402

FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures" / "l5"
RESULTS_DIR = REPO_ROOT / "results"
WALL_CLOCK_BUDGET_SECONDS = 5

_FUNCTION_KINDS = {NodeKind.FUNCTION, NodeKind.ASYNCFUNCTION, NodeKind.METHOD}


class _TimeoutError(Exception):
    pass


def _alarm_handler(signum, frame):
    raise _TimeoutError(f"exceeded {WALL_CLOCK_BUDGET_SECONDS}s wall-clock budget")


def discover_fixtures(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir())


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


def measure_fixture(fixture_dir: Path) -> dict:
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
        "resolution_rule": None,
        "confidence": None,
        "reason": None,
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
        report = build_l1_index(repo_root)
        symbol_index = build_symbol_index(report)
        edges = build_edge_index(report, symbol_index)
        entrypoints = build_entrypoint_index(report, symbol_index, repo_root)
        target_symbol = resolve_target(symbol_index, target_module, label["sink"]["line"], fixture_dir.name)

        row["target_module"] = target_module
        row["target_symbol"] = target_symbol

        result = compute_reachability(target_module, target_symbol, entrypoints, edges, report)

        row["verdict"] = result.verdict.value
        row["reason"] = result.reason
        if result.path:
            last_edge = result.path[-1]
            row["resolution_rule"] = last_edge.resolution_rule.value
            row["confidence"] = last_edge.confidence.value
        row["pass"] = result.verdict.value in label["allowed_verdicts"]
    except Exception as exc:  # G5: no fixture may crash the harness
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["pass"] = False
    finally:
        if has_alarm:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)

    row["elapsed_seconds"] = round(time.monotonic() - start, 4)
    return row


def evaluate_gates(results: list[dict]) -> dict:
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

    gates["overall_pass"] = gates["G1"]["pass"] and gates["G2"]["pass"] and gates["G3"]["pass"] and gates["G5"]["pass"]
    return gates


def git_sha() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def write_results_json(results: list[dict], gates: dict, sha: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"l5_{sha}.json"
    payload = {
        "git_sha": sha,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fixtures": results,
        "gates": gates,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    return out_path


def print_table(results: list[dict]) -> None:
    header = f'{"fixture":38} {"verdict":24} {"allowed?":9} {"resolution_rule":22} {"confidence":10} {"pass"}'
    print(header)
    print("-" * len(header))
    for r in results:
        allowed = "yes" if r["verdict"] in r["allowed_verdicts"] else "no"
        status = "FAIL" if not r["pass"] else "pass"
        if r["error"]:
            status = "FAIL"
        print(
            f'{r["id"]:38} {str(r["verdict"]):24} {allowed:9} '
            f'{str(r["resolution_rule"]):22} {str(r["confidence"]):10} {status}'
        )
        if r["error"]:
            print(f'    ERROR: {r["error"]}')


def main() -> int:
    fixture_dirs = discover_fixtures(FIXTURES_ROOT)
    results = [measure_fixture(d) for d in fixture_dirs]
    gates = evaluate_gates(results)
    sha = git_sha()
    out_path = write_results_json(results, gates, sha)

    print_table(results)
    print()

    for gate_name in ("G1", "G2", "G3", "G5"):
        g = gates[gate_name]
        status = "PASS" if g["pass"] else "FAIL"
        print(f"{gate_name}: {status} — {g}")

    g4 = gates["G4"]
    print(f"G4 (reported only, not a gate): unknown_count={g4['unknown_count']} target<=0 fixtures={g4['fixtures']}")

    if not gates["G1"]["pass"]:
        print()
        print("!" * 70)
        for fid in gates["G1"]["offending_fixtures"]:
            print(f"! FALSE NOT_REACHABLE: {fid}")
        print("!" * 70)

    print()
    print(f"Results written to {out_path}")
    print(f"Overall: {'PASS' if gates['overall_pass'] else 'FAIL'}")

    return 0 if gates["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
