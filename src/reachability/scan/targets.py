import ast
import copy
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from reachability.index import build_l1_index, build_symbol_index
from reachability.index.symbol_models import ModuleSymbolTable, NodeKind
from reachability.scan.combined import CombinedIndex, wheel_top_level_files
from reachability.scan.models import Target

TARGET_KINDS = frozenset({NodeKind.FUNCTION, NodeKind.ASYNCFUNCTION, NodeKind.METHOD, NodeKind.CLASS})
_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

Key = tuple[str, str, int]  # (module, qualname without @lineno, ordinal among same-qualname defs)


@dataclass(frozen=True)
class SymDef:
    key: Key
    node_id: str
    file: Path
    lineno: int
    shape: str


def _strip(qualname: str) -> str:
    return ".".join(seg.split("@")[0] for seg in qualname.split("."))


def _shallow_shape(node: ast.AST) -> str:
    clone = copy.copy(node)
    clone.body = [s for s in node.body if not isinstance(s, _DEF_TYPES)]  # type: ignore[attr-defined]
    return ast.dump(clone, include_attributes=False)


def defs_from_tables(tables: dict[str, ModuleSymbolTable], modules: set[str] | None = None) -> dict[Key, SymDef]:
    out: dict[Key, SymDef] = {}
    for module in sorted(tables):
        if modules is not None and module not in modules:
            continue
        nodes = [n for n in tables[module].nodes if n.kind in TARGET_KINDS]
        if not nodes:
            continue
        by_file: dict[Path, dict[int, ast.AST]] = {}
        counts: dict[str, int] = {}
        for node in sorted(nodes, key=lambda n: (n.lineno, n.qualname)):
            if node.file not in by_file:
                tree = ast.parse(node.file.read_text(), filename=str(node.file))
                by_file[node.file] = {
                    n.lineno: n for n in ast.walk(tree) if isinstance(n, _DEF_TYPES)
                }
            ast_node = by_file[node.file].get(node.lineno)
            if ast_node is None:
                continue
            qual = _strip(node.qualname)
            ordinal = counts.get(qual, 0)
            counts[qual] = ordinal + 1
            key = (module, qual, ordinal)
            out[key] = SymDef(key, node.node_id, node.file, node.lineno, _shallow_shape(ast_node))
    return out


def defs_from_tree(tree: Path, tmp_parent: Path | None = None) -> dict[Key, SymDef]:
    """Symbol defs of a wheel tree, restricted to the files the combined root would take."""
    view = Path(tempfile.mkdtemp(prefix="rscan-fix-", dir=tmp_parent))
    try:
        for rel, src in wheel_top_level_files(tree):
            dest = view / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        report = build_l1_index(view)
        return defs_from_tables(build_symbol_index(report))
    finally:
        shutil.rmtree(view, ignore_errors=True)


def diff_defs(old: dict[Key, SymDef], new: dict[Key, SymDef]) -> list[tuple[Key, str]]:
    changes: list[tuple[Key, str]] = []
    for key in sorted(set(old) | set(new)):
        if key not in new:
            changes.append((key, "removed"))
        elif key not in old:
            changes.append((key, "added"))
        elif old[key].shape != new[key].shape:
            changes.append((key, "changed"))
    return changes


def resolve_targets(
    changes: list[tuple[Key, str]],
    installed: dict[Key, SymDef],
    combined: CombinedIndex,
    dist: str,
) -> list[Target]:
    """Targets are the changed/removed symbols present in the installed index, owned by dist."""
    origins = combined.module_origins()
    targets: list[Target] = []
    for key, kind in changes:
        if kind == "added":
            continue
        module, qual, ordinal = key
        sym = installed.get(key)
        if sym is None or origins.get(module) != dist:
            continue
        targets.append(
            Target(sym.node_id, module, qual, ordinal, combined.display_path(sym.file), sym.lineno, kind)
        )
    return targets
