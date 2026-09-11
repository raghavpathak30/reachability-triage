`vulnerable` is registered with `app.on_event("startup", vulnerable)` — passed by
reference, not called directly anywhere in this file. `pkg/framework.py`'s `App`
class models the common pattern of a web framework's lifecycle-hook API: real
frameworks store registered handlers and invoke them from their own internal run
loop when the corresponding event actually fires, and that dispatch logic lives
inside the framework's own code, not in code a caller writes. From this repository's
own source we can confirm `vulnerable` is handed to the framework for the "startup"
event; we cannot mechanically confirm from here alone that the framework's dispatch
logic definitely invokes it, but this is exactly the situation a reviewer auditing
code built on a third-party framework normally faces, and treating it as
unreachable would be the wrong call.
