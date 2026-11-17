import re
from enum import Enum

from reachability.index.reachability import (
    LOW_CONFIDENCE_REASON_PREFIX,
    NAME_ESCAPE_REASON_TEMPLATE,
    NO_ENTRYPOINTS_REASON,
    OPAQUE_CALL_REASON_PREFIX,
)


class ReasonCode(str, Enum):
    NOT_IMPORTED_BY_APP_CODE = "NOT_IMPORTED_BY_APP_CODE"
    NO_PATH_FOUND = "NO_PATH_FOUND"
    LOW_CONFIDENCE_PATH = "LOW_CONFIDENCE_PATH"
    OPAQUE_CALL = "OPAQUE_CALL"
    NAME_ESCAPE = "NAME_ESCAPE"
    NO_APP_ENTRYPOINTS = "NO_APP_ENTRYPOINTS"
    NO_FIX_VERSION = "NO_FIX_VERSION"
    NO_WHEEL_FOR_FIX = "NO_WHEEL_FOR_FIX"
    NATIVE_ONLY_CHANGE = "NATIVE_ONLY_CHANGE"
    DEPENDENCY_PIN_FIX = "DEPENDENCY_PIN_FIX"
    NO_CANDIDATE = "NO_CANDIDATE"
    NO_WHEEL = "NO_WHEEL"
    SCAN_BUDGET = "SCAN_BUDGET"


class EvidenceLevel(str, Enum):
    PATH_PROVEN = "path_proven"
    MODULE_IMPORTED_BY_APP = "module_imported_by_app"
    NO_TARGET_SYMBOL = "no_target_symbol"
    PACKAGE_INSTALLED_NOT_IMPORTED = "package_installed_not_imported"


_IMPORTED = EvidenceLevel.MODULE_IMPORTED_BY_APP
_NO_SYMBOL = EvidenceLevel.NO_TARGET_SYMBOL

# Spec section 2.5: which evidence levels a code is allowed at (S2a assigns levels;
# S1 emits no level for any unknown finding).
ALLOWED_LEVELS: dict[ReasonCode, frozenset[EvidenceLevel]] = {
    ReasonCode.NOT_IMPORTED_BY_APP_CODE: frozenset({EvidenceLevel.PACKAGE_INSTALLED_NOT_IMPORTED}),
    ReasonCode.NO_PATH_FOUND: frozenset({_IMPORTED}),
    ReasonCode.LOW_CONFIDENCE_PATH: frozenset({_IMPORTED}),
    ReasonCode.OPAQUE_CALL: frozenset({_IMPORTED}),
    ReasonCode.NAME_ESCAPE: frozenset({_IMPORTED}),
    ReasonCode.NO_APP_ENTRYPOINTS: frozenset({_IMPORTED}),
    ReasonCode.NO_FIX_VERSION: frozenset({_NO_SYMBOL}),
    ReasonCode.NO_WHEEL_FOR_FIX: frozenset({_NO_SYMBOL}),
    ReasonCode.NATIVE_ONLY_CHANGE: frozenset({_NO_SYMBOL}),
    ReasonCode.DEPENDENCY_PIN_FIX: frozenset({_IMPORTED, _NO_SYMBOL}),
    ReasonCode.NO_CANDIDATE: frozenset({_NO_SYMBOL}),
    ReasonCode.NO_WHEEL: frozenset({_NO_SYMBOL}),
    ReasonCode.SCAN_BUDGET: frozenset({_IMPORTED, _NO_SYMBOL}),
}

# Codes S1 can produce (the other registry codes are S2a's).
S1_EMITTED = frozenset(ReasonCode) - {ReasonCode.NOT_IMPORTED_BY_APP_CODE, ReasonCode.DEPENDENCY_PIN_FIX}

# Aggregation over one advisory's targets: first match wins.
REASON_PRIORITY = (
    ReasonCode.LOW_CONFIDENCE_PATH,
    ReasonCode.OPAQUE_CALL,
    ReasonCode.NAME_ESCAPE,
    ReasonCode.NO_PATH_FOUND,
)

_prefix, _suffix = NAME_ESCAPE_REASON_TEMPLATE.split("{target_symbol}")
_NAME_ESCAPE_RE = re.compile(re.escape(_prefix) + r".*" + re.escape(_suffix), re.DOTALL)


def classify_engine_reason(reason: str | None) -> ReasonCode | None:
    """Map an engine UNKNOWN reason string to a code, or None if it is not recognised."""
    if reason is None:
        return None
    if reason == NO_ENTRYPOINTS_REASON:
        return ReasonCode.NO_APP_ENTRYPOINTS
    if reason.startswith(LOW_CONFIDENCE_REASON_PREFIX):
        return ReasonCode.LOW_CONFIDENCE_PATH
    if reason.startswith(OPAQUE_CALL_REASON_PREFIX):
        return ReasonCode.OPAQUE_CALL
    if _NAME_ESCAPE_RE.fullmatch(reason):
        return ReasonCode.NAME_ESCAPE
    return None


def aggregate_reasons(codes: list[ReasonCode]) -> ReasonCode:
    for code in REASON_PRIORITY:
        if code in codes:
            return code
    return ReasonCode.NO_PATH_FOUND
