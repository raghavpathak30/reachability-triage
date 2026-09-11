from pkg.sink import vulnerable


def run():
    def vulnerable():
        return "shadow"

    vulnerable()


if __name__ == "__main__":
    run()
