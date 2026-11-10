from enum import Enum
import logging
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# `reachability.*` lives under src/, not on sys.path by default outside of
# pytest (conftest.py does this same insertion for the test suite) -- this
# mirrors that precedent so `uvicorn main:app --reload` (CLAUDE.md's
# documented run command) keeps starting cleanly now that main.py imports
# from reachability.triage below.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, HttpUrl, ValidationError, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from reachability.db.repository import create_job, get_job
from reachability.db.models import TriageJob
from reachability.db.session import get_sessionmaker
from reachability.triage.agent_models import TriageFinding

logger = logging.getLogger(__name__)

app = FastAPI(title="Reachability Triage Service", version="0.3.4")


# --- Exception Handlers (Uniform Error Shape) ---
@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Unpack custom detail dicts, or fallback to standard Starlette strings
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        code = exc.detail.get("code")
        message = exc.detail.get("message", str(exc.detail))
    else:
        code = "HTTP_ERROR"
        message = str(exc.detail)

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}}
    )
    
@app.exception_handler(HTTPException)
async def fastapi_http_exception_handler(request: Request, exc: HTTPException):
    # If detail is structured as a dict with code and message, unwrap it cleanly
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        payload = {
            "error": {
                "code": exc.detail.get("code"),
                "message": exc.detail.get("message", str(exc.detail)),
            }
        }
    else:
        payload = {
            "error": {
                "code": "HTTP_ERROR",
                "message": str(exc.detail),
            }
        }
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()

    # 1. Guard against 500s safely using 'or'
    first_msg = errors[0].get("msg") or "Validation error" if errors else "Validation error"
    if first_msg.startswith("Value error, "):
        first_msg = first_msg[len("Value error, "):]

    # 2. Strip Pydantic internals
    safe_details = [{"loc": err.get("loc", []), "msg": err.get("msg", "")} for err in errors]

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": first_msg,
                "details": safe_details,
            }
        },
    )

# --- Enums & Models ---

class TriageStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TriageRequest(BaseModel):
    package: str | None = None
    version: str | None = None
    repo_url: HttpUrl | None = None
    target_module: str = Field(min_length=1)
    target_symbol: str | None = None

    @field_validator("repo_url")
    @classmethod
    def _reject_repo_url_userinfo(cls, value: HttpUrl | None) -> HttpUrl | None:
        # Credentials in a URL would be stored in triage_jobs.target as-is.
        if value is not None and (value.username or value.password):
            raise ValueError("'repo_url' must not contain credentials (user:password@).")
        return value

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


class TriageTarget(BaseModel):
    """Read model for the stored submitted target. Deliberately tolerant:
    every field is optional, because the worker guards against malformed
    stored targets (tests/test_worker.py)."""

    package: str | None = None
    version: str | None = None
    repo_url: str | None = None
    target_module: str | None = None
    target_symbol: str | None = None

    @field_validator("package", "version", "repo_url", "target_module", mode="before")
    @classmethod
    def _empty_string_is_null(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("repo_url")
    @classmethod
    def _redact_repo_url_userinfo(cls, value: str | None) -> str | None:
        # Never echo stored credentials; the stored value is left unchanged.
        if value is None:
            return None
        try:
            parts = urlsplit(value)
            _ = parts.port  # raises ValueError on a malformed port
        except ValueError:
            return None
        host = parts.netloc.rpartition("@")[2]
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _target_out(raw) -> TriageTarget:
    try:
        return TriageTarget.model_validate(raw)
    except ValidationError:
        logger.warning("Stored triage target has an unexpected shape; returning an empty target")
        return TriageTarget()


class TriageOut(BaseModel):
    id: uuid.UUID
    status: TriageStatus
    finding: TriageFinding | None = None
    error: str | None = None
    target: TriageTarget
    llm_mode: str | None = Field(
        default=None,
        description=(
            "LLM client mode that produced the verdict: 'stub' (deterministic "
            "policy client, not a real model) or 'groq'. Null until the job "
            "is finalized. On a job that fails before the client is built it "
            "is still the resolved mode if TRIAGE_LLM_MODE resolved, else null."
        ),
    )


# --- Routes ---

@app.get("/healthz", status_code=status.HTTP_200_OK)
def healthz():
    return {"status": "ok"}


@app.post(
    "/v1/triage",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriageOut,
)
def create_triage(payload: TriageRequest, response: Response):
    target = (
        {
            "repo_url": str(payload.repo_url),
            "target_module": payload.target_module,
            "target_symbol": payload.target_symbol,
        }
        if payload.repo_url is not None
        else {
            "package": payload.package,
            "version": payload.version,
            "target_module": payload.target_module,
            "target_symbol": payload.target_symbol,
        }
    )

    session = get_sessionmaker()()
    try:
        triage_id = create_job(session, target)
        session.commit()
        record = get_job(session, triage_id)
    finally:
        session.close()

    response.headers["Location"] = f"/v1/triage/{triage_id}"
    return {**record, "target": _target_out(target)}


@app.get("/v1/triage/{triage_id}", response_model=TriageOut)
def get_triage(triage_id: str):
    record = None
    parsed_id = None
    try:
        parsed_id = uuid.UUID(triage_id)
    except ValueError:
        pass # It's not a valid UUID, so record stays None

    if parsed_id is not None:
        session = get_sessionmaker()()
        try:
            record = get_job(session, parsed_id)
            if record is not None:
                row = session.get(TriageJob, parsed_id)
                if row is None:
                    record = None
                else:
                    record = {**record, "target": _target_out(row.target)}
        finally:
            session.close()

    if record is None:
        raise StarletteHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": f"Triage job '{triage_id}' does not exist.",
            },
        )
    return record
