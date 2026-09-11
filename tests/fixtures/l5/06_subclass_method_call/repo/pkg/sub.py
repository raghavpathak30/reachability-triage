from pkg.base import Base


class _App:
    def route(self, path):
        def decorator(func):
            return func

        return decorator


app = _App()


class Sub(Base):
    @app.route("/x")
    def run(self):
        self.vulnerable()
