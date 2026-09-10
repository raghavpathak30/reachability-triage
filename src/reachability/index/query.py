import fnmatch

from .edges_models import CallEdge
from .models import DiscoveryReport, TargetKind
from .symbol_models import ModuleSymbolTable, SymbolNode


def search_symbol(symbol_index: dict[str, ModuleSymbolTable], pattern: str) -> list[SymbolNode]:
    matches: list[SymbolNode] = []
    for table in symbol_index.values():
        for node in table.nodes:
            if fnmatch.fnmatch(node.node_id, pattern) or fnmatch.fnmatch(node.qualname, pattern):
                matches.append(node)
    return matches


def find_callers(edges: list[CallEdge], node_id: str) -> list[CallEdge]:
    return [edge for edge in edges if edge.callee_id == node_id]


def resolve_import(report: DiscoveryReport, module: str, name: str) -> str | None:
    table = report.import_tables.get(module)
    if table is None:
        return None

    eligible = [
        alias
        for alias in table.aliases
        if alias.local_name == name
        and alias.target_symbol is not None
        and not alias.conditional
        and not alias.type_checking_only
    ]
    if not eligible:
        return None

    distinct = {(alias.resolved_absolute, alias.target_symbol) for alias in eligible}
    if len(distinct) > 1:
        return None

    alias = eligible[0]
    if alias.resolved_absolute is None:
        return None
    if alias.target_kind == TargetKind.FIRST_PARTY:
        return f"{alias.resolved_absolute}:{alias.target_symbol}"
    return f"ext:{alias.resolved_absolute}.{alias.target_symbol}"
