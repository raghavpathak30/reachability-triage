import ast
import re
import stat
import zipfile
from pathlib import Path


class WheelRejected(Exception):
    """A wheel that must not be used; the message is the named reason."""


_DRIVE = re.compile(r"[A-Za-z]:")

MAX_MEMBERS = 20_000
MAX_PY_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
_CHUNK = 64 * 1024


def _wanted(name: str) -> bool:
    if name.endswith("/"):
        return False
    parts = name.split("/")
    if name.endswith(".py"):
        return True
    return len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] == "RECORD"


def _check_name(name: str) -> None:
    if "\x00" in name:
        raise WheelRejected(f"member name contains NUL: {name!r}")
    if "\\" in name:
        raise WheelRejected(f"member name contains a backslash: {name!r}")
    if name.startswith("/"):
        raise WheelRejected(f"absolute member path: {name!r}")
    if _DRIVE.match(name):
        raise WheelRejected(f"drive-letter member path: {name!r}")
    parts = name.rstrip("/").split("/")
    if ".." in parts:
        raise WheelRejected(f"member path escapes the wheel (..): {name!r}")
    if "" in parts or "." in parts:
        raise WheelRejected(f"member path has an empty or '.' component: {name!r}")


def check_members(infos: list[zipfile.ZipInfo]) -> None:
    """Reject the whole wheel on any unsafe member; nothing is written before this passes."""
    if len(infos) > MAX_MEMBERS:
        raise WheelRejected(f"too many members: {len(infos)} (limit {MAX_MEMBERS})")
    seen: set[str] = set()
    files: set[str] = set()
    for info in infos:
        name = info.filename
        _check_name(name)
        if name in seen:
            raise WheelRejected(f"duplicate member name: {name!r}")
        seen.add(name)
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind == stat.S_IFLNK:
            raise WheelRejected(f"symlink member: {name!r}")
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise WheelRejected(f"non-regular member (hardlink, device or fifo): {name!r}")
        if not name.endswith("/") and kind != stat.S_IFDIR:
            files.add(name)
    for name in files:
        parts = name.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in files:
                raise WheelRejected(f"file member {'/'.join(parts[:i])!r} is also a directory prefix of {name!r}")


def preparse_error(path: Path) -> str | None:
    """Exception class name if the engine's index could not read this file, else None."""
    try:
        ast.parse(path.read_bytes().decode("utf-8"), filename=str(path))
    except (SyntaxError, RecursionError, MemoryError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
        return type(exc).__name__
    return None


def extract_wheel(wheel_path: Path, dest: Path) -> list[tuple[str, str]]:
    """Write only `.py` files and `*.dist-info/RECORD` from the wheel into dest.

    Sizes are enforced on the bytes actually streamed, not on declared header sizes.
    Every `.py` is pre-parsed; one the index could not read is removed and returned as
    (relative path, exception class name).
    """
    dest.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(wheel_path)
    except zipfile.BadZipFile as exc:
        raise WheelRejected(f"not a readable zip: {exc}") from exc
    total = 0
    unparsed: list[tuple[str, str]] = []
    with zf:
        infos = zf.infolist()
        check_members(infos)
        for info in infos:
            if not _wanted(info.filename):
                continue
            out = dest / info.filename
            out.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with zf.open(info) as src, out.open("wb") as sink:
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    total += len(chunk)
                    if info.filename.endswith(".py") and size > MAX_PY_BYTES:
                        raise WheelRejected(f"{info.filename!r} exceeds {MAX_PY_BYTES} bytes")
                    if total > MAX_EXTRACTED_BYTES:
                        raise WheelRejected(f"extracted size exceeds {MAX_EXTRACTED_BYTES} bytes")
                    sink.write(chunk)
            if info.filename.endswith(".py"):
                err = preparse_error(out)
                if err is not None:
                    out.unlink()
                    unparsed.append((info.filename, err))
    return unparsed
