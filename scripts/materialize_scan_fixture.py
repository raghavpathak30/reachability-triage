#!/usr/bin/env python3
"""Materialise the S1 scan fixtures that cannot hold `.py` files in the repo.

`tests/fixtures/app_scan/` may not contain Python files (hygiene test), so the E1
app is stored as `.py.txt` and the stand-in wheels are built here. Stdlib only.

    python scripts/materialize_scan_fixture.py OUTDIR

writes OUTDIR/app, OUTDIR/wheels/*.whl (+ .sha256 sidecars) and
OUTDIR/osv-pypi-2026-11-12.zip, and prints the snapshot sha256.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
E1_DIR = REPO_ROOT / "tests" / "fixtures" / "app_scan" / "eval" / "E1"
E1B_DIR = REPO_ROOT / "tests" / "fixtures" / "app_scan" / "eval" / "E1b"
FIXED_DATE_TIME = (2026, 1, 1, 0, 0, 0)
SNAPSHOT_NAME = "osv-pypi-2026-11-12.zip"


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"sha256={digest}"


def build_wheel_zip(
    dist: str,
    version: str,
    files: dict[str, bytes | str],
    out_dir: Path,
    *,
    tag: str = "py3-none-any",
    sidecar: bool = True,
) -> Path:
    """Write a deterministic wheel with a RECORD; returns its path."""
    norm = dist.replace("-", "_")
    dist_info = f"{norm}-{version}.dist-info"
    members: dict[str, bytes] = {
        name: (data.encode() if isinstance(data, str) else data) for name, data in files.items()
    }
    members[f"{dist_info}/METADATA"] = f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n".encode()
    members[f"{dist_info}/WHEEL"] = (
        f"Wheel-Version: 1.0\nGenerator: materialize_scan_fixture\nRoot-Is-Purelib: true\nTag: {tag}\n".encode()
    )
    lines = [f"{n},{_record_hash(d)},{len(d)}" for n, d in sorted(members.items())]
    lines.append(f"{dist_info}/RECORD,,")
    members[f"{dist_info}/RECORD"] = ("\n".join(lines) + "\n").encode()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{norm}-{version}-{tag}.whl"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    if sidecar:
        path.with_name(path.name + ".sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest() + "\n")
    return path


_BASE_1_1_13 = '''\
from __future__ import annotations


def _count_tokens(text):
    return len(text.split())


def _url_to_size(image_source):
    """Fetch the image and return its size (vulnerable: resolves DNS twice)."""
    return _count_tokens(image_source)


class ChatOpenAI:
    def get_num_tokens_from_messages(self, messages):
        return sum(_count_tokens(m) for m in messages)
'''

_BASE_1_1_14 = '''\
from __future__ import annotations


def _count_tokens(text):
    return len(text.split()) + 0


def _get_ssrf_safe_client(url):
    return ("pinned", url)


def _url_to_size(image_source):
    """Fetch the image and return its size (fixed: pinned client)."""
    client = _get_ssrf_safe_client(image_source)
    return _count_tokens(client[1])


class ChatOpenAI:
    def get_num_tokens_from_messages(self, messages):
        return sum(_count_tokens(m) for m in messages)
'''


def e1_wheel_files(version: str) -> dict[str, str]:
    base = _BASE_1_1_13 if version == "1.1.13" else _BASE_1_1_14
    return {
        "langchain_openai/__init__.py": "from langchain_openai.chat_models.base import ChatOpenAI\n",
        "langchain_openai/chat_models/__init__.py": "",
        "langchain_openai/chat_models/base.py": base,
    }


def materialize_e1(outdir: Path) -> dict[str, Path | str]:
    outdir = Path(outdir)
    app = outdir / "app"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    shutil.copy(E1_DIR / "requirements.txt", app / "requirements.txt")
    for src in sorted((E1_DIR / "app").rglob("*.py.txt")):
        rel = src.relative_to(E1_DIR / "app")
        dest = app / rel.with_name(rel.name[: -len(".txt")])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
    wheels = outdir / "wheels"
    for version in ("1.1.13", "1.1.14"):
        build_wheel_zip("langchain-openai", version, e1_wheel_files(version), wheels)
    snapshot = outdir / SNAPSHOT_NAME
    with zipfile.ZipFile(snapshot, "w") as z:
        for rec in sorted((E1_DIR / "advisories").glob("*.json")):
            z.writestr(rec.name, rec.read_bytes())
    sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    return {"app": app, "wheels": wheels, "snapshot": snapshot, "sha256": sha}


def materialize_e1b(outdir: Path) -> dict[str, Path | str]:
    """E1b: real anyio wheels, the verbatim GHSA-3w57 record and its fix-commit diff."""
    outdir = Path(outdir)
    app = outdir / "app"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    shutil.copy(E1B_DIR / "requirements.txt", app / "requirements.txt")
    for src in sorted((E1B_DIR / "app").rglob("*.py.txt")):
        rel = src.relative_to(E1B_DIR / "app")
        dest = app / rel.with_name(rel.name[: -len(".txt")])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
    wheels = outdir / "wheels"
    if wheels.exists():
        shutil.rmtree(wheels)
    shutil.copytree(E1B_DIR / "wheels", wheels)
    (wheels / "NOTICE").unlink()
    for whl in sorted(wheels.glob("*.whl")):
        whl.with_name(whl.name + ".sha256").write_text(hashlib.sha256(whl.read_bytes()).hexdigest() + "\n")
    snapshot = outdir / SNAPSHOT_NAME
    with zipfile.ZipFile(snapshot, "w") as z:
        for rec in sorted((E1B_DIR / "advisories").glob("*.json")):
            z.writestr(rec.name, rec.read_bytes())
    sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    return {"app": app, "wheels": wheels, "snapshot": snapshot, "sha256": sha}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outdir")
    parser.add_argument("--fixture", choices=["E1", "E1b"], default="E1")
    args = parser.parse_args()
    result = (materialize_e1b if args.fixture == "E1b" else materialize_e1)(Path(args.outdir))
    sys.stdout.write(
        f"app: {result['app']}\nwheels: {result['wheels']}\nsnapshot: {result['snapshot']}\nsha256: {result['sha256']}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
