"""Scan-side link step: extra MEDIUM call edges beside the L3 edges.

Link edges are not CallEdge objects (CallEdge forbids MEDIUM except SELF_MRO and
ResolutionRule is frozen). They carry the CallEdge field names so the engine can read
them duck-typed. They never replace an edge and always point at an existing node.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from reachability.index.edges import _index_nodes_by_qualname, _search_class_for_method
from reachability.index.edges_models import Confidence
from reachability.index.models import DiscoveryReport
from reachability.index.symbol_models import ModuleSymbolTable, NodeKind, SymbolNode


class LinkRule(str, Enum):
    CONSTRUCTOR_INIT = "constructor_init"
    REEXPORT_ALIAS = "reexport_alias"
    MODULE_CLASS_ATTR = "module_class_attr"
    SUBMODULE_ATTR = "submodule_attr"
    MODULE_SINGLETON = "module_singleton"


@dataclass(frozen=True)
class LinkEdge:
    caller_id: str
    callee_id: str
    file: Path
    lineno: int
    resolution_rule: LinkRule
    confidence: Confidence = Confidence.MEDIUM


@dataclass
class LinkResult:
    edges: list[LinkEdge] = field(default_factory=list)
    per_rule_counts: dict[str, int] = field(default_factory=dict)


class _Linker:
    def __init__(self, report: DiscoveryReport, symbol_index: dict[str, ModuleSymbolTable]) -> None:
        self.report = report
        self.symbol_index = symbol_index
        self.by_module = {m: _index_nodes_by_qualname(t) for m, t in symbol_index.items()}
        self.out: list[LinkEdge] = []
        self._seen: set[tuple[str, str, int, LinkRule]] = set()

    def node(self, node_id: str) -> SymbolNode | None:
        if ":" not in node_id:
            return None
        module, qual = node_id.split(":", 1)
        return self.by_module.get(module, {}).get(qual)

    def add(self, caller_id: str, callee_id: str, file: Path, lineno: int, rule: LinkRule) -> None:
        key = (caller_id, callee_id, lineno, rule)
        if key in self._seen or self.node(callee_id) is None:
            return
        self._seen.add(key)
        self.out.append(LinkEdge(caller_id, callee_id, file, lineno, rule))

    def class_init(self, cls: SymbolNode) -> SymbolNode | None:
        return _search_class_for_method(cls, "__init__", cls.dotted_module, self.by_module, set())

    def constructor(self, edge, cls: SymbolNode) -> None:
        init = self.class_init(cls)
        if init is not None:
            self.add(edge.caller_id, init.node_id, edge.file, edge.lineno, LinkRule.CONSTRUCTOR_INIT)

    def run(self, edges) -> None:
        for edge in edges:
            target = self.node(edge.callee_id)
            if target is not None and target.kind == NodeKind.CLASS:
                self.constructor(edge, target)


def compute_link_edges(report: DiscoveryReport, symbol_index: dict[str, ModuleSymbolTable], edges) -> LinkResult:
    linker = _Linker(report, symbol_index)
    linker.run(edges)
    counts: dict[str, int] = {}
    for e in linker.out:
        counts[e.resolution_rule.value] = counts.get(e.resolution_rule.value, 0) + 1
    return LinkResult(linker.out, counts)
