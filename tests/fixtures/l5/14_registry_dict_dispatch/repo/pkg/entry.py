from pkg.registry import HANDLERS


def dispatch(key):
    HANDLERS[key]()


if __name__ == "__main__":
    dispatch("go")
