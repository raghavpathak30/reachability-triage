"""OSV PyPI snapshot loading and advisory matching (unit A1a)."""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from packaging.utils import canonicalize_name

from reachability.scan.inventory import Inventory, ScanError, Status

NO_FIX = "none"

logger = logging.getLogger(__name__)

_SNAPSHOT_NAME = re.compile(r"osv-pypi-([0-9]{4})-([0-9]{2})-([0-9]{2})\.zip", re.ASCII)


class SnapshotError(ScanError):
    pass


class SnapshotHashMismatchError(SnapshotError):
    pass


class SnapshotFilenameError(SnapshotError):
    pass


@dataclass(frozen=True)
class AffectedEntry:
    dist: str
    versions: tuple[str, ...]


@dataclass(frozen=True)
class Record:
    id: str
    aliases: tuple[str, ...]
    affected: tuple[AffectedEntry, ...]


@dataclass(frozen=True)
class AdvisoryGroup:
    group_id: str
    ids: tuple[str, ...]
    malicious_signal: str | None = None


@dataclass(frozen=True)
class Snapshot:
    path: str
    date: datetime.date
    sha256: str
    records_loaded: int
    records: dict[str, Record] = field(default_factory=dict, repr=False)
    withdrawn_excluded: int = 0
    groups: dict[str, AdvisoryGroup] = field(default_factory=dict, repr=False)
    group_of: dict[str, str] = field(default_factory=dict, repr=False)


def _build_groups(records: dict[str, Record]) -> tuple[dict[str, AdvisoryGroup], dict[str, str]]:
    """Union-find over each record's id and aliases. `related` is never read."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for rec in records.values():
        for alias in rec.aliases:
            ra, rb = find(rec.id), find(alias)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
        find(rec.id)
    nodes: dict[str, set[str]] = {}
    record_ids: dict[str, list[str]] = {}
    for node in list(parent):
        nodes.setdefault(find(node), set()).add(node)
    for rid in records:
        record_ids.setdefault(find(rid), []).append(rid)
    groups: dict[str, AdvisoryGroup] = {}
    group_of: dict[str, str] = {}
    for root, members in nodes.items():
        ghsa = sorted(m for m in members if m.startswith("GHSA-"))
        rep = ghsa[0] if ghsa else sorted(members)[0]
        ids = tuple(sorted(record_ids.get(root, [])))
        if not ids:
            continue
        groups[rep] = AdvisoryGroup(rep, ids)
        for rid in ids:
            group_of[rid] = rep
    return dict(sorted(groups.items())), group_of


def _reduce(raw: dict) -> Record:
    entries = []
    for a in raw.get("affected", []) or []:
        pkg = a.get("package") or {}
        if pkg.get("ecosystem") != "PyPI":
            continue
        entries.append(
            AffectedEntry(
                dist=str(canonicalize_name(pkg.get("name", ""))),
                versions=tuple(a.get("versions") or ()),
            )
        )
    return Record(raw["id"], tuple(raw.get("aliases") or ()), tuple(entries))


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
    records: dict[str, Record] = {}
    withdrawn = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                if not name.endswith(".json"):
                    continue
                try:
                    raw = json.loads(z.read(name))
                except (ValueError, zipfile.BadZipFile) as exc:
                    raise SnapshotError(f"snapshot member {name!r} is not valid JSON: {exc}") from exc
                if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                    raise SnapshotError(f"snapshot member {name!r} is not an OSV record with an id")
                if "withdrawn" in raw:
                    withdrawn += 1
                    continue
                records[raw["id"]] = _reduce(raw)
    except zipfile.BadZipFile as exc:
        raise SnapshotError(f"snapshot is not a readable zip: {exc}") from exc
    groups, group_of = _build_groups(records)
    return Snapshot(str(path), date, actual, len(records), records, withdrawn, groups, group_of)


@dataclass(frozen=True)
class MatchResult:
    dist: str
    version: str
    group_id: str
    ids: tuple[str, ...]
    fixed_in: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScanReport:
    snapshot: Snapshot
    inventory: Inventory
    findings: tuple[MatchResult, ...]


def build_report(inventory: Inventory, snapshot: Snapshot) -> ScanReport:
    findings: list[MatchResult] = []
    for entry in inventory.entries:
        if entry.status is not Status.PINNED:
            continue
        for rec in snapshot.records.values():
            for aff in rec.affected:
                if aff.dist == entry.dist and entry.version in aff.versions:
                    findings.append(
                        MatchResult(entry.dist, entry.version, rec.id, (rec.id,), NO_FIX)
                    )
    return ScanReport(snapshot, inventory, tuple(findings))
