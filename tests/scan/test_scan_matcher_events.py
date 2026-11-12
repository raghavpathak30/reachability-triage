"""Ordered-event OSV range evaluator. Inputs here are SYNTHETIC; real rows are in test_scan_matcher_table.py."""

import pytest
from packaging.version import Version

from reachability.scan.advisories import range_matches, reduce_events


def m(events, installed, label="SYNTH-1"):
    return range_matches(reduce_events(events), Version(installed), label)


def test_introduced_zero_is_minus_infinity_prerelease_matches():
    matched, notes = m([{"introduced": "0"}, {"fixed": "1.0"}], "0.0.0rc1")
    assert matched is True and notes == ()
    # the trap this guards: Version("0.0.0rc1") < Version("0")
    assert Version("0.0.0rc1") < Version("0")


def test_fixed_is_exclusive():
    ev = [{"introduced": "0"}, {"fixed": "2.0"}]
    assert m(ev, "1.9.9")[0] is True
    assert m(ev, "2.0")[0] is False
    assert m(ev, "2.0.0")[0] is False
    assert m(ev, "2.1")[0] is False


def test_last_affected_is_inclusive_and_next_is_not():
    ev = [{"introduced": "1.0"}, {"last_affected": "1.5"}]
    assert m(ev, "1.5")[0] is True
    assert m(ev, "1.5.post1")[0] is False
    assert m(ev, "1.5.0.1")[0] is False
    assert m(ev, "1.0")[0] is True
    assert m(ev, "0.9")[0] is False


def test_limit_is_exclusive_and_not_a_fixed():
    ev = [{"introduced": "0"}, {"limit": "3.0"}]
    assert m(ev, "2.9")[0] is True
    assert m(ev, "3.0")[0] is False


def test_multiple_intervals():
    ev = [{"introduced": "1.0"}, {"fixed": "1.5"}, {"introduced": "2.0"}, {"fixed": "2.5"}]
    assert [m(ev, v)[0] for v in ("0.9", "1.0", "1.4", "1.5", "1.9", "2.0", "2.4", "2.5", "3.0")] == [
        False, True, True, False, False, True, True, False, False,
    ]


def test_events_are_sorted_before_walking():
    ev = [{"fixed": "2.0"}, {"introduced": "1.0"}]
    assert m(ev, "1.5")[0] is True
    assert m(ev, "2.0")[0] is False


def test_prerelease_of_fixed_is_still_affected():
    assert m([{"introduced": "0"}, {"fixed": "2.17.0"}], "2.17.0rc1")[0] is True


def test_tie_introduced_after_terminator_is_conservative():
    ev = [{"introduced": "1.0"}, {"fixed": "2.0"}, {"introduced": "2.0"}]
    assert m(ev, "2.0")[0] is True
    assert m(ev, "2.1")[0] is True


def test_unparseable_bound_matches_conservatively_with_note():
    matched, notes = m([{"introduced": "0"}, {"fixed": "0.9-stable"}], "5.0")
    assert matched is True
    assert any("unparseable range bound '0.9-stable' in SYNTH-1; matched conservatively" == n for n in notes)


def test_range_without_introduced_conservative():
    matched, notes = m([{"fixed": "1.0"}], "5.0")
    assert matched is True and any("no introduced" in n for n in notes)


def test_event_with_two_keys_conservative():
    matched, notes = m([{"introduced": "0", "fixed": "1.0"}], "5.0")
    assert matched is True and any("malformed event" in n for n in notes)


def test_event_with_unknown_key_conservative():
    matched, notes = m([{"introduced": "0"}, {"wat": "1.0"}], "5.0")
    assert matched is True and notes


def test_non_dict_event_conservative():
    matched, notes = m([{"introduced": "0"}, "x"], "5.0")
    assert matched is True and notes


def test_empty_events_conservative():
    matched, notes = m([], "1.0")
    assert matched is True and notes


from reachability.scan.advisories import NO_FIX, fixed_in_for  # noqa: E402


def f(events, installed, label="SYNTH-1"):
    return fixed_in_for(reduce_events(events), Version(installed), label)


def test_fixed_in_lowest_fixed_above_installed():
    ev = [{"introduced": "1.0"}, {"fixed": "1.5"}, {"introduced": "2.0"}, {"fixed": "2.5"}]
    assert f(ev, "1.2")[0] == "1.5"
    assert f(ev, "2.2")[0] == "2.5"


def test_fixed_in_none_when_only_last_affected():
    assert f([{"introduced": "0"}, {"last_affected": "2.0"}], "1.0")[0] == NO_FIX == "none"


def test_fixed_in_ignores_limit():
    assert f([{"introduced": "0"}, {"limit": "3.0"}], "1.0")[0] == NO_FIX


def test_fixed_in_is_strictly_above_installed():
    assert f([{"introduced": "0"}, {"fixed": "2.0"}, {"fixed": "3.0"}], "2.0")[0] == "3.0"


def test_fixed_in_returns_original_spelling():
    assert f([{"introduced": "0"}, {"fixed": "1.5.0"}], "1.0")[0] == "1.5.0"


def test_fixed_in_ignores_unparseable_fixed_with_note():
    val, notes = f([{"introduced": "0"}, {"fixed": "0.9-stable"}, {"fixed": "3.0"}], "1.0")
    assert val == "3.0"
    assert any("0.9-stable" in n for n in notes)


def test_fixed_in_none_when_only_unparseable_fixed():
    val, notes = f([{"introduced": "0"}, {"fixed": "0.9-stable"}], "1.0")
    assert val == NO_FIX and notes


def test_fixed_in_min_across_matching_ranges_only():
    from reachability.scan.advisories import fixed_in_across

    matching = reduce_events([{"introduced": "0"}, {"fixed": "5.0"}])
    other = reduce_events([{"introduced": "9.0"}, {"fixed": "9.5"}])
    val, _ = fixed_in_across([matching, other], Version("1.0"), "SYNTH-1")
    assert val == "5.0"  # `other` does not match 1.0, so its fixed 9.5 is not considered
    both = reduce_events([{"introduced": "0"}, {"fixed": "4.0"}])
    assert fixed_in_across([matching, both], Version("1.0"), "SYNTH-1")[0] == "4.0"


from reachability.scan.advisories import AffectedEntry, entry_matches  # noqa: E402


def entry(versions=(), ranges=(), lkavr=None, ignored=()):
    return AffectedEntry(
        dist="x",
        versions=tuple(versions),
        ranges=tuple(reduce_events(r) for r in ranges),
        lkavr=lkavr,
        ignored_range_types=tuple(ignored),
    )


def em(e, installed):
    return entry_matches(e, installed, "SYNTH-1")


def test_lkavr_le_matches_inclusive():
    e = entry(lkavr="<= 1.2.3")
    assert em(e, "1.2.3").matched is True
    assert em(e, "1.2.4").matched is False
    assert em(e, "0.1").matched is True


def test_lkavr_lt_includes_prerelease_of_bound():
    e = entry(lkavr="< 2.17.0")
    assert em(e, "2.17.0rc1").matched is True
    assert em(e, "2.17.0").matched is False


def test_lkavr_range_expression_matches_only_inside():
    e = entry(lkavr=">= 1.0, < 2.0")
    assert [em(e, v).matched for v in ("0.9", "1.0", "1.9", "2.0")] == [False, True, True, False]


def test_lkavr_unparseable_conservative_with_note():
    for text in ("~= 1.2", "<= 1.0 || 2.0", "banana", "<= 1.0-stable", ""):
        r = em(entry(lkavr=text), "9.9")
        assert r.matched is True, text
        assert any("last_known_affected_version_range" in n and "matched conservatively" in n for n in r.notes), text


def test_lkavr_is_union_not_override():
    # versions and range say no; lkavr says yes -> still a match (union)
    e = entry(versions=["1.0"], ranges=[[{"introduced": "5.0"}, {"fixed": "6.0"}]], lkavr="<= 2.0")
    r = em(e, "1.5")
    assert r.matched is True
    assert any("last_known_affected_version_range" in n for n in r.notes)
    # lkavr says no but versions says yes -> still a match
    assert em(entry(versions=["3.0"], lkavr="<= 2.0"), "3.0").matched is True


def test_lkavr_note_only_when_it_is_the_sole_reason():
    e = entry(ranges=[[{"introduced": "0"}, {"fixed": "3.0"}]], lkavr="<= 2.0")
    assert not any("last_known_affected_version_range" in n for n in em(e, "1.0").notes)


def test_lkavr_le_with_local_installed_uses_public():
    r = em(entry(lkavr="<= 1.0"), "1.0+cpu")
    assert r.matched is True


def test_local_version_matches_public_versions_entry_with_note():
    r = em(entry(versions=["1.0"]), "1.0+cpu")
    assert r.matched is True
    assert "local version segment +cpu ignored for matching" in r.notes


def test_local_note_absent_without_local_segment():
    assert not any("local version" in n for n in em(entry(versions=["1.0"]), "1.0").notes)


def test_local_version_with_range_uses_public_part():
    e = entry(ranges=[[{"introduced": "0"}, {"fixed": "1.0"}]])
    assert em(e, "0.9+cpu").matched is True
    assert em(e, "1.0+cpu").matched is False
