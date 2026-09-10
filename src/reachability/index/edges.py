import ast
import builtins
from pathlib import Path

from .edges_models import _RULE_CONFIDENCE, CallEdge, ResolutionRule
from .models import DiscoveryReport, ModuleImportTable, TargetKind
from .symbol_models import ModuleSymbolTable, NodeKind, SymbolNode

_BUILTIN_NAMES = frozenset(n for n in dir(builtins) if not n.startswith("_"))


def _flatten_attribute_chain(node: ast.AST) -> tuple[str, list[str]] | None:
    attrs: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        attrs.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    attrs.reverse()
    return cur.id, attrs


def _index_nodes_by_qualname(table: ModuleSymbolTable) -> dict[str, SymbolNode]:
    return {node.qualname: node for node in table.nodes}


def _index_callers_by_lineno(table: ModuleSymbolTable) -> dict[int, SymbolNode]:
    result: dict[int, SymbolNode] = {}
    for node in table.nodes:
        if node.kind in (NodeKind.FUNCTION, NodeKind.ASYNCFUNCTION, NodeKind.METHOD):
            result[node.lineno] = node
    return result


def _build_module_scope_index(table: ModuleSymbolTable) -> dict[str, str | None]:
    groups: dict[str, list[SymbolNode]] = {}
    for node in table.nodes:
        if node.kind == NodeKind.MODULE:
            continue
        if "." in node.qualname:
            continue
        base_name = node.qualname.split("@")[0]
        groups.setdefault(base_name, []).append(node)

    index: dict[str, str | None] = {}
    for base_name, members in groups.items():
        if len(members) != 1:
            index[base_name] = None
            continue
        member = members[0]
        if member.conditional or member.type_checking_only:
            index[base_name] = None
            continue
        if member.kind == NodeKind.ALIAS:
            # One-hop resolution: use target_id exactly as L2 left it, whatever shape it
            # is (a resolved node id, or an unresolved "?:name" marker, or in principle
            # None). Never chase a further hop. Interpreting the shape correctly is the
            # consumer's job (_resolve_name_callee step (c)), not this index's.
            index[base_name] = member.target_id
        else:
            index[base_name] = member.node_id
    return index


def _build_parent_map(tree: ast.Module) -> dict[ast.AST, tuple[ast.AST, str]]:
    parent_map: dict[ast.AST, tuple[ast.AST, str]] = {}

    def visit(node: ast.AST) -> None:
        for field_name, value in ast.iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        parent_map[item] = (node, field_name)
                        visit(item)
            elif isinstance(value, ast.AST):
                parent_map[value] = (node, field_name)
                visit(value)

    visit(tree)
    return parent_map


def _nearest_enclosing_scope(
    node: ast.AST, parent_map: dict[ast.AST, tuple[ast.AST, str]]
) -> ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None:
    current = node
    while current in parent_map:
        parent, field_name = parent_map[current]
        if (
            isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and field_name == "body"
        ):
            return parent
        # Not a body-anchored def/class scope: either some other node entirely, or a
        # def/class reached via a non-body field (decorator_list, args, returns, bases,
        # keywords) — a call there executes in the *enclosing* scope, not the def/class's
        # own body, so treat it as transparent and keep climbing past it.
        current = parent
    return None


def _module_node(module_table: ModuleSymbolTable) -> SymbolNode:
    for node in module_table.nodes:
        if node.kind == NodeKind.MODULE:
            return node
    raise RuntimeError(f"module symbol table for {module_table.module!r} has no MODULE node")


def _resolve_name_callee(
    name: str,
    caller_node: SymbolNode,
    nodes_by_qualname: dict[str, SymbolNode],
    module_scope_index: dict[str, str | None],
    import_table: ModuleImportTable,
) -> tuple[str, ResolutionRule]:
    # (a) caller's own qualname prefixes, innermost first, excluding the empty/module-level
    # prefix (that's step (c)'s job).
    segments = caller_node.qualname.split(".") if caller_node.qualname else []
    prefixes = [".".join(segments[:i]) for i in range(len(segments), 0, -1)]

    is_first_level = True
    for prefix in prefixes:
        if not is_first_level:
            # (LEGB) class scopes are not visible to nested defs the way function scopes
            # are — skip searching this level, but keep climbing past it.
            prefix_node = nodes_by_qualname.get(prefix)
            if prefix_node is not None and prefix_node.kind == NodeKind.CLASS:
                continue

        candidates = [
            node
            for node in nodes_by_qualname.values()
            if node.kind in (NodeKind.FUNCTION, NodeKind.ASYNCFUNCTION, NodeKind.METHOD, NodeKind.CLASS)
            and "." in node.qualname
            and node.qualname.rsplit(".", 1)[0] == prefix
            and node.qualname.rsplit(".", 1)[1].split("@")[0] == name
        ]

        if len(candidates) == 1:
            rule = ResolutionRule.LOCAL_SCOPE if is_first_level else ResolutionRule.ENCLOSING_SCOPE
            return candidates[0].node_id, rule
        if len(candidates) > 1:
            # Ambiguous at this level: stop the whole chain, never fall further outward.
            return f"?:{name}", ResolutionRule.UNRESOLVED_NAME

        is_first_level = False

    # (b) module-scope step.
    if name in module_scope_index:
        value = module_scope_index[name]
        if value is None:
            return f"?:{name}", ResolutionRule.UNRESOLVED_NAME
        if value.startswith("?:"):
            # An ALIAS whose own RHS never resolved (L2 left target_id as "?:name").
            # Propagate that string as-is — it's more specific than a generic "?:{name}" —
            # but never treat it as a confident resolution.
            return value, ResolutionRule.UNRESOLVED_NAME
        return value, ResolutionRule.MODULE_SCOPE

    # (c) import-alias step.
    eligible = [
        a
        for a in import_table.aliases
        if a.local_name == name
        and a.target_symbol is not None
        and not a.conditional
        and not a.type_checking_only
    ]
    if eligible:
        distinct = {(a.resolved_absolute, a.target_symbol) for a in eligible}
        if len(distinct) > 1:
            return f"?:{name}", ResolutionRule.UNRESOLVED_NAME
        alias = eligible[0]
        if alias.resolved_absolute is not None:
            if alias.target_kind == TargetKind.FIRST_PARTY:
                return f"{alias.resolved_absolute}:{alias.target_symbol}", ResolutionRule.IMPORT_ALIAS
            return f"ext:{alias.resolved_absolute}.{alias.target_symbol}", ResolutionRule.IMPORT_ALIAS
        # resolved_absolute is None (e.g. an escaped relative import): falls through.

    # (d) builtin step.
    if name in _BUILTIN_NAMES:
        return f"ext:builtins.{name}", ResolutionRule.BUILTIN

    # (e) fallback.
    return f"?:{name}", ResolutionRule.UNRESOLVED_NAME


def _search_class_for_method(
    class_node: SymbolNode,
    method_name: str,
    class_module: str,
    module_qualname_indices: dict[str, dict[str, SymbolNode]],
    visited: set[str],
) -> SymbolNode | None:
    if class_node.node_id in visited:
        return None
    visited.add(class_node.node_id)

    own_index = module_qualname_indices.get(class_module, {})
    matches = [
        node
        for node in own_index.values()
        if node.kind == NodeKind.METHOD
        and "." in node.qualname
        and node.qualname.rsplit(".", 1)[0] == class_node.qualname
        and node.qualname.rsplit(".", 1)[1].split("@")[0] == method_name
    ]
    if len(matches) == 1:
        return matches[0]
    # Zero matches, or 2+ (an intra-class collision — never guess which one runs): either
    # way, fall through to the base chain rather than resolving at this class level.

    for base in class_node.bases:
        if ":" not in base:
            continue  # raw string: non-first-party or unresolved base, skip without inspection
        base_module, base_qualname = base.split(":", 1)
        if base_module not in module_qualname_indices:
            continue
        base_node = module_qualname_indices[base_module].get(base_qualname)
        if base_node is None or base_node.kind != NodeKind.CLASS:
            continue
        result = _search_class_for_method(base_node, method_name, base_module, module_qualname_indices, visited)
        if result is not None:
            return result
    return None


def _resolve_self_mro(
    caller_node: SymbolNode,
    method_name: str,
    current_module: str,
    module_qualname_indices: dict[str, dict[str, SymbolNode]],
) -> tuple[str, ResolutionRule]:
    class_qualname = caller_node.qualname.rsplit(".", 1)[0] if "." in caller_node.qualname else None
    class_node = (
        module_qualname_indices.get(current_module, {}).get(class_qualname)
        if class_qualname is not None
        else None
    )
    if class_node is None or class_node.kind != NodeKind.CLASS:
        return f"?:{method_name}", ResolutionRule.SELF_UNRESOLVED

    found = _search_class_for_method(class_node, method_name, current_module, module_qualname_indices, set())
    if found is not None:
        return found.node_id, ResolutionRule.SELF_MRO
    return f"?:{method_name}", ResolutionRule.SELF_UNRESOLVED


def _resolve_attribute_callee(
    attr_node: ast.Attribute,
    caller_node: SymbolNode,
    current_module: str,
    import_table: ModuleImportTable,
    module_qualname_indices: dict[str, dict[str, SymbolNode]],
) -> tuple[str, ResolutionRule]:
    flattened = _flatten_attribute_chain(attr_node)
    if flattened is None:
        return f"?:{attr_node.attr}", ResolutionRule.UNRESOLVED_ATTRIBUTE

    base_name, attrs = flattened

    if base_name == "self" and caller_node.kind == NodeKind.METHOD:
        method_name = attrs[-1] if attrs else attr_node.attr
        return _resolve_self_mro(caller_node, method_name, current_module, module_qualname_indices)

    eligible = [
        a
        for a in import_table.aliases
        if a.local_name == base_name
        and a.target_symbol is None
        and not a.conditional
        and not a.type_checking_only
    ]
    if eligible:
        distinct = {(a.resolved_absolute, a.target_symbol) for a in eligible}
        if len(distinct) == 1:
            alias = eligible[0]
            if alias.resolved_absolute is not None:
                if alias.target_kind == TargetKind.FIRST_PARTY:
                    if len(attrs) == 1:
                        return f"{alias.resolved_absolute}:{attrs[0]}", ResolutionRule.MODULE_ATTRIBUTE
                    return (
                        f"{alias.resolved_absolute}.{'.'.join(attrs[:-1])}:{attrs[-1]}",
                        ResolutionRule.MODULE_ATTRIBUTE,
                    )
                return f"ext:{alias.resolved_absolute}.{'.'.join(attrs)}", ResolutionRule.MODULE_ATTRIBUTE

    # Base isn't self, isn't an eligible plain-module import alias: no name-to-node lookup
    # of any kind happens here — this is the mechanism that guarantees obj.load() can never
    # match a same-named first-party function.
    return f"?:{attr_node.attr}", ResolutionRule.UNRESOLVED_ATTRIBUTE


def _make_edge(caller_id: str, callee_id: str, file: Path, lineno: int, rule: ResolutionRule) -> CallEdge:
    return CallEdge(
        caller_id=caller_id,
        callee_id=callee_id,
        confidence=_RULE_CONFIDENCE[rule],
        file=file,
        lineno=lineno,
        resolution_rule=rule,
    )


def build_module_call_edges(
    module_table: ModuleSymbolTable,
    tree: ast.Module,
    import_table: ModuleImportTable,
    module_qualname_indices: dict[str, dict[str, SymbolNode]],
) -> list[CallEdge]:
    nodes_by_qualname = _index_nodes_by_qualname(module_table)
    caller_by_lineno = _index_callers_by_lineno(module_table)
    module_scope_index = _build_module_scope_index(module_table)
    module_node = _module_node(module_table)
    parent_map = _build_parent_map(tree)
    current_module = module_table.module

    edges: list[CallEdge] = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue

        ancestor = _nearest_enclosing_scope(call, parent_map)
        if ancestor is None:
            caller_node = module_node
        elif isinstance(ancestor, ast.ClassDef):
            # Call sits directly in a class body, outside any method: not extracted.
            continue
        else:
            caller_node = caller_by_lineno.get(ancestor.lineno)
            if caller_node is None:
                raise RuntimeError(
                    "L3 internal inconsistency: no SymbolNode found for "
                    f"{ancestor.__class__.__name__} at {current_module}:{ancestor.lineno}"
                )

        func = call.func
        if isinstance(func, ast.Name):
            callee_id, rule = _resolve_name_callee(
                func.id, caller_node, nodes_by_qualname, module_scope_index, import_table
            )
        elif isinstance(func, ast.Attribute):
            callee_id, rule = _resolve_attribute_callee(
                func, caller_node, current_module, import_table, module_qualname_indices
            )
        elif isinstance(func, (ast.Call, ast.Subscript)):
            callee_id, rule = "?:<dynamic>", ResolutionRule.DYNAMIC_DISPATCH
        else:
            callee_id, rule = "?:<dynamic>", ResolutionRule.DYNAMIC_DISPATCH

        edges.append(_make_edge(caller_node.node_id, callee_id, module_node.file, call.lineno, rule))

    return edges


def build_edge_index(report: DiscoveryReport, symbol_index: dict[str, ModuleSymbolTable]) -> list[CallEdge]:
    module_qualname_indices: dict[str, dict[str, SymbolNode]] = {
        module: _index_nodes_by_qualname(table) for module, table in symbol_index.items()
    }

    edges: list[CallEdge] = []
    for module in report.modules:
        dotted_name = module.dotted_name
        if dotted_name not in report.import_tables or dotted_name not in symbol_index:
            continue
        source = module.file_path.read_text()
        tree = ast.parse(source, filename=str(module.file_path))
        module_table = symbol_index[dotted_name]
        import_table = report.import_tables[dotted_name]
        edges.extend(build_module_call_edges(module_table, tree, import_table, module_qualname_indices))

    return edges
