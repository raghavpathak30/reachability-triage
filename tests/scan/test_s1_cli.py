import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability import cli  # noqa: E402


def _args(r, fmt):
    return [
        "scan", str(r["app"]), "--snapshot", str(r["snapshot"]), "--snapshot-sha256", r["sha256"],
        "--wheel-dir", str(r["wheels"]), "--format", fmt,
    ]


def test_json_output(tmp_path, capsys):
    r = mat.materialize_e1b(tmp_path)
    assert cli.main(_args(r, "json")) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["schema_version"] == "0"
    assert d["snapshot"]["sha256"] == r["sha256"] and d["snapshot"]["date"] == "2026-11-12"
    assert d["import_closure_evaluated"] is False
    f = d["findings"][0]
    assert f["verdict"] == "reachable" and f["evidence_level"] == "path_proven"
    assert f["path"][-1]["callee"] == "anyio._core._subprocesses:open_process"
    assert d["summary"]["verdicts"]["reachable"] == 1


def test_text_output(tmp_path, capsys):
    r = mat.materialize_e1b(tmp_path)
    assert cli.main(_args(r, "text")) == 0
    out = capsys.readouterr().out
    assert "fix: upgrade anyio 4.14.0 -> 4.14.2" in out
    assert "app/main.py:" in out and "-> anyio._core._subprocesses:open_process" in out
    assert "Import closure was not evaluated" in out


def test_missing_snapshot_exit_2(tmp_path, capsys):
    r = mat.materialize_e1(tmp_path)
    args = _args(r, "json")
    args[args.index("--snapshot") + 1] = str(tmp_path / "nope" / "osv-pypi-2026-11-12.zip")
    assert cli.main(args) == 2
    assert "error:" in capsys.readouterr().err


def test_runs_as_module(tmp_path):
    r = mat.materialize_e1b(tmp_path)
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))
    p = subprocess.run([sys.executable, "-m", "reachability.cli", *_args(r, "json")], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["findings"][0]["verdict"] == "reachable"
