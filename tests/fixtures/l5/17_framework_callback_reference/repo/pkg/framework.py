class App:
    """Local stand-in for a third-party web framework's application object.

    Real implementations store registered lifecycle handlers and invoke them
    from the framework's own run loop; that dispatch logic lives inside the
    framework package itself, not in code a caller writes, so it is not shown
    here.
    """

    def on_event(self, name, handler):
        pass

    def run(self):
        pass
