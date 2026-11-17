import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name

from reachability.scan.inventory import MAX_LOCKFILE_BYTES
from reachability.scan.wheel_select import Candidate

PYPI_REGISTRY = "https://pypi.org/simple"


@dataclass(frozen=True)
class LockEntry:
    registry: str | None
    wheels: tuple[Candidate, ...]
    has_sdist: bool
    source_kind: str | None = None


def _digest(value: object) -> str | None:
    if isinstance(value, str) and value.startswith("sha256:"):
        return value[len("sha256:"):].lower()
    return None


def read_lock_wheels(lock_path: Path) -> dict[tuple[str, str], LockEntry]:
    """Re-read uv.lock for each package's wheel URLs and hashes (inventory keeps neither)."""
    try:
        if lock_path.stat().st_size > MAX_LOCKFILE_BYTES:
            return {}
        data = tomllib.loads(lock_path.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[tuple[str, str], LockEntry] = {}
    for pkg in data.get("package", []):
        if not isinstance(pkg, dict):
            continue
        name, version = pkg.get("name"), pkg.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        source = pkg.get("source")
        registry = source.get("registry") if isinstance(source, dict) else None
        source_kind = None
        if isinstance(source, dict) and not isinstance(registry, str):
            source_kind = next((k for k in sorted(source) if isinstance(k, str)), "unknown")
        wheels: list[Candidate] = []
        for w in pkg.get("wheels", []) if isinstance(pkg.get("wheels"), list) else []:
            if isinstance(w, dict) and isinstance(w.get("url"), str):
                filename = w["url"].split("?")[0].rsplit("/", 1)[-1]
                wheels.append(Candidate(filename, w["url"], _digest(w.get("hash"))))
        out[(canonicalize_name(name), version)] = LockEntry(
            registry if isinstance(registry, str) else None, tuple(wheels), isinstance(pkg.get("sdist"), dict),
            source_kind,
        )
    return out
