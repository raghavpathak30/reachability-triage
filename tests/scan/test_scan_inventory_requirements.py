from reachability.scan.inventory import Status, build_inventory


def inv_of(root, text, name="requirements.txt"):
    (root / name).write_text(text)
    return build_inventory(root)


def by_dist(inv):
    return {e.dist: e for e in inv.entries}


def test_comments_and_blank_lines_ignored(scan_root):
    inv = inv_of(scan_root, "# top\n\nfoo==1.0  # trailing\n   # indented comment\nbar==2.0\n")
    assert [(e.dist, e.line) for e in inv.entries] == [("foo", 3), ("bar", 5)]


def test_backslash_continuation_joined_with_first_line_number(scan_root):
    inv = inv_of(scan_root, "x==0.1\nfoo==1.0 \\\n    --hash=sha256:aa \\\n    --hash=sha256:bb\nbar==2.0\n")
    d = by_dist(inv)
    assert d["foo"].line == 2
    assert d["bar"].line == 5


def test_repeated_hash_options_recorded_in_order(scan_root):
    inv = inv_of(scan_root, "foo==1.0 --hash=sha256:aa --hash sha256:bb --hash=sha256:cc\n")
    assert inv.entries[0].hashes == ("sha256:aa", "sha256:bb", "sha256:cc")
    assert inv.entries[0].status is Status.PINNED


def test_extras_recorded_in_notes_and_dist_is_base_name(scan_root):
    inv = inv_of(scan_root, "Requests[security,socks]==2.0\n")
    e = inv.entries[0]
    assert e.dist == "requests"
    assert "extras: security,socks" in e.notes


def test_comment_line_ending_in_backslash_does_not_swallow_next_line(scan_root):
    inv = inv_of(scan_root, "# see x \\\nfoo==1.0\n")
    assert [(e.dist, e.line) for e in inv.entries] == [("foo", 2)]


def test_comment_line_ends_open_continuation(scan_root):
    inv = inv_of(scan_root, "foo==1.0 \\\n# comment\nbar==2.0\n")
    assert [(e.dist, e.line) for e in inv.entries] == [("foo", 1), ("bar", 3)]


def test_utf16_bom_requirements_parsed(scan_root):
    (scan_root / "requirements.txt").write_bytes("foo==1.0\nbar==2.0\n".encode("utf-16"))
    inv = build_inventory(scan_root)
    assert [e.dist for e in inv.entries] == ["foo", "bar"]


def test_utf8_bom_and_crlf_parsed(scan_root):
    (scan_root / "requirements.txt").write_bytes(b"\xef\xbb\xbffoo==1.0\r\nbar==2.0\r\n")
    inv = build_inventory(scan_root)
    assert [(e.dist, e.line) for e in inv.entries] == [("foo", 1), ("bar", 2)]


def test_undecodable_file_raises_lockfile_parse_error(scan_root):
    import pytest

    from reachability.scan.inventory import LockfileParseError

    (scan_root / "requirements.txt").write_bytes(b"foo==1.0\n\xff\xfe\xfa\n")
    with pytest.raises(LockfileParseError, match="requirements.txt"):
        build_inventory(scan_root)
