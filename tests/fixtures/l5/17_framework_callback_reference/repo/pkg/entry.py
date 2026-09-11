from pkg.sink import vulnerable
from pkg.framework import App

app = App()
app.on_event("startup", vulnerable)

if __name__ == "__main__":
    app.run()
