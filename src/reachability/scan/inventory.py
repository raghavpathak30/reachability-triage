"""Lockfile discovery and dependency inventory (unit A1a).

Reads text files only. Nothing scanned is built, installed or imported.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

logger = logging.getLogger(__name__)

HINT = (
    "We need exact versions and never run a resolver. Create a lockfile where you "
    "run the app, then scan again: `uv lock` (uv), `poetry lock` (Poetry), or "
    "`pip freeze > requirements.txt` run inside the environment you deploy."
)


class ScanError(Exception):
    """Base class for named scan errors."""


class ScanRootError(ScanError):
    pass


class Status(str, Enum):
    PINNED = "pinned"
    UNPINNED = "unpinned"
    UNSUPPORTED = "unsupported"
    MARKER_SKIPPED = "marker_skipped"


@dataclass(frozen=True)
class InventoryEntry:
    dist: str | None
    version: str | None
    status: Status
    source_file: str
    line: int | None = None
    groups: tuple[str, ...] = ()
    hashes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkippedItem:
    kind: str
    path: str
    source_file: str | None
    line: int | None
    detail: str


@dataclass(frozen=True)
class Inventory:
    root: str
    lockfile: str | None
    entries: tuple[InventoryEntry, ...]
    skipped: tuple[SkippedItem, ...]
    hint: str | None


def build_inventory(root: Path) -> Inventory:
    try:
        resolved = Path(root).resolve(strict=True)
    except OSError as exc:
        raise ScanRootError(f"scan root not found: {root}") from exc
    if not resolved.is_dir():
        raise ScanRootError(f"scan root is not a directory: {root}")
    entries: list[InventoryEntry] = []
    text = (resolved / "requirements.txt").read_text()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        req = Requirement(raw.strip())
        spec = next(iter(req.specifier))
        entries.append(
            InventoryEntry(
                dist=canonicalize_name(req.name),
                version=spec.version,
                status=Status.PINNED,
                source_file="requirements.txt",
                line=lineno,
            )
        )
    return Inventory(str(resolved), "requirements.txt", tuple(entries), (), None)
