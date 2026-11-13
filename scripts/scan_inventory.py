#!/usr/bin/env python3
"""A1a CLI: lockfile inventory matched against a pinned OSV PyPI snapshot.

Reads text files only. Exit 0 whenever the inventory completes (even with
advisories, unpinned packages or no lockfile); exit 1 with `error: <Class>: <msg>`
on stderr for a named error; argparse errors exit 2.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from reachability.scan.advisories import ScanReport, build_report, load_snapshot  # noqa: E402
from reachability.scan.inventory import ScanError, Status, build_inventory  # noqa: E402

REFERENCE_SHA256 = "c7ea4e15b83912960fc5cbd42c6003dd630e422ec4dfbfc4ed19445573e1b08c"


def render(report: ScanReport) -> list[str]:
    snap, inv = report.snapshot, report.inventory
    out: list[str] = []
    if report.lead is not None:
        out.append(report.lead)
    out.append(f"advisories as of {snap.date.isoformat()} (snapshot sha256 {snap.sha256})")
    out.append("== Malicious packages ==")
    if report.malicious:
        for m in report.malicious:
            out.append(f"{m.id} | {m.dist} | {m.version} | {m.signal}")
    else:
        out.append("none")
    out.append("== Inventory ==")
    out.append("dist | version | status | advisory ids | fixed-in")
    for e in inv.entries:
        found = sorted(
            (f for f in report.findings if e.status is Status.PINNED and f.dist == e.dist and f.version == e.version),
            key=lambda f: f.group_id,
        )
        ids = "; ".join("[" + ",".join(f.ids) + "]" for f in found) or "-"
        fixed = "; ".join(f.fixed_in for f in found) or "-"
        out.append(f"{e.dist or '-'} | {e.version or '-'} | {e.status.value} | {ids} | {fixed}")
        if e.status.value != "pinned":
            out.append(f"    at {e.source_file}" + (f":{e.line}" if e.line is not None else ""))
        for f in found:
            for n in f.notes:
                out.append(f"    note: {n}")
        for n in e.notes:
            out.append(f"    note: {n}")
    out.append("== Skipped and ignored ==")
    if inv.skipped or inv.notes:
        for s in inv.skipped:
            where = ""
            if s.source_file:
                where = f" | at {s.source_file}" + (f":{s.line}" if s.line is not None else "")
            out.append(f"{s.kind} | {s.path}{where} | {s.detail}")
        for n in inv.notes:
            out.append(f"note | {n}")
    else:
        out.append("none")
    if inv.hint:
        out.append("== Hint ==")
        out.append(inv.hint)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dir", help="scan root containing the lockfile")
    parser.add_argument("--snapshot", required=True, help="path to osv-pypi-YYYY-MM-DD.zip")
    parser.add_argument(
        "--snapshot-sha256",
        default=REFERENCE_SHA256,
        help=f"expected sha256 of the snapshot (default: reference {REFERENCE_SHA256})",
    )
    args = parser.parse_args()
    try:
        snapshot = load_snapshot(Path(args.snapshot).expanduser(), args.snapshot_sha256)
        inventory = build_inventory(Path(args.dir))
        report = build_report(inventory, snapshot)
    except ScanError as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write("\n".join(render(report)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
