#!/usr/bin/env python3
"""Phase 5 U4: diff the stub-lane and real-lane eval results for the same
git sha, emitting a per-fixture disagreement report. Never fails the
build on a disagreement (a disagreement is expected and interesting, not
a defect) -- only a genuine script error (e.g. a missing results file)
returns non-zero.

Usage: `python scripts/diff_eval_lanes.py` (uses the current git sha and
the default `results/eval_<sha>.json` / `results/eval_real_<sha>.json`
paths) or `python scripts/diff_eval_lanes.py --stub PATH --real PATH`.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from reachability.agent.eval_harness import RESULTS_DIR, git_sha  # noqa: E402


def build_disagreement_report(stub_report: dict, real_report: dict) -> dict:
    stub_by_id = {row["id"]: row for row in stub_report["fixtures"]}
    real_by_id = {row["id"]: row for row in real_report["fixtures"]}

    rows = []
    for fixture_id in sorted(set(stub_by_id) | set(real_by_id)):
        stub_row = stub_by_id.get(fixture_id)
        real_row = real_by_id.get(fixture_id)
        stub_verdict = stub_row["verdict"] if stub_row else None
        real_verdict = real_row["verdict"] if real_row else None
        rows.append(
            {
                "id": fixture_id,
                "stub_verdict": stub_verdict,
                "real_verdict": real_verdict,
                "agree": stub_verdict == real_verdict,
            }
        )

    disagreement_count = sum(1 for r in rows if not r["agree"])
    return {
        "git_sha": stub_report["git_sha"],
        "disagreement_count": disagreement_count,
        "fixtures": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stub", type=Path, default=None)
    parser.add_argument("--real", type=Path, default=None)
    args = parser.parse_args()

    sha = git_sha()
    stub_path = args.stub or (RESULTS_DIR / f"eval_{sha}.json")
    real_path = args.real or (RESULTS_DIR / f"eval_real_{sha}.json")

    if not stub_path.is_file():
        print(f"stub lane results not found: {stub_path}", file=sys.stderr)
        return 1
    if not real_path.is_file():
        print(f"real lane results not found: {real_path}", file=sys.stderr)
        return 1

    stub_report = json.loads(stub_path.read_text())
    real_report = json.loads(real_path.read_text())

    report = build_disagreement_report(stub_report, real_report)

    out_path = RESULTS_DIR / f"eval_disagreement_{sha}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    print(f"disagreement_count: {report['disagreement_count']} / {len(report['fixtures'])}")
    for row in report["fixtures"]:
        if not row["agree"]:
            print(f"  {row['id']}: stub={row['stub_verdict']} real={row['real_verdict']}")
    print(f"written to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
