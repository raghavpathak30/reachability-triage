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
from packaging.version import InvalidVersion, Version

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


_EVENT_KINDS = ("introduced", "fixed", "last_affected", "limit")
_BAD_EVENT = "?"


def reduce_events(events: object) -> tuple[tuple[str, str], ...]:
    """Reduce OSV events to (kind, value); anything malformed becomes ("?", json)."""
    out: list[tuple[str, str]] = []
    if not isinstance(events, (list, tuple)):
        return ((_BAD_EVENT, json.dumps(events, default=str)),)
    for ev in events:
        if isinstance(ev, dict) and len(ev) == 1:
            (kind, value), = ev.items()
            if kind in _EVENT_KINDS and isinstance(value, str):
                out.append((kind, value))
                continue
        out.append((_BAD_EVENT, json.dumps(ev, default=str)))
    return tuple(out)


_RANK = {"fixed": 0, "limit": 0, "last_affected": 0, "introduced": 1}


def _parse_bound(value: str) -> Version | None:
    try:
        return Version(value)
    except InvalidVersion:
        return None


def range_matches(
    events: tuple[tuple[str, str], ...], installed: Version, label: str = ""
) -> tuple[bool, tuple[str, ...]]:
    """Ordered-event walk. `introduced "0"` is minus infinity; fixed/limit exclusive,
    last_affected inclusive. Anything it cannot read makes the range match, with a note."""
    where = f" in {label}" if label else ""
    parsed: list[tuple[Version | None, str]] = []
    saw_introduced = False
    for kind, value in events:
        if kind == _BAD_EVENT:
            return True, (f"malformed event {value}{where}; matched conservatively",)
        if kind == "introduced":
            saw_introduced = True
            if value == "0":
                parsed.append((None, kind))
                continue
        bound = _parse_bound(value)
        if bound is None:
            return True, (f"unparseable range bound {value!r}{where}; matched conservatively",)
        parsed.append((bound, kind))
    if not saw_introduced:
        return True, (f"range has no introduced event{where}; matched conservatively",)
    parsed.sort(key=lambda p: ((0, Version("0")) if p[0] is None else (1, p[0]), _RANK[p[1]]))
    affected = False
    for bound, kind in parsed:
        if kind == "introduced":
            if bound is None or installed >= bound:
                affected = True
        elif kind == "last_affected":
            if installed > bound:
                affected = False
        elif installed >= bound:
            affected = False
    return affected, ()


def fixed_in_for(
    events: tuple[tuple[str, str], ...], installed: Version, label: str = ""
) -> tuple[str, tuple[str, ...]]:
    """Lowest valid `fixed` strictly above `installed`, else NO_FIX. `limit` is never a fix."""
    where = f" in {label}" if label else ""
    best: tuple[Version, str] | None = None
    notes: list[str] = []
    for kind, value in events:
        if kind != "fixed":
            continue
        bound = _parse_bound(value)
        if bound is None:
            notes.append(f"unparseable fixed {value!r}{where} ignored for fixed-in")
            continue
        if bound > installed and (best is None or bound < best[0]):
            best = (bound, value)
    return (best[1] if best else NO_FIX), tuple(notes)


def fixed_in_across(
    ranges: list[tuple[tuple[str, str], ...]], installed: Version, label: str = ""
) -> tuple[str, tuple[str, ...]]:
    """Minimum fixed-in over the ranges that match `installed`; non-matching ranges are skipped."""
    best: tuple[Version, str] | None = None
    notes: list[str] = []
    for events in ranges:
        matched, _ = range_matches(events, installed, label)
        if not matched:
            continue
        val, n = fixed_in_for(events, installed, label)
        notes.extend(n)
        if val != NO_FIX:
            v = Version(val)
            if best is None or v < best[0]:
                best = (v, val)
    return (best[1] if best else NO_FIX), tuple(notes)


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
