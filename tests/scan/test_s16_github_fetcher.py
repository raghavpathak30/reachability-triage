import pytest

from reachability.scan.fixcommit import (
    Commit, DiffStore, DiffUnavailable, GitHubDiffFetcher, check_diff_url, diff_url,
)

SHA = "a" * 40
DIFF = b"diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"


class Resp:
    def __init__(self, data=DIFF, url=None, chunk=64 * 1024, on_read=None):
        self.data, self.url, self.chunk, self.on_read, self.closed = data, url, chunk, on_read, False
        self.pos = 0

    def geturl(self):
        return self.url or diff_url("owner", "repo", SHA)

    def read(self, n):
        if self.on_read:
            self.on_read()
        out = self.data[self.pos : self.pos + min(n, self.chunk)]
        self.pos += len(out)
        return out

    def close(self):
        self.closed = True


class Opener:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.requests = resp, exc, []

    def open(self, req, timeout=None):
        self.requests.append((req.full_url, timeout, dict(req.header_items())))
        if self.exc:
            raise self.exc
        return self.resp


def test_url_built_from_validated_parts():
    op = Opener(Resp())
    assert GitHubDiffFetcher(op).fetch_diff("Owner", "repo", SHA.upper()) .startswith("diff --git")
    url, timeout, headers = op.requests[0]
    assert url == f"https://github.com/Owner/repo/commit/{SHA}.diff" and timeout == 30.0
    assert "Authorization" not in headers and "api.github" not in url
    for o, r in (("..", "r"), ("o", ".."), ("o/x", "r"), ("o", "r?x=1")):
        with pytest.raises(DiffUnavailable):
            diff_url(o, r, SHA)


@pytest.mark.parametrize("url", [
    "https://evil.com/o/r/commit/%s.diff" % SHA,
    "https://github.com.evil.com/o/r/commit/%s.diff" % SHA,
    "https://api.github.com/o/r/commit/%s.diff" % SHA,
    "https://raw.githubusercontent.com/o/r/commit/%s.diff" % SHA,
    "https://github.com/o/r/pull/3.diff",
    "https://github.com/o/r/commit/%s.patch" % SHA,
    "https://github.com/o/r/commit/%s.diff?x=1" % SHA,
])
def test_non_github_host_refused(url):
    with pytest.raises(DiffUnavailable):
        check_diff_url(url)


def test_http_scheme_refused():
    with pytest.raises(DiffUnavailable):
        check_diff_url(f"http://github.com/o/r/commit/{SHA}.diff")


def test_userinfo_and_port_refused():
    for url in (f"https://u:p@github.com/o/r/commit/{SHA}.diff", f"https://github.com:8443/o/r/commit/{SHA}.diff"):
        with pytest.raises(DiffUnavailable):
            check_diff_url(url)
    check_diff_url(f"https://github.com:443/o/r/commit/{SHA}.diff")


def test_redirect_off_host_refused():
    op = Opener(Resp(url=f"https://evil.example/o/r/commit/{SHA}.diff"))
    with pytest.raises(DiffUnavailable, match="off-host redirect"):
        GitHubDiffFetcher(op).fetch_diff("owner", "repo", SHA)
    assert op.resp.closed


def test_redirect_handler_refuses_other_host():
    from reachability.scan.fixcommit import _CheckedGitHubRedirect

    h = _CheckedGitHubRedirect()
    with pytest.raises(DiffUnavailable, match="off-host redirect"):
        h.redirect_request(None, None, 302, "Found", {}, "https://evil.example/x")


def test_size_cap_enforced():
    resp = Resp(b"x" * (2 * 1024 * 1024 + 10))
    with pytest.raises(DiffUnavailable, match="size cap"):
        GitHubDiffFetcher(Opener(resp)).fetch_diff("owner", "repo", SHA)
    assert resp.closed


def test_deadline_enforced():
    t = [0.0]

    def tick():
        t[0] += 20

    resp = Resp(DIFF, chunk=4, on_read=tick)
    with pytest.raises(DiffUnavailable, match="timeout"):
        GitHubDiffFetcher(Opener(resp), clock=lambda: t[0]).fetch_diff("owner", "repo", SHA)


def test_http_403_reason_in_gap(tmp_path):
    class E(Exception):
        code = 403

    store = DiffStore(tmp_path, GitHubDiffFetcher(Opener(exc=E("PDF or PS header bytes"))))
    assert store.get(Commit("owner", "repo", SHA)) == (None, "HTTP 403")
    assert store.unavailable == {"HTTP 403": 1}
