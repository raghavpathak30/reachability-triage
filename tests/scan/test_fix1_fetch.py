import pytest

from reachability.scan.fetch import FILES_HOST, LocalDirFetcher, fetch_dist
from reachability.scan.lock_wheels import LockEntry
from reachability.scan.wheel_select import Candidate


# ---- F5
@pytest.mark.parametrize("host", [FILES_HOST, "evil.example"])
def test_f5_non_pypi_registry_is_no_wheel_naming_registry_host(tmp_path, host):
    class Rec(LocalDirFetcher):
        def get_json(self, url):
            raise AssertionError("PyPI must not be consulted")

        def download(self, *a, **k):
            raise AssertionError("must not download")

    lock = LockEntry(
        "https://private.example/simple",
        (Candidate("p-1-py3-none-any.whl", f"https://{host}/p/p-1-py3-none-any.whl", "aa" * 32),),
        False,
    )
    fd, gaps = fetch_dist("p", "1", Rec(tmp_path), tmp_path / "s", lock=lock)
    assert fd.status == "no_wheel" and "private.example" in fd.reason
    assert gaps[0].kind == "no_wheel" and "private.example" in gaps[0].detail


# ---- G3
from reachability.scan.lock_wheels import read_lock_wheels  # noqa: E402

_LOCK_HEAD = 'version = 1\nrequires-python = ">=3.12"\n\n'


def _lock(tmp_path, source_line, extra=""):
    p = tmp_path / "uv.lock"
    p.write_text(_LOCK_HEAD + f'[[package]]\nname = "p"\nversion = "1"\nsource = {source_line}\n{extra}')
    return read_lock_wheels(p)


@pytest.mark.parametrize(
    "source_line,kind",
    [
        ('{ git = "https://github.com/x/p?rev=abc#abc" }', "git"),
        ('{ url = "https://files.pythonhosted.org/p-1.tar.gz" }', "url"),
        ('{ path = "../p" }', "path"),
        ('{ directory = "../p" }', "directory"),
        ('{ editable = "." }', "editable"),
        ('{ virtual = "." }', "virtual"),
    ],
)
def test_g3_non_registry_lock_source_is_no_wheel_never_pypi(tmp_path, source_line, kind):
    class Rec(LocalDirFetcher):
        def get_json(self, url):
            raise AssertionError("PyPI must not be consulted")

        def download(self, *a, **k):
            raise AssertionError("must not download")

    locks = _lock(tmp_path, source_line)
    fd, gaps = fetch_dist("p", "1", Rec(tmp_path), tmp_path / "s", lock=locks[("p", "1")])
    assert fd.status == "no_wheel" and kind in fd.reason
    assert gaps[0].kind == "no_wheel" and kind in gaps[0].detail


def test_g3_pypi_registry_source_still_uses_pypi_lookup(tmp_path):
    locks = _lock(tmp_path, '{ registry = "https://pypi.org/simple" }')

    class Json(LocalDirFetcher):
        def get_json(self, url):
            return {"urls": []}

    fd, _ = fetch_dist("p", "1", Json(tmp_path), tmp_path / "s", lock=locks[("p", "1")])
    assert fd.reason == "no suitable wheel"
