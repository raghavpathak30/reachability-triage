from reachability.scan.targets import named_names
from tests.scan.s1_support import advisory, make_scan


def m(text, module, qual):
    return named_names(text).matches(module, qual)


def test_pyjwkset_init_does_not_match_pyjwk_init():
    text = "A flaw in `PyJWKSet.__init__` lets an attacker."
    assert m(text, "jwt.api_jwk", "PyJWKSet.__init__")
    assert not m(text, "jwt.api_jwk", "PyJWK.__init__")


def test_backticked_pyjwk_init_matches_dotted_class_method():
    text = "`PyJWK.__init__` accepts bad keys"
    assert m(text, "jwt.api_jwk", "PyJWK.__init__")
    assert not m(text, "jwt.api_jwk", "PyJWK")
    assert m("see `jwt.api_jwk.PyJWK.__init__`", "jwt.api_jwk", "PyJWK.__init__")


def test_vanna_prose_ask_matches_nothing():
    text = "The ask function and other helpers; you can ask the server to run things."
    assert not m(text, "vanna", "ask")
    assert not m(text, "vanna.base", "VannaBase.ask")


def test_open_process_call_form_matches():
    text = "Passing input to open_process() allows injection."
    assert m(text, "anyio._core._subprocesses", "open_process")
    assert m(text, "anyio._backends._asyncio", "AsyncIOBackend.open_process")


def test_dotted_token_never_matches_by_last_segment():
    text = "Affects `Other.run` only."
    assert not m(text, "pkg", "run")
    assert not m(text, "pkg", "Real.run")
    assert not m("see `Inner.m`", "pkg", "Outer.Inner.m")
    assert not m("see `a.b.run`", "pkg", "run")


def test_fragment_after_redaction_names_nothing():
    n = named_names("x.[REDACTED: token].baz(")
    assert not n.simple and not n.dotted
    assert not m("x.[REDACTED: token].baz(", "p", "baz")
    assert not m("x.[REDACTED: token].baz(", "p", "x")


def test_backticked_absolute_path_then_prose_makes_no_prose_tokens():
    text = "Edit `/home/user/project/app.py` then handle ask and run safely."
    n = named_names(text)
    assert not n.simple and "ask" not in n.dotted
    assert not m(text, "p", "ask") and not m(text, "p", "run")


def test_unbalanced_backtick_does_not_flip_later_lines():
    text = "a stray ` backtick here\nthen ask the run helper"
    assert not m(text, "p", "ask") and not m(text, "p", "run")


def test_fenced_block_only_call_and_chain_tokens():
    text = "Example:\n```\nplain_word\nvuln(x)\nobj.method(y)\n```\nafter fence prose"
    n = named_names(text)
    assert "vuln" in n.simple and "obj.method" in n.dotted
    assert "plain_word" not in n.simple and "prose" not in n.simple


def test_plural_file_s_is_not_a_call_token():
    assert not m("remove the file(s) listed and the match(es)", "p", "file")
    assert not m("remove the file(s) listed and the match(es)", "p", "match")


def test_span_with_redaction_marker_dropped():
    assert not named_names("`[REDACTED: token]`").simple


def test_sandbox_redacted_long_digit_identifier_is_missed_so_stays_medium():
    # triage/sandbox.py redacts 20+ char identifier-like runs containing a digit; the name is
    # missed and the target stays medium (a missed path_proven, never a wrong one).
    assert not m("`_read_pdf15_xref_stream` is broken", "pypdf._reader", "PdfReader._read_pdf15_xref_stream")
    assert m("`_read_xref` is broken", "pypdf._reader", "PdfReader._read_xref")


def test_named_computed_over_union_of_group_records(tmp_path):
    from reachability.scan.fetch import LocalDirFetcher
    from reachability.scan.pipeline import run_scan
    from tests.scan.s1_support import APP_CALLS_F, LIB_NEW, LIB_OLD, lib_files

    a = advisory("GHSA-aaaa-bbbb-cccc", "lib", aliases=["CVE-2099-0001"], details="nothing named here")
    b = advisory("CVE-2099-0001", "lib", details="the function `f()` is vulnerable")
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=[a, b],
    )
    res = run_scan(s["app"], s["snapshot"], s["sha256"], LocalDirFetcher(s["wheels"]))
    assert len(res.findings) == 1
    f = res.findings[0]
    assert f.evidence_level == "path_proven" and f.targets[0].named is True
