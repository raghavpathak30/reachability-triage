import pkg.sink as sink


def dispatch(name):
    getattr(sink, name)()


if __name__ == "__main__":
    dispatch("vulnerable")
