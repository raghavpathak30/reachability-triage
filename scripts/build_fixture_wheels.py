#!/usr/bin/env python3
"""Build deterministic, offline pure-Python wheels from three L5 fixtures.

Used only by `scripts/smoke_compose.sh` and `scripts/demo.sh`: the worker's
acquisition is pip-download-only (`acquisition.py`, `--only-binary=:all:`),
so smoke/demo point pip at these wheels via `PIP_FIND_LINKS`/`PIP_NO_INDEX`.
Stdlib only; never writes into `tests/fixtures/`.

Also writes `cases.json` into the output directory. Each case's
`allowed_verdicts` is read from the fixture's own `label.json` (never
retyped here); `target_module`/`target_symbol` are derived from the label's
`sink` (file + line).
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "l5"

# case name -> fixture directory name. Everything else comes from label.json.
CASES = {
    "reachable": "02_transitive_three_hop",
    "not_reachable": "09_never_imported",
    "unknown": "13_getattr_dynamic_dispatch",
}

VERSION = "1.0"
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _sink_target(fixture_dir: Path, label: dict) -> tuple[str, str]:
    sink_file = label["sink"]["file"]  # e.g. "repo/pkg/sink.py"
    rel = Path(sink_file).relative_to("repo")
    target_module = ".".join(rel.with_suffix("").parts)
    tree = ast.parse((fixture_dir / sink_file).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.lineno == label["sink"]["line"]:
                return target_module, node.name
    raise ValueError(f"no def at {sink_file}:{label['sink']['line']}")


def build_wheel(fixture_dir: Path, out_dir: Path, number: str) -> tuple[str, Path]:
    dist_name = f"reach-fx-{number}"
    norm = dist_name.replace("-", "_")
    dist_info = f"{norm}-{VERSION}.dist-info"
    wheel_path = out_dir / f"{norm}-{VERSION}-py3-none-any.whl"

    files: dict[str, bytes] = {}
    repo = fixture_dir / "repo"
    for path in sorted(repo.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            files[path.relative_to(repo).as_posix()] = path.read_bytes()

    files[f"{dist_info}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {dist_name}\nVersion: {VERSION}\n".encode()
    )
    files[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: build_fixture_wheels\n"
        b"Root-Is-Purelib: true\nTag: py3-none-any\n"
    )
    record_lines = [
        f"{name},{_record_hash(data)},{len(data)}" for name, data in sorted(files.items())
    ]
    record_lines.append(f"{dist_info}/RECORD,,")
    files[f"{dist_info}/RECORD"] = ("\n".join(record_lines) + "\n").encode()

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return dist_name, wheel_path


def build_all(out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    for case_name, fixture_name in CASES.items():
        fixture_dir = FIXTURES_DIR / fixture_name
        label = json.loads((fixture_dir / "label.json").read_text())
        target_module, target_symbol = _sink_target(fixture_dir, label)
        dist_name, _ = build_wheel(fixture_dir, out_dir, fixture_name.split("_", 1)[0])
        cases.append(
            {
                "case": case_name,
                "fixture": fixture_name,
                "package": dist_name,
                "version": VERSION,
                "target_module": target_module,
                "target_symbol": target_symbol,
                "allowed_verdicts": label["allowed_verdicts"],
                "decidable": label["decidable"],
            }
        )
    (out_dir / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    for case in build_all(args.out):
        print(f"{case['case']}: {case['package']}=={case['version']} -> {case['fixture']}")


if __name__ == "__main__":
    main()
