"""Phase 8 U1: `scripts/demo.sh` refuses a dirty tree before any docker or
wheel step, and ignores a local .env."""

import shutil
import subprocess
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "scripts" / "demo.sh"
TOOLS = ("bash", "git", "env", "dirname", "mktemp", "rm", "cat", "sleep", "awk", "tr", "sed", "head")
DIRTY_MESSAGE = "working tree is dirty"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash and git",
)


def _run_git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def dirty_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(DEMO, repo / "scripts" / "demo.sh")
    _run_git(repo, "init", "-q")
    _run_git(repo, "add", "-A")
    _run_git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    (repo / "untracked.txt").write_text("dirty")
    return repo


@pytest.fixture
def no_docker_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in TOOLS:
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    assert not (bin_dir / "docker").exists()
    return str(bin_dir)


def _run_demo(repo, path, **extra_env):
    env = {"PATH": path, "HOME": str(repo)}
    env.update(extra_env)
    return subprocess.run(
        ["bash", str(repo / "scripts" / "demo.sh")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_dirty_tree_exits_2_before_any_docker_call(dirty_repo, no_docker_path):
    result = _run_demo(dirty_repo, no_docker_path)

    assert result.returncode == 2
    assert DIRTY_MESSAGE in result.stderr
    assert "docker" not in result.stderr.lower().replace("docker_host", "")


def test_allow_dirty_skips_the_dirty_check(dirty_repo, no_docker_path):
    result = _run_demo(dirty_repo, no_docker_path, DEMO_ALLOW_DIRTY="1", DEMO_PACE="0")

    assert DIRTY_MESSAGE not in result.stderr


def test_compose_array_ignores_local_env_file():
    compose_lines = [
        line for line in DEMO.read_text().splitlines() if line.startswith("COMPOSE=(")
    ]
    assert len(compose_lines) == 1
    assert "--env-file" in compose_lines[0]
