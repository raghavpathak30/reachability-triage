import re
from collections.abc import Callable
from dataclasses import dataclass, field

from reachability.index.edges_models import CallEdge
from reachability.index.entrypoint_models import Entrypoint
from reachability.index.reachability import _bfs, _build_by_caller, compute_reachability
from reachability.index.reachability_models import ReachabilityResult, Verdict
from reachability.scan.combined import CombinedIndex
from reachability.scan.models import PathEdge, Target
from reachability.scan.reasons import ReasonCode, aggregate_reasons, classify_engine_reason


@dataclass
class QueryContext:
    combined: CombinedIndex
    entrypoints: list[Entrypoint]
    by_caller: dict[str, list[CallEdge]]
    paths_non_test: dict[str, list[CallEdge]]
    paths_test: dict[str, list[CallEdge]]
    queries: int = 0
    query_seconds: list[float] = field(default_factory=list)


def make_context(combined: CombinedIndex) -> QueryContext:
    app_modules = combined.app_modules
    entrypoints = [ep for ep in combined.index.entrypoints if ep.module in app_modules]
    by_caller = _build_by_caller(combined.index.edges)
    non_test = list(dict.fromkeys(ep.node_id for ep in entrypoints if not ep.is_test))
    test = list(dict.fromkeys(ep.node_id for ep in entrypoints if ep.is_test))
    return QueryContext(
        combined,
        entrypoints,
        by_caller,
        _bfs(non_test, by_caller, allow_low=False),
        _bfs(test, by_caller, allow_low=False),
    )


def to_path(ctx: QueryContext, edges: list[CallEdge]) -> list[PathEdge]:
    return [
        PathEdge(ctx.combined.display_path(e.file), e.lineno, e.caller_id, e.callee_id) for e in edges
    ]


@dataclass(frozen=True)
class ExactHit:
    verdict: str  # reachable | reachable_only_from_tests
    target: Target
    path: list[PathEdge]


def exact_lookup(ctx: QueryContext, targets: list[Target]) -> ExactHit | None:
    """The target's exact node id must be reached by the confident-edge BFS."""
    best: tuple[tuple[int, int, str], ExactHit] | None = None
    for t in targets:
        if t.node_id in ctx.paths_non_test:
            verdict, edges = "reachable", ctx.paths_non_test[t.node_id]
        elif t.node_id in ctx.paths_test:
            verdict, edges = "reachable_only_from_tests", ctx.paths_test[t.node_id]
        else:
            continue
        if not edges:
            continue
        rank = (0 if verdict == "reachable" else 1, len(edges), t.node_id)
        if best is None or rank < best[0]:
            best = (rank, ExactHit(verdict, t, to_path(ctx, edges)))
    return best[1] if best else None


def _clean(ctx: QueryContext, text: str) -> str:
    root = re.escape(str(ctx.combined.root))
    return re.sub(root + r"/([^\s:]+)", lambda m: ctx.combined.display.get(m.group(1), m.group(1)), text)


def classify_result(result: ReachabilityResult) -> tuple[ReasonCode, str | None, bool]:
    """Map one engine result to (code, detail, path_is_partial). Never a confident negative."""
    if result.verdict in (Verdict.REACHABLE, Verdict.REACHABLE_ONLY_FROM_TESTS):
        matched = result.path[-1].callee_id if result.path else "?"
        return ReasonCode.NO_PATH_FOUND, f"engine name match hit a different node: {matched}", False
    if result.verdict is Verdict.NOT_REACHABLE:
        return ReasonCode.NO_PATH_FOUND, None, False
    code = classify_engine_reason(result.reason)
    if code is None:
        return ReasonCode.NO_PATH_FOUND, f"unmapped engine reason: {result.reason}", result.path is not None
    return code, None, result.path is not None


@dataclass(frozen=True)
class ReasonResult:
    code: ReasonCode
    detail: str | None
    partial_path: list[PathEdge] | None


def derive_reason(
    ctx: QueryContext,
    targets: list[Target],
    can_continue: Callable[[], bool] = lambda: True,
    clock: Callable[[], float] | None = None,
) -> ReasonResult | None:
    """Engine reason for an advisory with no exact hit; None if the budget stopped it."""
    if not ctx.entrypoints:
        return ReasonResult(ReasonCode.NO_APP_ENTRYPOINTS, None, None)
    codes: list[ReasonCode] = []
    details: dict[ReasonCode, str] = {}
    partials: dict[ReasonCode, list[PathEdge]] = {}
    for t in targets:
        if not can_continue():
            return None
        start = clock() if clock else 0.0
        result = compute_reachability(
            t.module, t.qualname.split(".")[-1], ctx.entrypoints, ctx.combined.index.edges, ctx.combined.index.report
        )
        ctx.queries += 1
        if clock:
            ctx.query_seconds.append(clock() - start)
        code, detail, partial = classify_result(result)
        codes.append(code)
        if detail and code not in details:
            details[code] = _clean(ctx, detail)
        if partial and result.path and code not in partials:
            partials[code] = to_path(ctx, result.path)
    code = aggregate_reasons(codes)
    return ReasonResult(code, details.get(code), partials.get(code))
