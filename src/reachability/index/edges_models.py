from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ResolutionRule(str, Enum):
    LOCAL_SCOPE = "local_scope"
    ENCLOSING_SCOPE = "enclosing_scope"
    MODULE_SCOPE = "module_scope"
    IMPORT_ALIAS = "import_alias"
    MODULE_ATTRIBUTE = "module_attribute"
    BUILTIN = "builtin"
    SELF_MRO = "self_mro"
    SELF_UNRESOLVED = "self_unresolved"
    UNRESOLVED_ATTRIBUTE = "unresolved_attribute"
    UNRESOLVED_NAME = "unresolved_name"
    DYNAMIC_DISPATCH = "dynamic_dispatch"


_RULE_CONFIDENCE: dict[ResolutionRule, Confidence] = {
    ResolutionRule.LOCAL_SCOPE: Confidence.HIGH,
    ResolutionRule.ENCLOSING_SCOPE: Confidence.HIGH,
    ResolutionRule.MODULE_SCOPE: Confidence.HIGH,
    ResolutionRule.IMPORT_ALIAS: Confidence.HIGH,
    ResolutionRule.MODULE_ATTRIBUTE: Confidence.HIGH,
    ResolutionRule.BUILTIN: Confidence.HIGH,
    ResolutionRule.SELF_MRO: Confidence.MEDIUM,
    ResolutionRule.SELF_UNRESOLVED: Confidence.LOW,
    ResolutionRule.UNRESOLVED_ATTRIBUTE: Confidence.LOW,
    ResolutionRule.UNRESOLVED_NAME: Confidence.LOW,
    ResolutionRule.DYNAMIC_DISPATCH: Confidence.LOW,
}


@dataclass(frozen=True)
class CallEdge:
    caller_id: str
    callee_id: str
    confidence: Confidence
    file: Path
    lineno: int
    resolution_rule: ResolutionRule

    def __post_init__(self) -> None:
        expected = _RULE_CONFIDENCE[self.resolution_rule]
        if self.confidence != expected:
            raise ValueError(
                f"confidence {self.confidence} does not match the fixed confidence "
                f"{expected} for resolution_rule {self.resolution_rule}"
            )
