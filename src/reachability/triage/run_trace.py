"""Phase 8: pure helpers that turn a finished run's `ToolCallRecord`s into a
normalized, content-free tool-call trace and deterministic loop metrics, plus
the org-ID redaction walker shared by the local real-run scripts.

No I/O. Imported only by `scripts/` and tests -- never by the agent loop, the
job runner or the eval harness (a static test guards this). `ToolCallRecord.result`
(post-sandbox tool output, which carries any injected payload) is never read
here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from .agent_models import ToolCallRecord
from .tool_dispatch import TOOL_SCHEMAS

ORG_ID_RE = re.compile(r"org_[A-Za-z0-9_\-]+")

_MAX_ARG_CHARS = 200
_DIGEST_CHARS = 12


def redact_org_ids(value):
    """Recursively replaces Groq organization IDs in every string of a
    report structure (dict keys included)."""
    if isinstance(value, str):
        return ORG_ID_RE.sub("org_[REDACTED]", value)
    if isinstance(value, dict):
        return {redact_org_ids(k): redact_org_ids(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_org_ids(v) for v in value]
    return value


def _truncate(text: str) -> str:
    if len(text) <= _MAX_ARG_CHARS:
        return text
    return f"{text[:_MAX_ARG_CHARS]}...[+{len(text) - _MAX_ARG_CHARS} chars]"


def cap_reason(reason: str | None, limit: int = 300) -> str | None:
    """Length-caps a result `reason` for the real-run results rows only."""
    if reason is None or len(reason) <= limit:
        return reason
    return f"{reason[:limit]}...[+{len(reason) - limit} chars]"


def _stripped_args(tool_name: str, arguments: dict) -> dict:
    """Only the keys named in the tool's schema, in schema order; string
    values stripped, any other value replaced by its `repr`."""
    schema_keys = [key for key, _ in TOOL_SCHEMAS.get(tool_name, ())]
    stripped: dict = {}
    for key in schema_keys:
        if key not in arguments:
            continue
        value = arguments[key]
        stripped[key] = value.strip() if isinstance(value, str) else repr(value)
    return stripped


def _args_digest(stripped: dict) -> str:
    encoded = json.dumps(stripped, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:_DIGEST_CHARS]


def normalize_tool_arguments(tool_name: str, arguments: dict) -> dict:
    """Schema-keyed, stripped, org-ID-redacted, length-capped arguments."""
    return {
        key: _truncate(redact_org_ids(value))
        for key, value in _stripped_args(tool_name, arguments).items()
    }


def build_tool_call_trace(tool_calls: list[ToolCallRecord]) -> list[dict]:
    trace = []
    for record in tool_calls:
        stripped = _stripped_args(record.tool_name, record.arguments)
        trace.append(
            {
                "sequence": record.sequence,
                "tool_name": record.tool_name,
                "arguments": normalize_tool_arguments(record.tool_name, record.arguments),
                "args_digest": _args_digest(stripped),
            }
        )
    return trace


def loop_metrics_from_trace(trace: list[dict], limit: int | None = None) -> dict:
    entries = trace if limit is None else trace[:limit]
    keys = [(e["tool_name"], e["args_digest"]) for e in entries]
    n = len(keys)
    distinct = len(set(keys))
    longest = 0
    run = 0
    previous = None
    for key in keys:
        run = run + 1 if key == previous else 1
        previous = key
        longest = max(longest, run)
    return {
        "tool_call_count": n,
        "distinct_calls": distinct,
        "repeated_calls": n - distinct,
        "longest_identical_streak": longest,
        "per_tool_counts": dict(Counter(e["tool_name"] for e in entries)),
    }


def loop_metrics(tool_calls: list[ToolCallRecord], limit: int | None = None) -> dict:
    return loop_metrics_from_trace(build_tool_call_trace(tool_calls), limit=limit)
