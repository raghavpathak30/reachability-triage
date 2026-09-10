from dataclasses import dataclass
from enum import Enum

from .edges_models import CallEdge


class Verdict(str, Enum):
    REACHABLE = "reachable"
    REACHABLE_ONLY_FROM_TESTS = "reachable_only_from_tests"
    NOT_REACHABLE = "not_reachable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReachabilityResult:
    target_module: str
    target_symbol: str | None
    verdict: Verdict
    path: list[CallEdge] | None
    reason: str | None
