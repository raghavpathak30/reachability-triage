from dataclasses import dataclass
from pathlib import Path

from reachability.index import (
    CallEdge,
    DiscoveryReport,
    Entrypoint,
    ModuleSymbolTable,
    build_edge_index,
    build_entrypoint_index,
    build_l1_index,
    build_symbol_index,
)


class IndexBuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepoIndex:
    report: DiscoveryReport
    symbol_index: dict[str, ModuleSymbolTable]
    edges: list[CallEdge]
    entrypoints: list[Entrypoint]


def build_repo_index(repo_root: Path) -> RepoIndex:
    if not repo_root.is_dir():
        raise IndexBuildError(f"repo_root does not exist or is not a directory: {repo_root}")

    try:
        report = build_l1_index(repo_root)
    except Exception as exc:
        raise IndexBuildError(
            f"L1 module discovery/import mapping failed for repo at {repo_root}: {exc}"
        ) from exc

    try:
        symbol_index = build_symbol_index(report)
    except Exception as exc:
        raise IndexBuildError(f"L2 symbol table construction failed: {exc}") from exc

    try:
        edges = build_edge_index(report, symbol_index)
    except Exception as exc:
        raise IndexBuildError(f"L3 call-edge extraction failed: {exc}") from exc

    try:
        entrypoints = build_entrypoint_index(report, symbol_index, repo_root)
    except Exception as exc:
        raise IndexBuildError(f"L4 entrypoint detection failed: {exc}") from exc

    return RepoIndex(
        report=report,
        symbol_index=symbol_index,
        edges=edges,
        entrypoints=entrypoints,
    )
