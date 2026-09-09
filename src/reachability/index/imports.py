import ast
import dataclasses
import sys

from .models import ImportAlias, ModuleImportTable, ModuleKind, ModuleRecord, StarImport, TargetKind

_IMPORT_ERROR_NAMES = ("ImportError", "ModuleNotFoundError", "Exception")


def classify_target(dotted: str | None, first_party_roots: set[str]) -> TargetKind:
    if dotted is None:
        return TargetKind.UNKNOWN
    top = dotted.split(".")[0]
    if top in first_party_roots:
        return TargetKind.FIRST_PARTY
    if top in sys.stdlib_module_names:
        return TargetKind.STDLIB
    return TargetKind.THIRD_PARTY


def resolve_relative(module: ModuleRecord, level: int, module_name: str | None) -> str | None:
    bits = module.package.rsplit(".", level - 1) if module.package else [""]
    if len(bits) < level or not bits[0]:
        return None  # escapes repo root — surfaced as unresolved, not guessed
    base = bits[0]
    return f"{base}.{module_name}" if module_name else base


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


def _walk_body(stmts, conditional, type_checking, collected):
    for stmt in stmts:
        _walk_stmt(stmt, conditional, type_checking, collected)


def _walk_stmt(stmt, conditional, type_checking, collected):
    if isinstance(stmt, ast.Import):
        collected.append(("import", stmt, conditional, type_checking))
        return
    if isinstance(stmt, ast.ImportFrom):
        collected.append(("importfrom", stmt, conditional, type_checking))
        return
    if isinstance(stmt, ast.Try):
        try_conditional = conditional or _try_handles_import_error(stmt)
        _walk_body(stmt.body, try_conditional, type_checking, collected)
        for handler in stmt.handlers:
            _walk_body(handler.body, try_conditional, type_checking, collected)
        _walk_body(stmt.orelse, try_conditional, type_checking, collected)
        _walk_body(stmt.finalbody, conditional, type_checking, collected)  # finally always runs
        return
    if isinstance(stmt, ast.If):
        if_type_checking = type_checking or _is_type_checking_test(stmt.test)
        _walk_body(stmt.body, conditional, if_type_checking, collected)
        _walk_body(stmt.orelse, conditional, type_checking, collected)
        return
    for _, value in ast.iter_fields(stmt):
        if isinstance(value, list):
            sub_stmts = [v for v in value if isinstance(v, ast.stmt)]
            if sub_stmts:
                _walk_body(sub_stmts, conditional, type_checking, collected)
        elif isinstance(value, ast.stmt):
            _walk_stmt(value, conditional, type_checking, collected)


def build_import_table(module: ModuleRecord, tree: ast.Module, first_party_roots: set[str]) -> ModuleImportTable:
    table = ModuleImportTable(module=module)
    collected: list[tuple] = []
    _walk_body(tree.body, False, False, collected)

    for kind, stmt, conditional, type_checking in collected:
        if kind == "import":
            for alias in stmt.names:
                if alias.asname is not None:
                    local_name = alias.asname
                    target_module = alias.name
                elif "." in alias.name:
                    local_name = alias.name.split(".")[0]
                    target_module = alias.name
                else:
                    local_name = target_module = alias.name
                resolved_absolute = target_module
                table.aliases.append(
                    ImportAlias(
                        local_name=local_name,
                        target_module=target_module,
                        target_symbol=None,
                        is_relative=False,
                        resolved_absolute=resolved_absolute,
                        target_kind=classify_target(target_module, first_party_roots),
                        lineno=stmt.lineno,
                        conditional=conditional,
                        type_checking_only=type_checking,
                    )
                )
        else:  # importfrom
            is_relative = stmt.level > 0
            if is_relative:
                resolved_absolute = resolve_relative(module, stmt.level, stmt.module)
            else:
                resolved_absolute = stmt.module
            for alias in stmt.names:
                if alias.name == "*":
                    table.star_imports.append(
                        StarImport(
                            module=stmt.module,
                            resolved_absolute=resolved_absolute,
                            target_kind=classify_target(resolved_absolute, first_party_roots),
                            lineno=stmt.lineno,
                        )
                    )
                    continue
                local_name = alias.asname or alias.name
                table.aliases.append(
                    ImportAlias(
                        local_name=local_name,
                        target_module=stmt.module,
                        target_symbol=alias.name,
                        is_relative=is_relative,
                        resolved_absolute=resolved_absolute,
                        target_kind=classify_target(resolved_absolute, first_party_roots),
                        lineno=stmt.lineno,
                        conditional=conditional,
                        type_checking_only=type_checking,
                    )
                )

    return table


def build_package_reexport_map(table: ModuleImportTable) -> tuple[dict[str, str], set[int]]:
    eligible = [
        a
        for a in table.aliases
        if a.target_kind == TargetKind.FIRST_PARTY
        and a.resolved_absolute is not None
        and not a.conditional
        and not a.type_checking_only
    ]

    by_local_name: dict[str, list[ImportAlias]] = {}
    for alias in eligible:
        by_local_name.setdefault(alias.local_name, []).append(alias)

    reexport_map: dict[str, str] = {}
    protected_ids: set[int] = set()
    for local_name, aliases in by_local_name.items():
        distinct_targets = {a.resolved_absolute for a in aliases}
        if len(distinct_targets) > 1:
            continue  # collision — drop entirely rather than guess
        reexport_map[local_name] = aliases[0].resolved_absolute
        protected_ids.update(id(a) for a in aliases)

    return reexport_map, protected_ids


def resolve_reexports(import_tables: dict[str, ModuleImportTable]) -> None:
    reexport_maps: dict[str, dict[str, str]] = {}
    protected_ids: set[int] = set()

    for table in import_tables.values():
        if table.module.kind == ModuleKind.PACKAGE:
            reexport_map, ids = build_package_reexport_map(table)
            reexport_maps[table.module.dotted_name] = reexport_map
            protected_ids.update(ids)

    for table in import_tables.values():
        for i, alias in enumerate(table.aliases):
            if id(alias) in protected_ids:
                continue
            if (
                alias.target_kind == TargetKind.FIRST_PARTY
                and alias.target_symbol is not None
                and alias.resolved_absolute in reexport_maps
                and alias.target_symbol in reexport_maps[alias.resolved_absolute]
            ):
                table.aliases[i] = dataclasses.replace(
                    alias,
                    resolved_absolute=reexport_maps[alias.resolved_absolute][alias.target_symbol],
                    via_reexport=True,
                )
