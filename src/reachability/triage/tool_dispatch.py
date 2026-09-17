"""Shared tool-schema/dispatch/target-matching surface used by the
LangGraph triage loop (`langgraph_loop.py`), extracted from the now-deleted
hand-rolled triage loop module in Unit 6 of Phase LangGraph -- this exists
as a plan decision recorded here, not restated per-callsite.
"""

from __future__ import annotations

from reachability.index import find_callers, resolve_import, search_symbol

from .index_adapter import RepoIndex

TOOL_SCHEMAS: dict[str, tuple[tuple[str, type], ...]] = {
    "search_symbol": (("pattern", str),),
    "find_callers": (("node_id", str),),
    "resolve_import": (("module", str), ("name", str)),
}


class AgentLoopError(RuntimeError):
    """Raised only for a genuine internal-consistency bug in this module
    (its own dispatch table, or an `llm_client` returning an action type
    it doesn't recognize) -- never for a gate-(i) degradation case."""


def _is_well_formed_tool_call(tool_name: str, arguments: object) -> bool:
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        return False
    if not isinstance(arguments, dict):
        return False
    for key, expected_type in schema:
        if key not in arguments or not isinstance(arguments[key], expected_type):
            return False
    return True


def _dispatch_tool(tool_name: str, arguments: dict[str, object], repo_index: RepoIndex) -> object:
    if tool_name == "search_symbol":
        return search_symbol(repo_index.symbol_index, arguments["pattern"])
    if tool_name == "find_callers":
        return find_callers(repo_index.edges, arguments["node_id"])
    if tool_name == "resolve_import":
        return resolve_import(repo_index.report, arguments["module"], arguments["name"])
    raise AgentLoopError(f"unregistered tool in dispatch table: {tool_name!r}")


def _confirmed_id_matches_target(node_id: str, target_module: str, target_symbol: str | None) -> bool:
    """Harness-level check for case (b): does a `search_symbol`-confirmed
    `node_id` correspond to `(target_module, target_symbol)`?

    Deliberately independent of `reachability.py`'s own (private,
    unexported) target-matching logic -- this is harness bookkeeping, not
    a reachability computation, and does not need `?:`/`ext:`-prefixed id
    handling since `search_symbol` only ever returns first-party
    `SymbolNode`s from `repo_index.symbol_index`.
    """
    if ":" in node_id:
        module_part, qualname = node_id.split(":", 1)
    else:
        module_part, qualname = node_id, ""

    if module_part != target_module:
        return False
    if target_symbol is None:
        return not qualname
    if not qualname:
        return False
    final_segment = qualname.rsplit(".", 1)[-1].split("@")[0]
    return final_segment == target_symbol
