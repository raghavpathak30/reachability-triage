"""Fix-commit lookup: which functions did an advisory's own fix commit change.

Commit URLs come only from the snapshot record's references. Diff text and wheel source are
parsed as text and with `ast`; nothing is imported or run.
"""

import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from reachability.scan.advisories import Record
from reachability.scan.models import Gap
from reachability.scan.textsafe import strip_control_line

OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
SHA_RE = re.compile(r"[0-9a-fA-F]{7,40}")
_COMMIT_URL = re.compile(
    r"https://github\.com/([^/\s?#]+)/([^/\s?#]+)/commit/([0-9a-f]{7,40})/?", re.IGNORECASE
)
_PACKAGE_URL = re.compile(r"https://github\.com/([^/\s?#]+)/([^/\s?#]+?)(?:\.git)?/?", re.IGNORECASE)


@dataclass(frozen=True)
class Commit:
    owner: str
    repo: str
    sha: str

    def label(self) -> str:
        return f"{self.owner}/{self.repo}@{self.sha}"


def valid_parts(owner: str, repo: str, sha: str) -> bool:
    return bool(
        OWNER_RE.fullmatch(owner)
        and REPO_RE.fullmatch(repo)
        and not repo.startswith(".")
        and SHA_RE.fullmatch(sha)
    )


def package_repo(record: Record) -> tuple[str, str] | None:
    for rtype, url in record.references:
        if rtype == "PACKAGE":
            m = _PACKAGE_URL.fullmatch(url)
            if m:
                return m.group(1).lower(), m.group(2).lower()
    return None


def usable_commits(records: list[Record]) -> list[Commit]:
    """Same-repo GitHub commit URLs (any reference type), union over the records, in sorted order."""
    found: dict[tuple[str, str, str], Commit] = {}
    for rec in records:
        repo = package_repo(rec)
        if repo is None:
            continue
        for _rtype, url in rec.references:
            m = _COMMIT_URL.fullmatch(url)
            if not m:
                continue
            owner, name, sha = m.group(1), m.group(2), m.group(3).lower()
            if (owner.lower(), name.lower()) != repo or not valid_parts(owner, name, sha):
                continue
            found.setdefault((owner.lower(), name.lower(), sha), Commit(owner.lower(), name.lower(), sha))
    return [found[k] for k in sorted(found)]


# ---- diff sources -------------------------------------------------------------------------

MAX_DIFF_BYTES = 2 * 1024 * 1024
DIFF_SECONDS = 30.0
MAX_DIFFS = 250
DIFFS_TOTAL_SECONDS = 300.0
GAP_KIND = "fix_commit_unavailable"


class DiffUnavailable(Exception):
    """A diff that cannot be had; the message is a short status class, never a response body."""


class DiffFetcher(Protocol):
    def fetch_diff(self, owner: str, repo: str, sha: str) -> str: ...


def looks_like_diff(text: str) -> bool:
    return "diff --git" in text or text.startswith("--- ") or "\n--- " in text


def safe_path(root: Path, owner: str, repo: str, sha: str) -> Path:
    """`root/owner/repo/sha.diff` from validated parts; never through a symlink, never outside root."""
    if not valid_parts(owner, repo, sha):
        raise DiffUnavailable("invalid owner/repo/sha")
    path = root / owner.lower() / repo.lower() / f"{sha.lower()}.diff"
    cur = root
    for part in path.relative_to(root).parts:
        cur = cur / part
        if cur.is_symlink():
            raise DiffUnavailable("unsafe path (symlink)")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise DiffUnavailable("unsafe path") from None
    return path


def _read_text(path: Path) -> str:
    with path.open("rb") as f:
        data = f.read(MAX_DIFF_BYTES + 1)
    if len(data) > MAX_DIFF_BYTES:
        raise DiffUnavailable("size cap")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise DiffUnavailable("decode error") from None


class LocalDiffDir:
    """Offline diffs: `<root>/<owner>/<repo>/<sha>.diff`, read only for a commit named by the record."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def fetch_diff(self, owner: str, repo: str, sha: str) -> str:
        path = safe_path(self.root, owner, repo, sha)
        if not path.is_file():
            raise DiffUnavailable("no local diff")
        return _read_text(path)


def short_reason(exc: BaseException) -> str:
    if isinstance(exc, DiffUnavailable):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, UnicodeDecodeError):
        return "decode error"
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return f"HTTP {code}"
    return f"error: {type(exc).__name__}"


def gap_for(dist: str | None, commit: Commit, reason: str) -> Gap:
    detail = strip_control_line(f"{commit.label()}: {reason}")[:200]
    return Gap(GAP_KIND, dist, detail)


class DiffStore:
    """Cache plus fetch for fix-commit diffs. Every failure is recorded as a reason, never raised."""

    def __init__(
        self,
        cache_root: Path,
        fetcher: DiffFetcher | None,
        *,
        max_diffs: int = MAX_DIFFS,
        max_seconds: float = DIFFS_TOTAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.root = Path(cache_root) / "fix-commits"
        self.fetcher = fetcher
        self.max_diffs = max_diffs
        self.max_seconds = max_seconds
        self.clock = clock
        self.results: dict[Commit, tuple[str | None, str | None]] = {}
        self.fetched = 0
        self.cached = 0
        self.unavailable: dict[str, int] = {}
        self.spent = 0.0

    def _fail(self, commit: Commit, reason: str) -> tuple[None, str]:
        self.unavailable[reason] = self.unavailable.get(reason, 0) + 1
        self.results[commit] = (None, reason)
        return None, reason

    def get(self, commit: Commit) -> tuple[str | None, str | None]:
        if commit in self.results:
            return self.results[commit]
        try:
            return self._get(commit)
        except Exception as exc:
            return self._fail(commit, short_reason(exc))

    def _get(self, commit: Commit) -> tuple[str | None, str | None]:
        cache = safe_path(self.root, commit.owner, commit.repo, commit.sha)
        if cache.is_file():
            try:
                text = _read_text(cache)
                if looks_like_diff(text):
                    self.cached += 1
                    self.results[commit] = (text, None)
                    return text, None
            except DiffUnavailable:
                pass
            cache.unlink(missing_ok=True)  # corrupt: refetch once
        if self.fetcher is None:
            return self._fail(commit, "no diff source")
        if self.fetched >= self.max_diffs:
            return self._fail(commit, f"diff budget: {self.max_diffs}")
        if self.spent >= self.max_seconds:
            return self._fail(commit, f"diff budget: {self.max_seconds:.0f} s")
        start = self.clock()
        self.fetched += 1
        try:
            text = self.fetcher.fetch_diff(commit.owner, commit.repo, commit.sha)
        finally:
            self.spent += self.clock() - start
        if len(text.encode("utf-8", "replace")) > MAX_DIFF_BYTES:
            return self._fail(commit, "size cap")
        if not looks_like_diff(text):
            return self._fail(commit, "not a diff")
        self._write(cache, text)
        self.results[commit] = (text, None)
        return text, None

    def _write(self, cache: Path, text: str) -> None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=cache.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(text.encode("utf-8"))
                os.replace(tmp, cache)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError:
            pass  # a cache that cannot be written only costs a refetch


# ---- unified-diff parser ------------------------------------------------------------------

MAX_HUNKS_PER_DIFF = 2000
MAX_HUNKS_PER_SCAN = 20000
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SKIP_SEGMENTS = frozenset({"tests", "test", "docs"})
_LINES = re.compile(r"\r\n|\r|\n")


def split_lines(text: str) -> list[str]:
    """Split on the line boundaries `ast` counts (never str.splitlines)."""
    return _LINES.split(text)


@dataclass(frozen=True)
class Hunk:
    items: tuple[tuple[str, str], ...]  # (" " | "-" | "+", text without trailing whitespace)

    @property
    def removed_side(self) -> list[str]:
        return [t for op, t in self.items if op in " -"]

    @property
    def added_side(self) -> list[str]:
        return [t for op, t in self.items if op in " +"]


@dataclass(frozen=True)
class FileDiff:
    path: str
    hunks: tuple[Hunk, ...]


def _clean_path(raw: str) -> str | None:
    raw = raw.split("\t")[0].strip()
    if raw == "/dev/null" or raw.startswith('"'):
        return None
    return raw[2:] if raw[:2] in ("a/", "b/") else raw


def _wanted(path: str) -> bool:
    parts = path.split("/")
    return path.endswith(".py") and not any(p in _SKIP_SEGMENTS for p in parts[:-1])


def parse_diff(text: str, counters: dict[str, int] | None = None) -> list[FileDiff]:
    """Parse a unified diff into per-file hunks, keeping only .py files outside tests/test/docs.

    A hunk whose body disagrees with its header counts, or that is cut short, is dropped.
    """
    counters = counters if counters is not None else {}

    def bump(name: str) -> None:
        counters[name] = counters.get(name, 0) + 1

    lines = text.split("\n")
    out: list[FileDiff] = []
    i, n = 0, len(lines)
    while i < n:
        if not lines[i].startswith("diff --git "):
            i += 1
            continue
        i += 1
        old = new = None
        saw_old = saw_new = False
        hunks: list[Hunk] = []
        while i < n and not lines[i].startswith("diff --git "):
            line = lines[i]
            if not hunks and line.startswith("--- "):
                old, saw_old = _clean_path(line[4:]), True
            elif not hunks and line.startswith("+++ "):
                new, saw_new = _clean_path(line[4:]), True
            elif line.startswith("@@"):
                m = _HUNK.match(line)
                i += 1
                if not m:
                    bump("bad_header")
                    continue
                old_rem = int(m.group(2)) if m.group(2) is not None else 1
                new_rem = int(m.group(4)) if m.group(4) is not None else 1
                items: list[tuple[str, str]] = []
                ok = True
                while old_rem > 0 or new_rem > 0:
                    if i >= n or (i == n - 1 and not lines[i]):
                        ok = False
                        bump("truncated")
                        break
                    body = lines[i]
                    op, rest = (body[:1] or " "), body[1:]
                    if op == " " and old_rem > 0 and new_rem > 0:
                        old_rem -= 1
                        new_rem -= 1
                    elif op == "-" and old_rem > 0:
                        old_rem -= 1
                    elif op == "+" and new_rem > 0:
                        new_rem -= 1
                    else:
                        ok = False
                        bump("bad_header")
                        break
                    items.append((op, rest.rstrip()))
                    i += 1
                while i < n and lines[i].startswith("\\"):
                    i += 1
                if ok:
                    hunks.append(Hunk(tuple(items)))
                continue
            i += 1
        if not (saw_old and saw_new) or old is None or new is None or old != new or not _wanted(new):
            if saw_old and saw_new and old is not None and new is not None and old != new:
                bump("rename_skipped")
            continue
        if len(hunks) > MAX_HUNKS_PER_DIFF:
            counters["too_many_hunks"] = counters.get("too_many_hunks", 0) + len(hunks) - MAX_HUNKS_PER_DIFF
            hunks = hunks[:MAX_HUNKS_PER_DIFF]
        out.append(FileDiff(new, tuple(hunks)))
    return out
