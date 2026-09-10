# Chapter 3: Request validation — the exactly-one-of rule

Chapter 2 walked `create_triage` and pointed at one line without explaining
it: by the time the handler body runs, `payload` is guaranteed to be either
a package/version pair or a `repo_url`, never both, never neither. This
chapter opens `TriageRequest` and its `@model_validator` to show exactly how
that guarantee is enforced, and how a plain `ValueError` raised inside a
Pydantic model becomes the `422` a client sees.

## `TriageStatus`: four states, one reachable today

`TriageStatus` is a `str`-subclassed `Enum` with four members:

```python
class TriageStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
```

Subclassing `str` alongside `Enum` is what lets FastAPI serialize a
`TriageStatus.QUEUED` value as the plain JSON string `"queued"` instead of
an object wrapper. Nothing in `main.py` constructs any member other than
`TriageStatus.QUEUED` — Chapter 1 already established why: there's no
worker to move a record through `RUNNING`, `COMPLETED`, or `FAILED`. The
enum declares a four-state machine; the running code only ever touches one
of its states. `DECISIONS.md` §1 describes the full transition diagram for
the other three — that's Chapter 5's territory, not this one.

## `TriageRequest`: three optional fields, one combination rule

```python
class TriageRequest(BaseModel):
    package: str | None = None
    version: str | None = None
    repo_url: HttpUrl | None = None
```

Every field defaults to `None`. Read in isolation, this model would accept
an empty body, a `package` with no `version`, a `repo_url` alongside a full
package spec — any combination at all, because nothing here says the fields
are related. The relationship is enforced separately, after Pydantic has
already validated each field on its own terms (in particular, `repo_url`
being typed `HttpUrl` means a malformed URL string is rejected by the field
type itself, before the model as a whole is ever considered valid).

## Walking `_validate_exactly_one_target`

```python
    @model_validator(mode="after")
    def _validate_exactly_one_target(self) -> "TriageRequest":
        has_pkg = self.package is not None
        has_ver = self.version is not None
        has_repo = self.repo_url is not None

        if has_pkg ^ has_ver:
            raise ValueError("Both 'package' and 'version' must be provided together.")

        has_package_pair = has_pkg and has_ver

        if has_package_pair and has_repo:
            raise ValueError("Provide either ('package' and 'version') OR 'repo_url', not both.")

        if not has_package_pair and not has_repo:
            raise ValueError("Must provide either ('package' and 'version') OR 'repo_url'.")

        return self
```

`mode="after"` means this method runs once, on a fully field-validated
instance — `self` already has whatever `package`, `version`, and `repo_url`
survived individual field validation. The method itself is three sequential
checks, each one a distinct failure the caller could have made:

1. **`has_pkg ^ has_ver`** — a boolean XOR. This is `True` only when exactly
   one of `package` / `version` was supplied and the other wasn't. It's the
   check that catches a half-given package spec: `{"package": "foo"}` with
   no `version` fails here, before the method even looks at `repo_url`.
2. **`has_package_pair and has_repo`** — once the XOR check has passed
   (so `package` and `version` are either both present or both absent),
   this catches the case where a complete package spec *and* a `repo_url`
   arrived together.
3. **`not has_package_pair and not has_repo`** — the remaining case: no
   complete package spec and no `repo_url`, i.e. an empty or irrelevant
   body.

Any request that falls through all three `if` blocks returns `self`
unchanged — the method's only job is to raise or not raise; it doesn't
modify the model.

## Every combination, worked through

`DECISIONS.md` §3 gives five worked examples. Walking the code against all
eight possible presence/absence combinations of the three fields confirms
which check catches each invalid case:

| `package` | `version` | `repo_url` | Result | Caught by |
|---|---|---|---|---|
| absent | absent | absent | Invalid | check 3 (neither) |
| absent | absent | present | **Valid** | — |
| absent | present | absent | Invalid | check 1 (XOR) |
| absent | present | present | Invalid | check 1 (XOR) |
| present | absent | absent | Invalid | check 1 (XOR) |
| present | absent | present | Invalid | check 1 (XOR) |
| present | present | absent | **Valid** | — |
| present | present | present | Invalid | check 2 (both given) |

Only two of the eight combinations pass: a package/version pair with no
`repo_url`, or a `repo_url` with neither `package` nor `version`. Every
partial or over-specified body is rejected, and each rejection carries a
distinct message string, even though — as Chapter 4 will show — the client
ultimately sees them all wrapped in the same `422` envelope.

> **Note:** the presence checks are `is not None`, not truthiness checks.
> `has_pkg = self.package is not None` is satisfied by an empty string.
> A body like `{"package": "", "version": ""}` passes both the XOR check
> and the "both given" check — the validator only confirms that *some*
> string was supplied for each field, not that it's a non-empty or
> meaningful one. There's no `min_length` or similar constraint on
> `package` or `version` anywhere in the model. `repo_url` doesn't have
> this gap, because `HttpUrl` field validation rejects an empty or
> unparseable string before `_validate_exactly_one_target` ever runs.

## Why this lives in the model, not the handler

`DECISIONS.md` §3 states the alternative that was considered and rejected:

> Enforcing this in the endpoint handler with an explicit
> `HTTPException(status_code=400)` was rejected to keep business schema
> rules unified inside the model layer.

The handler, `create_triage`, never sees an invalid `TriageRequest` — by
the time its body executes, `payload` has already passed through this
validator, because FastAPI constructs the model (and runs its validators)
while resolving the `payload: TriageRequest` parameter, before calling the
route function at all. Putting the rule in `_validate_exactly_one_target`
instead of a handler-level `if` statement means every consumer of
`TriageRequest` — this route today, any future route that happens to take
the same request shape — gets the rule for free, rather than each handler
needing to remember to re-check it. It also means the "exactly one target"
rule is enforced at the same layer, and produces the same class of error,
as ordinary field-level problems like a missing `version` key entirely.

## From `ValueError` to a `422`

`_validate_exactly_one_target` raises a bare `ValueError`, not a
`pydantic.ValidationError` and not a `fastapi.HTTPException`. That's
deliberate on Pydantic's part: a `ValueError` raised inside any validator —
field-level or, as here, model-level with `mode="after"` — is the
documented way to signal "this value is invalid" without importing Pydantic's
own exception classes into the model. Pydantic catches it during model
construction and folds it into the model's collected validation errors.

FastAPI is what turns that into an HTTP response. When it resolves the
`payload: TriageRequest` parameter for `create_triage`, it's Pydantic that
raises if construction fails; FastAPI catches the resulting error and
re-raises it as a `fastapi.exceptions.RequestValidationError` — the same
exception type FastAPI uses for a missing required field or a wrong type.
`main.py` registers a handler for exactly that type:

```python
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
```

That handler is what actually decides the response is `422` and what its
JSON body looks like — that's the whole subject of
[Chapter 4](04-error-handling.md). What's worth noting here, as direct
evidence that the mechanism described above is really happening, is a
couple of lines inside that handler:

```python
    first_msg = errors[0].get("msg") or "Validation error" if errors else "Validation error"
    if first_msg.startswith("Value error, "):
        first_msg = first_msg[len("Value error, "):]
```

Pydantic prefixes the message from a validator-raised `ValueError` with
`"Value error, "` when it reports the error. The handler strips that prefix
back off before building the response. That stripping logic only makes
sense if a bare `ValueError` — like the three raised inside
`_validate_exactly_one_target` — is exactly what's arriving at this
handler. It's the clearest evidence in the file that the path really is
`ValueError` inside the model → Pydantic validation error → FastAPI
`RequestValidationError` → this handler, and it's the last stop before
Chapter 4 picks up the story of how the rest of that response gets built.

## Trade-offs

Unifying the rule inside the model buys consistency: one rule, enforced
once, at the same point every other field constraint is enforced, producing
the same exception type as any other malformed body. It costs precision in
the response. Because the rule spans three fields at once rather than
belonging to any single one, none of the three raised messages can point a
client at "the `version` field" the way a per-field type error naturally
can — the error is about the *shape* of the whole body, and the message
text is the only thing carrying that information across the ValueError →
`422` boundary.

Checking presence with `is not None` rather than validating content buys
simplicity — three short boolean checks, no separate string-content rule to
maintain — at the cost of accepting technically-present-but-empty values
like `package: ""` as if they were real identifiers, since nothing later in
`main.py` inspects the string contents of `package` or `version` again.

## Check yourself

1. Which of the three checks in `_validate_exactly_one_target` rejects
   `{"package": "foo"}` with no `version` key, and which one rejects
   `{"package": "foo", "version": "1.0.0", "repo_url": "https://x.com/y"}`?
2. Why does `has_pkg = self.package is not None` accept
   `{"package": "", "version": ""}` as satisfying "both provided," and what
   stops the same gap from applying to `repo_url`?
3. According to `DECISIONS.md` §3, what alternative to model-level
   validation was considered for enforcing this rule, and why was it
   rejected?
4. What exception type does raising a bare `ValueError` inside
   `_validate_exactly_one_target` eventually turn into by the time it
   reaches `create_triage`'s caller, and which line of code in the
   registered exception handler is direct evidence that this is what's
   happening?
5. What does `mode="after"` mean for when `_validate_exactly_one_target`
   runs relative to the individual validation of `package`, `version`, and
   `repo_url`?
