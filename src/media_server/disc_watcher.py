"""Watches the optical drive so putting a disc in is the only thing you do.

The drive is polled rather than subscribed to, for the reason given in
``disc_drive``: encrypted Blu-rays often never present a mountable volume, so
mount events would miss exactly the discs that matter most.

A rip is started only when the drive goes from empty to holding something. That
matters more than it sounds: the rip worker ejects when it finishes, so if an
eject ever fails, a watcher keyed on "a disc is present" would rip the same
disc over and over. Keyed on the change instead, a stuck disc is simply ignored
until a person takes it out.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .configuration import Configuration
from .disc_drive import DiscDrive
from .job_catalog import JobCatalog
from .makemkv import MakeMkv
from .rip_worker import RipWorker
from .service_log import ServiceLog
from .volume_space import space_at

POLL_SECONDS = 5.0

# drutil reports a disc the moment the tray closes, well before the drive has
# spun up and read the table of contents. Scanning that early gets a read
# error on a disc that is perfectly fine a few seconds later.
SETTLE_SECONDS = 15.0


@dataclass(frozen=True)
class WatchSettings:
    """How eagerly to poll, and when to stop accepting discs."""

    poll_seconds: float = POLL_SECONDS
    settle_seconds: float = SETTLE_SECONDS
    maximum_queue_depth: int | None = None


@dataclass(frozen=True)
class WatchOutcome:
    """What one look at the drive came to."""

    message: str = ""
    is_ripped: bool = False
    is_holding: bool = False

    @property
    def is_idle(self) -> bool:
        return not self.message


IDLE = WatchOutcome()


class DiscWatcher:
    """Polls the drive and rips whatever turns up in it.

    The clock is injected so the tests can run a watcher through a whole
    sequence of discs without waiting on a real one.
    """

    def __init__(
        self,
        configuration: Configuration,
        catalog: JobCatalog,
        rip_worker: RipWorker | None = None,
        drive: DiscDrive | None = None,
        settings: WatchSettings | None = None,
        announce=print,
        sleep=time.sleep,
    ):
        self.configuration = configuration
        self.catalog = catalog
        self.drive = drive or DiscDrive()
        self.settings = settings or WatchSettings()
        self.sleep = sleep
        self.log = ServiceLog(announce)
        self.rip_worker = rip_worker or RipWorker(
            configuration, catalog, MakeMkv(), self.drive, announce
        )
        self._is_disc_handled = False

    def watch_forever(self) -> None:
        """Poll until interrupted. This is what the launchd agent runs."""
        self.log.write(
            f"Watching the drive. Rips are staged in {self.configuration.staging_root}."
        )
        while True:
            self.check_once()
            self.sleep(self.settings.poll_seconds)

    def check_once(self) -> WatchOutcome:
        """Look at the drive once and act if there is something new in it."""
        disc_status = self.drive.read_status()

        if not disc_status.is_present:
            self._is_disc_handled = False
            return IDLE

        if self._is_disc_handled:
            return IDLE

        holding_reason = self._holding_reason()
        if holding_reason:
            self.log.write_if_changed(holding_reason)
            return WatchOutcome(message=holding_reason, is_holding=True)

        return self._rip_the_disc(disc_status)

    def _rip_the_disc(self, disc_status) -> WatchOutcome:
        """Rip the disc now in the drive, whatever the outcome turns out to be.

        The disc counts as handled before the rip runs rather than after, so a
        rip that dies part way through still does not get retried in a loop.
        """
        self.log.write(f"{disc_status.describe()} Letting it spin up.")
        self.sleep(self.settings.settle_seconds)
        self._is_disc_handled = True

        outcome = self.rip_worker.rip_disc_in_drive()
        if outcome.is_ripped or outcome.is_duplicate:
            self.log.write(outcome.message)
            return WatchOutcome(message=outcome.message, is_ripped=outcome.is_ripped)

        return self._report_failure(outcome.message)

    def _report_failure(self, failure_message: str) -> WatchOutcome:
        """Leave a failed disc in the drive on purpose.

        A disc that pops out means the pipeline is done with it. Ejecting a
        disc that failed would use the same signal for both outcomes, so the
        one that needs a person stays put where it can be seen.
        """
        message = f"{failure_message} Leaving it in the drive so you can see it stopped here."
        self.log.write(message)
        return WatchOutcome(message=message)

    def _holding_reason(self) -> str:
        """Why this disc should wait, or an empty string to go ahead.

        Holding beats refusing. The disc stays in the drive and is picked up on
        a later pass once the encoder has drained some of the backlog, which
        means a full staging disk costs you time rather than attention.
        """
        staging_space = space_at(self.configuration.staging_root)
        if staging_space.is_low:
            return (
                f"Staging is down to {staging_space.free_gigabytes:.0f} GB free. "
                "Holding this disc until the transcode queue drains."
            )

        if self._is_queue_full():
            return (
                f"{self.settings.maximum_queue_depth} disc(s) already waiting. "
                "Holding this one until the transcode queue drains."
            )
        return ""

    def _is_queue_full(self) -> bool:
        if self.settings.maximum_queue_depth is None:
            return False
        return len(self.catalog.unfinished_jobs()) >= self.settings.maximum_queue_depth
