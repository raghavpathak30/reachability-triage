import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from packaging.utils import canonicalize_name

from reachability.scan.advisories import NO_FIX, MatchResult, Snapshot, build_report, load_snapshot
from reachability.scan.combined import CombinedIndex, build_combined
from reachability.scan.fetch import Fetcher, fetch_dist, fetch_dists
from reachability.scan.inventory import TARGET_ENV, Status, build_inventory
from reachability.scan.lock_wheels import read_lock_wheels
from reachability.scan.models import Finding, Gap, ScanResult, Target
from reachability.scan.query import QueryContext, derive_reason, exact_lookup, make_context
from reachability.scan.reasons import EvidenceLevel, ReasonCode
from reachability.scan.targets import defs_from_tables, defs_from_tree, diff_defs, resolve_targets


def _advisory_identity(snapshot: Snapshot, ids: tuple[str, ...]) -> tuple[str, list[str]]:
    chosen = next((i for i in ids if i.startswith("GHSA-")), ids[0])
    others = set(ids)
    for i in ids:
        rec = snapshot.records.get(i)
        if rec:
            others.update(rec.aliases)
    others.discard(chosen)
    return chosen, sorted(others)


def _unknown(identity, m: MatchResult, code: ReasonCode, *, targets=None, detail=None, truncated=False,
             partial=None) -> Finding:
    advisory_id, aliases = identity
    return Finding(
        advisory_id, aliases, m.dist, m.version, None if m.fixed_in == NO_FIX else m.fixed_in,
        "unknown", None, code.value, None, partial, targets or [], truncated, detail,
    )


def _proven(identity, m: MatchResult, hit, targets) -> Finding:
    advisory_id, aliases = identity
    return Finding(
        advisory_id, aliases, m.dist, m.version, m.fixed_in, hit.verdict,
        EvidenceLevel.PATH_PROVEN.value, None, hit.path, None, targets, False, None,
    )


def run_scan(
    root: Path,
    snapshot_path: Path,
    snapshot_sha256: str,
    fetcher: Fetcher,
    *,
    member: str | None = None,
    cache_dir: Path | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ScanResult:
    t0 = clock()
    snapshot = load_snapshot(Path(snapshot_path), snapshot_sha256)
    inventory = build_inventory(Path(root), member=member)
    report = build_report(inventory, snapshot)
    app_dir = Path(root) / (inventory.app_root or ".")
    gaps: list[Gap] = []
    scratch = tempfile.TemporaryDirectory(prefix="rscan-work-")
    try:
        store = Path(cache_dir) if cache_dir else Path(scratch.name) / "cache"
        # fetch phase
        t_fetch = clock()
        pinned = sorted(
            {
                (canonicalize_name(e.dist), e.version)
                for e in inventory.entries
                if e.status is Status.PINNED and e.dist and e.version
            }
        )
        locks = (
            read_lock_wheels(Path(inventory.root) / inventory.lockfile) if inventory.lockfile == "uv.lock" else {}
        )
        req_hashes = {
            (canonicalize_name(e.dist), e.version): e.hashes
            for e in inventory.entries
            if e.status is Status.PINNED and e.dist and e.version and e.hashes
        }
        fetched = fetch_dists(pinned, fetcher, store, locks=locks, req_hashes=req_hashes)
        gaps.extend(fetched.gaps)
        fetch_seconds = clock() - t_fetch

        # combined index
        t_comb = clock()
        dists = {d: fd.root for (d, _v), fd in fetched.dists.items() if fd.status == "fetched" and fd.root}
        combined = build_combined(app_dir, dists, Path(scratch.name))
        gaps.extend(combined.gaps)
        combine_seconds = clock() - t_comb
        try:
            findings, stats_extra, targets_seconds, query_seconds = _analyse(
                report.findings, snapshot, fetched, combined, fetcher, store, clock
            )
            gaps.extend(stats_extra.pop("gaps"))
            combined_modules = len(combined.index.report.modules)
            combined_edges = len(combined.index.edges)
        finally:
            combined.close()
    finally:
        scratch.cleanup()

    packages = [
        {
            "dist": e.dist,
            "version": e.version,
            "status": e.status.value,
            "wheel_status": _wheel_status(e, fetched),
        }
        for e in inventory.entries
    ]
    malicious = [
        {
            "advisory_id": mal.id,
            "aliases": _advisory_identity(snapshot, tuple(snapshot.groups[mal.group_id].ids))[1],
            "dist": mal.dist,
            "version": mal.version,
            "signal": mal.signal,
        }
        for mal in report.malicious
    ]
    qs = stats_extra.pop("query_seconds_list")
    stats = {
        "wheels_fetched": sum(1 for fd in fetched.dists.values() if fd.status == "fetched"),
        "wheels_no_wheel": sum(1 for fd in fetched.dists.values() if fd.status == "no_wheel"),
        "combined_modules": combined_modules,
        "combined_edges": combined_edges,
        "queries": stats_extra["queries"],
        "query_seconds_median": statistics.median(qs) if qs else 0.0,
        "query_seconds_max": max(qs) if qs else 0.0,
    }
    return ScanResult(
        snapshot_date=snapshot.date.isoformat(),
        snapshot_sha256=snapshot.sha256,
        target_env=TARGET_ENV,
        app_root=inventory.app_root,
        lead=report.lead,
        lockfile=inventory.lockfile,
        hint=inventory.hint,
        packages=packages,
        malicious=malicious,
        findings=findings,
        gaps=gaps,
        closure_complete=not gaps,
        stats=stats,
        timings={
            "fetch_seconds": fetch_seconds,
            "combine_seconds": combine_seconds,
            "build_seconds": combine_seconds,
            "targets_seconds": targets_seconds,
            "query_seconds": query_seconds,
            "total_seconds": clock() - t0,
        },
        notes=list(inventory.notes),
    )


def _wheel_status(entry, fetched) -> str:
    if entry.status is Status.MARKER_SKIPPED:
        return "marker_skipped"
    if entry.status is not Status.PINNED or not entry.dist:
        return "n/a"
    fd = fetched.dists.get((canonicalize_name(entry.dist), entry.version))
    return fd.status if fd else "not_fetched"


def _analyse(matches, snapshot, fetched, combined: CombinedIndex, fetcher, store, clock):
    gaps: list[Gap] = []
    t_targets = clock()
    ordered = sorted(matches, key=lambda m: (m.dist, m.version, _advisory_identity(snapshot, m.ids)[0]))
    ctx: QueryContext = make_context(combined)
    origins = combined.module_origins()
    installed_cache: dict[str, dict] = {}
    fixed_cache: dict[tuple[str, str], dict | None] = {}
    per: list[tuple[MatchResult, tuple, list[Target] | None, Finding | None]] = []
    for m in ordered:
        identity = _advisory_identity(snapshot, m.ids)
        dist = canonicalize_name(m.dist)
        if m.fixed_in == NO_FIX:
            per.append((m, identity, None, _unknown(identity, m, ReasonCode.NO_FIX_VERSION)))
            continue
        fd = fetched.dists.get((dist, m.version))
        if fd is None or fd.status != "fetched":
            per.append((m, identity, None, _unknown(identity, m, ReasonCode.NO_WHEEL)))
            continue
        if dist not in installed_cache:
            mods = {mod for mod, o in origins.items() if o == dist}
            installed_cache[dist] = defs_from_tables(combined.index.symbol_index, mods)
        fkey = (dist, m.fixed_in)
        if fkey not in fixed_cache:
            ffd, fgaps = fetch_dist(dist, m.fixed_in, fetcher, store)
            gaps.extend(fgaps)
            fixed_cache[fkey] = defs_from_tree(ffd.root) if ffd.status == "fetched" and ffd.root else None
        fixed_defs = fixed_cache[fkey]
        if fixed_defs is None:
            per.append((m, identity, None, _unknown(identity, m, ReasonCode.NO_WHEEL_FOR_FIX)))
            continue
        changes = diff_defs(installed_cache[dist], fixed_defs)
        targets = resolve_targets(changes, installed_cache[dist], combined, dist)
        if not targets:
            per.append((m, identity, None, _unknown(identity, m, ReasonCode.NO_CANDIDATE)))
            continue
        per.append((m, identity, targets, None))
    targets_seconds = clock() - t_targets

    t_query = clock()
    findings: list[Finding] = []
    for m, identity, targets, done in per:
        if done is not None:
            findings.append(done)
            continue
        hit = exact_lookup(ctx, targets)
        if hit is not None:
            findings.append(_proven(identity, m, hit, targets))
            continue
        res = derive_reason(ctx, targets, clock=clock)
        findings.append(
            _unknown(identity, m, res.code, targets=targets, detail=res.detail, partial=res.partial_path)
        )
    query_seconds = clock() - t_query
    return findings, {"gaps": gaps, "queries": ctx.queries, "query_seconds_list": ctx.query_seconds}, targets_seconds, query_seconds
