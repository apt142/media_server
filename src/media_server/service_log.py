"""Logging for the two services that run on a timer.

Both services wake up every few seconds and look at the world. Most of the time
nothing has changed, and a condition that persists — an unplugged library
drive, a staging disk with no room left — would otherwise write the same line
to the log on every pass until somebody noticed it. A log that repeats itself
a thousand times is a log nobody reads.
"""

from __future__ import annotations


class ServiceLog:
    """Writes service output, holding back lines that only repeat the last one."""

    def __init__(self, announce=print):
        self.announce = announce
        self._last_message = ""

    def write(self, message: str) -> None:
        """Say something that matters every time it happens."""
        self._last_message = ""
        self.announce(message)

    def write_if_changed(self, message: str) -> None:
        """Say something only while it is news.

        Repeating the same standing condition is noise, but the moment it
        changes to something else, that is worth writing down.
        """
        if message == self._last_message:
            return
        self._last_message = message
        self.announce(message)
