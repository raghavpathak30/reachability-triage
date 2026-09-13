import pkg.mod_b as mod_b
from pkg.framework import App

app = App()

if __name__ == "__main__":
    mod_b.run()
    app.on_event("startup", mod_b.run)
