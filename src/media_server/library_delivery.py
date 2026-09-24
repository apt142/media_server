"""Moves finished files from staging onto the library drive.

Staging and the library are usually different volumes, which makes this more
delicate than a rename. A move across volumes is really a copy followed by a
delete, so an unplugged drive or a crash part way through can leave a truncated
file in the library that looks complete. Everything here copies to a temporary
name, checks the size, renames into place, and only then removes the original.

When the library drive is not mounted, nothing fails: the job simply stays put
and is retried on the next pass.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .configuration import Configuration
from .job_catalog import Job, JobCatalog

PARTIAL_SUFFIX = ".partial"

# Copies are refused unless the library has the file size plus a little room,
# so a delivery cannot be the thing that fills the drive completely.
SPACE_HEADROOM_MULTIPLIER = 1.05


@dataclass
class DeliveryReport:
    """What one pass over the waiting jobs managed to do."""

    delivered_jobs: list[Job] = field(default_factory=list)
    held_jobs: list[Job] = field(default_factory=list)
    is_library_available: bool = True
    library_root: Path | None = None

    @property
    def has_deliveries(self) -> bool:
        return bool(self.delivered_jobs)

    def describe(self) -> str:
        if not self.is_library_available:
            # Naming the path matters: a drive that is plugged in but has no
            # library folder on it yet looks exactly like one that is absent.
            return (
                f"Library drive is not mounted, so {len(self.held_jobs)} finished "
                f"job(s) are on hold. Looking for {self.library_root}."
            )
        if not self.delivered_jobs and not self.held_jobs:
            return "Nothing waiting to be delivered."
        return (
            f"Delivered {len(self.delivered_jobs)} job(s), "
            f"holding {len(self.held_jobs)}."
        )


class LibraryDelivery:
    """Delivers encoded output into the library and clears the staged copy."""

    def __init__(self, configuration: Configuration, catalog: JobCatalog):
        self.configuration = configuration
        self.catalog = catalog

    def deliver_waiting_jobs(self) -> DeliveryReport:
        """Try to deliver everything that finished encoding."""
        waiting_jobs = self.catalog.jobs_awaiting_delivery()
        if not self.configuration.is_library_available():
            return DeliveryReport(
                held_jobs=waiting_jobs,
                is_library_available=False,
                library_root=self.configuration.library_root,
            )

        report = DeliveryReport()
        for job in waiting_jobs:
            if self.deliver_job(job):
                report.delivered_jobs.append(job)
                continue
            report.held_jobs.append(job)
        return report

    def deliver_job(self, job: Job) -> bool:
        """Copy one job's files into the library. False means try again later."""
        if job.encoded_path is None or not job.encoded_path.is_dir():
            self.catalog.mark_failed(
                job.job_id, f"Encoded output is missing at {job.encoded_path}"
            )
            return False

        deliverable_files = files_under(job.encoded_path)
        if not deliverable_files:
            self.catalog.mark_failed(
                job.job_id, f"Encoded output at {job.encoded_path} is empty"
            )
            return False

        if not self._has_room_for(deliverable_files):
            return False

        for source_file in deliverable_files:
            relative_path = source_file.relative_to(job.encoded_path)
            destination = self.configuration.library_root / relative_path
            if not self._copy_into_place(source_file, destination):
                return False

        self._clear_staged_files(job)
        self.catalog.mark_delivered(job.job_id)
        return True

    def _has_room_for(self, source_files: list[Path]) -> bool:
        needed_bytes = sum(source_file.stat().st_size for source_file in source_files)
        free_bytes = shutil.disk_usage(self.configuration.library_root).free
        return needed_bytes * SPACE_HEADROOM_MULTIPLIER <= free_bytes

    def _copy_into_place(self, source_file: Path, destination: Path) -> bool:
        """Copy one file so that a partial copy can never look like a finished one."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial_destination = destination.with_name(destination.name + PARTIAL_SUFFIX)
        partial_destination.unlink(missing_ok=True)

        try:
            shutil.copy2(source_file, partial_destination)
        except OSError:
            partial_destination.unlink(missing_ok=True)
            return False

        if partial_destination.stat().st_size != source_file.stat().st_size:
            partial_destination.unlink(missing_ok=True)
            return False

        partial_destination.replace(destination)
        return True

    def _clear_staged_files(self, job: Job) -> None:
        """Remove the raw rip and the encoded copy now that the library has them."""
        for staged_directory in (job.encoded_path, job.staged_path):
            if staged_directory is None:
                continue
            shutil.rmtree(staged_directory, ignore_errors=True)

    def remove_abandoned_partial_files(self) -> list[Path]:
        """Clean up partial copies left behind by an interrupted delivery."""
        if not self.configuration.is_library_available():
            return []

        removed_files = []
        for partial_file in self.configuration.library_root.rglob(f"*{PARTIAL_SUFFIX}"):
            partial_file.unlink(missing_ok=True)
            removed_files.append(partial_file)
        return removed_files


def files_under(directory: Path) -> list[Path]:
    """Every file in a directory tree, sorted so delivery order is predictable."""
    return sorted(item for item in directory.rglob("*") if item.is_file())
