import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability.scan.fetch import FILES_HOST, LocalDirFetcher, fetch_dist  # noqa: E402
from reachability.scan.lock_wheels import LockEntry, read_lock_wheels  # noqa: E402
from reachability.scan.wheel_select import Candidate, choose_wheel, rank_wheel  # noqa: E402


@pytest.mark.parametrize(
    "filename,matches",
    [
        ("a-1-py3-none-any.whl", True),
        ("a-1-py2.py3-none-any.whl", True),
        ("a-1-py2-none-any.whl", False),
        ("a-1-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", True),
        ("a-1-cp313-cp313-manylinux2014_x86_64.whl", True),
        ("a-1-cp312-cp312-manylinux2014_x86_64.whl", False),
        ("a-1-cp39-abi3-manylinux_2_28_x86_64.whl", True),
        ("a-1-cp313-cp313-musllinux_1_2_x86_64.whl", False),
        ("a-1-cp313-cp313-manylinux2014_aarch64.whl", False),
        ("a-1-cp313-cp313-win_amd64.whl", False),
        ("a-1-cp313-cp313-macosx_11_0_arm64.whl", False),
        ("a-1-py3-none-manylinux2014_x86_64.whl", True),
        ("a-1-cp314-cp314-manylinux2014_x86_64.whl", False),
        ("not-a-wheel.whl", False),
    ],
)
def test_tag_table(filename, matches):
    assert (rank_wheel(filename) is not None) is matches


def test_pure_python_preferred_over_native():
    cands = [
        Candidate("a-1-cp313-cp313-manylinux2014_x86_64.whl", "u1", "x"),
        Candidate("a-1-py3-none-any.whl", "u2", "y"),
    ]
    assert choose_wheel(cands).filename == "a-1-py3-none-any.whl"
    assert choose_wheel(cands[:1]).filename.startswith("a-1-cp313")
    assert choose_wheel([Candidate("a-1-cp312-cp312-win_amd64.whl", "u", "z")]) is None


LOCK = f"""
version = 1
[[package]]
name = "Pure_Pkg"
version = "1.0"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://{FILES_HOST}/p/pure_pkg-1.0.tar.gz", hash = "sha256:aa" }}
wheels = [
  {{ url = "https://{FILES_HOST}/p/pure_pkg-1.0-py3-none-any.whl", hash = "sha256:ABCD" }},
]
[[package]]
name = "sdistonly"
version = "2.0"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://{FILES_HOST}/p/sdistonly-2.0.tar.gz", hash = "sha256:bb" }}
[[package]]
name = "private"
version = "3.0"
source = {{ registry = "https://evil.example/simple" }}
wheels = [{{ url = "https://evil.example/p/private-3.0-py3-none-any.whl", hash = "sha256:cc" }}]
"""


def test_lock_reread(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK)
    entries = read_lock_wheels(lock)
    e = entries[("pure-pkg", "1.0")]
    assert e.wheels[0].filename == "pure_pkg-1.0-py3-none-any.whl" and e.wheels[0].sha256 == "abcd"
    assert entries[("sdistonly", "2.0")].wheels == () and entries[("sdistonly", "2.0")].has_sdist


def test_sdist_only_lock_is_no_wheel_without_pypi_fallback(tmp_path):
    class Boom(LocalDirFetcher):
        def get_json(self, url):
            raise AssertionError("PyPI must not be consulted")

    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK)
    e = read_lock_wheels(lock)[("sdistonly", "2.0")]
    fd, gaps = fetch_dist("sdistonly", "2.0", Boom(tmp_path), tmp_path / "s", lock=e)
    assert fd.status == "no_wheel" and "sdist" in fd.reason and gaps[0].kind == "no_wheel"


def test_non_pypi_registry_is_no_wheel_naming_host(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK)
    e = read_lock_wheels(lock)[("private", "3.0")]
    fd, _ = fetch_dist("private", "3.0", LocalDirFetcher(tmp_path), tmp_path / "s", lock=e)
    assert fd.status == "no_wheel" and "evil.example" in fd.reason


def test_lock_hash_overrides_pypi_digest(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    real = (r["wheels"] / "langchain_openai-1.1.13-py3-none-any.whl.sha256").read_text().strip()
    wrong = LockEntry(
        "https://pypi.org/simple",
        (Candidate("langchain_openai-1.1.13-py3-none-any.whl", f"https://{FILES_HOST}/local/langchain_openai-1.1.13-py3-none-any.whl", "00" * 32),),
        False,
    )
    fd, _ = fetch_dist("langchain-openai", "1.1.13", LocalDirFetcher(r["wheels"]), tmp_path / "s", lock=wrong)
    assert fd.status == "no_wheel" and "sha256" in fd.reason
    right = LockEntry(wrong.registry, (Candidate(wrong.wheels[0].filename, wrong.wheels[0].url, real),), False)
    fd, _ = fetch_dist("langchain-openai", "1.1.13", LocalDirFetcher(r["wheels"]), tmp_path / "s2", lock=right)
    assert fd.status == "fetched"


def test_requirements_hash_must_match_pypi_wheel(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    fd, _ = fetch_dist("langchain-openai", "1.1.13", LocalDirFetcher(r["wheels"]), tmp_path / "s", req_hashes=("sha256:" + "11" * 32,))
    assert fd.status == "no_wheel"
