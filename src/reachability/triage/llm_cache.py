"""Phase 5 U3: cache-key computation and the `TRIAGE_LLM_CACHE_DISABLED`
bypass flag for `GroqLLMClient` (`groq_llm.py`).

`compute_cache_key` derives a deterministic sha256 digest from
(`prompt_version`, `model`, `prompt_fingerprint`, `context`) -- the same
real LLM call, against the same prompt version, model, and exact
system-prompt/tool-schema text, always produces the same key, so a second
identical run (e.g. `tests/test_llm_cache.py`) never re-hits the real API.

`prompt_fingerprint` exists because `prompt_version` alone is a static
constant (`prompt_registry.PROMPT_VERSION`) that this project's own
convention never bumps for an in-code prompt-string edit (Phase 5's own
`_SYSTEM_PROMPT` glob-pattern fix, `agent_docs/PHASE5_INJECTION_REAL_MODEL.md`,
changed the literal prompt text without bumping it) -- omitting the actual
prompt/tool-schema text from the key would let a post-edit run silently
keep serving a pre-edit cached response. The caller (`groq_llm.py`) passes
a hash of its own `_SYSTEM_PROMPT` + serialized tool defs; this module
never constructs that fingerprint itself, to avoid a circular import.

`is_cache_disabled` mirrors `db/session.py`'s lazy-env-var-read pattern; it
must be read fresh on every call (not cached at import time), since
`TRIAGE_LLM_CACHE_DISABLED` is set per-CI-job (the `eval-real` workflow job
requires it -- see `.github/workflows/tests.yml`), not per-process-startup.
"""

from __future__ import annotations

import hashlib
import json
import os

from .agent_models import Message

_TRUTHY_VALUES = {"1", "true"}


def compute_cache_key(
    prompt_version: str, model: str, prompt_fingerprint: str, context: list[Message]
) -> str:
    serialized = json.dumps(
        [{"role": m.role, "content": m.content} for m in context], sort_keys=True
    )
    digest_input = prompt_version + model + prompt_fingerprint + serialized
    return hashlib.sha256(digest_input.encode()).hexdigest()


def is_cache_disabled() -> bool:
    """Lazy, per-call read of `TRIAGE_LLM_CACHE_DISABLED` -- never cached,
    unlike `llm_config.get_groq_api_key`, since this flag is expected to
    change per-invocation (e.g. one CI job sets it, another doesn't) and
    caching it would require the same test-only reset ceremony
    `db/session.py::reset_engine_for_tests` exists for, for no benefit."""
    return os.environ.get("TRIAGE_LLM_CACHE_DISABLED", "").strip().lower() in _TRUTHY_VALUES
