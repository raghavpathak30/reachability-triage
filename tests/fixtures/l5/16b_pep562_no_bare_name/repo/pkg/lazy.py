import importlib


def __getattr__(name):
    if name == "vulnerable":
        module = importlib.import_module("pkg.sink")
        return getattr(module, "vulnerable")
    raise AttributeError(name)
