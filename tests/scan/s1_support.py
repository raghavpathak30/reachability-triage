"""Helpers that build a complete offline scan setup in tmp_path (no network, no repo fixtures)."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

LIB_OLD = "def f(x):\n    return x + 1\n\n\ndef helper():\n    return 0\n"
LIB_NEW = "def f(x):\n    return x + 2\n\n\ndef helper():\n    return 0\n"
APP_CALLS_F = "from lib.core import f\n\n\ndef main():\n    return f(1)\n\n\nif __name__ == '__main__':\n    main()\n"


DEFAULT_DETAILS = "The function `f()` is vulnerable."


def advisory(adv_id, dist, fixed="2.0", summary="synthetic", aliases=(), details=DEFAULT_DETAILS):
    events = [{"introduced": "0"}] + ([{"fixed": fixed}] if fixed else [])
    return {
        "id": adv_id,
        "aliases": list(aliases),
        "summary": summary,
        "details": details,
        "affected": [
            {"package": {"name": dist, "ecosystem": "PyPI"}, "ranges": [{"type": "ECOSYSTEM", "events": events}]}
        ],
    }


def lib_files(core=LIB_OLD, extra=None, pkg="lib"):
    files = {f"{pkg}/__init__.py": "", f"{pkg}/core.py": core}
    files.update(extra or {})
    return files


def make_scan(tmp_path, *, app_files, pins, wheels=None, advisories=(), snapshot_name="osv-pypi-2026-11-12.zip"):
    """pins: {dist: version}; wheels: {(dist, version): files dict}; returns paths + snapshot sha."""
    app = tmp_path / "app"
    app.mkdir(parents=True, exist_ok=True)
    for rel, text in app_files.items():
        p = app / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (app / "requirements.txt").write_text("".join(f"{d}=={v}\n" for d, v in pins.items()))
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir(exist_ok=True)
    for (dist, version), files in (wheels or {}).items():
        mat.build_wheel_zip(dist, version, files, wheel_dir)
    snap = tmp_path / snapshot_name
    with zipfile.ZipFile(snap, "w") as z:
        for adv in advisories:
            z.writestr(f"{adv['id']}.json", json.dumps(adv))
    return {
        "app": app,
        "wheels": wheel_dir,
        "snapshot": snap,
        "sha256": hashlib.sha256(snap.read_bytes()).hexdigest(),
    }


def simple_lib_scan(tmp_path, *, old=LIB_OLD, new=LIB_NEW, app_files=None, extra_old=None, extra_new=None,
                    adv_id="GHSA-aaaa-bbbb-cccc"):
    return make_scan(
        tmp_path,
        app_files=app_files if app_files is not None else {"main.py": APP_CALLS_F},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(old, extra_old), ("lib", "2.0"): lib_files(new, extra_new)},
        advisories=[advisory(adv_id, "lib")],
    )
