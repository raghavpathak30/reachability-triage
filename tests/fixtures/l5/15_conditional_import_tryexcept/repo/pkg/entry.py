try:
    from pkg.sink import vulnerable
except ImportError:
    vulnerable = None


def run():
    if vulnerable:
        vulnerable()


if __name__ == "__main__":
    run()
