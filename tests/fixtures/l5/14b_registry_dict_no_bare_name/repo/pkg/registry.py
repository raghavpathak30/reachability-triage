import importlib

HANDLERS = {"go": getattr(importlib.import_module("pkg.sink"), "vulnerable")}
