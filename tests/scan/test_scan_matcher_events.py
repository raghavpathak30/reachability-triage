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
