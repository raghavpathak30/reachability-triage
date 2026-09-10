import ast
from dataclasses import dataclass, field

from .models import DiscoveryReport, ModuleImportTable, ModuleRecord, TargetKind
from .symbol_models import ModuleSymbolTable, NodeKind, SymbolNode

_IMPORT_ERROR_NAMES = ("ImportError", "ModuleNotFoundError", "Exception")


@dataclass
class PendingNode:
    ast_node: ast.AST
    qualname: str
    kind: NodeKind
    is_async: bool
    lineno: int
    conditional: bool
    type_checking_only: bool


@dataclass
class _AliasCandidate:
    name: str
    lineno: int
    is_lambda: bool
    rhs: ast.expr
    segment: str = ""


def _handler_type_matches(type_node: ast.expr) -> bool:
    if isinstance(type_node, ast.Name):
        return type_node.id in _IMPORT_ERROR_NAMES
    if isinstance(type_node, ast.Attribute):
        return type_node.attr in _IMPORT_ERROR_NAMES
    return False


def _try_handles_import_error(try_node: ast.Try) -> bool:
    for handler in try_node.handlers:
        t = handler.type
        if t is None:
            return True  # bare except
        if isinstance(t, ast.Tuple):
            if any(_handler_type_matches(elt) for elt in t.elts):
                return True
        elif _handler_type_matches(t):
            return True
    return False


def _is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _collect_scope_defs(stmts):
    collected: list[tuple] = []
    _walk_scope_body(stmts, False, False, collected)
    return collected


def _walk_scope_body(stmts, conditional, type_checking, collected):
    for stmt in stmts:
        _walk_scope_stmt(stmt, conditional, type_checking, collected)


def _walk_scope_stmt(stmt, conditional, type_checking, collected):
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        collected.append((stmt, conditional, type_checking))
        return
    if isinstance(stmt, ast.Try):
        try_conditional = conditional or _try_handles_import_error(stmt)
        _walk_scope_body(stmt.body, try_conditional, type_checking, collected)
        for handler in stmt.handlers:
            _walk_scope_body(handler.body, try_conditional, type_checking, collected)
        _walk_scope_body(stmt.orelse, try_conditional, type_checking, collected)
        _walk_scope_body(stmt.finalbody, conditional, type_checking, collected)  # finally always runs
        return
    if isinstance(stmt, ast.If):
        if_type_checking = type_checking or _is_type_checking_test(stmt.test)
        _walk_scope_body(stmt.body, conditional, if_type_checking, collected)
        _walk_scope_body(stmt.orelse, conditional, type_checking, collected)
        return
    for _, value in ast.iter_fields(stmt):
        if isinstance(value, list):
            sub_stmts = [v for v in value if isinstance(v, ast.stmt)]
            if sub_stmts:
                _walk_scope_body(sub_stmts, conditional, type_checking, collected)
        elif isinstance(value, ast.stmt):
            _walk_scope_stmt(value, conditional, type_checking, collected)


def _assign_segments(defs):
    groups: dict[str, list] = {}
    for d, _cond, _tc in defs:
        groups.setdefault(d.name, []).append(d)

    segments: dict[int, str] = {}
    for name, nodes in groups.items():
        if len(nodes) == 1:
            segments[id(nodes[0])] = name
        else:
            for n in nodes:
                segments[id(n)] = f"{name}@{n.lineno}"
    return segments


def _walk_scope(stmts, parent_qualname, parent_is_class, inherited_conditional, inherited_type_checking, pending):
    defs = _collect_scope_defs(stmts)
    segments = _assign_segments(defs)

    for d, local_conditional, local_type_checking in defs:
        segment = segments[id(d)]
        qualname = f"{parent_qualname}.{segment}" if parent_qualname else segment

        if isinstance(d, ast.ClassDef):
            kind = NodeKind.CLASS
        elif parent_is_class:
            kind = NodeKind.METHOD
        elif isinstance(d, ast.AsyncFunctionDef):
            kind = NodeKind.ASYNCFUNCTION
        else:
            kind = NodeKind.FUNCTION

        is_async = isinstance(d, ast.AsyncFunctionDef)
        own_conditional = inherited_conditional or local_conditional
        own_type_checking = inherited_type_checking or local_type_checking

        pending.append(
            PendingNode(
                ast_node=d,
                qualname=qualname,
                kind=kind,
                is_async=is_async,
                lineno=d.lineno,
                conditional=own_conditional,
                type_checking_only=own_type_checking,
            )
        )

        _walk_scope(
            d.body,
            qualname,
            isinstance(d, ast.ClassDef),
            own_conditional,
            own_type_checking,
            pending,
        )


def _resolve_name_ref(name, module_top_level_index, import_table):
    eligible_aliases = [
        a
        for a in import_table.aliases
        if a.local_name == name and not a.conditional and not a.type_checking_only
    ]

    if name in module_top_level_index:
        if eligible_aliases:
            return None  # ambiguous: local def and import binding share this name
        return module_top_level_index[name]

    if not eligible_aliases:
        return None

    distinct = {(a.resolved_absolute, a.target_symbol) for a in eligible_aliases}
    if len(distinct) > 1:
        return None

    alias = eligible_aliases[0]
    if (
        alias.target_kind == TargetKind.FIRST_PARTY
        and alias.resolved_absolute is not None
        and alias.target_symbol is not None
    ):
        return f"{alias.resolved_absolute}:{alias.target_symbol}"
    return None


def _flatten_attribute_chain(node):
    attrs: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        attrs.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    attrs.reverse()
    return cur.id, attrs


def _resolve_attribute_ref(node, module_top_level_index, import_table):
    flattened = _flatten_attribute_chain(node)
    if flattened is None:
        return None
    base_name, attrs = flattened

    if base_name in module_top_level_index:
        return None  # composing attribute access onto an in-file symbol is out of scope

    eligible_aliases = [
        a
        for a in import_table.aliases
        if a.local_name == base_name and not a.conditional and not a.type_checking_only
    ]
    if not eligible_aliases:
        return None

    distinct = {(a.resolved_absolute, a.target_symbol) for a in eligible_aliases}
    if len(distinct) > 1:
        return None

    alias = eligible_aliases[0]
    if alias.target_kind != TargetKind.FIRST_PARTY or alias.resolved_absolute is None:
        return None
    if alias.target_symbol is not None:
        return None  # base alias is an imported symbol, not a module — further attrs unresolved

    if len(attrs) == 1:
        return f"{alias.resolved_absolute}:{attrs[0]}"
    return f"{alias.resolved_absolute}.{'.'.join(attrs[:-1])}:{attrs[-1]}"


def _resolve_decorator(expr, module_top_level_index, import_table):
    if isinstance(expr, ast.Name):
        resolved = _resolve_name_ref(expr.id, module_top_level_index, import_table)
        return resolved if resolved is not None else f"?:{expr.id}"

    if isinstance(expr, ast.Attribute):
        resolved = _resolve_attribute_ref(expr, module_top_level_index, import_table)
        return resolved if resolved is not None else f"?:{expr.attr}"

    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Name):
            return f"?:{func.id}"
        if isinstance(func, ast.Attribute):
            flattened = _flatten_attribute_chain(func)
            if flattened is not None:
                base_name, attrs = flattened
                return f"?:{'.'.join([base_name] + attrs)}"
        return "?:<dynamic_decorator>"

    return "?:<dynamic_decorator>"


def _resolve_base(expr, module_top_level_index, import_table):
    if isinstance(expr, ast.Name):
        resolved = _resolve_name_ref(expr.id, module_top_level_index, import_table)
        return resolved if resolved is not None else expr.id

    if isinstance(expr, ast.Attribute):
        resolved = _resolve_attribute_ref(expr, module_top_level_index, import_table)
        return resolved if resolved is not None else ast.unparse(expr)

    return ast.unparse(expr)


def _resolve_alias_rhs(expr, module_top_level_index, import_table):
    if isinstance(expr, ast.Name):
        resolved = _resolve_name_ref(expr.id, module_top_level_index, import_table)
        return resolved if resolved is not None else f"?:{expr.id}"

    if isinstance(expr, ast.Attribute):
        resolved = _resolve_attribute_ref(expr, module_top_level_index, import_table)
        return resolved if resolved is not None else f"?:{expr.attr}"

    return "?:<dynamic_decorator>"


def build_module_symbol_table(module: ModuleRecord, tree: ast.Module, import_table: ModuleImportTable) -> ModuleSymbolTable:
    pending: list[PendingNode] = []
    _walk_scope(tree.body, "", False, False, False, pending)

    alias_candidates: list[_AliasCandidate] = []
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            continue
        name = stmt.targets[0].id
        rhs = stmt.value
        if isinstance(rhs, (ast.Name, ast.Attribute)):
            alias_candidates.append(_AliasCandidate(name=name, lineno=stmt.lineno, is_lambda=False, rhs=rhs))
        elif isinstance(rhs, ast.Lambda):
            alias_candidates.append(_AliasCandidate(name=name, lineno=stmt.lineno, is_lambda=True, rhs=rhs))
        # any other RHS shape: not recorded as an alias node at all

    depth0_defs = [p for p in pending if "." not in p.qualname]

    groups: dict[str, list] = {}
    for p in depth0_defs:
        groups.setdefault(p.ast_node.name, []).append(("def", p))
    for candidate in alias_candidates:
        groups.setdefault(candidate.name, []).append(("alias", candidate))

    for name, members in groups.items():
        if len(members) == 1:
            member_kind, item = members[0]
            if member_kind == "def":
                item.qualname = name
            else:
                item.segment = name
        else:
            for member_kind, item in members:
                if member_kind == "def":
                    item.qualname = f"{name}@{item.lineno}"
                else:
                    item.segment = f"{name}@{item.lineno}"

    module_top_level_index = {
        p.qualname: f"{module.dotted_name}:{p.qualname}"
        for p in pending
        if "." not in p.qualname
        and "@" not in p.qualname
        and not p.conditional
        and not p.type_checking_only
    }

    nodes: list[SymbolNode] = []
    nodes.append(
        SymbolNode(
            node_id=module.dotted_name,
            dotted_module=module.dotted_name,
            qualname="",
            kind=NodeKind.MODULE,
            file=module.file_path,
            lineno=1,
        )
    )

    for p in pending:
        decorators: list[str] = []
        bases: list[str] = []
        if isinstance(p.ast_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            decorators = [
                _resolve_decorator(d, module_top_level_index, import_table)
                for d in p.ast_node.decorator_list
            ]
        if isinstance(p.ast_node, ast.ClassDef):
            bases = [
                _resolve_base(b, module_top_level_index, import_table)
                for b in p.ast_node.bases
            ]

        nodes.append(
            SymbolNode(
                node_id=f"{module.dotted_name}:{p.qualname}",
                dotted_module=module.dotted_name,
                qualname=p.qualname,
                kind=p.kind,
                file=module.file_path,
                lineno=p.lineno,
                is_async=p.is_async,
                decorators=decorators,
                bases=bases,
                conditional=p.conditional,
                type_checking_only=p.type_checking_only,
            )
        )

    for candidate in alias_candidates:
        if candidate.is_lambda:
            nodes.append(
                SymbolNode(
                    node_id=f"{module.dotted_name}:{candidate.segment}",
                    dotted_module=module.dotted_name,
                    qualname=candidate.segment,
                    kind=NodeKind.FUNCTION,
                    file=module.file_path,
                    lineno=candidate.lineno,
                    decorators=[],
                )
            )
        else:
            target_id = _resolve_alias_rhs(candidate.rhs, module_top_level_index, import_table)
            nodes.append(
                SymbolNode(
                    node_id=f"{module.dotted_name}:{candidate.segment}",
                    dotted_module=module.dotted_name,
                    qualname=candidate.segment,
                    kind=NodeKind.ALIAS,
                    file=module.file_path,
                    lineno=candidate.lineno,
                    target_id=target_id,
                )
            )

    return ModuleSymbolTable(module=module.dotted_name, nodes=nodes)


def build_symbol_index(report: DiscoveryReport) -> dict[str, ModuleSymbolTable]:
    tables: dict[str, ModuleSymbolTable] = {}
    for module in report.modules:
        if module.dotted_name not in report.import_tables:
            continue
        source = module.file_path.read_text()
        tree = ast.parse(source, filename=str(module.file_path))
        tables[module.dotted_name] = build_module_symbol_table(
            module, tree, report.import_tables[module.dotted_name]
        )
    return tables
