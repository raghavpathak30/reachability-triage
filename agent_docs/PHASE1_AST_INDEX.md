# Phase 1 — AST Index & Reachability

Drop this in `agent_docs/PHASE1_AST_INDEX.md`. It is the spec the `/loop` planner reads.

---

## 0. What Phase 1 decides

Phase 1 answers one question:

> Does a call path exist, in this repository's own code, from an entrypoint to symbol `S` exported by package `P`?

It does **not** analyse inside `P`. Reachability from `P`'s public API down to the vulnerable
internal function is Phase 2 work and needs the dependency source on disk. Scoping it out here
is deliberate — say so in the README so it isn't read as a silent limitation.

Scope: **Python source only**. `ast` is stdlib, so there is no parser dependency and no
tree-sitter grammar to maintain. Other languages are out of scope for the whole project until
Python is measured.

**The dangerous error is a false `not_reachable`** — telling someone not to patch something that
is in fact exploitable. Every design tradeoff below resolves toward emitting `unknown` instead.

---

## 1. Storage

In-memory during a run, serialised to `.reachability/index.json` at the end. No Postgres,
no SQLAlchemy, no Alembic in this phase — that is Phase 3, and pulling it forward will eat the
two weeks. `requirements.txt` gains nothing in Phase 1 except test deps.

---

## 2. Node and edge model

Node ID scheme (stable, greppable, human-readable in evidence output):

```
pkg.module:Class.method
pkg.module:function
pkg.module                     # module-level code
?:name                         # unresolved callee, name-only
```

Every node carries `file`, `lineno`, `kind` (function/asyncfunction/class/method/module),
`decorators` (list of resolved decorator IDs).

Every edge carries:

```
caller_id, callee_id, confidence, file, lineno, resolution_rule
```

`resolution_rule` is the name of the branch that produced the edge (`local_scope`,
`import_alias`, `module_attribute`, `self_mro`, `unresolved_attribute`, ...). When a verdict is
wrong you need to know which rule lied, not just that the graph did.

`confidence` is `high` / `medium` / `low`. It is a property of the resolution rule, not a
learned score — do not invent a number.

---

## 3. Build order

Five `/loop` units. Each is independently testable and independently revertable. Do not start a
unit before the previous one's gate passes.

### L1 — Module discovery + import map

- Walk the repo for `.py` files; map file path → dotted module name. Handle `src/` layout,
  packages with `__init__.py`, and PEP 420 namespace packages.
- Parse each file with `ast.parse`. On `SyntaxError`, record the file as `unparsed` and
  continue — never abort a run on one bad file, and surface the count in the final report.
- Extract `Import` / `ImportFrom` into a per-module alias table:
  `local_name → (target_module, target_symbol | None, is_relative, resolved_absolute)`.
- Resolve relative imports (`from .. import x`) using the module's own package position.
- Classify each target as `first_party` (resolves inside the repo), `third_party`, or `stdlib`
  (`sys.stdlib_module_names`).
- `from x import *`: record as an unresolved star-import on the module. Do not expand it by
  guessing. Count it.

**Gate:** fixture package with src-layout, a two-level relative import, `import numpy as np`,
`from a.b import c as d`, and one star-import produces the exact expected alias table. ~10 tests.

### L2 — Symbol table

- Per module: functions, async functions, classes, methods, nested functions (flattened to
  qualnames), and module-level assignments binding a callable (`handler = do_thing`).
- Record decorators as resolved node IDs where possible — L4 needs them for entrypoint
  detection.
- Class bases recorded as node IDs when first-party, as raw strings otherwise.

**Gate:** a fixture module with a nested function, a class with a method, an async method, and a
callable alias yields exactly the expected node set with correct linenos.

### L3 — Call edge extraction

For every `ast.Call` inside every function body, resolve the callee:

| Callee shape | Resolution | Confidence |
|---|---|---|
| `Name` | local scope → enclosing → module scope → import alias → builtins | high |
| `Attribute` rooted at an import alias (`yaml.load`, `np.linalg.norm`) | walk the dotted chain against the alias table | high |
| `self.method()` | resolve within the class, then first-party MRO | medium |
| `Attribute` on any other expression (`obj.load()`) | **do not infer the receiver type.** Emit edge to `?:load` | low |
| `getattr(...)`, dict dispatch, call-on-call-result | emit `?:<dynamic>`, record the site | low |

That fourth row is the whole honesty story. The fraction of call sites landing in `low` is the
measured blindness of the call graph, and it goes in the README as a headline number, not a
footnote.

**Gate:** fixture with one of each row above produces edges with the expected
`resolution_rule`. Plus one negative test: an `obj.load()` where a same-named first-party
function exists must **not** produce an edge to that function.

### L4 — Entrypoints + BFS + query layer

Entrypoint detection:

- `if __name__ == "__main__"` blocks
- `console_scripts` / `[project.scripts]` in `pyproject.toml` or `setup.py`
- Route decorators (FastAPI, Flask, Django URLconf), Celery tasks
- CLI command decorators (click, typer) and argparse dispatch targets
- Test functions (`test_*`, pytest fixtures) — collected as a **separate** entrypoint set

BFS from the entrypoint set over the edge list. Target match: any node whose module is `P`
(optionally narrowed to symbol `S`).

Four verdicts:

- `reachable` — path exists from a non-test entrypoint, all edges `high`/`medium`
- `reachable_only_from_tests` — path exists only from the test entrypoint set
- `not_reachable` — no path, **and** no `low`-confidence edge sits on any partial path toward `P`
- `unknown` — no confident path, but an unresolved call site could bridge the gap

Most tools collapse the fourth into the third. Not collapsing it is the reason yours is worth
running.

Output for a `reachable` verdict is the path itself — `file:lineno` per hop, with the
`resolution_rule` for each edge. The verdict is the headline; the evidence chain is the product.

Query layer (thin functions over the index, later exposed as Phase 2's agent tools):

```python
search_symbol(pattern)        -> [node]
find_callers(node_id)         -> [edge]
resolve_import(module, name)  -> node_id | None
```

**Gate:** on a fixture repo with a known-reachable and a known-unreachable dependency call, BFS
returns the right verdict and a path whose linenos actually contain the calls.

### L5 — Fixture corpus + measurement

12–20 small hand-labelled repos, one behaviour each:

1. Direct call to the vulnerable symbol
2. Two-hop transitive call
3. Imported but never called
4. Called only in `tests/`
5. Called behind `if TYPE_CHECKING:` (import exists, call never executes)
6. Dynamic dispatch (`getattr`) onto the symbol → must be `unknown`, not `not_reachable`
7. Called from a FastAPI route
8. Called from a `console_scripts` entrypoint
9. Vendored copy of the package inside the repo
10. Conditional import inside a `try/except ImportError`
11. Star-import then call → must be `unknown`
12. Call through a class method on a first-party subclass

Metrics reported after every run:

- Recall on the reachable set
- **False `not_reachable` count** — the only metric that can veto the phase
- `unknown` rate
- Fraction of call sites resolved `low`

**Phase gate — pre-register this before running the corpus:**

> If false `not_reachable` > 0 on the fixture corpus, the tool emits `unknown` for that class of
> input rather than a negative verdict, and the README says so.

Same discipline you used for the banking-AUC claim. Write the threshold down, commit it, then
measure.

---

## 4. What is explicitly not in Phase 1

Postgres · background workers · quotas · token accounting · prompt versioning · any LLM call ·
Docker · deployment · cross-file type inference · analysis inside third-party packages · perf work

If a `/loop` iteration proposes any of these, the critic rejects the plan.
