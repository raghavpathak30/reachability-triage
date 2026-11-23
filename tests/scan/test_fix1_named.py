from reachability.scan.targets import named_names


def m(text, module, qual):
    return named_names(text).matches(module, qual)


def test_call_chain_span_names_neither_head_nor_tail():
    text = "Use `a.b(c).d()` carefully."
    assert not m(text, "p", "d")
    assert not m(text, "a", "b")
    assert "a.b" not in named_names(text).dotted


def test_plain_call_span_still_matches():
    assert m("Call `open_process()` here.", "p", "open_process")


def test_dotted_call_span_still_matches():
    assert m("Call `Cls.run()` here.", "p", "Cls.run")
