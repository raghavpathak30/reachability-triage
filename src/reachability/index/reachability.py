from collections.abc import Iterable

from .edges_models import CallEdge, Confidence
from .entrypoint_models import Entrypoint
from .reachability_models import ReachabilityResult, Verdict


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _match_target(node_id: str, target_module: str, target_symbol: str | None) -> bool:
    if node_id.startswith("?:"):
        # An unresolved callee carries no module information, so it can never confirm a
        # match against target_module. But every LOW-confidence edge's callee is,
        # structurally, always a "?:..."-shaped dead end (no CallEdge ever has one as a
        # caller_id) — so if a bare "?:"-prefixed id could never match, no low-confidence
        # edge could ever be "on a path that eventually reaches P", making the UNKNOWN
        # verdict's own worked example (an obj.load() hop bridging toward a same-named
        # target) structurally unreachable. A *named* unresolved call (not the nameless
        # "?:<dynamic>" dynamic-dispatch marker) whose name equals target_symbol is
        # exactly that bridge — a plausible, unconfirmed call into the target by name.
        if target_symbol is None:
            return False
        unresolved_name = node_id[len("?:"):]
        return unresolved_name == target_symbol

    target_segments = target_module.split(".")

    if node_id.startswith("ext:"):
        dotted_path = node_id[len("ext:"):]
        segments = dotted_path.split(".")
        if segments[: len(target_segments)] != target_segments:
            return False
        if target_symbol is None:
            return True
        return target_symbol in segments[len(target_segments):]

    # First-party: "module:qualname" (function/class/method) or bare "module" (MODULE node).
    if ":" in node_id:
        module_part, qualname = node_id.split(":", 1)
    else:
        module_part, qualname = node_id, ""

    module_segments = module_part.split(".")
    if module_segments[: len(target_segments)] != target_segments:
        return False

    if target_symbol is None:
        return True
    if not qualname:
        return False  # a bare MODULE node has no symbol to match against target_symbol

    final_segment = qualname.rsplit(".", 1)[-1].split("@")[0]
    return final_segment == target_symbol


def _build_by_caller(edges: list[CallEdge]) -> dict[str, list[CallEdge]]:
    by_caller: dict[str, list[CallEdge]] = {}
    for edge in edges:
        by_caller.setdefault(edge.caller_id, []).append(edge)
    return by_caller


def _bfs(start_ids: list[str], by_caller: dict[str, list[CallEdge]], allow_low: bool) -> dict[str, list[CallEdge]]:
    paths: dict[str, list[CallEdge]] = {}
    visited: set[str] = set()
    queue: list[str] = []

    for start_id in start_ids:
        if start_id in visited:
            continue
        visited.add(start_id)
        paths[start_id] = []
        queue.append(start_id)

    head = 0
    while head < len(queue):
        current = queue[head]
        head += 1
        for edge in by_caller.get(current, []):
            if not allow_low and edge.confidence == Confidence.LOW:
                continue
            if edge.callee_id in visited:
                continue
            visited.add(edge.callee_id)
            paths[edge.callee_id] = paths[current] + [edge]
            queue.append(edge.callee_id)

    return paths


def _find_match(paths: dict[str, list[CallEdge]], target_module: str, target_symbol: str | None) -> str | None:
    for node_id in paths:
        if _match_target(node_id, target_module, target_symbol):
            return node_id
    return None


def compute_reachability(
    target_module: str,
    target_symbol: str | None,
    entrypoints: list[Entrypoint],
    edges: list[CallEdge],
) -> ReachabilityResult:
    if not entrypoints:
        return ReachabilityResult(
            target_module=target_module,
            target_symbol=target_symbol,
            verdict=Verdict.NOT_REACHABLE,
            path=None,
            reason="no entrypoints detected in repository",
        )

    by_caller = _build_by_caller(edges)
    non_test_ids = _dedupe_preserve_order(ep.node_id for ep in entrypoints if not ep.is_test)
    test_ids = _dedupe_preserve_order(ep.node_id for ep in entrypoints if ep.is_test)

    # Step 1: confident BFS from non-test entrypoints only.
    paths = _bfs(non_test_ids, by_caller, allow_low=False)
    match = _find_match(paths, target_module, target_symbol)
    if match is not None:
        return ReachabilityResult(
            target_module=target_module,
            target_symbol=target_symbol,
            verdict=Verdict.REACHABLE,
            path=paths[match],
            reason=None,
        )

    # Step 2: confident BFS from test-only entrypoints.
    paths = _bfs(test_ids, by_caller, allow_low=False)
    match = _find_match(paths, target_module, target_symbol)
    if match is not None:
        return ReachabilityResult(
            target_module=target_module,
            target_symbol=target_symbol,
            verdict=Verdict.REACHABLE_ONLY_FROM_TESTS,
            path=paths[match],
            reason=None,
        )

    # Step 3: any-confidence BFS from all entrypoints combined. A match here is
    # guaranteed to carry >=1 low-confidence hop — if it didn't, step 1 or 2 would
    # already have matched it.
    combined_ids = _dedupe_preserve_order(non_test_ids + test_ids)
    paths = _bfs(combined_ids, by_caller, allow_low=True)
    match = _find_match(paths, target_module, target_symbol)
    if match is not None:
        path = paths[match]
        low_rules = sorted({edge.resolution_rule.value for edge in path if edge.confidence == Confidence.LOW})
        reason = f"reachable only via low-confidence edge(s): {', '.join(low_rules)}"
        return ReachabilityResult(
            target_module=target_module,
            target_symbol=target_symbol,
            verdict=Verdict.UNKNOWN,
            path=path,
            reason=reason,
        )

    # Step 4: no path found even ignoring confidence.
    target = f"{target_module}:{target_symbol}" if target_symbol else target_module
    return ReachabilityResult(
        target_module=target_module,
        target_symbol=target_symbol,
        verdict=Verdict.NOT_REACHABLE,
        path=None,
        reason=f"no call path found from any entrypoint to {target}",
    )
