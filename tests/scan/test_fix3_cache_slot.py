from reachability.scan.fetch import FetchBudget, LocalDirFetcher, fetch_dists
from tests.scan.s1_support import simple_lib_scan


def test_cache_hit_still_consumes_a_fetch_budget_slot(tmp_path):
    s = simple_lib_scan(tmp_path)
    store = tmp_path / "st"
    fetcher = LocalDirFetcher(s["wheels"])
    fetch_dists([("lib", "1.0")], fetcher, store, budget=FetchBudget())
    budget = FetchBudget(max_dists=1)
    res = fetch_dists([("lib", "1.0"), ("lib", "2.0")], fetcher, store, budget=budget)
    assert res.dists[("lib", "1.0")].status == "fetched"
    assert budget.count == 1
    assert res.dists[("lib", "2.0")].status == "budget"
