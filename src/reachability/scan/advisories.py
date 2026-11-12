"""OSV PyPI snapshot loading and advisory matching (unit A1a)."""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from reachability.scan.inventory import ScanError

logger = logging.getLogger(__name__)

_SNAPSHOT_NAME = re.compile(r"osv-pypi-([0-9]{4})-([0-9]{2})-([0-9]{2})\.zip", re.ASCII)


class SnapshotError(ScanError):
    pass


class SnapshotHashMismatchError(SnapshotError):
    pass


class SnapshotFilenameError(SnapshotError):
    pass


@dataclass(frozen=True)
class Snapshot:
    path: str
    date: datetime.date
    sha256: str
    records_loaded: int


def load_snapshot(path: Path, expected_sha256: str) -> Snapshot:
    path = Path(path)
    m = _SNAPSHOT_NAME.fullmatch(path.name)
    if not m:
        raise SnapshotFilenameError(
            f"snapshot filename must be osv-pypi-YYYY-MM-DD.zip, got {path.name!r}"
        )
    try:
        date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError as exc:
        raise SnapshotFilenameError(f"snapshot filename has an invalid date: {path.name!r}") from exc
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256.lower():
        raise SnapshotHashMismatchError(
            f"snapshot sha256 {actual} does not match expected {expected_sha256}"
        )
    count = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if name.endswith(".json"):
                count += 1
    return Snapshot(str(path), date, actual, count)
