#!/usr/bin/env python3
"""U6 eval suite CLI.

Thin wrapper around `reachability.agent.eval_harness.run_eval_suite`, same
division of labor as `scripts/measure_l5.py`'s `main()`/`__main__` block:
`run_eval_suite()` never decides an exit code itself, this script does.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from reachability.agent.eval_harness import print_eval_table, run_eval_suite  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lane",
        choices=("stub", "real"),
        default="stub",
        help="stub (default, no network, required CI gate) or real (live Groq API)",
    )
    args = parser.parse_args()

    report = run_eval_suite(lane=args.lane)

    print(f"lane: {report.lane}")
    print_eval_table(report.fixtures)
    print()

    for gate_name in ("G1", "G2", "G3", "G5", "G6"):
        g = report.gates[gate_name]
        status = "PASS" if g["pass"] else "FAIL"
        print(f"{gate_name}: {status} — {g}")

    g4 = report.gates["G4"]
    print(f"G4 (reported only, not a gate): unknown_count={g4['unknown_count']} target<=0 fixtures={g4['fixtures']}")

    print()
    print(f"Overall: {'PASS' if report.gates['overall_pass'] else 'FAIL'}")

    return 0 if report.gates["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
