import io
import json
import urllib.request as ur

import pytest

from reachability.scan.fetch import (
    FILES_HOST,
    PyPIFetcher,
    FetchRefused,
    _CheckedRedirect,
    check_url,
    json_url,
)


class FakeResp:
    def __init__(self, data: bytes, url: str, tick=None):
        self.buf = io.BytesIO(data)
        self._url = url
        self.closed = False
        self.tick = tick

    def read(self, n):
        if self.tick:
            self.tick()
        return self.buf.read(n)

    def geturl(self):
        return self._url

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append((req.full_url, timeout))
        return self.resp


def test_get_json_ok():
    url = "https://pypi.org/pypi/x/1.0/json"
    f = PyPIFetcher(FakeOpener(FakeResp(json.dumps({"urls": []}).encode(), url)))
    assert f.get_json(url) == {"urls": []}


def test_oversized_json_refused():
    url = "https://pypi.org/pypi/x/1.0/json"
    f = PyPIFetcher(FakeOpener(FakeResp(b" " * (10 * 1024 * 1024 + 10), url)))
    with pytest.raises(FetchRefused, match="exceeded"):
        f.get_json(url)


def test_oversized_body_refused_and_closed(tmp_path):
    url = f"https://{FILES_HOST}/x.whl"
    resp = FakeResp(b"a" * 5000, url)
    f = PyPIFetcher(FakeOpener(resp))
    with pytest.raises(FetchRefused, match="exceeded"):
        f.download(url, tmp_path / "w", max_bytes=1000, deadline_seconds=10)
    assert resp.closed


def test_slow_body_past_deadline(tmp_path):
    url = f"https://{FILES_HOST}/x.whl"
    now = [0.0]

    def tick():
        now[0] += 50.0

    f = PyPIFetcher(FakeOpener(FakeResp(b"a" * 500000, url, tick)), clock=lambda: now[0])
    with pytest.raises(FetchRefused, match="exceeded 120"):
        f.download(url, tmp_path / "w", max_bytes=10**9, deadline_seconds=120)


def test_download_writes_file(tmp_path):
    url = f"https://{FILES_HOST}/x.whl"
    f = PyPIFetcher(FakeOpener(FakeResp(b"abc", url)))
    f.download(url, tmp_path / "w", max_bytes=100, deadline_seconds=10)
    assert (tmp_path / "w").read_bytes() == b"abc"


def test_final_url_on_other_host_refused(tmp_path):
    f = PyPIFetcher(FakeOpener(FakeResp(b"abc", "https://evil.example/x")))
    with pytest.raises(FetchRefused, match="evil.example"):
        f.download(f"https://{FILES_HOST}/x.whl", tmp_path / "w", max_bytes=100, deadline_seconds=10)


@pytest.mark.parametrize(
    "url",
    ["http://pypi.org/x", "https://evil.example/x", "https://pypi.org.evil.example/x",
     "https://user@pypi.org/x", "https://pypi.org:8443/x", "ftp://pypi.org/x"],
)
def test_check_url_refuses(url):
    with pytest.raises(FetchRefused):
        check_url(url)


def test_redirect_hops_are_checked():
    h = _CheckedRedirect()
    req = ur.Request("https://pypi.org/pypi/x/1.0/json")
    with pytest.raises(FetchRefused, match="evil.example"):
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/y")
    with pytest.raises(FetchRefused, match="only https"):
        h.redirect_request(req, None, 302, "Found", {}, "http://pypi.org/y")
    assert h.redirect_request(req, None, 302, "Found", {}, f"https://{FILES_HOST}/y") is not None


def test_json_url_validation_and_quoting():
    assert json_url("Foo_Bar", "1.0") == "https://pypi.org/pypi/foo-bar/1.0/json"
    assert json_url("a", "1.0+local.1").endswith("/1.0%2Blocal.1/json")
    for bad_name, ver in [("a/b", "1.0"), ("a b", "1.0"), ("../x", "1.0"), ("a", "1.0.0-1"), ("a", "1/../2"), ("a", "latest")]:
        with pytest.raises(FetchRefused):
            json_url(bad_name, ver)
