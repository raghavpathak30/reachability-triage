def __getattr__(name):
    if name == "vulnerable":
        from pkg.sink import vulnerable

        return vulnerable
    raise AttributeError(name)
