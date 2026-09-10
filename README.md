# reachability-triage

An async service that triages dependency vulnerabilities by checking whether the
vulnerable code is actually reachable from your codebase — instead of reporting
every advisory that matches a version range.

**Status:** in development. Not usable yet.

## Why

A scanner that matches versions tells you a package you depend on has a CVE. It
doesn't tell you whether your code ever calls the vulnerable function. Most of
what a version-matching scanner reports is noise, and triaging that noise by hand
is where security teams lose their week. This service does that triage.

## Scope

Fixed on 18 Aug 2026. Not revisited.

- Python only.
- Direct imports and direct calls only.
- No dynamic dispatch, no `getattr`, no decorator indirection.

Anything outside this line is out of scope, permanently. The analyzer is not the
point of the project; the agent, the evals and the injection work are.

## Stack

Python · FastAPI · Pydantic · pytest · PostgreSQL · SQLAlchemy · Alembic ·
Docker · Docker Compose · GitHub Actions · AWS EC2

## Repo

- `main.py` — the API
- `src/reachability/index/` — AST index (see `agent_docs/PHASE1_AST_INDEX.md`). L1
  (module discovery + import map), L2 (symbol table), and L3 (call edge extraction)
  are built; L4–L5 are not.
- `DECISIONS.md` — dated design decisions and their reasoning
