"""Stand-ins for the optical drive, so tests need no hardware and no discs."""

from __future__ import annotations

from media_server.disc_drive import DiscStatus

EMPTY_DRIVE = DiscStatus(is_present=False)
DVD_IN_DRIVE = DiscStatus(
    is_present=True, media_type="DVD-ROM", device_path="/dev/disk4"
)
BLURAY_IN_DRIVE = DiscStatus(
    is_present=True, media_type="BD-ROM", device_path="/dev/disk4"
)


class RecordingDiscDrive:
    """A drive that only remembers whether it was asked to eject."""

    def __init__(self):
        self.eject_count = 0

    def eject(self) -> None:
        self.eject_count += 1

    @property
    def has_ejected(self) -> bool:
        return self.eject_count > 0


class ScriptedDiscDrive:
    """A drive that reports a prepared sequence of statuses, one per look.

    The last entry repeats once the script runs out, so a test only has to
    describe the part of the sequence it actually cares about.
    """

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.read_count = 0
        self.eject_count = 0

    def read_status(self) -> DiscStatus:
        status = self.statuses[min(self.read_count, len(self.statuses) - 1)]
        self.read_count += 1
        return status

    def eject(self) -> None:
        self.eject_count += 1
