from reachability.scan.targets import named_names


def test_bare_get_names_no_method():
    n = named_names("The handler calls `get()` unsafely.")
    assert not n.matches("lib.a", "Client.get")
    assert not n.matches("lib.a", "Client.get@12")


def test_bare_init_names_no_method():
    assert not named_names("`__init__()` is affected").matches("lib.a", "PyJWK.__init__")


def test_class_dot_method_names_method():
    assert named_names("call `Client.get()` first").matches("lib.a", "Client.get")


def test_module_qualname_names_method():
    assert named_names("see `lib.a.Client.get`").matches("lib.a", "Client.get")


def test_bare_names_module_level_function():
    assert named_names("`f()` is vulnerable").matches("lib.a", "f")
    assert named_names("`f()` is vulnerable").matches("lib.a", "f@3")


def test_nested_function_not_named_by_bare_token():
    assert not named_names("`inner()` is vulnerable").matches("lib.a", "outer.inner")
