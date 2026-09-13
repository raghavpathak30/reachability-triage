import pkg.target3 as target3
from pkg.framework import App

app = App()

if __name__ == "__main__":
    target3.handler()
    app.on_event("startup", target3.handler)
