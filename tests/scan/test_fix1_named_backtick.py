from reachability.scan.targets import named_names


def m(text, module, qual):
    return named_names(text).matches(module, qual)


def test_odd_backtick_line_makes_no_span_tokens():
    text = "a `x` ` y `z`"
    assert not m(text, "p", "y")


def test_odd_backtick_line_does_not_affect_next_line():
    assert m("a ` stray\nsee `PyJWK.encode`", "p", "PyJWK.encode")
