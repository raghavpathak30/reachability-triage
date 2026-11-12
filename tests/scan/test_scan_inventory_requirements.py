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


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "text",
    ["foo==1.*", "foo==1.0.*", "foo>=1", "foo~=1.4", "foo", "foo>=1,<2", "foo!=1.0", "foo==1.0,!=1.0.1"],
)
def test_unpinned_kinds_with_line_numbers(scan_root, text):
    inv = inv_of(scan_root, "# c\n" + text + "\n")
    e = inv.entries[0]
    assert (e.dist, e.version, e.status, e.line) == ("foo", None, Status.UNPINNED, 2)


def test_triple_equals_is_unsupported(scan_root):
    e = inv_of(scan_root, "foo===1.0\n").entries[0]
    assert (e.dist, e.status) == ("foo", Status.UNSUPPORTED)


@pytest.mark.parametrize(
    "text",
    [
        "-e ./x",
        "-c c.txt",
        "git+https://example.invalid/y.git#egg=y",
        "https://example.invalid/p.whl",
        "--no-binary :all:",
        "--trusted-host example.invalid",
        "--pre",
        "./local_pkg",
        "this is not valid !!!",
    ],
)
def test_unsupported_kinds_have_no_dist_and_keep_raw_text(scan_root, text):
    e = inv_of(scan_root, text + "\n").entries[0]
    assert e.dist is None
    assert e.status is Status.UNSUPPORTED
    assert any(text in n for n in e.notes)


def test_direct_url_requirement_is_unsupported_but_named(scan_root):
    e = inv_of(scan_root, "pkg @ https://example.invalid/p.whl\n").entries[0]
    assert (e.dist, e.status) == ("pkg", Status.UNSUPPORTED)


def test_non_pep440_pin_is_unsupported_with_note(scan_root):
    e = inv_of(scan_root, "foo==1.0-custom\n").entries[0]
    assert e.dist == "foo"
    assert e.status is Status.UNSUPPORTED
    assert any("1.0-custom" in n for n in e.notes)


def test_local_version_pinned(scan_root):
    e = inv_of(scan_root, "foo==1.0+cpu\n").entries[0]
    assert (e.version, e.status) == ("1.0+cpu", Status.PINNED)


def test_option_value_spellings_for_unsupported_options(scan_root):
    inv = inv_of(scan_root, "-cc.txt\n--constraint=c.txt\n--constraint c.txt\n")
    assert [e.status for e in inv.entries] == [Status.UNSUPPORTED] * 3


TAINT_LINES = [
    "--index-url=https://example.invalid/simple",
    "--index-url https://example.invalid/simple",
    "-i https://example.invalid/simple",
    "-ihttps://example.invalid/simple",
    "--extra-index-url=https://example.invalid/simple",
    "--extra-index-url https://example.invalid/simple",
    "--no-index",
    "--find-links=https://example.invalid/wheels",
    "--find-links https://example.invalid/wheels",
    "-f https://example.invalid/wheels",
    "-fhttps://example.invalid/wheels",
]


@pytest.mark.parametrize("opt", TAINT_LINES)
def test_index_option_spellings_all_taint(scan_root, opt):
    inv = inv_of(scan_root, f"{opt}\nfoo==1.0\nbar>=2\n")
    assert [(e.dist, e.status) for e in inv.entries] == [
        ("foo", Status.UNSUPPORTED),
        ("bar", Status.UNSUPPORTED),
    ]
    assert any("original status: pinned" in n for n in inv.entries[0].notes)
    assert any("original status: unpinned" in n for n in inv.entries[1].notes)


def test_taint_applies_to_entries_before_the_option_line(scan_root):
    inv = inv_of(scan_root, "foo==1.0\n--extra-index-url https://example.invalid/simple\n")
    assert inv.entries[0].status is Status.UNSUPPORTED


def test_tainted_tree_does_not_raise_hint_for_converted_unpinned(scan_root):
    inv = inv_of(scan_root, "--index-url https://example.invalid/simple\nfoo>=1\n")
    assert inv.hint is None


def test_extra_index_url_marks_all_entries_unsupported_and_keeps_original_status_in_notes(scan_root):
    inv = inv_of(scan_root, "--extra-index-url https://example.invalid/simple\nfoo==1.0\n")
    e = inv.entries[0]
    assert e.status is Status.UNSUPPORTED
    assert e.version == "1.0"
    assert any("original status: pinned" in n for n in e.notes)


def test_trusted_host_is_unsupported_option_row_no_taint(scan_root):
    inv = inv_of(scan_root, "--trusted-host example.invalid\nfoo==1.0\n")
    assert inv.entries[0].dist is None and inv.entries[0].status is Status.UNSUPPORTED
    assert inv.entries[1].status is Status.PINNED


def test_include_inside_root_adds_entries_with_their_own_source_file(scan_root):
    (scan_root / "base").mkdir()
    (scan_root / "base" / "common.txt").write_text("# c\nshared==2.0\n")
    inv = inv_of(scan_root, "-r base/common.txt\nown==1.0\n")
    assert [(e.dist, e.source_file, e.line) for e in inv.entries] == [
        ("shared", "base/common.txt", 2),
        ("own", "requirements.txt", 2),
    ]
    assert inv.skipped == ()


@pytest.mark.parametrize("spelling", ["-r inc.txt", "-rinc.txt", "--requirement=inc.txt", "--requirement inc.txt"])
def test_requirement_include_spellings(scan_root, spelling):
    (scan_root / "inc.txt").write_text("shared==2.0\n")
    inv = inv_of(scan_root, spelling + "\n")
    assert [e.dist for e in inv.entries] == ["shared"]


def test_include_cycle_recorded_not_followed(scan_root):
    (scan_root / "a.txt").write_text("a==1.0\n-r requirements.txt\n")
    inv = inv_of(scan_root, "root==1.0\n-r a.txt\n")
    assert sorted(e.dist for e in inv.entries) == ["a", "root"]
    assert [s.kind for s in inv.skipped] == ["include_cycle"]
    assert inv.skipped[0].source_file == "a.txt"
    assert inv.skipped[0].line == 2


def test_diamond_include_not_a_cycle(scan_root):
    (scan_root / "shared.txt").write_text("shared==1.0\n")
    (scan_root / "a.txt").write_text("-r shared.txt\n")
    (scan_root / "b.txt").write_text("-r shared.txt\n")
    inv = inv_of(scan_root, "-r a.txt\n-r b.txt\n")
    assert [e.dist for e in inv.entries] == ["shared"]
    assert inv.skipped == ()


def test_missing_include_recorded(scan_root):
    inv = inv_of(scan_root, "-r nope.txt\nfoo==1.0\n")
    assert [e.dist for e in inv.entries] == ["foo"]
    assert [(s.kind, s.path, s.line) for s in inv.skipped] == [("include_missing", "nope.txt", 1)]


def test_include_without_value_is_unsupported_row(scan_root):
    inv = inv_of(scan_root, "-r\n")
    assert inv.entries[0].status is Status.UNSUPPORTED


def test_index_option_in_included_file_taints_whole_tree(scan_root):
    (scan_root / "inc.txt").write_text("--extra-index-url https://example.invalid/simple\ninc==1.0\n")
    inv = inv_of(scan_root, "root==1.0\n-r inc.txt\n")
    assert {e.dist: e.status for e in inv.entries} == {
        "root": Status.UNSUPPORTED,
        "inc": Status.UNSUPPORTED,
    }


def test_index_option_in_root_taints_included_entries(scan_root):
    (scan_root / "inc.txt").write_text("inc==1.0\n")
    inv = inv_of(scan_root, "--index-url https://example.invalid/simple\n-r inc.txt\n")
    assert inv.entries[0].status is Status.UNSUPPORTED
