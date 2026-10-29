"""clipper's own error: a problem it can explain in one line (a bad setting, a missing file,
a refused action, a service that said no).

Library code raises ClipperError; only `cli.main` turns it into an exit (the message on
stderr, status 1). Long-running callers (the bot, the war room, the worker) catch it like
any other exception, so one bad stream or button press can't stop them."""


class ClipperError(Exception):
    pass
