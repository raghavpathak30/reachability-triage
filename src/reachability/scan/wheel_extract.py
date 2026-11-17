import re
import stat
import zipfile
from pathlib import Path


class WheelRejected(Exception):
    """A wheel that must not be used; the message is the named reason."""


_DRIVE = re.compile(r"[A-Za-z]:")


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


def extract_wheel(wheel_path: Path, dest: Path) -> None:
    """Write only `.py` files and `*.dist-info/RECORD` from the wheel into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(wheel_path)
    except zipfile.BadZipFile as exc:
        raise WheelRejected(f"not a readable zip: {exc}") from exc
    with zf:
        infos = zf.infolist()
        check_members(infos)
        for info in infos:
            if not _wanted(info.filename):
                continue
            out = dest / info.filename
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(zf.read(info))
