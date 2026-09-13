from pkg.framework import App


def other_handler():
    return "other_handler"


app = App()

if __name__ == "__main__":
    other_handler()
    app.on_event("startup", other_handler)
