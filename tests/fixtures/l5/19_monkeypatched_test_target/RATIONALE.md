Normally, `run` calls `pkg.decoy.safe`, which does nothing interesting. But the test
`test_it` replaces (`monkeypatch.setattr`) `pkg.decoy`'s `safe` attribute with
`vulnerable` before calling `run()`. Because `entry.py` looks up `pkg.decoy.safe`
through the module rather than binding its own copy of the name at import time, the
patched attribute is what `run()` actually calls during that test — so for the
duration of `test_it`, calling `run` really does call `vulnerable`, even though
nothing about `entry.py` or `decoy.py` read on their own suggests that. Outside of
this test, there is no code path from `run` to `vulnerable`.
