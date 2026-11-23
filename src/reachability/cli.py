import argparse
import sys
from pathlib import Path

from reachability.scan.fetch import LocalDirFetcher, PyPIFetcher
from reachability.scan.fixcommit import LocalDiffDir
from reachability.scan.inventory import ScanError
from reachability.scan.pipeline import run_scan
from reachability.scan.render import render_json, render_text
from reachability.scan.textsafe import strip_control_line

REFERENCE_SHA256 = "c7ea4e15b83912960fc5cbd42c6003dd630e422ec4dfbfc4ed19445573e1b08c"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reachability")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan an application directory against an OSV snapshot")
    scan.add_argument("dir")
    scan.add_argument("--snapshot", required=True)
    scan.add_argument("--snapshot-sha256", default=REFERENCE_SHA256)
    scan.add_argument("--member", default=None)
    scan.add_argument("--format", choices=["text", "json"], default="text")
    scan.add_argument("--cache-dir", default=None)
    scan.add_argument("--wheel-dir", default=None, help="offline wheel source (testing)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        fetcher = LocalDirFetcher(Path(args.wheel_dir)) if args.wheel_dir else PyPIFetcher()
        result = run_scan(
            Path(args.dir),
            Path(args.snapshot).expanduser(),
            args.snapshot_sha256,
            fetcher,
            member=args.member,
            cache_dir=Path(args.cache_dir) if args.cache_dir else None,
            diff_fetcher=LocalDiffDir(Path(args.wheel_dir) / "fix-commits") if args.wheel_dir else None,
        )
    except (ScanError, OSError) as exc:
        sys.stderr.write(strip_control_line(f"error: {type(exc).__name__}: {exc}") + "\n")
        return 2
    except Exception as exc:
        sys.stderr.write(strip_control_line(f"error: unexpected {type(exc).__name__}: {exc}") + "\n")
        return 2
    sys.stdout.write(render_json(result) if args.format == "json" else render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
