import uuid

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _postgres(db_session):
    yield


def test_healthz_returns_200():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_triage_package_version_valid():
    response = client.post(
        "/v1/triage",
        json={"package": "requests", "version": "2.31.0", "target_module": "placeholder"},
    )
    assert response.status_code == 202
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["status"] == "queued"


def test_create_triage_package_version_location_header():
    response = client.post(
        "/v1/triage",
        json={"package": "requests", "version": "2.31.0", "target_module": "placeholder"},
    )
    body = response.json()
    assert response.headers["location"] == f"/v1/triage/{body['id']}"


def test_create_triage_repo_url_valid():
    response = client.post(
        "/v1/triage",
        json={"repo_url": "https://github.com/org/repo", "target_module": "placeholder"},
    )
    assert response.status_code == 202
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["status"] == "queued"


def test_create_triage_repo_url_location_header():
    response = client.post(
        "/v1/triage",
        json={"repo_url": "https://github.com/org/repo", "target_module": "placeholder"},
    )
    body = response.json()
    assert response.headers["location"] == f"/v1/triage/{body['id']}"


def test_create_triage_both_shapes_given_422():
    response = client.post(
        "/v1/triage",
        json={
            "package": "requests",
            "version": "2.31.0",
            "repo_url": "https://github.com/org/repo",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_triage_neither_shape_given_422():
    response = client.post("/v1/triage", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_triage_package_without_version_422():
    response = client.post("/v1/triage", json={"package": "requests"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_triage_malformed_json_422():
    response = client.post(
        "/v1/triage",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_triage_happy_path():
    create_response = client.post(
        "/v1/triage",
        json={"package": "requests", "version": "2.31.0", "target_module": "placeholder"},
    )
    triage_id = create_response.json()["id"]

    response = client.get(f"/v1/triage/{triage_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == triage_id
    # As of U2, dispatch is exclusively the polling worker's job (U3) -- there
    # is no BackgroundTasks dispatch anymore, so with no worker running this
    # GET always finds the job still "queued".
    assert body["status"] == "queued"


def test_get_triage_unknown_id_404():
    unknown_id = uuid.uuid4()
    response = client.get(f"/v1/triage/{unknown_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_create_triage_response_model_fields_package_shape():
    response = client.post(
        "/v1/triage",
        json={"package": "requests", "version": "2.31.0", "target_module": "placeholder"},
    )
    body = response.json()
    assert set(body.keys()) == {"id", "status", "finding", "error", "llm_mode", "target"}


def test_create_triage_response_model_fields_repo_shape():
    response = client.post(
        "/v1/triage",
        json={"repo_url": "https://github.com/org/repo", "target_module": "placeholder"},
    )
    body = response.json()
    assert set(body.keys()) == {"id", "status", "finding", "error", "llm_mode", "target"}


def test_validation_error_envelope_shape():
    response = client.post("/v1/triage", json={})
    error = response.json()["error"]
    assert isinstance(error["code"], str)
    assert isinstance(error["message"], str)
    assert isinstance(error["details"], list)


def test_get_triage_after_multiple_creates_isolated():
    first = client.post(
        "/v1/triage",
        json={"package": "requests", "version": "2.31.0", "target_module": "placeholder"},
    ).json()
    second = client.post(
        "/v1/triage",
        json={"repo_url": "https://github.com/org/repo", "target_module": "placeholder"},
    ).json()

    first_get = client.get(f"/v1/triage/{first['id']}").json()
    second_get = client.get(f"/v1/triage/{second['id']}").json()

    assert first_get["id"] == first["id"]
    assert second_get["id"] == second["id"]
    assert first_get["id"] != second_get["id"]


def test_create_triage_persists_as_queued_with_no_worker_running():
    response = client.post(
        "/v1/triage",
        json={"package": "requests", "version": "2.31.0", "target_module": "placeholder"},
    )
    assert response.status_code == 202
    triage_id = response.json()["id"]

    get_response = client.get(f"/v1/triage/{triage_id}")
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["status"] == "queued"


def test_post_and_get_queued_job_return_posted_target():
    posted = {
        "package": "requests",
        "version": "2.31.0",
        "target_module": "requests.api",
        "target_symbol": "get",
    }
    post = client.post("/v1/triage", json=posted)
    assert post.status_code == 202
    expected = {**posted, "repo_url": None}
    assert post.json()["target"] == expected

    got = client.get(f"/v1/triage/{post.json()['id']}")
    assert got.status_code == 200
    assert got.json()["target"] == expected


def _insert_job(db_session, target):
    from reachability.db.models import TriageJob

    job_id = uuid.uuid4()
    db_session.add(TriageJob(id=job_id, status="queued", target=target, finding=None, error=None))
    db_session.commit()
    return job_id


def test_get_running_job_returns_target(db_session):
    from reachability.triage.worker import claim_next_queued_job

    posted = {
        "package": "requests",
        "version": "2.31.0",
        "target_module": "requests.api",
        "target_symbol": "get",
    }
    triage_id = client.post("/v1/triage", json=posted).json()["id"]
    assert claim_next_queued_job(db_session, "test-worker") is not None

    body = client.get(f"/v1/triage/{triage_id}").json()
    assert body["status"] == "running"
    assert body["target"] == {**posted, "repo_url": None}


def test_repo_shape_target_has_no_package(db_session):
    job_id = _insert_job(
        db_session,
        {"repo_url": "https://github.com/org/repo", "target_module": "m", "target_symbol": None},
    )
    response = client.get(f"/v1/triage/{job_id}")
    assert response.status_code == 200
    target = response.json()["target"]
    assert target["package"] is None
    assert target["version"] is None
    assert target["repo_url"] == "https://github.com/org/repo"
    assert target["target_module"] == "m"


def test_get_stored_target_without_target_module_returns_null_module(db_session):
    job_id = _insert_job(db_session, {"package": "p", "version": "1"})
    response = client.get(f"/v1/triage/{job_id}")
    assert response.status_code == 200
    target = response.json()["target"]
    assert target["target_module"] is None
    assert target["package"] == "p"


def test_get_stored_empty_target_returns_all_null(db_session):
    job_id = _insert_job(db_session, {})
    response = client.get(f"/v1/triage/{job_id}")
    assert response.status_code == 200
    assert set(response.json()["target"].values()) == {None}


def test_get_stored_wrong_typed_target_module_returns_all_null_and_warns(db_session, caplog):
    job_id = _insert_job(db_session, {"package": "p", "version": "1", "target_module": 5})
    with caplog.at_level("WARNING", logger="main"):
        response = client.get(f"/v1/triage/{job_id}")
    assert response.status_code == 200
    assert set(response.json()["target"].values()) == {None}
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and r.name == "main"]
    assert len(warnings) == 1


@pytest.mark.parametrize("stored", [[], "x", 42])
def test_get_stored_non_object_target_returns_all_null_and_warns(db_session, caplog, stored):
    job_id = _insert_job(db_session, stored)
    with caplog.at_level("WARNING", logger="main"):
        response = client.get(f"/v1/triage/{job_id}")
    assert response.status_code == 200
    target = response.json()["target"]
    assert set(target) == {"package", "version", "repo_url", "target_module", "target_symbol"}
    assert set(target.values()) == {None}
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and r.name == "main"]
    assert len(warnings) == 1


def test_get_redacts_userinfo_from_stored_repo_url(db_session):
    job_id = _insert_job(
        db_session,
        {"repo_url": "https://user:secret@github.com/a/b", "target_module": "m", "target_symbol": None},
    )
    response = client.get(f"/v1/triage/{job_id}")
    assert response.status_code == 200
    assert response.json()["target"]["repo_url"] == "https://github.com/a/b"
    assert "secret" not in response.text
    assert "user" not in response.json()["target"]["repo_url"]


def test_get_redaction_keeps_port_path_query_fragment(db_session):
    job_id = _insert_job(
        db_session,
        {"repo_url": "https://tok@Example.com:8443/a/b.git?x=1#frag", "target_module": "m"},
    )
    target = client.get(f"/v1/triage/{job_id}").json()["target"]
    assert target["repo_url"] == "https://Example.com:8443/a/b.git?x=1#frag"


@pytest.mark.parametrize("stored_url", ["https://[::1/a", "https://host:notaport/a"])
def test_get_unparseable_stored_repo_url_returns_null(db_session, stored_url):
    job_id = _insert_job(db_session, {"repo_url": stored_url, "target_module": "m"})
    response = client.get(f"/v1/triage/{job_id}")
    assert response.status_code == 200
    target = response.json()["target"]
    assert target["repo_url"] is None
    assert target["target_module"] == "m"


def test_get_stored_empty_strings_read_as_null(db_session):
    job_id = _insert_job(
        db_session,
        {"package": "", "version": "", "repo_url": "", "target_module": "", "target_symbol": None},
    )
    response = client.get(f"/v1/triage/{job_id}")
    assert response.status_code == 200
    assert set(response.json()["target"].values()) == {None}


@pytest.mark.parametrize(
    "repo_url",
    ["https://user:secret@github.com/a/b", "https://secret@github.com/a/b"],
)
def test_create_triage_repo_url_with_userinfo_422(repo_url):
    response = client.post("/v1/triage", json={"repo_url": repo_url, "target_module": "m"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "secret" not in response.text


def test_get_returns_404_when_row_vanishes_after_get_job(monkeypatch):
    import main

    ghost = uuid.uuid4()
    monkeypatch.setattr(
        main,
        "get_job",
        lambda session, job_id: {
            "id": job_id,
            "status": "queued",
            "finding": None,
            "error": None,
            "llm_mode": None,
        },
    )
    response = client.get(f"/v1/triage/{ghost}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_validation_error_422_emits_no_starlette_deprecation_warning():
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        response = client.post("/v1/triage", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert not [w for w in caught if w.category.__name__ == "StarletteDeprecationWarning"]


def test_main_does_not_use_deprecated_422_constant():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert "HTTP_422_UNPROCESSABLE_ENTITY" not in text


def test_post_valid_dotted_module_returns_202():
    response = client.post(
        "/v1/triage",
        json={"package": "requests", "version": "2.31.0", "target_module": "pkg.sub_mod"},
    )
    assert response.status_code == 202


def test_post_newline_in_target_module_422():
    response = client.post(
        "/v1/triage",
        json={"package": "requests", "version": "2.31.0", "target_module": "pkg\nsub"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert any(d["loc"][-1] == "target_module" for d in body["error"]["details"])
