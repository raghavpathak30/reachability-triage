from collections.abc import Iterable, Sequence

from .edges import collect_load_referenced_names
from .edges_models import CallEdge, Confidence
from .entrypoint_models import Entrypoint
from .models import DiscoveryReport
from .reachability_models import ReachabilityResult, Verdict

NO_ENTRYPOINTS_REASON = "no entrypoints detected; reachability cannot be determined without an entrypoint"
LOW_CONFIDENCE_REASON_PREFIX = "reachable only via low-confidence edge(s): "
OPAQUE_CALL_REASON_PREFIX = "a reachable opaque call ("
NAME_ESCAPE_REASON_TEMPLATE = (
    "'{target_symbol}' is referenced by name outside any call position elsewhere in the "
    "repo (e.g. passed as a callback, stored in a container, or reassigned); a confident "
    "not_reachable verdict cannot be given"
)


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


_OPAQUE_IDS = {"?:<dynamic>", "ext:builtins.eval", "ext:builtins.exec"}


class ReachabilityBatch:
    """Target-independent work (D3 name set, by_caller, three BFS runs, opaque hit) done once.

    `query` applies steps 1-6 for one target in the same order and with the same texts as
    the original single-target function. Everything cached is computed lazily on first use.
    """

    def __init__(self, entrypoints: list[Entrypoint], edges: list[CallEdge], report: DiscoveryReport) -> None:
        self._entrypoints = entrypoints
        self._edges = edges
        self._report = report
        self._cache: dict[str, object] = {}

    def _once(self, key: str, make):
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    def _referenced(self) -> set[str]:
        return self._once("d3", lambda: collect_load_referenced_names(self._report))

    def _by_caller(self) -> dict[str, list[CallEdge]]:
        return self._once("by_caller", lambda: _build_by_caller(self._edges))

    def _non_test_ids(self) -> list[str]:
        return self._once(
            "non_test_ids", lambda: _dedupe_preserve_order(ep.node_id for ep in self._entrypoints if not ep.is_test)
        )

    def _test_ids(self) -> list[str]:
        return self._once(
            "test_ids", lambda: _dedupe_preserve_order(ep.node_id for ep in self._entrypoints if ep.is_test)
        )

    def _paths_confident_non_test(self) -> dict[str, list[CallEdge]]:
        return self._once("p1", lambda: _bfs(self._non_test_ids(), self._by_caller(), allow_low=False))

    def _paths_confident_test(self) -> dict[str, list[CallEdge]]:
        return self._once("p2", lambda: _bfs(self._test_ids(), self._by_caller(), allow_low=False))

    def _paths_any(self) -> dict[str, list[CallEdge]]:
        def make():
            combined_ids = _dedupe_preserve_order(self._non_test_ids() + self._test_ids())
            return _bfs(combined_ids, self._by_caller(), allow_low=True)

        return self._once("p3", make)

    def _opaque_hit(self) -> str | None:
        paths = self._paths_any()
        return self._once("opaque", lambda: next((oid for oid in _OPAQUE_IDS if oid in paths), None))

    def prepare(self) -> None:
        """Force every target-independent computation now, so callers can time it separately."""
        if not self._entrypoints:
            return
        self._referenced()
        self._opaque_hit()
        self._paths_confident_non_test()
        self._paths_confident_test()

    def query(self, target_module: str, target_symbol: str | None) -> ReachabilityResult:
        if not self._entrypoints:
            return ReachabilityResult(
                target_module=target_module,
                target_symbol=target_symbol,
                verdict=Verdict.UNKNOWN,
                path=None,
                reason=NO_ENTRYPOINTS_REASON,
            )

        # Step 1: confident BFS from non-test entrypoints only.
        paths = self._paths_confident_non_test()
        match = _find_match(paths, target_module, target_symbol)
        if match is not None:
            return ReachabilityResult(
                target_module=target_module,
                target_symbol=target_symbol,
                verdict=Verdict.REACHABLE,
                path=list(paths[match]),
                reason=None,
            )

        # Step 2: confident BFS from test-only entrypoints.
        paths = self._paths_confident_test()
        match = _find_match(paths, target_module, target_symbol)
        if match is not None:
            return ReachabilityResult(
                target_module=target_module,
                target_symbol=target_symbol,
                verdict=Verdict.REACHABLE_ONLY_FROM_TESTS,
                path=list(paths[match]),
                reason=None,
            )

        # Step 3: any-confidence BFS from all entrypoints combined. A match here is
        # guaranteed to carry >=1 low-confidence hop.
        paths = self._paths_any()
        match = _find_match(paths, target_module, target_symbol)
        if match is not None:
            path = list(paths[match])
            low_rules = sorted({edge.resolution_rule.value for edge in path if edge.confidence == Confidence.LOW})
            reason = LOW_CONFIDENCE_REASON_PREFIX + ", ".join(low_rules)
            return ReachabilityResult(
                target_module=target_module,
                target_symbol=target_symbol,
                verdict=Verdict.UNKNOWN,
                path=path,
                reason=reason,
            )

        # Step 4: a reachable opaque call (nameless getattr dispatch, eval/exec) makes a
        # NOT_REACHABLE verdict untrustworthy; repo-wide, not scoped to the target.
        opaque_hit = self._opaque_hit()
        if opaque_hit is not None:
            opaque_edge = paths[opaque_hit][-1]
            return ReachabilityResult(
                target_module=target_module,
                target_symbol=target_symbol,
                verdict=Verdict.UNKNOWN,
                path=list(paths[opaque_hit]),
                reason=(
                    f"{OPAQUE_CALL_REASON_PREFIX}{opaque_hit}) at {opaque_edge.file}:{opaque_edge.lineno} "
                    "carries no literal target name, so a not_reachable verdict cannot be trusted"
                ),
            )

        # Step 5: the target's name is loaded outside a call position somewhere in the repo.
        if target_symbol is not None and target_symbol in self._referenced():
            return ReachabilityResult(
                target_module=target_module,
                target_symbol=target_symbol,
                verdict=Verdict.UNKNOWN,
                path=None,
                reason=NAME_ESCAPE_REASON_TEMPLATE.format(target_symbol=target_symbol),
            )

        # Step 6: no path found even ignoring confidence, and nothing to blame it on.
        target = f"{target_module}:{target_symbol}" if target_symbol else target_module
        return ReachabilityResult(
            target_module=target_module,
            target_symbol=target_symbol,
            verdict=Verdict.NOT_REACHABLE,
            path=None,
            reason=f"no call path found from any entrypoint to {target}",
        )


def compute_reachability_batch(
    targets: Sequence[tuple[str, str | None]],
    entrypoints: list[Entrypoint],
    edges: list[CallEdge],
    report: DiscoveryReport,
) -> list[ReachabilityResult]:
    batch = ReachabilityBatch(entrypoints, edges, report)
    return [batch.query(module, symbol) for module, symbol in targets]


def compute_reachability(
    target_module: str,
    target_symbol: str | None,
    entrypoints: list[Entrypoint],
    edges: list[CallEdge],
    report: DiscoveryReport,
) -> ReachabilityResult:
    return compute_reachability_batch([(target_module, target_symbol)], entrypoints, edges, report)[0]
