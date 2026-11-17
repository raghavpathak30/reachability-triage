import zipfile
from pathlib import Path


class WheelRejected(Exception):
    """A wheel that must not be used; the message is the named reason."""


def _wanted(name: str) -> bool:
    if name.endswith("/"):
        return False
    parts = name.split("/")
    if name.endswith(".py"):
        return True
    return len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] == "RECORD"


def extract_wheel(wheel_path: Path, dest: Path) -> None:
    """Write only `.py` files and `*.dist-info/RECORD` from the wheel into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel_path) as zf:
        for info in zf.infolist():
            if not _wanted(info.filename):
                continue
            out = dest / info.filename
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(zf.read(info))
