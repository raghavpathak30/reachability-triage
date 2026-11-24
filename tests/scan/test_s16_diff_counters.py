from reachability.scan.fixcommit import Commit, DiffStore, DiffUnavailable, safe_path

DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
OK, DENIED, CACHED = ("1" * 40, "2" * 40, "3" * 40)


class PerSha:
    def __init__(self):
        self.calls = []

    def fetch_diff(self, owner, repo, sha):
        self.calls.append(sha)
        if sha == DENIED:
            raise DiffUnavailable("HTTP 403")
        return DIFF


def test_fetched_counts_successes_only_attempted_counts_network_tries(tmp_path):
    fetcher = PerSha()
    store = DiffStore(tmp_path / "cache", fetcher)
    cached = safe_path(store.root, "owner", "repo", CACHED)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(DIFF)

    assert store.get(Commit("owner", "repo", OK)) == (DIFF, None)
    assert store.get(Commit("owner", "repo", DENIED)) == (None, "HTTP 403")
    assert store.get(Commit("owner", "repo", CACHED)) == (DIFF, None)

    assert fetcher.calls == [OK, DENIED]  # the cache hit never reaches the network
    assert store.fetched == 2  # one HTTP success + one cache hit; the 403 is not counted
    assert store.cached == 1
    assert store.attempted == 2  # network tries: the success and the 403
    assert store.unavailable == {"HTTP 403": 1}


def test_budget_still_counts_attempts(tmp_path):
    fetcher = PerSha()
    store = DiffStore(tmp_path / "cache", fetcher, max_diffs=1)
    assert store.get(Commit("owner", "repo", DENIED)) == (None, "HTTP 403")
    assert store.get(Commit("owner", "repo", OK)) == (None, "diff budget: 1")
    assert fetcher.calls == [DENIED]
    assert store.fetched == 0 and store.attempted == 1
