"""The untrusted-text boundary between tool results and the agent's context.

Every string that a tool call (`search_symbol`/`find_callers`/
`resolve_import`, or their stringified results) hands back to the triage
agent loop passes through `sandbox_untrusted_text` before it is appended to
the loop's `list[Message]` context (`agent_loop.py`'s `_append_tool_result`
is the one call site). This module exists, and this function is named and
called from U3's very first commit, specifically so the safety boundary is
never something later "remembered" and retrofitted onto an already-gated
loop -- see `agent_docs/PHASE2_TRIAGE_AGENT.md:140-146` and
`DECISIONS.md`'s D3/D5 precedent, which this project has already paid for
twice.

U3's commit landed this function as an explicit identity passthrough; U4
(this revision) fills in the real body below. Only the function's *body*
changed between those two states -- its signature, its docstring's
contract, and every caller's call site stayed fixed, so U3's gate (i)
tests re-run byte-for-byte unmodified against this real implementation
(`tests/test_triage_agent_loop.py`), per the plan's stated success
criterion. `agent_loop.py` itself has zero diff between the commit that
landed the stub and the commit that landed this real body.

`sandbox_untrusted_text` performs three independent, non-raising passes
over `raw` and returns a redacted copy. These are **heuristic, regex/
substring-driven detectors, not perfect ones** -- the same "resolve
toward safety over a confident wrong answer" precedent this project
already established at the L3/L5 layer (CLAUDE.md's L5 paragraph)
applies here too: over-redaction of benign text is an accepted
trade-off, silently letting an attack through is not.

1. **Verdict manipulation** -- imperative phrasing ("ignore previous
   instructions", "disregard", "report as", ...) appearing near a
   `Verdict` enum member's text (`REACHABLE`, `NOT_REACHABLE`, `UNKNOWN`,
   `REACHABLE_ONLY_FROM_TESTS`, case-insensitive), or near a
   `target_module=`/`target_symbol=`-shaped directive trying to redirect
   the agent onto a different target. Matches are replaced with
   `"[REDACTED: instruction-like text]"`.
2. **Unauthorized tool-invocation instructions** -- phrasing like "call
   the `X` tool", "invoke `X`", "use `search_symbol`/`find_callers`/
   `resolve_import`" in imperative context. Same redaction marker.
3. **Context echo**:
   a. a generic absolute-path regex, redacted **unconditionally** (not
      root-relative) -- `sandbox_untrusted_text`'s one-argument signature
      (`(raw: str) -> str`, per spec) has no way to receive the scanned
      repo's root to tell a benign in-repo absolute path from a malicious
      out-of-repo one, so this is a deliberate over-redaction rather than
      a precise check. Known false-positive risk: a benign absolute path
      appearing in ordinary tool output (unlikely given the three tools'
      actual return shapes, but not impossible) would also be redacted.
   b. a generic high-entropy-token heuristic (a long run of mixed
      alphanumeric characters with no whitespace, the shape of an API key
      or bearer token), redacted unconditionally.
   c. a system-prompt-fragment heuristic (lines starting with
      `"system:"`/`"you are an ai"`/`"developer:"`, case-insensitive),
      redacted.

Tests that need to plant a specific literal secret string use
`temporary_secret_pattern(literal)` (below) rather than mutating the
module-level pattern list directly, so a mid-test failure can't leak a
registration into a later, unrelated test.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager

_REDACTION_MARKER = "[REDACTED: instruction-like text]"

# Category 1: verdict manipulation.
_VERDICT_NAMES = r"(?:REACHABLE_ONLY_FROM_TESTS|NOT_REACHABLE|REACHABLE|UNKNOWN)"
_VERDICT_MANIPULATION_PATTERNS = [
    re.compile(
        r"(?:ignore\s+(?:all\s+)?previous\s+instructions|disregard\s+(?:all\s+)?(?:previous|prior)"
        r"\s+instructions)[^.]*\." ,
        re.IGNORECASE,
    ),
    re.compile(
        r"report(?:\s+it)?\s+as\s+" + _VERDICT_NAMES,
        re.IGNORECASE,
    ),
    re.compile(
        r"report\s+" + _VERDICT_NAMES + r"\b[^.]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"target_module\s*=\s*\S+\s+target_symbol\s*=\s*\S+",
        re.IGNORECASE,
    ),
]

# Category 2: unauthorized tool-invocation instructions.
_TOOL_NAMES = r"(?:search_symbol|find_callers|resolve_import)"
_TOOL_INVOCATION_PATTERNS = [
    re.compile(r"call\s+(?:the\s+)?" + _TOOL_NAMES + r"\b[^.]*", re.IGNORECASE),
    re.compile(r"invoke\s+`?" + _TOOL_NAMES + r"`?[^.]*", re.IGNORECASE),
    re.compile(r"use\s+`?" + _TOOL_NAMES + r"`?\s+(?:tool|to)\b[^.]*", re.IGNORECASE),
]

# Category 3a: absolute filesystem paths, redacted unconditionally.
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<!\S)/[^\s\"']+")

# Category 3b: high-entropy tokens (API-key/bearer-token shaped).
_HIGH_ENTROPY_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}")

# Category 3c: system-prompt-fragment heuristics.
_SYSTEM_PROMPT_FRAGMENT_PATTERN = re.compile(
    r"^\s*(?:system:|you are an ai|developer:).*$",
    re.IGNORECASE | re.MULTILINE,
)

# Registered via temporary_secret_pattern only -- never mutated directly.
_SECRET_MARKER_PATTERNS: list[re.Pattern[str]] = []


@contextmanager
def temporary_secret_pattern(literal: str) -> Iterator[None]:
    """Register a literal secret string for redaction for the duration of
    a `with` block, then remove exactly that registration on exit --
    including when the block raises -- so a failing test can never leak a
    registration into a later, unrelated test.
    """
    pattern = re.compile(re.escape(literal))
    _SECRET_MARKER_PATTERNS.append(pattern)
    try:
        yield
    finally:
        _SECRET_MARKER_PATTERNS.remove(pattern)


def sandbox_untrusted_text(raw: str) -> str:
    """Sanitize one string of untrusted tool-result text before it is
    appended to the agent loop's context.

    Runs three independent, non-raising redaction passes (verdict
    manipulation, unauthorized tool-invocation instructions, context
    echo) and returns the redacted copy. See this module's docstring for
    the full detection-category breakdown and known false-positive risks.
    """
    text = raw

    for pattern in _VERDICT_MANIPULATION_PATTERNS:
        text = pattern.sub(_REDACTION_MARKER, text)

    for pattern in _TOOL_INVOCATION_PATTERNS:
        text = pattern.sub(_REDACTION_MARKER, text)

    text = _SYSTEM_PROMPT_FRAGMENT_PATTERN.sub(_REDACTION_MARKER, text)
    text = _ABSOLUTE_PATH_PATTERN.sub(_REDACTION_MARKER, text)
    text = _HIGH_ENTROPY_TOKEN_PATTERN.sub(_REDACTION_MARKER, text)

    for pattern in _SECRET_MARKER_PATTERNS:
        text = pattern.sub(_REDACTION_MARKER, text)

    return text
