import ast
from pathlib import Path

from .discovery import discover_modules
from .imports import build_import_table, resolve_reexports
from .models import DiscoveryReport, ModuleImportTable, UnparsedFile


def build_l1_index(repo_root: Path) -> DiscoveryReport:
    modules = discover_modules(repo_root)
    first_party_roots = {m.dotted_name.split(".")[0] for m in modules}

    import_tables: dict[str, ModuleImportTable] = {}
    unparsed: list[UnparsedFile] = []

    for module in modules:
        source = module.file_path.read_text()
        try:
            tree = ast.parse(source, filename=str(module.file_path))
        except SyntaxError as e:
            unparsed.append(
                UnparsedFile(file_path=module.file_path, error=str(e), lineno=e.lineno)
            )
            continue

        import_tables[module.dotted_name] = build_import_table(module, tree, first_party_roots)

    resolve_reexports(import_tables)

    star_import_count = sum(len(t.star_imports) for t in import_tables.values())

    return DiscoveryReport(
        modules=modules,
        import_tables=import_tables,
        unparsed=unparsed,
        star_import_count=star_import_count,
    )
