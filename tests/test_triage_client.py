"""Phase 8 U1: `scripts/triage_client.py` poll loop survives transient
errors and gives up with exit code 4 instead of a traceback."""

import importlib.util
import io
import json
import urllib.error
import uuid
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "triage_client.py"
JOB_ID = str(uuid.uuid4())


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *args):
        return self._body


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b"{}"))


@pytest.fixture
def client(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("triage_client_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {
                    "case": "c1",
                    "package": "p",
                    "version": "1",
                    "target_module": "m",
                    "target_symbol": "s",
                    "allowed_verdicts": ["reachable"],
                }
            ]
        )
    )
    args = Namespace(base="http://x", cases=str(cases), case="c1", timeout=60.0, interval=0.0)
    return module, args


def _install(monkeypatch, module, poll_steps):
    """POST serves 202 + UUID; each GET consumes the next step (an exception
    instance is raised, anything else is returned as the response); the last
    step repeats forever."""
    steps = list(poll_steps)

    def fake_urlopen(req, timeout=None):
        if not isinstance(req, str):
            return _Resp(202, {"id": JOB_ID, "status": "queued"})
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)


COMPLETED = {
    "status": "completed",
    "llm_mode": "stub",
    "finding": {"result": {"verdict": "reachable", "path": None}},
}


def test_url_error_once_then_completed_returns_zero(monkeypatch, client):
    module, args = client
    _install(monkeypatch, module, [urllib.error.URLError("boom"), _Resp(200, COMPLETED)])
    assert module.run(args) == 0


def test_persistent_http_500_returns_4(monkeypatch, client, capsys):
    module, args = client
    _install(monkeypatch, module, [_http_error(500)])
    assert module.run(args) == 4
    err = capsys.readouterr().err
    assert "FAIL" in err and "5 consecutive errors" in err


def test_http_404_returns_4_on_first_poll(monkeypatch, client, capsys):
    module, args = client
    _install(monkeypatch, module, [_http_error(404)])
    assert module.run(args) == 4
    assert "1 consecutive errors" in capsys.readouterr().err


def test_invalid_json_five_times_returns_4(monkeypatch, client, capsys):
    module, args = client
    _install(monkeypatch, module, [_Resp(200, b"not json")])
    assert module.run(args) == 4
    assert "JSONDecodeError" in capsys.readouterr().err
