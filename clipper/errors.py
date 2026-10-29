"""clipper's own error: a problem it can explain in one line (a bad setting, a missing file,
a refused action, a service that said no).

Library code raises ClipperError; only `cli.main` turns it into an exit (the message on
stderr, status 1). Long-running callers (the bot, the war room, the worker) catch it like
any other exception, so one bad stream or button press can't stop them."""


class ClipperError(Exception):
    """`status` is the exit status cli.main uses: 1, unless a caller's contract says
    otherwise (setup's EXIT_SYNC = 3 tells the bootstrap script to install packages)."""

    def __init__(self, message: str = "", status: int = 1):
        super().__init__(message)
        self.status = status
