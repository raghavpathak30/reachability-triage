"""Lockfile discovery and dependency inventory (unit A1a).

Reads text files only. Nothing scanned is built, installed or imported.
"""

from __future__ import annotations

import codecs
import logging
import re
import shlex
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

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


_URL_LIKE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://|file:)")
_PATH_LIKE = ("./", "../", "/", "~", ".\\")
_EQ_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*={2,3}\s*([^\s;,]+)")
_SHORT_VALUE_OPTIONS = ("-r", "-i", "-f", "-c", "-e")


def _split_option(line: str) -> tuple[str, str | None] | None:
    """Return (option, value) for a whole-line option, else None.

    Accepts `--opt=V`, `--opt V`, `-xV` and `-x V`.
    """
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    if not tokens:
        return None
    first = tokens[0]
    if first.startswith("--"):
        if "=" in first:
            name, _, value = first.partition("=")
            return name, value
        return first, " ".join(tokens[1:]) if len(tokens) > 1 else None
    if first.startswith("-"):
        if len(first) > 2 and first[:2] in _SHORT_VALUE_OPTIONS:
            return first[:2], first[2:]
        return first, " ".join(tokens[1:]) if len(tokens) > 1 else None
    return None


def _unsupported(rel: str, lineno: int, raw: str, why: str, dist: str | None = None) -> InventoryEntry:
    return InventoryEntry(
        dist=dist,
        version=None,
        status=Status.UNSUPPORTED,
        source_file=rel,
        line=lineno,
        notes=(why, f"raw: {raw}"),
    )


def _parse_requirement_line(line: str, rel: str, lineno: int) -> InventoryEntry:
    if line.startswith("-"):
        opt = _split_option(line)
        name = opt[0] if opt else line
        return _unsupported(rel, lineno, line, f"unsupported option {name}")
    req_text, *rest = _PER_REQ_SPLIT.split(line, maxsplit=1)
    req_text = req_text.strip()
    hashes: list[str] = []
    if rest:
        for m in re.finditer(r"--hash(?:=|\s+)(\S+)", "--" + rest[0]):
            hashes.append(m.group(1))
    if _URL_LIKE.match(req_text) or req_text.startswith(_PATH_LIKE) or re.match(
        r"^(git|hg|svn|bzr)\+", req_text
    ):
        return _unsupported(rel, lineno, line, "URL, VCS or path requirement")
    try:
        req = Requirement(req_text)
    except InvalidRequirement as exc:
        salvage = _EQ_PIN.match(req_text)
        if salvage:
            return _unsupported(
                rel,
                lineno,
                line,
                f"version {salvage.group(3)!r} is not valid PEP 440",
                canonicalize_name(salvage.group(1)),
            )
        return _unsupported(rel, lineno, line, f"invalid requirement: {exc}")
    dist = canonicalize_name(req.name)
    notes: list[str] = []
    if req.extras:
        notes.append("extras: " + ",".join(sorted(req.extras)))
    if req.url:
        return replace(_unsupported(rel, lineno, line, "direct URL requirement", dist), hashes=tuple(hashes))
    specs = list(req.specifier)
    status = Status.UNPINNED
    version: str | None = None
    if len(specs) == 1 and specs[0].operator == "===":
        status = Status.UNSUPPORTED
        notes.append("=== arbitrary equality is not supported")
    elif len(specs) == 1 and specs[0].operator == "==" and "*" not in specs[0].version:
        try:
            Version(specs[0].version)
        except InvalidVersion:
            status = Status.UNSUPPORTED
            notes.append(f"version {specs[0].version!r} is not valid PEP 440")
        else:
            status = Status.PINNED
            version = specs[0].version
    return InventoryEntry(
        dist=dist,
        version=version,
        status=status,
        source_file=rel,
        line=lineno,
        hashes=tuple(hashes),
        notes=tuple(notes),
    )


_TAINT_OPTIONS = frozenset(
    {"-i", "--index-url", "--extra-index-url", "--no-index", "-f", "--find-links"}
)


def _parse_requirements(path: Path, rel: str) -> tuple[list[InventoryEntry], list[str]]:
    entries: list[InventoryEntry] = []
    taints: list[str] = []
    for lineno, raw in _logical_lines(_read_text(path, rel)):
        line = _INLINE_COMMENT.sub("", raw).strip()
        if not line:
            continue
        if line.startswith("-"):
            opt = _split_option(line)
            if opt and opt[0] in _TAINT_OPTIONS:
                taints.append(f"{opt[0]} at {rel}:{lineno}")
                continue
        entries.append(_parse_requirement_line(line, rel, lineno))
    return entries, taints


def _apply_taint(entries: list[InventoryEntry], taints: list[str]) -> list[InventoryEntry]:
    if not taints:
        return entries
    out = []
    for e in entries:
        if e.dist is None:
            out.append(e)
            continue
        notes = e.notes + (f"original status: {e.status.value}", "package source changed by " + "; ".join(taints))
        out.append(replace(e, status=Status.UNSUPPORTED, notes=notes))
    return out


def build_inventory(root: Path) -> Inventory:
    try:
        resolved = Path(root).resolve(strict=True)
    except OSError as exc:
        raise ScanRootError(f"scan root not found: {root}") from exc
    if not resolved.is_dir():
        raise ScanRootError(f"scan root is not a directory: {root}")
    entries, taints = _parse_requirements(resolved / "requirements.txt", "requirements.txt")
    entries = _apply_taint(entries, taints)
    return Inventory(str(resolved), "requirements.txt", tuple(entries), (), None)
