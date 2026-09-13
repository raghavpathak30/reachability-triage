import sys


def something():
    return "something"


def dispatch(name):
    getattr(sys.modules[__name__], name)()
