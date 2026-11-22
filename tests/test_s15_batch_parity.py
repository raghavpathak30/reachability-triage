"""Batch parity: compute_reachability_batch vs a frozen verbatim copy of the pre-S1.5 engine.

The copy below is taken from src/reachability/index/reachability.py at commit 421fd93
(only the entry-point function is renamed). It imports models/constants and the frozen
collect_load_referenced_names, never the code under test.
"""

from collections.abc import Iterable
from pathlib import Path

import pytest
from conftest import write_tree

from reachability.agent.eval_harness import (
    EVAL_FIXTURES_ROOTS,
    _module_dotted_name,
    discover_eval_fixtures,
    load_label,
    resolve_target,
)
from reachability.index.edges import collect_load_referenced_names
from reachability.index.edges_models import CallEdge, Confidence
from reachability.index.entrypoint_models import Entrypoint
from reachability.index.models import DiscoveryReport
from reachability.index.reachability import (
    LOW_CONFIDENCE_REASON_PREFIX,
    NAME_ESCAPE_REASON_TEMPLATE,
    NO_ENTRYPOINTS_REASON,
    OPAQUE_CALL_REASON_PREFIX,
    compute_reachability,
    compute_reachability_batch,
)
from reachability.index.reachability_models import ReachabilityResult, Verdict
from reachability.index.symbol_models import NodeKind
from reachability.triage.index_adapter import build_repo_index

# ---- frozen verbatim copy begins ----


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


def _frozen_compute_reachability(
    target_module: str,
    target_symbol: str | None,
    entrypoints: list[Entrypoint],
    edges: list[CallEdge],
    report: DiscoveryReport,
) -> ReachabilityResult:
    if not entrypoints:
        return ReachabilityResult(
            target_module=target_module,
            target_symbol=target_symbol,
            verdict=Verdict.UNKNOWN,
            path=None,
            reason=NO_ENTRYPOINTS_REASON,
        )

    # Computed unconditionally, every call -- not an opt-in a caller can skip or
    # override with an empty set. See DECISIONS.md for why this must not be a
    # caller-supplied parameter.
    referenced_outside_call = collect_load_referenced_names(report)

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
        reason = LOW_CONFIDENCE_REASON_PREFIX + ", ".join(low_rules)
        return ReachabilityResult(
            target_module=target_module,
            target_symbol=target_symbol,
            verdict=Verdict.UNKNOWN,
            path=path,
            reason=reason,
        )

    # Step 4: before concluding NOT_REACHABLE, check whether the repo contains a
    # reachable opaque call with no literal target name to filter on -- a nameless
    # getattr(...)() dispatch, or eval()/exec() of a string whose contents this
    # engine never parses as code. Neither carries a name to bridge on the way
    # `?:<name>` unresolved calls already do, so a NOT_REACHABLE verdict cannot be
    # trusted anywhere such a call is reachable: the opaque call could resolve to
    # the queried symbol at runtime and this engine has no way to rule that out.
    # This is deliberately repo-wide, not scoped to the query target -- see
    # DECISIONS.md for the accepted trade-off this represents.
    opaque_ids = {"?:<dynamic>", "ext:builtins.eval", "ext:builtins.exec"}
    opaque_hit = next((oid for oid in opaque_ids if oid in paths), None)
    if opaque_hit is not None:
        opaque_edge = paths[opaque_hit][-1]
        return ReachabilityResult(
            target_module=target_module,
            target_symbol=target_symbol,
            verdict=Verdict.UNKNOWN,
            path=paths[opaque_hit],
            reason=(
                f"{OPAQUE_CALL_REASON_PREFIX}{opaque_hit}) at {opaque_edge.file}:{opaque_edge.lineno} "
                "carries no literal target name, so a not_reachable verdict cannot be trusted"
            ),
        )

    # Step 5: before concluding NOT_REACHABLE, check whether target_symbol's name is
    # loaded anywhere in the repo outside a call position -- passed as a callback
    # argument, stored in a container, reassigned, etc. L3 only extracts edges from
    # `call.func`; it never inspects `call.args`/`call.keywords`, so a function
    # handed to a framework or a dict by reference produces no call edge naming it
    # at all. A NOT_REACHABLE verdict cannot be trusted for a symbol that is
    # genuinely referenced by name elsewhere in the repo. Name-level check only: no
    # target resolution, no guessing who the eventual caller is. Like Step 4, this
    # is repo-wide, not scoped to the query target -- see DECISIONS.md.
    if target_symbol is not None and target_symbol in referenced_outside_call:
        return ReachabilityResult(
            target_module=target_module,
            target_symbol=target_symbol,
            verdict=Verdict.UNKNOWN,
            path=None,
            reason=NAME_ESCAPE_REASON_TEMPLATE.format(target_symbol=target_symbol),
        )

    # Step 6: no path found even ignoring confidence, and no opaque call or bare
    # name reference to blame it on.
    target = f"{target_module}:{target_symbol}" if target_symbol else target_module
    return ReachabilityResult(
        target_module=target_module,
        target_symbol=target_symbol,
        verdict=Verdict.NOT_REACHABLE,
        path=None,
        reason=f"no call path found from any entrypoint to {target}",
    )


# ---- frozen verbatim copy ends ----


def _targets_for(idx, extra_label_target=None):
    targets: list[tuple[str, str | None]] = []
    for module, table in idx.symbol_index.items():
        targets.append((module, None))
        for node in table.nodes:
            if node.kind == NodeKind.MODULE:
                continue
            targets.append((module, node.qualname.rsplit(".", 1)[-1].split("@")[0]))
    if extra_label_target:
        targets.append(extra_label_target)
    targets.append(("no.such.module", "nothing"))
    targets.append(("no.such.module", None))
    for edge in idx.edges:
        if edge.callee_id.startswith("?:"):
            targets.append(("zzz", edge.callee_id[2:]))
    for name in sorted(collect_load_referenced_names(idx.report))[:40]:
        targets.append(("zzz", name))
    return list(dict.fromkeys(targets))


def _assert_parity(idx, targets):
    batch = compute_reachability_batch(targets, idx.entrypoints, idx.edges, idx.report)
    assert len(batch) == len(targets)
    for (module, symbol), got in zip(targets, batch):
        want = _frozen_compute_reachability(module, symbol, idx.entrypoints, idx.edges, idx.report)
        assert got == want, (module, symbol)
        single = compute_reachability(module, symbol, idx.entrypoints, idx.edges, idx.report)
        assert single == want, (module, symbol)


def _fixture_dirs():
    return discover_eval_fixtures(EVAL_FIXTURES_ROOTS)


def test_batch_parity_l5_and_l5_phase4_every_target():
    dirs = _fixture_dirs()
    assert len(dirs) == 30
    seen_verdicts = set()
    for d in dirs:
        idx = build_repo_index(d / "repo")
        label = load_label(d)
        module = _module_dotted_name(label["sink"]["file"])
        symbol = resolve_target(idx.symbol_index, module, label["sink"]["line"], d.name)
        targets = _targets_for(idx, (module, symbol))
        _assert_parity(idx, targets)
        for m, s in targets:
            seen_verdicts.add(_frozen_compute_reachability(m, s, idx.entrypoints, idx.edges, idx.report).verdict)
    assert seen_verdicts == set(Verdict)


def test_batch_no_entrypoints_matches_frozen():
    d = _fixture_dirs()[0]
    idx = build_repo_index(d / "repo")
    targets = [("a", "b"), ("a", None)]
    got = compute_reachability_batch(targets, [], idx.edges, idx.report)
    for (m, s), r in zip(targets, got):
        assert r == _frozen_compute_reachability(m, s, [], idx.edges, idx.report)
        assert r.reason == NO_ENTRYPOINTS_REASON and r.verdict is Verdict.UNKNOWN


def test_batch_multi_target_single_call_equals_singletons():
    d = _fixture_dirs()[3]
    idx = build_repo_index(d / "repo")
    targets = _targets_for(idx)
    together = compute_reachability_batch(targets, idx.entrypoints, idx.edges, idx.report)
    for t, r in zip(targets, together):
        assert r == compute_reachability_batch([t], idx.entrypoints, idx.edges, idx.report)[0]


SYNTHETIC = {
    "app.py": (
        "import lib\nfrom lib import used\n\n\ndef main(obj, name):\n    used()\n    obj.load()\n"
        "    getattr(obj, name)()\n    eval(name)\n\n\ndef cb():\n    pass\n\n\n"
        "REG = {'k': cb}\n\nif __name__ == '__main__':\n    main(1, 'x')\n"
    ),
    "lib/__init__.py": "def used():\n    pass\n\n\ndef load():\n    pass\n\n\ndef cb():\n    pass\n",
}


def test_batch_parity_synthetic_bridge_opaque_d3_cases(tmp_path):
    write_tree(tmp_path, SYNTHETIC)
    idx = build_repo_index(tmp_path)
    targets = _targets_for(idx) + [("lib", "load"), ("lib", "cb"), ("lib", "used"), ("lib", None), ("app", "main")]
    _assert_parity(idx, targets)
    verdicts = {
        _frozen_compute_reachability(m, s, idx.entrypoints, idx.edges, idx.report).verdict for m, s in targets
    }
    assert Verdict.UNKNOWN in verdicts and Verdict.REACHABLE in verdicts


def test_batch_results_have_independent_path_lists(tmp_path):
    write_tree(tmp_path, SYNTHETIC)
    idx = build_repo_index(tmp_path)
    targets = [("lib", "used"), ("lib", "used"), ("lib", "load"), ("lib", "load")]
    a, b, c, d = compute_reachability_batch(targets, idx.entrypoints, idx.edges, idx.report)
    assert a.path and a.path == b.path and a.path is not b.path
    assert c.path is not None and c.path is not d.path
    a.path.clear()
    c.path.clear()
    assert b.path and d.path
