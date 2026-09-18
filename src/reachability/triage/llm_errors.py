"""Typed error taxonomy for a real LLM client (`groq_llm.py`).

Every failure mode a live LLM API call can produce is mapped, at the
client boundary, to exactly one of these classes -- never a raw SDK
exception, never a bare `Exception`. `langgraph_loop.py`'s agent node
catches each of the six `LLMError` subclasses by exact class (U2) and
degrades the run to `Verdict.UNKNOWN` with a reason string derived from
the class; `GroqConfigError` is deliberately not one of the six -- a
missing/invalid API key is a startup/configuration failure, not a
per-request degradation case, and must fail job/worker construction
instead of being caught mid-loop (see `job_runner.py`/`worker.py`).
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for every real-LLM-client failure this project maps."""


class LLMTimeoutError(LLMError):
    """The request exceeded its configured timeout. Transient -- retried."""


class LLMRateLimitedError(LLMError):
    """The provider returned HTTP 429. Transient -- retried."""


class LLMTransportError(LLMError):
    """A network/connection failure below the HTTP response layer.
    Transient -- retried."""


class LLMRefusalError(LLMError):
    """The model declined to continue (a refusal signal in the response).
    Not retried -- retrying a refusal would not change the model's
    decision and burns budget for no benefit."""


class LLMTruncatedError(LLMError):
    """The response was cut off before completion (`finish_reason ==
    "length"`). Not retried -- a longer completion is not obtained by
    resending the identical request."""


class LLMMalformedResponseError(LLMError):
    """The response could not be parsed into a well-formed tool call or
    final answer (invalid JSON arguments, missing required fields, or an
    unrecognized `finish_reason`). Not retried, same rationale as
    truncation."""


class GroqConfigError(LLMError):
    """Missing or invalid Groq configuration (e.g. `GROQ_API_KEY` unset).
    Raised at client-construction/worker-startup time, never caught by
    the running loop's per-call error handling."""
