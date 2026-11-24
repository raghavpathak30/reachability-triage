from reachability.scan.advisories import MAX_REFERENCES, _reduce


def test_record_keeps_references():
    rec = _reduce({"id": "X-1", "references": [{"type": "WEB", "url": "https://a/b"}]})
    assert rec.references == (("WEB", "https://a/b"),)


def test_non_string_reference_skipped():
    rec = _reduce({"id": "X-2", "references": [5, {"url": 3}, {"type": "WEB"}, {"type": "WEB", "url": "u"}]})
    assert rec.references == (("WEB", "u"),)
    assert _reduce({"id": "X-3", "references": "nope"}).references == ()


def test_reference_cap():
    refs = [{"type": "WEB", "url": f"u{i}"} for i in range(MAX_REFERENCES + 50)]
    assert len(_reduce({"id": "X-4", "references": refs}).references) == MAX_REFERENCES
    assert _reduce({"id": "X-5", "references": [{"url": "x" * 600}]}).references == ()
