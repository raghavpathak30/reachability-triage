"""Unit-level tests for `run_triage_job` (U5). No FastAPI, no network by
default -- `acquire_source`, `build_repo_index`, and `run_triage_loop` are
monkeypatched directly on `reachability.triage.job_runner`, per that
module's own import style (`from .acquisition import acquire_source`, etc.
-- patch the name as looked up in `job_runner`, not where it's defined).

`test_sanitize_error_against_real_captured_exceptions` is the one test in
this file that captures real exceptions from U1/U2's real code paths
instead of synthetic ones -- see its own docstring and `.agent/plan.md`'s
Test plan section (Critique #4's two-sub-case revision) for why.
"""

import subprocess
import uuid

import pytest

from main import TriageRequest
from reachability.triage import job_runner
from reachability.triage.acquisition import AcquisitionError, acquire_source
from reachability.triage.agent_models import TriageFinding
from reachability.triage.index_adapter import IndexBuildError, RepoIndex, build_repo_index
from reachability.triage.job_runner import (
    EmptyRepoIndexError,
    _sanitize_error,
    run_triage_job,
)
from reachability.index.models import DiscoveryReport
from reachability.index.reachability_models import ReachabilityResult, Verdict


def _make_request(**overrides) -> TriageRequest:
    fields = {
        "package": "somepkg",
        "version": "1.0.0",
        "target_module": "somepkg",
        "target_symbol": None,
    }
    fields.update(overrides)
    return TriageRequest(**fields)


def _make_record(triage_id: uuid.UUID) -> dict:
    return {"id": triage_id, "status": "queued", "target": {}, "finding": None, "error": None}


def _make_finding() -> TriageFinding:
    return TriageFinding(
        result=ReachabilityResult(
            target_module="somepkg",
            target_symbol=None,
            verdict=Verdict.UNKNOWN,
            path=None,
            reason="target_symbol_not_found_in_index",
        ),
        rationale="",
        tool_calls=[],
    )


def _make_empty_repo_index() -> RepoIndex:
    return RepoIndex(
        report=DiscoveryReport(modules=[], import_tables={}, unparsed=[], star_import_count=0),
        symbol_index={},
        edges=[],
        entrypoints=[],
    )


def test_success_path_sets_completed_with_finding(tmp_path, monkeypatch):
    finding = _make_finding()

    monkeypatch.setattr(job_runner, "acquire_source", lambda request, workdir: tmp_path)
    monkeypatch.setattr(
        job_runner,
        "build_repo_index",
        lambda repo_root: RepoIndex(
            report=DiscoveryReport(
                modules=["fake"], import_tables={}, unparsed=[], star_import_count=0
            ),
            symbol_index={},
            edges=[],
            entrypoints=[],
        ),
    )
    monkeypatch.setattr(
        job_runner,
        "run_triage_loop",
        lambda llm_client, repo_index, target_module, target_symbol, budget, _on_raw_tool_result=None: finding,
    )

    triage_id = uuid.uuid4()
    triage_db = {triage_id: _make_record(triage_id)}
    run_triage_job(triage_db, triage_id, _make_request())

    record = triage_db[triage_id]
    assert record["status"] == "completed"
    assert record["finding"] is finding
    assert record["error"] is None


def test_acquisition_error_sets_failed_with_error(monkeypatch):
    def _boom(request, workdir):
        raise AcquisitionError("boom")

    monkeypatch.setattr(job_runner, "acquire_source", _boom)

    triage_id = uuid.uuid4()
    triage_db = {triage_id: _make_record(triage_id)}
    run_triage_job(triage_db, triage_id, _make_request())

    record = triage_db[triage_id]
    assert record["status"] == "failed"
    assert "boom" in record["error"]


def test_index_build_error_sets_failed_with_error(tmp_path, monkeypatch):
    monkeypatch.setattr(job_runner, "acquire_source", lambda request, workdir: tmp_path)

    def _boom(repo_root):
        raise IndexBuildError("boom")

    monkeypatch.setattr(job_runner, "build_repo_index", _boom)

    triage_id = uuid.uuid4()
    triage_db = {triage_id: _make_record(triage_id)}
    run_triage_job(triage_db, triage_id, _make_request())

    record = triage_db[triage_id]
    assert record["status"] == "failed"
    assert "boom" in record["error"]


def test_empty_repo_index_sets_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(job_runner, "acquire_source", lambda request, workdir: tmp_path)
    monkeypatch.setattr(job_runner, "build_repo_index", lambda repo_root: _make_empty_repo_index())

    triage_id = uuid.uuid4()
    triage_db = {triage_id: _make_record(triage_id)}
    run_triage_job(triage_db, triage_id, _make_request())

    record = triage_db[triage_id]
    assert record["status"] == "failed"
    assert "EmptyRepoIndexError" in record["error"]


def test_unexpected_run_triage_loop_exception_never_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr(job_runner, "acquire_source", lambda request, workdir: tmp_path)
    monkeypatch.setattr(
        job_runner,
        "build_repo_index",
        lambda repo_root: RepoIndex(
            report=DiscoveryReport(
                modules=["fake"], import_tables={}, unparsed=[], star_import_count=0
            ),
            symbol_index={},
            edges=[],
            entrypoints=[],
        ),
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("contract violation")

    monkeypatch.setattr(job_runner, "run_triage_loop", _boom)

    triage_id = uuid.uuid4()
    triage_db = {triage_id: _make_record(triage_id)}

    # Must not raise -- called directly, no pytest.raises.
    run_triage_job(triage_db, triage_id, _make_request())

    record = triage_db[triage_id]
    assert record["status"] == "failed"
    assert "contract violation" in record["error"]


def test_error_message_is_sanitized_and_bounded(monkeypatch):
    secret_path = "/home/attacker/.secret/workdir-xyz"

    def _boom(request, workdir):
        raise AcquisitionError(f"pip download failed: {secret_path}")

    monkeypatch.setattr(job_runner, "acquire_source", _boom)

    triage_id = uuid.uuid4()
    triage_db = {triage_id: _make_record(triage_id)}
    run_triage_job(triage_db, triage_id, _make_request())

    record = triage_db[triage_id]
    assert record["status"] == "failed"
    assert secret_path not in record["error"]
    assert len(record["error"]) <= 2000 + len("... [truncated]")


def test_workdir_is_removed_after_job(monkeypatch):
    captured_workdir = {}

    def _fake_acquire(request, workdir):
        captured_workdir["path"] = workdir
        raise AcquisitionError("stop here, workdir capture is all this test needs")

    monkeypatch.setattr(job_runner, "acquire_source", _fake_acquire)

    triage_id = uuid.uuid4()
    triage_db = {triage_id: _make_record(triage_id)}
    run_triage_job(triage_db, triage_id, _make_request())

    assert "path" in captured_workdir
    assert not captured_workdir["path"].exists()


@pytest.mark.network
def test_sanitize_error_against_real_captured_exceptions(tmp_path):
    """Checks `_sanitize_error`'s coverage against REAL captured exceptions
    from U1/U2's actual failure paths, not synthetic payloads -- see
    `.agent/plan.md`'s Test plan section (Critique #4) for why this needs
    two `AcquisitionError` sub-cases rather than one.
    """
    # --- AcquisitionError sub-case (i): real network call, nonexistent
    # package. Negative-result check: real pip's "no wheel"/"no matching
    # distribution" stderr for this failure mode contains nothing sensitive
    # to redact in the first place -- this confirms _sanitize_error doesn't
    # mangle an already-safe message, not that it redacted anything.
    request = _make_request(
        package="this-package-definitely-does-not-exist-reachability-triage",
        version="0.0.0",
    )
    with pytest.raises(AcquisitionError) as exc_info:
        acquire_source(request, tmp_path / "sub1")
    sanitized = _sanitize_error(exc_info.value)
    assert "AcquisitionError" in sanitized

    # --- AcquisitionError sub-case (ii): a real subprocess.TimeoutExpired
    # instance (not a hand-written string), raised via a monkeypatched
    # subprocess.run, exercising acquisition.py's real timeout-handling
    # branch (acquisition.py:92-95). Only the 120-second wait is
    # short-circuited -- the exception class, its real __str__ formatting,
    # and acquisition.py's real f-string construction all run for real.
    # This is the sub-case that actually has a path to redact: `cmd`
    # embeds "--dest", str(download_dir), an absolute path under the job's
    # real workdir.
    workdir2 = tmp_path / "sub2"

    def _fake_run(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)

    import reachability.triage.acquisition as acquisition_module

    original_run = acquisition_module.subprocess.run
    acquisition_module.subprocess.run = _fake_run
    try:
        request2 = _make_request(package="somepkg", version="1.0.0")
        with pytest.raises(AcquisitionError) as exc_info2:
            acquire_source(request2, workdir2)
    finally:
        acquisition_module.subprocess.run = original_run

    real_download_dir = str(workdir2 / "download")
    assert real_download_dir in str(exc_info2.value)
    sanitized2 = _sanitize_error(exc_info2.value)
    assert real_download_dir not in sanitized2

    # --- IndexBuildError half: a real invocation of build_repo_index
    # against a real nonexistent path.
    nonexistent = tmp_path / "does" / "not" / "exist"
    with pytest.raises(IndexBuildError) as exc_info3:
        build_repo_index(nonexistent)
    sanitized3 = _sanitize_error(exc_info3.value)
    assert str(nonexistent) not in sanitized3
