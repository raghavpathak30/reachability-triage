class Toolbox:
    class Probe:
        def execute(self):
            return "executed"

    def __init__(self):
        self.probe = Toolbox.Probe()


toolbox = Toolbox()

if __name__ == "__main__":
    toolbox.probe.execute()
