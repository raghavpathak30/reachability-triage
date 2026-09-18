"""Lazy, single-source-of-truth configuration for the real Groq client.

Mirrors `src/reachability/db/session.py`'s lazy-read-and-cache pattern:
`GROQ_API_KEY` is read from the environment on first use, not at import
time, so tests can set/unset it before any client is constructed. Never
logs the key, never writes it to a file.
"""

from __future__ import annotations

import os

from .llm_errors import GroqConfigError

_CACHED_API_KEY: str | None = None

# Verified against this key's live `client.models.list()` output at
# implementation time (18 Sep 2026): "openai/gpt-oss-20b" is listed
# active and was exercised with a real tool-calling round trip
# (`search_symbol` invoked correctly from a natural-language prompt).
# Groq's supported-model list changes over time -- this is an
# operational risk (model deprecation), not a one-time fact; re-verify
# via `client.models.list()` before assuming this string is still valid.
GROQ_MODEL = "openai/gpt-oss-20b"

GROQ_TIMEOUT_SECONDS = 30.0

# Per-token price table, USD, keyed by model string. **Not verified against
# a live Groq pricing page at implementation time** (no fetch tool available
# in this environment) -- these are best-effort placeholder figures for the
# one pinned model above, not to be trusted for real billing/budget
# decisions without checking https://groq.com/pricing directly. U3's
# `total_cost_accrued` is a narrow, job-scoped cost *record* for observability,
# not the project-wide "cost accounting" capability CLAUDE.md's NOT BUILT list
# still names as absent (no budgets/alerts/aggregation here).
GROQ_PRICE_PER_MILLION_INPUT_TOKENS: dict[str, float] = {
    "openai/gpt-oss-20b": 0.10,
}
GROQ_PRICE_PER_MILLION_OUTPUT_TOKENS: dict[str, float] = {
    "openai/gpt-oss-20b": 0.50,
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Best-effort USD cost estimate for one call. Returns 0.0 for an
    unpriced model rather than raising -- an unknown price must never
    crash a real run over a bookkeeping gap."""
    input_price = GROQ_PRICE_PER_MILLION_INPUT_TOKENS.get(model, 0.0)
    output_price = GROQ_PRICE_PER_MILLION_OUTPUT_TOKENS.get(model, 0.0)
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000.0


def get_groq_api_key() -> str:
    """Lazily read and cache `GROQ_API_KEY`. Raises `GroqConfigError` if
    unset -- never returns an empty/placeholder key."""
    global _CACHED_API_KEY
    if _CACHED_API_KEY is None:
        value = os.environ.get("GROQ_API_KEY")
        if not value:
            raise GroqConfigError("GROQ_API_KEY is not set")
        _CACHED_API_KEY = value
    return _CACHED_API_KEY


def reset_api_key_cache_for_tests() -> None:
    """Clear the cached key so a test can simulate an unset/changed env
    var. Mirrors `db/session.py::reset_engine_for_tests`."""
    global _CACHED_API_KEY
    _CACHED_API_KEY = None
