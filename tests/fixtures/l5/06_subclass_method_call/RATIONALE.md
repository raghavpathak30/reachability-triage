`run` is registered as a route handler on `Sub`, a subclass of `Base`. Inside `run`,
`self.vulnerable()` is called; `Sub` itself defines no `vulnerable`, so the call
resolves up the class hierarchy to `Base.vulnerable`, which every `Sub` instance
inherits unchanged.
