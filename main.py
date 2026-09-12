from enum import Enum
import sys
import uuid
from pathlib import Path

# `reachability.*` lives under src/, not on sys.path by default outside of
# pytest (conftest.py does this same insertion for the test suite) -- this
# mirrors that precedent so `uvicorn main:app --reload` (CLAUDE.md's
# documented run command) keeps starting cleanly now that main.py imports
# from reachability.triage below.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, HttpUrl, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from reachability.triage.agent_models import TriageFinding
from reachability.triage.job_runner import DEFAULT_TOOL_CALL_BUDGET, run_triage_job

app = FastAPI(title="Reachability Triage Service")

# In-memory store: {UUID: {"id": UUID, "status": TriageStatus, "target": dict,
#                          "finding": TriageFinding | None, "error": str | None}}
TRIAGE_DB: dict[uuid.UUID, dict] = {}


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
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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


class TriageOut(BaseModel):
    id: uuid.UUID
    status: TriageStatus
    finding: TriageFinding | None = None
    error: str | None = None


# --- Routes ---

@app.get("/healthz", status_code=status.HTTP_200_OK)
def healthz():
    return {"status": "ok"}


@app.post(
    "/v1/triage",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriageOut,
)
def create_triage(payload: TriageRequest, response: Response, background_tasks: BackgroundTasks):
    triage_id = uuid.uuid4()

    record = {
        "id": triage_id,
        "status": TriageStatus.QUEUED,
        "target": (
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
        ),
        "finding": None,
        "error": None,
    }
    TRIAGE_DB[triage_id] = record

    background_tasks.add_task(run_triage_job, TRIAGE_DB, triage_id, payload)

    response.headers["Location"] = f"/v1/triage/{triage_id}"
    return record


@app.get("/v1/triage/{triage_id}", response_model=TriageOut)
def get_triage(triage_id: str):
    record = None
    try:
        parsed_id = uuid.UUID(triage_id)
        record = TRIAGE_DB.get(parsed_id)
    except ValueError:
        pass # It's not a valid UUID, so record stays None

    if record is None:
        raise StarletteHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": f"Triage job '{triage_id}' does not exist.",
            },
        )
    return record
