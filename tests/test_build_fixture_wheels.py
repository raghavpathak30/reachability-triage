import json
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import build_fixture_wheels  # noqa: E402


def _fixtures_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "tests/fixtures"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_builds_three_wheels_with_valid_dist_info(tmp_path):
    before = _fixtures_status()
    cases = build_fixture_wheels.build_all(tmp_path)
    assert _fixtures_status() == before

    wheels = sorted(tmp_path.glob("*.whl"))
    assert len(wheels) == 3
    assert [c["case"] for c in cases] == ["reachable", "not_reachable", "unknown"]

    for wheel in wheels:
        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
            assert "pkg/sink.py" in names
            dist_infos = {n.split("/")[0] for n in names if ".dist-info/" in n}
            assert len(dist_infos) == 1
            di = dist_infos.pop()
            assert {f"{di}/METADATA", f"{di}/WHEEL", f"{di}/RECORD"} <= set(names)
            assert b"Root-Is-Purelib: true" in zf.read(f"{di}/WHEEL")

    written = json.loads((tmp_path / "cases.json").read_text())
    assert written == cases
    for case in written:
        assert case["target_module"] == "pkg.sink"
        assert case["target_symbol"] == "vulnerable"
    by_case = {c["case"]: c for c in written}
    assert by_case["reachable"]["allowed_verdicts"] == ["reachable"]
    assert by_case["not_reachable"]["allowed_verdicts"] == ["not_reachable"]
    assert by_case["unknown"]["allowed_verdicts"] == ["unknown", "reachable"]


def test_pip_download_resolves_wheel_offline(tmp_path):
    wheels_dir = tmp_path / "wheels"
    build_fixture_wheels.build_all(wheels_dir)
    dest = tmp_path / "dl"
    dest.mkdir()
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "download",
            "--no-index", "--find-links", str(wheels_dir),
            "--only-binary=:all:", "--no-deps",
            "-d", str(dest), "reach-fx-02==1.0",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert [p.name for p in dest.glob("*.whl")] == ["reach_fx_02-1.0-py3-none-any.whl"]
