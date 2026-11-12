"""Lockfile discovery and dependency inventory (unit A1a).

Reads text files only. Nothing scanned is built, installed or imported.
"""

from __future__ import annotations

import codecs
import logging
import re
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


class LockfileParseError(ScanError):
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


_COMMENT_LINE = re.compile(r"\s*#")
_INLINE_COMMENT = re.compile(r"(^|\s+)#.*$")
_PER_REQ_SPLIT = re.compile(r"\s--")


def _read_text(path: Path, rel: str) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise LockfileParseError(f"cannot read {rel}: {exc}") from exc
    if data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = "utf-8"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise LockfileParseError(f"cannot decode {rel} as {encoding}: {exc}") from exc


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Join `\\` continuations the way pip does; line number is the first physical line."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[tuple[int, str]] = []
    parts: list[str] = []
    start = 0
    for n, line in enumerate(lines, start=1):
        is_comment = bool(_COMMENT_LINE.match(line))
        if is_comment or not line.endswith("\\"):
            if parts:
                parts.append("" if is_comment else line)
                out.append((start, "".join(parts)))
                parts = []
            else:
                out.append((n, "" if is_comment else line))
        else:
            if not parts:
                start = n
            parts.append(line[:-1])
    if parts:
        out.append((start, "".join(parts)))
    return out


def _parse_requirement_line(line: str, rel: str, lineno: int) -> InventoryEntry:
    req_text, *rest = _PER_REQ_SPLIT.split(line, maxsplit=1)
    hashes: list[str] = []
    if rest:
        for m in re.finditer(r"--hash(?:=|\s+)(\S+)", "--" + rest[0]):
            hashes.append(m.group(1))
    req = Requirement(req_text.strip())
    notes: list[str] = []
    if req.extras:
        notes.append("extras: " + ",".join(sorted(req.extras)))
    spec = next(iter(req.specifier))
    return InventoryEntry(
        dist=canonicalize_name(req.name),
        version=spec.version,
        status=Status.PINNED,
        source_file=rel,
        line=lineno,
        hashes=tuple(hashes),
        notes=tuple(notes),
    )


def _parse_requirements(path: Path, rel: str) -> list[InventoryEntry]:
    entries: list[InventoryEntry] = []
    for lineno, raw in _logical_lines(_read_text(path, rel)):
        line = _INLINE_COMMENT.sub("", raw).strip()
        if not line:
            continue
        entries.append(_parse_requirement_line(line, rel, lineno))
    return entries


def build_inventory(root: Path) -> Inventory:
    try:
        resolved = Path(root).resolve(strict=True)
    except OSError as exc:
        raise ScanRootError(f"scan root not found: {root}") from exc
    if not resolved.is_dir():
        raise ScanRootError(f"scan root is not a directory: {root}")
    entries = _parse_requirements(resolved / "requirements.txt", "requirements.txt")
    return Inventory(str(resolved), "requirements.txt", tuple(entries), (), None)
