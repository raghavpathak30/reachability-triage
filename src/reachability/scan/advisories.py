"""OSV PyPI snapshot loading and advisory matching (unit A1a)."""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import logging
import re
import zipfile
import zlib
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


Events = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class AffectedEntry:
    dist: str
    versions: tuple[str, ...]
    ranges: tuple[Events, ...] = ()
    lkavr: str | None = None
    ignored_range_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class Record:
    id: str
    aliases: tuple[str, ...]
    affected: tuple[AffectedEntry, ...]
    cwe_ids: tuple[str, ...] = ()


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
    by_dist: dict[str, list[tuple[str, AffectedEntry]]] = field(default_factory=dict, repr=False)
    ignored_range_types: dict[str, int] = field(default_factory=dict)


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


def _parse_bound(value: str) -> Version | None:
    try:
        return Version(value)
    except (InvalidVersion, ValueError):
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
    parsed.sort(key=lambda p: (0, Version("0")) if p[0] is None else (1, p[0]))
    affected = False
    is_open = False  # structural: an interval is open between an introduced and its terminator
    i = 0
    while i < len(parsed):
        j = i
        while j < len(parsed) and parsed[j][0] == parsed[i][0]:
            j += 1
        group = parsed[i:j]
        # Open interval: close it before a new one starts here (fixed X, introduced X stays
        # affected from X). Closed: introduced first, so introduced X + fixed X is empty.
        terminators = [e for e in group if e[1] != "introduced"]
        introduced = [e for e in group if e[1] == "introduced"]
        for bound, kind in (terminators + introduced) if is_open else (introduced + terminators):
            if kind == "introduced":
                is_open = True
                if bound is None or installed >= bound:
                    affected = True
            else:
                is_open = False
                if kind == "last_affected":
                    if installed > bound:
                        affected = False
                elif installed >= bound:
                    affected = False
        i = j
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


_LKAVR_CLAUSE = re.compile(r"^\s*(<=|<|>=|>|==)\s*(\S+)\s*$")


def lkavr_matches(text: str, installed: Version) -> bool | None:
    """True/False for `<op> <version>[, ...]` clauses (all must hold); None if unparseable."""
    clauses = text.split(",")
    for clause in clauses:
        m = _LKAVR_CLAUSE.match(clause)
        if not m:
            return None
        bound = _parse_bound(m.group(2))
        if bound is None:
            return None
    result = True
    for clause in clauses:
        m = _LKAVR_CLAUSE.match(clause)
        bound = Version(m.group(2))
        op = m.group(1)
        ok = {
            "<=": installed <= bound,
            "<": installed < bound,
            ">=": installed >= bound,
            ">": installed > bound,
            "==": installed == bound,
        }[op]
        result = result and ok
    return result


@dataclass(frozen=True)
class EntryResult:
    matched: bool
    fixed_in: str
    notes: tuple[str, ...]


def entry_matches(entry: AffectedEntry, installed: str, label: str = "") -> EntryResult:
    """Union of: `versions` list, ECOSYSTEM ranges, last_known_affected_version_range.

    GIT and SEMVER ranges never contribute. Uses the public part of `installed`."""
    full = Version(installed)
    pub = Version(full.public)
    notes: list[str] = []
    matched = False

    for item in entry.versions:
        iv = _parse_bound(item)
        if iv is not None and iv == pub:
            matched = True
            break

    for events in entry.ranges:
        ok, n = range_matches(events, pub, label)
        if ok:
            matched = True
        notes.extend(n)

    lk_reason = False
    if entry.lkavr is not None:
        verdict = lkavr_matches(entry.lkavr, pub)
        if verdict is None:
            notes.append(
                f"unparseable last_known_affected_version_range {entry.lkavr!r}"
                + (f" in {label}" if label else "")
                + "; matched conservatively"
            )
            matched = True
        elif verdict and not matched:
            matched = True
            lk_reason = True
    if lk_reason:
        notes.append(f"last_known_affected_version_range {entry.lkavr!r} matched")
    if not matched:
        return EntryResult(False, NO_FIX, ())
    for t in dict.fromkeys(entry.ignored_range_types):
        notes.append(f"{t} range ignored")
    if full.local:
        notes.append(f"local version segment +{full.local} ignored for matching")
    fixed, fixed_notes = fixed_in_across(list(entry.ranges), pub, label)
    notes.extend(fixed_notes)
    return EntryResult(True, fixed, tuple(dict.fromkeys(notes)))


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
        if any(m.startswith("MAL-") for m in members):
            signal: str | None = "mal_prefix"
        elif any("CWE-506" in records[rid].cwe_ids for rid in ids):
            signal = "cwe_506"
        else:
            signal = None
        groups[rep] = AdvisoryGroup(rep, ids, signal)
        for rid in ids:
            group_of[rid] = rep
    return dict(sorted(groups.items())), group_of


def _bad_shape(member: str, field: str, expected: str, value: object) -> SnapshotError:
    return SnapshotError(
        f"snapshot member {member!r}: {field} must be {expected}, got {type(value).__name__}"
    )


def _opt_str_list(member: str, field: str, value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise _bad_shape(member, field, "a list of strings", value)
    return tuple(value)


def _opt_dict(member: str, field: str, value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _bad_shape(member, field, "an object", value)
    return value


def _reduce(raw: dict, member: str = "?") -> Record:
    """Reduce an OSV record. A wrong-shaped field raises SnapshotError naming the member;
    nothing is silently dropped. Unparseable range *contents* still match conservatively."""
    entries = []
    affected = raw.get("affected")
    if affected is not None and not isinstance(affected, list):
        raise _bad_shape(member, "'affected'", "a list", affected)
    for a in affected or []:
        if not isinstance(a, dict):
            raise _bad_shape(member, "an 'affected' entry", "an object", a)
        pkg = _opt_dict(member, "'affected[].package'", a.get("package"))
        if pkg.get("ecosystem") != "PyPI":
            continue
        raw_ranges = a.get("ranges")
        if raw_ranges is not None and not isinstance(raw_ranges, list):
            raise _bad_shape(member, "'affected[].ranges'", "a list", raw_ranges)
        ranges: list[Events] = []
        ignored: list[str] = []
        for rg in raw_ranges or []:
            if not isinstance(rg, dict):
                raise _bad_shape(member, "a 'ranges' entry", "an object", rg)
            rtype = rg.get("type")
            if rtype == "ECOSYSTEM":
                ranges.append(reduce_events(rg.get("events")))
            else:
                ignored.append(str(rtype))
        versions = _opt_str_list(member, "'affected[].versions'", a.get("versions"))
        dbs = _opt_dict(member, "'affected[].database_specific'", a.get("database_specific"))
        lk = dbs.get("last_known_affected_version_range")
        entries.append(
            AffectedEntry(
                dist=str(canonicalize_name(str(pkg.get("name", "")))),
                versions=versions,
                ranges=tuple(ranges),
                lkavr=lk if lk is None or isinstance(lk, str) else repr(lk),
                ignored_range_types=tuple(ignored),
            )
        )
    cwe = _opt_str_list(
        member, "'database_specific.cwe_ids'",
        _opt_dict(member, "'database_specific'", raw.get("database_specific")).get("cwe_ids"),
    )
    return Record(
        raw["id"],
        _opt_str_list(member, "'aliases'", raw.get("aliases")),
        tuple(entries),
        cwe,
    )


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
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SnapshotError(f"cannot read snapshot {path}: {exc}") from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256.lower():
        raise SnapshotHashMismatchError(
            f"snapshot sha256 {actual} does not match expected {expected_sha256}"
        )
    records: dict[str, Record] = {}
    seen_ids: dict[str, str] = {}
    withdrawn = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                if not name.endswith(".json"):
                    continue
                try:
                    raw = json.loads(z.read(name))
                except (
                    ValueError,
                    zipfile.BadZipFile,
                    zlib.error,
                    RuntimeError,  # encrypted member; RecursionError is a RuntimeError
                    NotImplementedError,  # unsupported compression method
                    EOFError,
                    OSError,
                ) as exc:
                    raise SnapshotError(
                        f"snapshot member {name!r} cannot be read as JSON ({type(exc).__name__}): {exc}"
                    ) from exc
                if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                    raise SnapshotError(f"snapshot member {name!r} is not an OSV record with an id")
                if raw["id"] in seen_ids:
                    raise SnapshotError(
                        f"snapshot member {name!r} repeats record id {raw['id']!r} "
                        f"already read from {seen_ids[raw['id']]!r}"
                    )
                seen_ids[raw["id"]] = name
                if "withdrawn" in raw:
                    withdrawn += 1
                    continue
                records[raw["id"]] = _reduce(raw, name)
    except zipfile.BadZipFile as exc:
        raise SnapshotError(f"snapshot is not a readable zip: {exc}") from exc
    groups, group_of = _build_groups(records)
    by_dist: dict[str, list[tuple[str, AffectedEntry]]] = {}
    ignored: dict[str, int] = {}
    for rec in records.values():
        for entry in rec.affected:
            by_dist.setdefault(entry.dist, []).append((rec.id, entry))
            for t in entry.ignored_range_types:
                ignored[t] = ignored.get(t, 0) + 1
    return Snapshot(
        str(path), date, actual, len(records), records, withdrawn, groups, group_of, by_dist, ignored
    )


@dataclass(frozen=True)
class MatchResult:
    dist: str
    version: str
    group_id: str
    ids: tuple[str, ...]
    fixed_in: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaliciousEntry:
    id: str
    group_id: str
    dist: str
    version: str
    signal: str


@dataclass(frozen=True)
class UnpinnedSummary:
    packages: int
    advisories_any_version: int
    per_dist: dict[str, int] = field(default_factory=dict)


LEAD_TEMPLATE = "{n} packages are not pinned; they have {m} known advisories (any version). Pin them to check."


def summarise_unpinned(inventory: Inventory, snapshot: Snapshot) -> UnpinnedSummary:
    """Count, never match: distinct non-malicious groups with any PyPI entry for an unpinned dist."""
    unpinned = [e for e in inventory.entries if e.status is Status.UNPINNED and e.dist is not None]
    dists = {e.dist for e in unpinned}
    groups: set[str] = set()
    per_dist: dict[str, set[str]] = {}
    for dist in sorted(dists):
        for rec_id, _ in snapshot.by_dist.get(dist, []):
            gid = snapshot.group_of[rec_id]
            if snapshot.groups[gid].malicious_signal:
                continue
            groups.add(gid)
            per_dist.setdefault(dist, set()).add(gid)
    return UnpinnedSummary(len(unpinned), len(groups), {d: len(g) for d, g in per_dist.items()})


@dataclass(frozen=True)
class ScanReport:
    snapshot: Snapshot
    inventory: Inventory
    findings: tuple[MatchResult, ...]
    malicious: tuple[MaliciousEntry, ...] = ()
    unpinned: UnpinnedSummary = field(default_factory=lambda: UnpinnedSummary(0, 0))
    lead: str | None = None


def build_report(inventory: Inventory, snapshot: Snapshot) -> ScanReport:
    findings: list[MatchResult] = []
    malicious: list[MaliciousEntry] = []
    seen: set[tuple[str, str]] = set()
    for entry in inventory.entries:
        if entry.status is not Status.PINNED or entry.dist is None or entry.version is None:
            continue
        key = (entry.dist, entry.version)
        if key in seen:
            continue
        seen.add(key)
        by_group: dict[str, list[tuple[str, EntryResult]]] = {}
        for rec_id, aff in snapshot.by_dist.get(entry.dist, []):
            res = entry_matches(aff, entry.version, rec_id)
            if res.matched:
                by_group.setdefault(snapshot.group_of[rec_id], []).append((rec_id, res))
        for group_id in sorted(by_group):
            members = by_group[group_id]
            signal = snapshot.groups[group_id].malicious_signal
            if signal:
                matched_ids = sorted({rid for rid, _ in members})
                chosen = next((i for i in matched_ids if i.startswith("MAL-")), matched_ids[0])
                malicious.append(MaliciousEntry(chosen, group_id, entry.dist, entry.version, signal))
                continue
            fixes = [Version(r.fixed_in) for _, r in members if r.fixed_in != NO_FIX]
            fixed = NO_FIX
            if fixes:
                low = min(fixes)
                fixed = next(r.fixed_in for _, r in members if r.fixed_in != NO_FIX and Version(r.fixed_in) == low)
            notes = tuple(dict.fromkeys(n for _, r in members for n in r.notes))
            ids = tuple(sorted({rid for rid, _ in members}))
            findings.append(MatchResult(entry.dist, entry.version, group_id, ids, fixed, notes))
    summary = summarise_unpinned(inventory, snapshot)
    lead = (
        LEAD_TEMPLATE.format(n=summary.packages, m=summary.advisories_any_version)
        if summary.packages > 0
        else None
    )
    return ScanReport(snapshot, inventory, tuple(findings), tuple(malicious), summary, lead)
