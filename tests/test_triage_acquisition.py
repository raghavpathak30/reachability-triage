import subprocess
import zipfile
from pathlib import Path

import pytest

from main import TriageRequest
from reachability.triage.acquisition import AcquisitionError, acquire_source
from reachability.triage.index_adapter import build_repo_index


@pytest.mark.network
def test_acquire_source_wheel_package_builds_nonempty_index(tmp_path):
    request = TriageRequest(package="six", version="1.16.0")

    source_root = acquire_source(request, tmp_path)

    idx = build_repo_index(source_root)
    assert idx.report.modules
    assert idx.report.unparsed == []


def test_acquire_source_rejects_repo_url_without_network_call(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("acquire_source must not invoke subprocess for a repo_url request")

    monkeypatch.setattr("reachability.triage.acquisition.subprocess.run", _boom)

    request = TriageRequest(repo_url="https://example.com/some/repo.git")

    with pytest.raises(AcquisitionError, match="not supported yet"):
        acquire_source(request, tmp_path)


def test_acquire_source_raises_on_no_wheel_available(tmp_path, monkeypatch):
    fake_stderr = (
        "ERROR: Could not find a version that satisfies the requirement "
        "definitely-sdist-only==9.9.9 (from versions: none)\n"
        "ERROR: No matching distribution found for definitely-sdist-only==9.9.9\n"
    )

    def _fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=fake_stderr)

    monkeypatch.setattr("reachability.triage.acquisition.subprocess.run", _fake_run)

    request = TriageRequest(package="definitely-sdist-only", version="9.9.9")

    with pytest.raises(
        AcquisitionError, match="no wheel available for definitely-sdist-only==9.9.9"
    ):
        acquire_source(request, tmp_path)


def test_acquire_source_raises_on_wheel_extraction_failure(tmp_path, monkeypatch):
    def _fake_run(cmd, capture_output, text, timeout):
        dest_dir = Path(cmd[cmd.index("--dest") + 1])
        (dest_dir / "brokenpkg-1.0.0-py3-none-any.whl").write_bytes(b"not a real zip file")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("reachability.triage.acquisition.subprocess.run", _fake_run)

    request = TriageRequest(package="brokenpkg", version="1.0.0")

    with pytest.raises(AcquisitionError, match="wheel extraction failed") as exc_info:
        acquire_source(request, tmp_path)
    assert isinstance(exc_info.value.__cause__, zipfile.BadZipFile)
