import ast
import tomllib
from pathlib import Path

from .entrypoint_models import Entrypoint, EntrypointSource
from .models import DiscoveryReport
from .symbol_models import ModuleSymbolTable, NodeKind, SymbolNode

_ROUTE_TAILS = {"get", "post", "put", "delete", "patch", "head", "options", "route", "websocket", "api_route"}
_CELERY_TAILS = {"task", "shared_task"}
_CLI_TAILS = {"command"}
_FIXTURE_TAILS = {"fixture"}


def _is_dunder_main_test(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    if len(test.comparators) != 1:
        return False
    left, right = test.left, test.comparators[0]

    def is_dunder_name(node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id == "__name__"

    def is_main_const(node: ast.expr) -> bool:
        return isinstance(node, ast.Constant) and node.value == "__main__"

    return (is_dunder_name(left) and is_main_const(right)) or (is_main_const(left) and is_dunder_name(right))


def _module_node(table: ModuleSymbolTable) -> SymbolNode | None:
    for node in table.nodes:
        if node.kind == NodeKind.MODULE:
            return node
    return None


def detect_dunder_main_entrypoints(
    report: DiscoveryReport, symbol_index: dict[str, ModuleSymbolTable]
) -> list[Entrypoint]:
    entrypoints: list[Entrypoint] = []
    for module in report.modules:
        dotted_name = module.dotted_name
        if dotted_name not in symbol_index:
            continue
        module_node = _module_node(symbol_index[dotted_name])
        if module_node is None:
            continue
        source = module.file_path.read_text()
        tree = ast.parse(source, filename=str(module.file_path))
        for stmt in tree.body:
            if isinstance(stmt, ast.If) and _is_dunder_main_test(stmt.test):
                # L3 (frozen) attributes every module-top-level call — guarded or not — to
                # the module node as caller_id, so this entrypoint's reachable set is the
                # whole module's top-level code, not just the guarded block.
                entrypoints.append(
                    Entrypoint(
                        source=EntrypointSource.DUNDER_MAIN,
                        node_id=module_node.node_id,
                        module=dotted_name,
                        file=module.file_path,
                        lineno=stmt.lineno,
                        is_test=False,
                    )
                )
    return entrypoints


def _parse_console_scripts(repo_root: Path) -> dict[str, str]:
    pyproject_path = repo_root / "pyproject.toml"
    try:
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}

    project = data.get("project", {})
    scripts: dict[str, str] = {}
    scripts.update(project.get("scripts", {}))
    scripts.update(project.get("gui-scripts", {}))
    return scripts


def _strip_collision_suffix(qualname: str) -> str:
    return ".".join(segment.split("@")[0] for segment in qualname.split("."))


def detect_console_script_entrypoints(
    repo_root: Path, symbol_index: dict[str, ModuleSymbolTable]
) -> list[Entrypoint]:
    entrypoints: list[Entrypoint] = []
    for target in _parse_console_scripts(repo_root).values():
        if ":" not in target:
            continue
        dotted_module, attr_path = target.rsplit(":", 1)
        table = symbol_index.get(dotted_module)
        if table is None:
            continue
        match = next(
            (
                node
                for node in table.nodes
                if node.kind in (NodeKind.FUNCTION, NodeKind.ASYNCFUNCTION, NodeKind.METHOD)
                and _strip_collision_suffix(node.qualname) == attr_path
            ),
            None,
        )
        if match is None:
            continue
        entrypoints.append(
            Entrypoint(
                source=EntrypointSource.CONSOLE_SCRIPT,
                node_id=match.node_id,
                module=dotted_module,
                file=match.file,
                lineno=match.lineno,
                is_test=False,
            )
        )
    return entrypoints


def _decorator_tail(decorator_id: str) -> str:
    if decorator_id.startswith("?:"):
        tail = decorator_id[2:]
    elif decorator_id.startswith("ext:"):
        tail = decorator_id[4:]
    elif ":" in decorator_id:
        tail = decorator_id.split(":", 1)[1]
    else:
        tail = decorator_id
    return tail.split(".")[-1]


def detect_decorator_entrypoints(symbol_index: dict[str, ModuleSymbolTable]) -> list[Entrypoint]:
    entrypoints: list[Entrypoint] = []
    for module, table in symbol_index.items():
        for node in table.nodes:
            if node.kind not in (NodeKind.FUNCTION, NodeKind.ASYNCFUNCTION, NodeKind.METHOD):
                continue
            for decorator_id in node.decorators:
                tail = _decorator_tail(decorator_id)
                if tail in _ROUTE_TAILS:
                    source, is_test = EntrypointSource.ROUTE_DECORATOR, False
                elif tail in _CELERY_TAILS:
                    source, is_test = EntrypointSource.CELERY_TASK, False
                elif tail in _CLI_TAILS:
                    source, is_test = EntrypointSource.CLI_DECORATOR, False
                elif tail in _FIXTURE_TAILS:
                    source, is_test = EntrypointSource.PYTEST_FIXTURE, True
                else:
                    continue
                entrypoints.append(
                    Entrypoint(
                        source=source,
                        node_id=node.node_id,
                        module=module,
                        file=node.file,
                        lineno=node.lineno,
                        is_test=is_test,
                    )
                )
    return entrypoints


def _looks_like_test_file(file_path: Path) -> bool:
    name = file_path.name
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    return any(part in ("tests", "test") for part in file_path.parts)


def detect_test_entrypoints(
    report: DiscoveryReport, symbol_index: dict[str, ModuleSymbolTable]
) -> list[Entrypoint]:
    entrypoints: list[Entrypoint] = []
    for module in report.modules:
        if not _looks_like_test_file(module.file_path):
            continue
        table = symbol_index.get(module.dotted_name)
        if table is None:
            continue
        for node in table.nodes:
            if node.kind not in (NodeKind.FUNCTION, NodeKind.ASYNCFUNCTION, NodeKind.METHOD):
                continue
            base_name = node.qualname.rsplit(".", 1)[-1].split("@")[0]
            if base_name.startswith("test_"):
                entrypoints.append(
                    Entrypoint(
                        source=EntrypointSource.TEST_FUNCTION,
                        node_id=node.node_id,
                        module=module.dotted_name,
                        file=node.file,
                        lineno=node.lineno,
                        is_test=True,
                    )
                )
    return entrypoints


def build_entrypoint_index(
    report: DiscoveryReport, symbol_index: dict[str, ModuleSymbolTable], repo_root: Path
) -> list[Entrypoint]:
    all_entrypoints = (
        detect_dunder_main_entrypoints(report, symbol_index)
        + detect_console_script_entrypoints(repo_root, symbol_index)
        + detect_decorator_entrypoints(symbol_index)
        + detect_test_entrypoints(report, symbol_index)
    )
    seen: set[tuple[EntrypointSource, str]] = set()
    deduped: list[Entrypoint] = []
    for ep in all_entrypoints:
        key = (ep.source, ep.node_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ep)
    return deduped
