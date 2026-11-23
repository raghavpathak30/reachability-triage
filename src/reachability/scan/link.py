"""Scan-side link step: extra MEDIUM call edges beside the L3 edges.

Link edges are not CallEdge objects (CallEdge forbids MEDIUM except SELF_MRO and
ResolutionRule is frozen). They carry the CallEdge field names so the engine can read
them duck-typed. They never replace an edge and always point at an existing node.
"""

import ast
from collections import Counter
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
    files_parsed: int = 0
    files_skipped: int = 0


MAX_HOPS = 8


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_bytes())


def _scope_bindings(tree: ast.Module) -> Counter:
    """Binding occurrences of each name in the module's own scope (nested defs/classes only
    contribute their own name; lambdas and comprehensions only contribute walrus targets)."""
    counts: Counter = Counter()

    def bind_target(t: ast.AST) -> None:
        for n in ast.walk(t):
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                counts[n.id] += 1

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            counts[node.name] += 1
            return
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for n in ast.walk(node):
                if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                    counts[n.target.id] += 1
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                counts[a.asname or a.name.split(".")[0]] += 1
        elif isinstance(node, ast.ExceptHandler) and node.name:
            counts[node.name] += 1
        elif isinstance(node, ast.MatchAs) and node.name:
            counts[node.name] += 1
        elif isinstance(node, ast.MatchStar) and node.name:
            counts[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            counts[node.rest] += 1
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            counts[node.id] += 1
        elif isinstance(node, ast.NamedExpr):
            bind_target(node.target)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for stmt in tree.body:
        visit(stmt)
    return counts


def _global_names(tree: ast.Module) -> set[str]:
    return {n for node in ast.walk(tree) if isinstance(node, ast.Global) for n in node.names}


def _locally_bound(scope: ast.AST) -> set[str]:
    """Every name bound anywhere inside a function/lambda/class (deliberately over-inclusive)."""
    names: set[str] = set()
    for n in ast.walk(scope):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            names.add(n.rest)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
    return names


_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _calls_by_line(tree: ast.Module) -> dict[int, list[tuple[ast.Call, list[ast.AST]]]]:
    """Every attribute call keyed by its line, with the chain of enclosing function/class scopes."""
    out: dict[int, list[tuple[ast.Call, list[ast.AST]]]] = {}

    def visit(node: ast.AST, scopes: list[ast.AST]) -> None:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            out.setdefault(node.lineno, []).append((node, scopes))
        inner = scopes + [node] if isinstance(node, _SCOPES) else scopes
        for child in ast.iter_child_nodes(node):
            visit(child, inner)

    visit(tree, [])
    return out


class _Linker:
    def __init__(self, report: DiscoveryReport, symbol_index: dict[str, ModuleSymbolTable]) -> None:
        self.report = report
        self.symbol_index = symbol_index
        self.by_module = {m: _index_nodes_by_qualname(t) for m, t in symbol_index.items()}
        self.out: list[LinkEdge] = []
        self.records = {m.dotted_name: m for m in report.modules}
        self._trees: dict[str, ast.Module | None] = {}
        self._bindings: dict[str, Counter | None] = {}
        self._reexport: dict[tuple[str, str], SymbolNode | None] = {}
        self._calls: dict[str, dict] = {}
        self.low_count: Counter = Counter()
        self.files_parsed = 0
        self.files_skipped = 0
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

    def tree(self, module: str) -> ast.Module | None:
        if module not in self._trees:
            rec = self.records.get(module)
            tree = None
            if rec is not None:
                self.files_parsed += 1
                try:
                    tree = _parse(rec.file_path)
                except (RecursionError, MemoryError, SyntaxError, ValueError, UnicodeDecodeError, OSError):
                    self.files_skipped += 1
            self._trees[module] = tree
        return self._trees[module]

    def module_bindings(self, module: str) -> Counter | None:
        if module not in self._bindings:
            t = self.tree(module)
            self._bindings[module] = _scope_bindings(t) if t is not None else None
        return self._bindings[module]

    def reexport_target(self, module: str, name: str) -> SymbolNode | None:
        """Follow a dangling `module:name` through plain-module from-imports (rule b)."""
        seen: set[tuple[str, str]] = set()
        cur = (module, name)
        for _ in range(MAX_HOPS):
            if cur in seen:
                return None
            seen.add(cur)
            mod, nm = cur
            table = self.report.import_tables.get(mod)
            if table is None or table.star_imports or nm in self.by_module.get(mod, {}):
                return None
            aliases = [a for a in table.aliases if a.local_name == nm]
            if len(aliases) != 1:
                return None
            alias = aliases[0]
            if alias.conditional or alias.type_checking_only or alias.target_symbol is None:
                return None
            bindings = self.module_bindings(mod)
            if bindings is None or bindings[nm] != 1:
                return None
            nxt = (alias.resolved_absolute, alias.target_symbol)
            found = self.by_module.get(nxt[0], {}).get(nxt[1])
            if found is not None:
                return found if found.kind != NodeKind.MODULE else None
            cur = nxt
        return None

    def resolve_symbol(self, module: str, name: str) -> SymbolNode | None:
        """`name` as defined in `module`, or reached through rule b."""
        found = self.by_module.get(module, {}).get(name)
        if found is not None:
            return found
        return self.reexport_target(module, name)

    def unique_receiver_call(self, module: str, edge, attr: str, receiver: str) -> bool:
        """True iff the LOW edge is the one `receiver.attr(...)` call on its line and the bare
        receiver name is bound nowhere in the call's enclosing scopes."""
        tree = self.tree(module)
        if tree is None:
            return False
        if module not in self._calls:
            self._calls[module] = _calls_by_line(tree)
        same_attr = [
            (call, scopes)
            for call, scopes in self._calls[module].get(edge.lineno, [])
            if call.func.attr == attr
        ]
        if len(same_attr) != 1 or self.low_count[(edge.caller_id, edge.callee_id, edge.lineno)] != 1:
            return False
        call, scopes = same_attr[0]
        value = call.func.value
        if not isinstance(value, ast.Name) or value.id != receiver:
            return False
        return not any(receiver in _locally_bound(scope) for scope in scopes)

    def class_init(self, cls: SymbolNode) -> SymbolNode | None:
        return _search_class_for_method(cls, "__init__", cls.dotted_module, self.by_module, set())

    def constructor(self, edge, cls: SymbolNode) -> None:
        init = self.class_init(cls)
        if init is not None:
            self.add(edge.caller_id, init.node_id, edge.file, edge.lineno, LinkRule.CONSTRUCTOR_INIT)

    def edge_method(self, edge, cls: SymbolNode, name: str, rule: LinkRule) -> None:
        method = _search_class_for_method(cls, name, cls.dotted_module, self.by_module, set())
        if method is not None:
            self.add(edge.caller_id, method.node_id, edge.file, edge.lineno, rule)

    def rule_c(self, edge) -> None:
        mod_part, attr = edge.callee_id.split(":", 1)
        if "." not in mod_part or "." in attr:
            return
        module, name = mod_part.rsplit(".", 1)
        if module not in self.records or mod_part in self.records:
            return
        cls = self.resolve_symbol(module, name)
        if cls is not None and cls.kind == NodeKind.CLASS:
            self.edge_method(edge, cls, attr, LinkRule.MODULE_CLASS_ATTR)

    def rule_d(self, edge, module: str, name: str) -> None:
        table = self.report.import_tables.get(module)
        if table is None or table.star_imports or not any(
            a.target_symbol == a.local_name and not a.conditional and not a.type_checking_only
            for a in table.aliases
        ):
            return
        bindings = self.module_bindings(module)
        if bindings is None:
            return
        for sub_alias in table.aliases:
            sub = sub_alias.local_name
            if sub_alias.target_symbol != sub or not self.qualifies_from_import(table, sub, bindings):
                continue
            pkg = sub_alias.resolved_absolute
            if f"{pkg}.{sub}" not in self.records or pkg not in self.records:
                continue
            pkg_table = self.report.import_tables.get(pkg)
            if pkg_table is None or pkg_table.star_imports:
                continue
            pkg_bindings = self.module_bindings(pkg)
            if pkg_bindings is None or pkg_bindings[sub] != 0 or sub in self.by_module.get(pkg, {}):
                continue
            if not self.unique_receiver_call(module, edge, name, sub):
                continue
            final = self.resolve_symbol(f"{pkg}.{sub}", name)
            if final is None:
                continue
            self.add(edge.caller_id, final.node_id, edge.file, edge.lineno, LinkRule.SUBMODULE_ATTR)
            if final.kind == NodeKind.CLASS:
                init = self.class_init(final)
                if init is not None:
                    self.add(edge.caller_id, init.node_id, edge.file, edge.lineno, LinkRule.CONSTRUCTOR_INIT)
            return

    @staticmethod
    def qualifies_from_import(table, name: str, bindings: Counter) -> bool:
        aliases = [a for a in table.aliases if a.local_name == name]
        return (
            len(aliases) == 1
            and not aliases[0].conditional
            and not aliases[0].type_checking_only
            and bindings[name] == 1
        )

    def singletons(self, module: str) -> dict[str, SymbolNode]:
        """`x = C(...)` direct module-body statements where x has no other binding and C is a class."""
        tree = self.tree(module)
        bindings = self.module_bindings(module)
        table = self.report.import_tables.get(module)
        if tree is None or bindings is None or table is None or table.star_imports:
            return {}
        globals_ = _global_names(tree)
        out: dict[str, SymbolNode] = {}
        for stmt in tree.body:
            if not (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
            ):
                continue
            x, cname = stmt.targets[0].id, stmt.value.func.id
            if bindings[x] != 1 or x in globals_ or bindings[cname] != 1:
                continue
            cls = self.by_module.get(module, {}).get(cname)
            if cls is None:
                if not self.qualifies_from_import(table, cname, bindings):
                    continue
                alias = next(a for a in table.aliases if a.local_name == cname)
                if alias.target_symbol is None:
                    continue
                cls = self.resolve_symbol(alias.resolved_absolute, alias.target_symbol)
            if cls is not None and cls.kind == NodeKind.CLASS:
                out[x] = cls
        return out

    def rule_e(self, edge, module: str, name: str, cache: dict[str, dict[str, SymbolNode]]) -> None:
        if module not in cache:
            cache[module] = self.singletons(module)
        for x, cls in cache[module].items():
            if self.unique_receiver_call(module, edge, name, x):
                self.edge_method(edge, cls, name, LinkRule.MODULE_SINGLETON)
                return

    def run(self, edges) -> None:
        low = [e for e in edges if e.confidence == Confidence.LOW and e.callee_id.startswith("?:")]
        for e in low:
            self.low_count[(e.caller_id, e.callee_id, e.lineno)] += 1
        for edge in edges:
            target = self.node(edge.callee_id)
            if target is None and ":" in edge.callee_id and not edge.callee_id.startswith(("?:", "ext:")):
                module, name = edge.callee_id.split(":", 1)
                if "." not in name:
                    final = self.reexport_target(module, name)
                    if final is not None:
                        self.add(edge.caller_id, final.node_id, edge.file, edge.lineno, LinkRule.REEXPORT_ALIAS)
                        if final.kind == NodeKind.CLASS:
                            self.constructor(edge, final)
                        continue
                self.rule_c(edge)
            elif target is not None and target.kind == NodeKind.CLASS:
                self.constructor(edge, target)
        singleton_cache: dict[str, dict[str, SymbolNode]] = {}
        for edge in low:
            module = edge.caller_id.split(":", 1)[0]
            name = edge.callee_id[2:]
            self.rule_d(edge, module, name)
            self.rule_e(edge, module, name, singleton_cache)


def compute_link_edges(report: DiscoveryReport, symbol_index: dict[str, ModuleSymbolTable], edges) -> LinkResult:
    linker = _Linker(report, symbol_index)
    linker.run(edges)
    counts: dict[str, int] = {}
    for e in linker.out:
        counts[e.resolution_rule.value] = counts.get(e.resolution_rule.value, 0) + 1
    return LinkResult(linker.out, counts, linker.files_parsed, linker.files_skipped)
