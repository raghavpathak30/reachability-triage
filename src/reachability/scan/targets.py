import ast
import copy
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from reachability.index import build_l1_index, build_symbol_index
from reachability.index.symbol_models import ModuleSymbolTable, NodeKind
from reachability.scan.combined import CombinedIndex, wheel_top_level_files
from reachability.scan.models import Target
from reachability.triage.sandbox import sandbox_untrusted_text

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
    end_lineno: int = 0
    kind: NodeKind = NodeKind.FUNCTION
    start_lineno: int = 0  # first line of the def, decorators included


def _strip(qualname: str) -> str:
    return ".".join(seg.split("@")[0] for seg in qualname.split("."))


def _shallow_shape(node: ast.AST) -> str:
    clone = copy.copy(node)
    clone.body = [s for s in node.body if not isinstance(s, _DEF_TYPES)]  # type: ignore[attr-defined]
    return ast.dump(clone, include_attributes=False)


VERSION_NAMES = frozenset({"__version__", "VERSION", "version", "version_info", "__version_info__"})
MAX_TARGETS = 20


@dataclass
class SymbolSet:
    defs: dict[Key, SymDef]
    modules: dict[str, str]  # module -> shape of its non-def, non-version statements


@dataclass(frozen=True)
class Diff:
    changes: list[tuple[Key, str]]
    module_level_changes: int

    @property
    def py_changed(self) -> bool:
        return bool(self.changes) or self.module_level_changes > 0


def is_test_module(module: str) -> bool:
    return any(seg in ("tests", "test", "testing") or seg.startswith("test_") for seg in module.split("."))


def _const(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Constant):
        return True
    return isinstance(node, ast.Tuple) and all(isinstance(e, ast.Constant) for e in node.elts)


def _is_version_only(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Assign):
        targets, value = stmt.targets, stmt.value
    elif isinstance(stmt, ast.AnnAssign):
        targets, value = [stmt.target], stmt.value
    else:
        return False
    return all(isinstance(t, ast.Name) and t.id in VERSION_NAMES for t in targets) and _const(value)


def _module_shape(tree: ast.Module) -> str:
    rest = [s for s in tree.body if not isinstance(s, _DEF_TYPES) and not _is_version_only(s)]
    return ast.dump(ast.Module(body=rest, type_ignores=[]), include_attributes=False) if rest else ""


def symbols_from_tables(tables: dict[str, ModuleSymbolTable], modules: set[str] | None = None) -> SymbolSet:
    defs: dict[Key, SymDef] = {}
    shapes: dict[str, str] = {}
    for module in sorted(tables):
        if modules is not None and module not in modules:
            continue
        nodes = tables[module].nodes
        if not nodes:
            continue
        file = nodes[0].file
        tree = ast.parse(file.read_text(), filename=str(file))
        shape = _module_shape(tree)
        if shape:
            shapes[module] = shape
        by_lineno = {n.lineno: n for n in ast.walk(tree) if isinstance(n, _DEF_TYPES)}
        counts: dict[str, int] = {}
        for node in sorted((n for n in nodes if n.kind in TARGET_KINDS), key=lambda n: (n.lineno, n.qualname)):
            ast_node = by_lineno.get(node.lineno)
            if ast_node is None:
                continue
            qual = _strip(node.qualname)
            ordinal = counts.get(qual, 0)
            counts[qual] = ordinal + 1
            key = (module, qual, ordinal)
            start = min([d.lineno for d in ast_node.decorator_list] + [ast_node.lineno])
            defs[key] = SymDef(
                key, node.node_id, node.file, node.lineno, _shallow_shape(ast_node),
                ast_node.end_lineno or node.lineno, node.kind, start,
            )
    return SymbolSet(defs, shapes)


def symbols_from_tree(tree: Path, tmp_parent: Path | None = None) -> SymbolSet:
    """Symbols of a wheel tree, restricted to the files the combined root would take."""
    view = Path(tempfile.mkdtemp(prefix="rscan-fix-", dir=tmp_parent))
    try:
        for rel, src in wheel_top_level_files(tree):
            dest = view / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        report = build_l1_index(view)
        return symbols_from_tables(build_symbol_index(report))
    finally:
        shutil.rmtree(view, ignore_errors=True)


def diff_symbols(old: SymbolSet, new: SymbolSet) -> Diff:
    changes: list[tuple[Key, str]] = []
    for key in sorted(set(old.defs) | set(new.defs)):
        if is_test_module(key[0]):
            continue
        if key not in new.defs:
            changes.append((key, "removed"))
        elif key not in old.defs:
            changes.append((key, "added"))
        elif old.defs[key].shape != new.defs[key].shape:
            changes.append((key, "changed"))
    module_level = sum(
        1
        for m in set(old.modules) | set(new.modules)
        if not is_test_module(m) and old.modules.get(m, "") != new.modules.get(m, "")
    )
    return Diff(changes, module_level)


def _record_non_py(tree: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for rec in sorted(tree.glob("*.dist-info/RECORD")):
        try:
            lines = rec.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            parts = line.rsplit(",", 2)
            if len(parts) != 3:
                continue
            path, digest, _size = parts
            if path.endswith(".py") or ".dist-info/" in path or path.endswith("/RECORD") or not digest:
                continue
            out[path] = digest
    return out


def non_py_changed(old_tree: Path, new_tree: Path) -> bool:
    """True if a non-Python file (for example a compiled extension) differs, per the RECORD files."""
    return _record_non_py(old_tree) != _record_non_py(new_tree)


_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_SPAN = re.compile(r"`([^`\n]+)`")
_DOTTED_OR_NAME = re.compile(rf"{_IDENT}(?:\.{_IDENT})*")


@dataclass(frozen=True)
class NamedSet:
    simple: frozenset[str] = frozenset()
    dotted: frozenset[str] = frozenset()

    def matches(self, module: str, qualname: str) -> bool:
        qual = _strip(qualname)
        if qual.rsplit(".", 1)[-1] in self.simple:
            return True
        if "." in qual and qual in self.dotted:
            return True
        return f"{module}.{qual}" in self.dotted


_CALL = re.compile(rf"(?<![\w.\])\]])({_IDENT})\((?!(?:s|es)\))")
_CHAIN = re.compile(rf"(?<![\w.\])\]])({_IDENT}(?:\.{_IDENT})+)(?!\w)(?!\([^()]*\)\.)")
_CALL_SUFFIX = re.compile(r"\([^()]*\)$")


def _call_and_chain_tokens(text: str) -> set[str]:
    return {m.group(1) for m in _CALL.finditer(text)} | {m.group(1) for m in _CHAIN.finditer(text)}


def named_names(text: str) -> NamedSet:
    """Code-form tokens of advisory text, after the sandbox has redacted it.

    Code form: inside a backtick span on one line, an identifier directly followed by `(`, or a
    dotted chain of two or more identifiers. Fenced blocks give only the last two. A candidate
    directly after `.` or `]` is a fragment of a longer chain and is never a token.
    """
    tokens: set[str] = set()
    prose: list[str] = []
    in_fence = False
    for line in sandbox_untrusted_text(text).splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            tokens |= _call_and_chain_tokens(line)
            continue
        rest = []
        last = 0
        spans = [] if line.count("`") % 2 else list(_SPAN.finditer(line))
        for m in spans:
            rest.append(line[last:m.start()])
            last = m.end()
            span = m.group(1)
            if "[REDACTED" in span:
                continue
            before = line[m.start() - 1] if m.start() > 0 else ""
            body = _CALL_SUFFIX.sub("", span.strip())
            if _DOTTED_OR_NAME.fullmatch(body):
                if before not in (".", "]"):
                    tokens.add(body)
            else:
                tokens |= _call_and_chain_tokens(span)
            rest.append(" ")
        rest.append(line[last:])
        prose.append("".join(rest))
    tokens |= _call_and_chain_tokens("\n".join(prose))
    return NamedSet(
        frozenset(t for t in tokens if "." not in t), frozenset(t for t in tokens if "." in t)
    )


_CHANGE_RANK = {"changed": 0, "removed": 1, "unchanged": 2}


def resolve_targets(
    changes: list[tuple[Key, str]],
    installed: SymbolSet,
    combined: CombinedIndex,
    dist: str,
    named: NamedSet | None = None,
) -> tuple[list[Target], bool]:
    """Changed/removed (and named) symbols present in the installed index and owned by dist.

    High confidence = named in code form AND changed or removed. All high targets come first;
    the cap is applied after that ordering.
    """
    origins = combined.module_origins()
    named = named or NamedSet()
    targets: list[Target] = []
    seen: set[Key] = set()

    def add(key: Key, kind: str) -> None:
        module, qual, ordinal = key
        sym = installed.defs.get(key)
        if sym is None or origins.get(module) != dist or key in seen:
            return
        seen.add(key)
        is_named = named.matches(module, qual)
        high = is_named and kind in ("changed", "removed")
        targets.append(
            Target(
                sym.node_id, module, qual, ordinal, combined.display_path(sym.file), sym.lineno, kind,
                "high" if high else "medium", is_named,
            )
        )

    for key, kind in changes:
        if kind != "added":
            add(key, kind)
    changed_keys = {key for key, _ in changes}
    for key in installed.defs:
        if key not in changed_keys and not is_test_module(key[0]) and named.matches(key[0], key[1]):
            add(key, "unchanged")
    targets.sort(
        key=lambda t: (t.confidence != "high", _CHANGE_RANK[t.changed], t.module, t.qualname, t.ordinal)
    )
    return targets[:MAX_TARGETS], len(targets) > MAX_TARGETS
