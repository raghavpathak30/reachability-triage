#!/usr/bin/env python3
"""Phase LangGraph, Unit 5 parallel-run check CLI.

Thin wrapper around `reachability.agent.langgraph_parity_check.run_parallel_check`,
same division of labor as `scripts/run_eval_suite.py`'s `main()`/`__main__`
block: `run_parallel_check()` never decides an exit code itself, this
script does.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from reachability.agent.langgraph_parity_check import (  # noqa: E402
    print_parity_mismatch,
    run_parallel_check,
)


def main() -> int:
    report = run_parallel_check()

    header = f'{"fixture":38} {"match"}'
    print(header)
    print("-" * len(header))
    for row in report.fixtures:
        print(f'{row["id"]:38} {row["match"]}')

    print()

    for row in report.fixtures:
        if not row["match"]:
            print_parity_mismatch(row)
            print()

    print(f"mismatch_count: {report.summary['mismatch_count']}")
    print(f"mismatched_fixtures: {report.summary['mismatched_fixtures']}")
    print()
    print(f"Overall: {'PASS' if report.summary['all_match'] else 'FAIL'}")

    return 0 if report.summary["all_match"] else 1


if __name__ == "__main__":
    sys.exit(main())
