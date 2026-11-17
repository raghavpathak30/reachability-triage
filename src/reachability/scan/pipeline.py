import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from packaging.utils import canonicalize_name

from reachability.scan.advisories import NO_FIX, MatchResult, Snapshot, build_report, load_snapshot
from reachability.scan.combined import CombinedIndex, build_combined
from reachability.scan.fetch import Fetcher, FetchBudget, fetch_dist, fetch_dists
from reachability.scan.inventory import TARGET_ENV, Status, build_inventory
from reachability.scan.lock_wheels import read_lock_wheels
from reachability.scan.models import FetchedDist, Finding, Gap, ScanResult, Target
from reachability.scan.query import QueryContext, derive_reason, exact_lookup, make_context
from reachability.scan.reasons import EvidenceLevel, ReasonCode
from reachability.scan.targets import (
    diff_symbols,
    non_py_changed,
    resolve_targets,
    symbols_from_tables,
    symbols_from_tree,
)


ANALYSIS_SECONDS = 600.0
MAX_ADVISORIES = 200


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


def _proven(identity, m: MatchResult, hit, targets, truncated=False) -> Finding:
    advisory_id, aliases = identity
    return Finding(
        advisory_id, aliases, m.dist, m.version, m.fixed_in, hit.verdict,
        EvidenceLevel.PATH_PROVEN.value, None, hit.path, None, targets, truncated, None,
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
    analysis_seconds: float = ANALYSIS_SECONDS,
    max_advisories: int = MAX_ADVISORIES,
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
        matched = {canonicalize_name(m.dist) for m in report.findings}
        pinned = sorted(
            {
                (canonicalize_name(e.dist), e.version)
                for e in inventory.entries
                if e.status is Status.PINNED and e.dist and e.version
            },
            key=lambda dv: (dv[0] not in matched, dv[0], dv[1]),
        )
        budget = FetchBudget(clock=clock)
        locks = (
            read_lock_wheels(Path(inventory.root) / inventory.lockfile) if inventory.lockfile == "uv.lock" else {}
        )
        req_hashes = {
            (canonicalize_name(e.dist), e.version): e.hashes
            for e in inventory.entries
            if e.status is Status.PINNED and e.dist and e.version and e.hashes
        }
        matched_pairs = [dv for dv in pinned if dv[0] in matched]
        rest_pairs = [dv for dv in pinned if dv[0] not in matched]
        fetched = fetch_dists(matched_pairs, fetcher, store, locks=locks, req_hashes=req_hashes, budget=budget)
        fix_fetched: dict[tuple[str, str], FetchedDist] = {}
        for m in sorted(report.findings, key=lambda m: (m.dist, m.version, m.fixed_in)):
            dist = canonicalize_name(m.dist)
            fd = fetched.dists.get((dist, m.version))
            if m.fixed_in == NO_FIX or fd is None or fd.status != "fetched" or (dist, m.fixed_in) in fix_fetched:
                continue
            ffd, fgaps = fetch_dist(dist, m.fixed_in, fetcher, store, budget=budget)
            fix_fetched[(dist, m.fixed_in)] = ffd
            fetched.gaps.extend(fgaps)
        rest = fetch_dists(rest_pairs, fetcher, store, locks=locks, req_hashes=req_hashes, budget=budget)
        fetched.dists.update(rest.dists)
        fetched.gaps.extend(rest.gaps)
        gaps.extend(fetched.gaps)
        fetch_seconds = clock() - t_fetch

        # combined index
        t_comb = clock()
        analysis_start = t_comb
        dists = {d: fd.root for (d, _v), fd in fetched.dists.items() if fd.status == "fetched" and fd.root}
        combined = build_combined(app_dir, dists, Path(scratch.name))
        gaps.extend(combined.gaps)
        combine_seconds = clock() - t_comb
        try:
            findings, stats_extra, targets_seconds, query_seconds = _analyse(
                report.findings, snapshot, fetched, fix_fetched, combined, clock,
                analysis_start, analysis_seconds, max_advisories,
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


def _analyse(matches, snapshot, fetched, fix_fetched, combined: CombinedIndex, clock,
             analysis_start, analysis_seconds, max_advisories):
    gaps: list[Gap] = []
    t_targets = clock()

    def within_time() -> bool:
        return clock() - analysis_start < analysis_seconds

    ordered = sorted(matches, key=lambda m: (m.dist, m.version, _advisory_identity(snapshot, m.ids)[0]))
    ctx: QueryContext | None = None
    ctx_failure = ""
    try:
        ctx = make_context(combined)
        origins = combined.module_origins()
    except Exception as exc:
        ctx = None
        ctx_failure = type(exc).__name__
        origins = {}
        gaps.append(Gap("dist_error", None, f"shared analysis context: {ctx_failure}"))
    installed_cache: dict[str, object] = {}
    fixed_cache: dict[tuple[str, str], tuple[object, Path] | None] = {}
    diff_failed: set[tuple[str, str]] = set()
    fix_cut: dict[tuple[str, str], str] = {}
    per: list[tuple[MatchResult, tuple, list[Target], bool, Finding | None]] = []

    def done(m, identity, code, detail=None):
        per.append((m, identity, [], False, _unknown(identity, m, code, detail=detail)))

    def step(m, identity):
        dist = canonicalize_name(m.dist)
        if m.fixed_in == NO_FIX:
            return done(m, identity, ReasonCode.NO_FIX_VERSION)
        fd = fetched.dists.get((dist, m.version))
        if fd is None or fd.status != "fetched":
            return done(m, identity, ReasonCode.NO_WHEEL)
        if not within_time():
            return done(m, identity, ReasonCode.SCAN_BUDGET,
                        f"not analysed: analysis time cap reached ({analysis_seconds:.0f} s)")
        if dist not in installed_cache:
            mods = {mod for mod, o in origins.items() if o == dist}
            try:
                installed_cache[dist] = symbols_from_tables(combined.index.symbol_index, mods)
            except Exception as exc:
                installed_cache[dist] = None
                gaps.append(Gap("dist_error", dist, f"{dist}=={m.version}: {type(exc).__name__} while indexing"))
        installed = installed_cache[dist]
        if installed is None:
            return done(m, identity, ReasonCode.NO_CANDIDATE)
        fkey = (dist, m.fixed_in)
        if fkey not in fixed_cache:
            ffd = fix_fetched[fkey]
            fixed_cache[fkey] = None
            if ffd.status == "budget":
                fix_cut[fkey] = ffd.reason or "fetch budget reached"
            if ffd.status == "fetched" and ffd.root:
                try:
                    fixed_cache[fkey] = (symbols_from_tree(ffd.root), ffd.root)
                except Exception as exc:
                    gaps.append(Gap("dist_error", dist, f"{dist}=={m.fixed_in}: {type(exc).__name__} while indexing"))
                    diff_failed.add(fkey)
        fixed = fixed_cache[fkey]
        if fixed is None:
            if fkey in fix_cut:
                return done(m, identity, ReasonCode.SCAN_BUDGET,
                            f"fix wheel {dist}=={m.fixed_in} not fetched: {fix_cut[fkey]}")
            return done(m, identity, ReasonCode.NO_CANDIDATE if fkey in diff_failed else ReasonCode.NO_WHEEL_FOR_FIX)
        if not within_time():
            return done(m, identity, ReasonCode.SCAN_BUDGET,
                        f"not analysed: analysis time cap reached ({analysis_seconds:.0f} s)")
        try:
            diff = diff_symbols(installed, fixed[0])
            targets, truncated = resolve_targets(diff.changes, installed, combined, dist)
            native = (
                not targets and not diff.py_changed and non_py_changed(fd.root, fixed[1])
            )
        except Exception as exc:
            gaps.append(Gap("dist_error", dist, f"{dist}=={m.version}: {type(exc).__name__} while diffing"))
            return done(m, identity, ReasonCode.NO_CANDIDATE)
        if not targets:
            return done(m, identity, ReasonCode.NATIVE_ONLY_CHANGE if native else ReasonCode.NO_CANDIDATE)
        per.append((m, identity, targets, truncated, None))

    def failed(m, identity, exc, phase):
        gaps.append(Gap("dist_error", canonicalize_name(m.dist),
                        f"{identity[0]}: {type(exc).__name__} during {phase}"))
        return _unknown(identity, m, ReasonCode.NO_CANDIDATE,
                        detail=f"analysis failed: {type(exc).__name__} during {phase}")

    for m in ordered:
        identity = _advisory_identity(snapshot, m.ids)
        size = len(per)
        try:
            step(m, identity)
        except Exception as exc:
            del per[size:]
            per.append((m, identity, [], False, failed(m, identity, exc, "target analysis")))
    targets_seconds = clock() - t_targets

    t_query = clock()
    # Phase A: exact-node lookup for every advisory; never cut by the budget.
    results: dict[int, Finding] = {}
    pending: list[int] = []
    for i, (m, identity, targets, truncated, finished) in enumerate(per):
        if finished is not None:
            results[i] = finished
        elif ctx is None:
            results[i] = _unknown(identity, m, ReasonCode.NO_CANDIDATE, targets=targets, truncated=truncated,
                                  detail=f"analysis context unavailable: {ctx_failure}")
        else:
            try:
                hit = exact_lookup(ctx, targets)
            except Exception as exc:
                results[i] = failed(m, identity, exc, "exact lookup")
                continue
            if hit is not None:
                results[i] = _proven(identity, m, hit, targets, truncated)
            else:
                pending.append(i)

    # Phase B: engine reason queries, clock checked before each one.
    # The advisory cap counts only advisories that still need a query.
    for n, i in enumerate(pending):
        m, identity, targets, truncated, _ = per[i]
        res = None
        why = f"advisory cap reached ({max_advisories} per scan)"
        try:
            if n < max_advisories:
                res = derive_reason(ctx, targets, within_time, clock)
                why = f"analysis time cap reached ({analysis_seconds:.0f} s)"
        except Exception as exc:
            results[i] = failed(m, identity, exc, "reason query")
            continue
        if res is None:
            results[i] = _unknown(identity, m, ReasonCode.SCAN_BUDGET, targets=targets, truncated=truncated,
                                  detail=f"not analysed: {why}")
        else:
            results[i] = _unknown(identity, m, res.code, targets=targets, detail=res.detail,
                                  partial=res.partial_path, truncated=truncated)
    findings = [results[i] for i in range(len(per))]
    query_seconds = clock() - t_query
    qn = ctx.queries if ctx else 0
    ql = ctx.query_seconds if ctx else []
    return findings, {"gaps": gaps, "queries": qn, "query_seconds_list": ql}, targets_seconds, query_seconds
