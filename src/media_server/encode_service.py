"""Keeps the transcode queue moving without anybody starting it.

The encoder half of the pipeline has nothing physical to react to, so it works
on a timer: drain whatever is waiting, deliver whatever finished, sleep, repeat.

The delivery pass runs on every cycle even when nothing was transcoded, because
the thing that changed may have been the external drive being plugged back in
rather than a new job arriving.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .configuration import Configuration
from .encode_worker import EncodeWorker
from .job_catalog import JobCatalog
from .library_delivery import LibraryDelivery
from .service_log import ServiceLog

# Transcodes run in hours, so there is nothing to gain from looking more often
# than this. A disc ripped seconds ago waits a minute at worst.
POLL_SECONDS = 60.0


@dataclass(frozen=True)
class ServicePass:
    """What one cycle of the service got done."""

    encoded_count: int = 0
    delivered_count: int = 0

    @property
    def had_work_to_do(self) -> bool:
        return bool(self.encoded_count or self.delivered_count)


class EncodeService:
    """Transcodes the backlog and delivers it, over and over.

    The clock is injected so tests can run whole cycles without waiting.
    """

    def __init__(
        self,
        configuration: Configuration,
        catalog: JobCatalog,
        encode_worker: EncodeWorker | None = None,
        delivery: LibraryDelivery | None = None,
        announce=print,
        sleep=time.sleep,
        poll_seconds: float = POLL_SECONDS,
    ):
        self.configuration = configuration
        self.catalog = catalog
        self.encode_worker = encode_worker or EncodeWorker(
            configuration, catalog, announce=announce
        )
        self.delivery = delivery or LibraryDelivery(configuration, catalog)
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.log = ServiceLog(announce)

    def serve_forever(self) -> None:
        """Work the queue until interrupted. This is what the launchd agent runs."""
        self.log.write(
            f"Working the transcode queue. Delivering to {self.configuration.library_root}."
        )
        while True:
            self.run_one_pass()
            self.sleep(self.poll_seconds)

    def run_one_pass(self) -> ServicePass:
        """Work the queue, putting each job on the library drive as it finishes.

        Delivering after every job rather than after the whole queue is what
        keeps the staging disk from filling. A finished film is a Blu-ray's
        worth of space, and holding it until the last disc in the backlog is
        done can mean hours of a full disk with nothing gained.

        The first delivery happens before any transcoding, because work may
        already be waiting from a run where the library drive was unplugged.
        """
        encoded_count = 0
        delivered_count = self._deliver_what_is_ready()

        while True:
            outcome = self.encode_worker.encode_next_job()
            if not outcome.had_work_to_do:
                return ServicePass(
                    encoded_count=encoded_count, delivered_count=delivered_count
                )

            self.log.write(outcome.message)
            if outcome.is_encoded:
                encoded_count += 1
            delivered_count += self._deliver_what_is_ready()

    def _deliver_what_is_ready(self) -> int:
        self.delivery.remove_abandoned_partial_files()
        report = self.delivery.deliver_waiting_jobs()

        for delivered_job in report.delivered_jobs:
            self.log.write(f"delivered {delivered_job.describe_title()}")

        if report.held_jobs and not report.delivered_jobs:
            self.log.write_if_changed(report.describe())
        return len(report.delivered_jobs)
