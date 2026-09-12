"""File-based prompt versioning scaffolding.

No caller in `triage/stub_llm.py` or `triage/agent_loop.py` exists today --
`DeterministicPolicyStubLLMClient` (`triage/stub_llm.py`) is a deterministic
Python policy object with no prompt-reading code path, and this module is not
something U6's own eval harness needs to call to do its job. This is
scaffolding for a later live-LLM phase: it describes, in versioned files
under `prompts/<version>/*.md`, what a future live-LLM client's system/tool
prompts would contain, gated behind `PROMPT_VERSION` so that version can
change without changing this module's code.
"""

from __future__ import annotations

from pathlib import Path

PROMPT_VERSION = "v1"
PROMPTS_ROOT = Path(__file__).parent / "prompts"


def load_prompt(name: str, version: str = PROMPT_VERSION) -> str:
    path = PROMPTS_ROOT / version / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no prompt file at {path}")
    return path.read_text()
