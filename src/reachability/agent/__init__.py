"""U6: file-based prompt-versioning scaffolding, and the eval harness.

This package hosts two independent things:

(a) forward-compatible, file-based prompt-versioning scaffolding
    (`prompt_registry.py`, `prompts/v1/*.md`) describing what a future
    live-LLM client would read -- not consumed by any live-LLM client
    today, since none exists yet (`triage/stub_llm.py` is a deterministic
    policy stub with no prompt-reading code path); and

(b) the U6 eval harness (`eval_harness.py`), which runs U3's full agent
    loop (`triage/agent_loop.py::run_triage_loop`) over the frozen
    `tests/fixtures/l5/` corpus, gated by pre-registered thresholds in
    `agent_docs/U6_EVAL_PROTOCOL.md`.
"""
