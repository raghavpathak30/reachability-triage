import uuid

import pytest
from fastapi.testclient import TestClient

from main import app, TRIAGE_DB

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_triage_db():
    TRIAGE_DB.clear()
    yield
    TRIAGE_DB.clear()


def test_healthz_returns_200():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_triage_package_version_valid():
    response = client.post(
        "/v1/triage", json={"package": "requests", "version": "2.31.0"}
    )
    assert response.status_code == 202
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["status"] == "queued"


def test_create_triage_package_version_location_header():
    response = client.post(
        "/v1/triage", json={"package": "requests", "version": "2.31.0"}
    )
    body = response.json()
    assert response.headers["location"] == f"/v1/triage/{body['id']}"


def test_create_triage_repo_url_valid():
    response = client.post(
        "/v1/triage", json={"repo_url": "https://github.com/org/repo"}
    )
    assert response.status_code == 202
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["status"] == "queued"


def test_create_triage_repo_url_location_header():
    response = client.post(
        "/v1/triage", json={"repo_url": "https://github.com/org/repo"}
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
        "/v1/triage", json={"package": "requests", "version": "2.31.0"}
    )
    triage_id = create_response.json()["id"]

    response = client.get(f"/v1/triage/{triage_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == triage_id
    assert body["status"] == "queued"


def test_get_triage_unknown_id_404():
    unknown_id = uuid.uuid4()
    response = client.get(f"/v1/triage/{unknown_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_create_triage_response_model_fields_package_shape():
    response = client.post(
        "/v1/triage", json={"package": "requests", "version": "2.31.0"}
    )
    body = response.json()
    assert set(body.keys()) == {"id", "status"}


def test_create_triage_response_model_fields_repo_shape():
    response = client.post(
        "/v1/triage", json={"repo_url": "https://github.com/org/repo"}
    )
    body = response.json()
    assert set(body.keys()) == {"id", "status"}


def test_validation_error_envelope_shape():
    response = client.post("/v1/triage", json={})
    error = response.json()["error"]
    assert isinstance(error["code"], str)
    assert isinstance(error["message"], str)
    assert isinstance(error["details"], list)


def test_get_triage_after_multiple_creates_isolated():
    first = client.post(
        "/v1/triage", json={"package": "requests", "version": "2.31.0"}
    ).json()
    second = client.post(
        "/v1/triage", json={"repo_url": "https://github.com/org/repo"}
    ).json()

    first_get = client.get(f"/v1/triage/{first['id']}").json()
    second_get = client.get(f"/v1/triage/{second['id']}").json()

    assert first_get["id"] == first["id"]
    assert second_get["id"] == second["id"]
    assert first_get["id"] != second_get["id"]
