"""Deterministic stub LLM clients for the U3/U4 triage agent loop.

`run_triage_loop` (`agent_loop.py`) takes an `llm_client` argument and
never calls a live LLM API -- every gate in this project (gate (i),
gate (ii), and U4's adversarial gate) runs against one of the small,
deterministic policy objects defined here (or, for U4's adversarial
tests, a deliberately naive one defined locally in
`tests/test_triage_agent_loop_adversarial.py`, never imported by this
module or any other `src/` code). Non-determinism from a live API cannot
gate CI -- see `agent_docs/PHASE2_TRIAGE_AGENT.md` §3, U3 gate (ii).

`DeterministicPolicyStubLLMClient` is the well-behaved client used for
gate (i) and gate (ii). It never inspects `repo_index` directly and holds
no reference to `edges`/`symbol_index` -- like a real LLM, it only reads
the `context: list[Message]` it is handed by `run_triage_loop`, plus
whatever state it decided to remember about its own prior requests. It
learns the confirmed target node id, and each newly discovered caller id,
by parsing the *stringified* tool-result text the loop appended to
context (the default `repr()` of the `list[SymbolNode]`/`list[CallEdge]`
tool return values, which is what `agent_loop.py` passes to
`sandbox_untrusted_text` before appending it as a `role="tool"`
`Message`). This is why it is deterministic per fixture without any
hand-written per-fixture script: given the same repo index, the same
tool-result text is produced every time.

Its policy performs a **backward BFS via repeated `find_callers` calls**,
not a single hop: after confirming the target's node id via one
`search_symbol` call, it calls `find_callers` on that node id, then on
every newly-discovered `caller_id` from that call's results, continuing
until a fixed point (no new caller ids remain to query) or the loop's own
budget forces early termination. A single-hop client cannot satisfy
gate (ii)'s path-provenance check on a multi-hop fixture like
`tests/fixtures/l5/02_transitive_three_hop` (a 3-edge path) -- the
confirmed `TriageFinding.result.path` comes from `compute_reachability`'s
own full-graph BFS, independent of what tool calls were made, so gate
(ii) can only assert "every path edge was actually returned by some
`find_callers` call in this run" if the client actually walked the full
ancestor chain.

Once its own BFS frontier is exhausted, it emits a `FinalAnswerAction`
targeting the `(module, symbol)` pair fixed at its own construction --
never a target derived from tool-result text -- so gate (ii)'s comparison
against a direct `compute_reachability(target_module, target_symbol, ...)`
call is meaningful.

`AlwaysExceedsBudgetStubLLMClient`, `NamesMissingSymbolStubLLMClient`, and
`MalformedArgumentStubLLMClient`, used to drive gate (i)'s three
degradation cases, are deliberately *not* defined here: they exist only
to exercise `run_triage_loop`'s degradation paths and belong in
`tests/test_triage_agent_loop.py`, not in shipped code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .agent_models import Message

_NODE_ID_PATTERN = re.compile(r"node_id='([^']*)'")
_CALLER_ID_PATTERN = re.compile(r"caller_id='([^']*)'")


@dataclass(frozen=True)
class ToolCallAction:
    """A request to invoke one of the three L4 query tools."""

    tool_name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class FinalAnswerAction:
    """A request to stop gathering evidence and answer.

    `target_module`/`target_symbol` are the pair `run_triage_loop` will
    pass to `compute_reachability` -- the loop trusts these fields exactly
    as given, never substituting its own configured target, which is what
    makes injected-text "target switching" (see
    `tests/test_triage_agent_loop_adversarial.py`) a meaningful attack to
    defend against rather than a structurally impossible one. `rationale`
    is the client's own free-text explanation; per `agent_models.py`'s
    `TriageFinding` docstring, it is never read back to derive a verdict.
    """

    target_module: str
    target_symbol: str | None
    rationale: str


AgentAction = ToolCallAction | FinalAnswerAction


class StubLLMClient(Protocol):
    """Minimal interface `run_triage_loop` requires of any LLM client."""

    def next_action(self, context: list[Message]) -> AgentAction: ...


class DeterministicPolicyStubLLMClient:
    """Well-behaved stub client: confirm target, backward-BFS callers, answer.

    See this module's docstring for the full policy description and why
    it must be a backward BFS over repeated `find_callers` calls rather
    than a single hop.
    """

    def __init__(self, target_module: str, target_symbol: str | None) -> None:
        self.target_module = target_module
        self.target_symbol = target_symbol
        self._searched = False
        self._confirmed_node_id: str | None = None
        self._frontier: list[str] = []
        self._visited: set[str] = set()
        self._pending_tool: str | None = None

    def _search_pattern(self) -> str:
        if self.target_symbol is None:
            return self.target_module
        return f"*{self.target_symbol}"

    def _rationale(self) -> str:
        return (
            f"confirmed_node_id={self._confirmed_node_id!r}; "
            f"walked {len(self._visited)} caller node id(s) via backward BFS"
        )

    def next_action(self, context: list[Message]) -> AgentAction:
        if self._pending_tool == "search_symbol":
            latest = context[-1].content
            match = _NODE_ID_PATTERN.search(latest)
            if match is not None:
                self._confirmed_node_id = match.group(1)
                self._frontier.append(self._confirmed_node_id)
                self._visited.add(self._confirmed_node_id)
            self._pending_tool = None
        elif self._pending_tool == "find_callers":
            latest = context[-1].content
            for caller_id in _CALLER_ID_PATTERN.findall(latest):
                if caller_id not in self._visited:
                    self._visited.add(caller_id)
                    self._frontier.append(caller_id)
            self._pending_tool = None

        if not self._searched:
            self._searched = True
            self._pending_tool = "search_symbol"
            return ToolCallAction("search_symbol", {"pattern": self._search_pattern()})

        if self._confirmed_node_id is None:
            # search_symbol found nothing -- run_triage_loop's own case-(b)
            # check will degrade this to unknown; nothing more to gather.
            return FinalAnswerAction(self.target_module, self.target_symbol, self._rationale())

        if self._frontier:
            node_id = self._frontier.pop(0)
            self._pending_tool = "find_callers"
            return ToolCallAction("find_callers", {"node_id": node_id})

        return FinalAnswerAction(self.target_module, self.target_symbol, self._rationale())
