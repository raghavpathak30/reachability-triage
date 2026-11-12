#!/usr/bin/env python3
"""A1a CLI: lockfile inventory matched against a pinned OSV PyPI snapshot."""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from reachability.scan.advisories import build_report, load_snapshot  # noqa: E402
from reachability.scan.inventory import build_inventory  # noqa: E402

REFERENCE_SHA256 = "c7ea4e15b83912960fc5cbd42c6003dd630e422ec4dfbfc4ed19445573e1b08c"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dir")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--snapshot-sha256", default=REFERENCE_SHA256)
    args = parser.parse_args()

    snapshot = load_snapshot(Path(args.snapshot).expanduser(), args.snapshot_sha256)
    inventory = build_inventory(Path(args.dir))
    report = build_report(inventory, snapshot)

    print(f"advisories as of {snapshot.date.isoformat()} (snapshot sha256 {snapshot.sha256})")
    print("== Malicious packages ==")
    print("none")
    print("== Inventory ==")
    print("dist | version | status | advisory ids | fixed-in")
    for e in inventory.entries:
        found = [f for f in report.findings if f.dist == e.dist and f.version == e.version]
        ids = ",".join(i for f in found for i in f.ids) or "-"
        fixed = ";".join(f.fixed_in for f in found) or "-"
        print(f"{e.dist} | {e.version or '-'} | {e.status.value} | {ids} | {fixed}")
    print("== Skipped and ignored ==")
    print("none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
