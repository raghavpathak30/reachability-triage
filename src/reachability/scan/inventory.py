"""Lockfile discovery and dependency inventory (unit A1a).

Reads text files only. Nothing scanned is built, installed or imported.
"""

from __future__ import annotations

import codecs
import logging
import os
import re
import shlex
import stat
import tomllib
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from packaging._parser import Variable
from packaging.markers import InvalidMarker, Marker, UndefinedComparison, UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

MAX_LOCKFILE_BYTES = 5 * 1024 * 1024

HINT = (
    "We need exact versions and never run a resolver. Create a lockfile where you "
    "run the app, then scan again: `uv lock` (uv), `poetry lock` (Poetry), or "
    "`pip freeze > requirements.txt` run inside the environment you deploy."
)


TARGET_ENV = "cp313-manylinux_x86_64"

# Python 3.13.15 is the deployed interpreter (Dockerfile:3), not the dev venv.
MARKER_ENV: dict[str, str] = {
    "python_version": "3.13",
    "python_full_version": "3.13.15",
    "sys_platform": "linux",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
    "os_name": "posix",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
}
_MARKER_KEYS = frozenset(MARKER_ENV) | {"extra"}


class ScanError(Exception):
    """Base class for named scan errors."""


class ScanRootError(ScanError):
    pass


class LockfileParseError(ScanError):
    pass


class UvLockFormatError(ScanError):
    pass


class UvLockRootError(ScanError):
    pass


class UvLockResolutionError(ScanError):
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


def _marker_variables(marker: Marker) -> set[str]:
    """Collect every variable name in the parsed marker tree (packaging 26.3 shape)."""
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, str):
            if node not in ("and", "or"):
                raise LockfileParseError(f"unexpected marker token {node!r}")
        elif isinstance(node, tuple) and len(node) == 3:
            for part in (node[0], node[2]):
                if isinstance(part, Variable):
                    found.add(part.value)
        elif isinstance(node, list):
            for child in node:
                walk(child)
        else:
            raise LockfileParseError(f"unexpected marker tree node {type(node).__name__}")

    walk(marker._markers)
    return found


def marker_applies(marker: Marker | None) -> tuple[bool, tuple[str, ...]]:
    """Evaluate `marker` for TARGET_ENV. Unmapped keys keep the entry, with a note."""
    if marker is None:
        return True, ()
    unmapped = sorted(_marker_variables(marker) - _MARKER_KEYS)
    if unmapped:
        return True, (f"marker uses unmapped key {unmapped[0]}; kept",)
    env = dict(MARKER_ENV, extra="")
    try:
        return marker.evaluate(env), ()
    except (UndefinedComparison, UndefinedEnvironmentName) as exc:
        return True, (f"marker could not be evaluated ({exc}); kept",)


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
    applies, marker_notes = marker_applies(req.marker)
    notes.extend(marker_notes)
    if req.url:
        notes.append("direct URL requirement")
        return InventoryEntry(
            dist=dist,
            version=None,
            status=Status.UNSUPPORTED if applies else Status.MARKER_SKIPPED,
            source_file=rel,
            line=lineno,
            hashes=tuple(hashes),
            notes=tuple(notes) + (f"raw: {line}",),
        )
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
    if not applies:
        notes.append(f"marker false for {TARGET_ENV}: {req.marker}")
        status = Status.MARKER_SKIPPED
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


_INCLUDE_OPTIONS = frozenset({"-r", "--requirement"})


def _check_path(root: Path, lexical: Path) -> tuple[Path | None, str | None, str]:
    """Return (resolved, None, "") if safe to open, else (None, kind, detail).

    `lexical` is already normalised and absolute. A symlink (the path itself or
    any directory component), a path outside `root`, a non-regular file and a
    file over MAX_LOCKFILE_BYTES are all refused without being opened.
    """
    if not lexical.is_relative_to(root):
        return None, "include_outside_root", "path is outside the scan root"
    if lexical.is_symlink():
        return None, "symlink", "symlinks are never followed"
    try:
        resolved = lexical.resolve(strict=True)
    except FileNotFoundError:
        return None, "include_missing", "file does not exist"
    except (OSError, RuntimeError):
        return None, "symlink", "symlink loop or unresolvable path"
    if resolved != lexical:
        return None, "symlink", "a path component is a symlink; not followed"
    if not resolved.is_relative_to(root):
        return None, "include_outside_root", "resolves outside the scan root"
    try:
        st = os.stat(resolved)
    except OSError as exc:
        return None, "include_missing", f"cannot stat: {exc}"
    if not stat.S_ISREG(st.st_mode):
        return None, "not_regular_file", "not a regular file; not opened"
    if st.st_size > MAX_LOCKFILE_BYTES:
        return None, "too_large", f"{st.st_size} bytes exceeds {MAX_LOCKFILE_BYTES}"
    return resolved, None, ""


class _RequirementsTree:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.entries: list[InventoryEntry] = []
        self.taints: list[str] = []
        self.skipped: list[SkippedItem] = []
        self._active: list[Path] = []
        self._done: set[Path] = set()

    def parse(self, path: Path) -> None:
        rel = path.relative_to(self.root).as_posix()
        self._active.append(path)
        for lineno, raw in _logical_lines(_read_text(path, rel)):
            line = _INLINE_COMMENT.sub("", raw).strip()
            if not line:
                continue
            if line.startswith("-"):
                opt = _split_option(line)
                if opt and opt[0] in _TAINT_OPTIONS:
                    self.taints.append(f"{opt[0]} at {rel}:{lineno}")
                    continue
                if opt and opt[0] in _INCLUDE_OPTIONS and opt[1]:
                    self._include(path, rel, lineno, opt[1])
                    continue
            self.entries.append(_parse_requirement_line(line, rel, lineno))
        self._active.pop()
        self._done.add(path)

    def _include(self, including: Path, rel: str, lineno: int, value: str) -> None:
        lexical = Path(os.path.normpath(including.parent / value))
        resolved, kind, detail = _check_path(self.root, lexical)
        if resolved is None:
            self.skipped.append(SkippedItem(kind or "include_missing", value, rel, lineno, detail))
            return
        if resolved in self._active:
            self.skipped.append(SkippedItem("include_cycle", value, rel, lineno, "include cycle; not followed"))
            return
        if resolved in self._done:
            return
        self.parse(resolved)


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


def _load_toml(path: Path, rel: str) -> dict:
    text = _read_text(path, rel)
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise LockfileParseError(f"cannot parse {rel} as TOML: {exc}") from exc


def _parse_marker_string(text: str, rel: str) -> Marker:
    try:
        return Marker(text)
    except InvalidMarker as exc:
        raise LockfileParseError(f"invalid marker in {rel}: {text!r}: {exc}") from exc


def _version_status(version: object) -> tuple[Status, str | None, str | None]:
    """(status, version, note) for a lock-file version value."""
    if not isinstance(version, str) or not version:
        return Status.UNSUPPORTED, None, "no version in lock"
    try:
        Version(version)
    except InvalidVersion:
        return Status.UNSUPPORTED, version, f"version {version!r} is not valid PEP 440"
    return Status.PINNED, version, None


_POETRY_OK_SOURCES = frozenset({None, "pypi"})


def _poetry_applies(pkg: dict, groups: tuple[str, ...], rel: str) -> tuple[bool, tuple[str, ...]]:
    markers = pkg.get("markers")
    if markers is None:
        return True, ()
    if isinstance(markers, str):
        return marker_applies(_parse_marker_string(markers, rel))
    if isinstance(markers, dict):
        notes: list[str] = []
        parsed: dict[str, bool] = {}
        for group, text in markers.items():
            ok, n = marker_applies(_parse_marker_string(str(text), rel))
            parsed[group] = ok
            notes.extend(n)
        if groups:
            applies = any(g not in parsed or parsed[g] for g in groups)
        else:
            applies = any(parsed.values()) if parsed else True
        return applies, tuple(notes)
    raise LockfileParseError(f"unexpected markers value in {rel}: {markers!r}")


def _parse_poetry_lock(path: Path, rel: str) -> list[InventoryEntry]:
    data = _load_toml(path, rel)
    out: list[InventoryEntry] = []
    for pkg in data.get("package", []) or []:
        name = pkg.get("name")
        if not isinstance(name, str):
            raise LockfileParseError(f"{rel}: a [[package]] has no name")
        if isinstance(pkg.get("groups"), list):
            groups = tuple(str(g) for g in pkg["groups"])
        elif isinstance(pkg.get("category"), str):
            groups = (pkg["category"],)
        else:
            groups = ()
        status, version, note = _version_status(pkg.get("version"))
        notes: list[str] = [note] if note else []
        source = pkg.get("source") or {}
        source_type = source.get("type") if isinstance(source, dict) else None
        if source_type not in _POETRY_OK_SOURCES:
            status = Status.UNSUPPORTED
            notes.append(f"source type {source_type}")
        if pkg.get("optional") is True:
            notes.append("optional = true")
        applies, marker_notes = _poetry_applies(pkg, groups, rel)
        notes.extend(marker_notes)
        if not applies:
            status = Status.MARKER_SKIPPED
            notes.append(f"marker false for {TARGET_ENV}")
        out.append(
            InventoryEntry(
                dist=canonicalize_name(name),
                version=version,
                status=status,
                source_file=rel,
                line=None,
                groups=groups,
                notes=tuple(notes),
            )
        )
    return out


def _describe_source(source: object) -> str:
    return ", ".join(f"{k} = {v!r}" for k, v in source.items()) if isinstance(source, dict) else repr(source)


def find_uv_root(packages: list[dict]) -> dict:
    """The single [[package]] whose source is `editable = "."` or `virtual = "."`."""
    roots = []
    candidates = []
    for pkg in packages:
        source = pkg.get("source")
        if not isinstance(source, dict):
            continue
        for kind in ("editable", "virtual"):
            if kind in source:
                candidates.append(f"{pkg.get('name')} ({kind} = {source[kind]!r})")
                if source[kind] == ".":
                    roots.append(pkg)
    listing = "; ".join(candidates) if candidates else "none"
    if len(roots) == 1:
        return roots[0]
    if not roots:
        raise UvLockRootError(
            "uv.lock has no root member (a package with source editable = \".\" or virtual = \".\"); "
            f"editable/virtual candidates: {listing}"
        )
    raise UvLockRootError(
        f"uv.lock has {len(roots)} root members, expected exactly one; candidates: {listing}"
    )


def _parse_uv_lock(path: Path, rel: str) -> list[InventoryEntry]:
    data = _load_toml(path, rel)
    version = data.get("version")
    if version != 1 or isinstance(version, bool):
        raise UvLockFormatError(f"{rel}: unsupported uv.lock version {version!r}; only version 1 is read")
    packages = data.get("package", []) or []
    find_uv_root(packages)
    return []


_CANDIDATES = ("uv.lock", "poetry.lock", "requirements.txt")
_A1B_FORMATS = ("Pipfile.lock", "pylock.toml")


def discover_lockfile(root: Path) -> tuple[str | None, Path | None, list[SkippedItem]]:
    """Pick the lockfile by precedence; record every other candidate. Parses nothing."""
    winner: tuple[str, Path] | None = None
    skipped: list[SkippedItem] = []
    for name in _CANDIDATES:
        lexical = root / name
        if not os.path.lexists(lexical):
            continue
        resolved, kind, detail = _check_path(root, lexical)
        if resolved is None:
            skipped.append(SkippedItem(kind or "symlink", name, None, None, detail))
        elif winner is None:
            winner = (name, resolved)
        else:
            skipped.append(
                SkippedItem("ignored_lockfile", name, None, None, f"lower precedence than {winner[0]}")
            )
    for name in _A1B_FORMATS:
        if os.path.lexists(root / name):
            skipped.append(SkippedItem("ignored_lockfile", name, None, None, "format arrives in A1b"))
    if winner is None:
        return None, None, skipped
    return winner[0], winner[1], skipped


def build_inventory(root: Path) -> Inventory:
    try:
        resolved = Path(root).resolve(strict=True)
    except OSError as exc:
        raise ScanRootError(f"scan root not found: {root}") from exc
    if not resolved.is_dir():
        raise ScanRootError(f"scan root is not a directory: {root}")
    lockfile, path, skipped = discover_lockfile(resolved)
    tree = _RequirementsTree(resolved)
    entries: list[InventoryEntry] = []
    if lockfile == "requirements.txt" and path is not None:
        tree.parse(path)
        entries = _apply_taint(tree.entries, tree.taints)
    elif lockfile == "poetry.lock" and path is not None:
        entries = _parse_poetry_lock(path, "poetry.lock")
    elif lockfile == "uv.lock" and path is not None:
        entries = _parse_uv_lock(path, "uv.lock")
    hint = HINT if lockfile is None or any(e.status is Status.UNPINNED for e in entries) else None
    return Inventory(str(resolved), lockfile, tuple(entries), tuple(skipped) + tuple(tree.skipped), hint)
