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
