"""Transcodes staged rips into files Plex and the Roku will play.

This is the slow, CPU-bound half of the pipeline. It claims one job at a time
and works through the queue, which is deliberate: HandBrake already saturates
every core, so running two encodes at once gains no throughput and only doubles
the memory.

Output is written into staging in the shape the library expects, so delivery
can mirror it onto the external drive without knowing anything about films,
shows or episode numbering.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .configuration import Configuration
from .encode_settings import HIGHEST_SUCCESSFUL_EXIT_CODE, EncodeSettings
from .job_catalog import Job, JobCatalog
from .library_layout import destinations_for

ENCODED_FOLDER_NAME = "encoded"
HANDBRAKE_COMMAND = "HandBrakeCLI"


@dataclass
class EncodeOutcome:
    """What happened to one job."""

    message: str
    job: Job | None = None
    is_encoded: bool = False

    @property
    def had_work_to_do(self) -> bool:
        return self.job is not None


def run_handbrake(arguments: list[str]) -> int:
    """Run HandBrakeCLI and hand back its exit code.

    Output is left attached to the terminal so the progress meter is visible
    during what is usually a multi-hour job.
    """
    try:
        completed_process = subprocess.run(
            [HANDBRAKE_COMMAND, *arguments], stdin=subprocess.DEVNULL
        )
    except OSError:
        return 127
    return completed_process.returncode


def is_handbrake_installed() -> bool:
    return shutil.which(HANDBRAKE_COMMAND) is not None


class EncodeWorker:
    """Takes one job off the queue and transcodes it."""

    def __init__(
        self,
        configuration: Configuration,
        catalog: JobCatalog,
        run_command=run_handbrake,
        announce=print,
        audio_languages: str = "",
        subtitle_languages: str = "",
    ):
        self.configuration = configuration
        self.catalog = catalog
        self.run_command = run_command
        self.announce = announce
        self.audio_languages = audio_languages
        self.subtitle_languages = subtitle_languages

    def encode_next_job(self) -> EncodeOutcome:
        """Claim the oldest staged job and transcode everything in it."""
        job = self.catalog.claim_next_for_encoding()
        if job is None:
            return EncodeOutcome(message="Nothing waiting to be transcoded.")

        self.announce(f"Transcoding {job.describe_title()}")
        source_files = raw_files_in(job.staged_path)
        if not source_files:
            return self._fail(job, f"No raw files left at {job.staged_path}")

        return self._encode_files(job, source_files)

    def _encode_files(self, job: Job, source_files: list[Path]) -> EncodeOutcome:
        settings = self._settings_for(job)
        encoded_path = self._encoded_path_for(job)
        destinations = destinations_for(
            job, len(source_files), settings.container_extension
        )

        for source_file, relative_destination in zip(source_files, destinations):
            output_file = encoded_path / relative_destination
            if not self._encode_one(settings, source_file, output_file):
                shutil.rmtree(encoded_path, ignore_errors=True)
                return self._fail(job, f"HandBrake failed on {source_file.name}")
            self.announce(f"  wrote {relative_destination}")

        self.catalog.mark_encoded(job.job_id, encoded_path)
        return EncodeOutcome(
            message=(
                f"Transcoded {job.describe_title()} into {len(destinations)} file(s). "
                "Waiting for the library drive."
            ),
            job=job,
            is_encoded=True,
        )

    def _encode_one(
        self, settings: EncodeSettings, source_file: Path, output_file: Path
    ) -> bool:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        exit_code = self.run_command(
            settings.command_arguments(source_file, output_file)
        )
        return exit_code <= HIGHEST_SUCCESSFUL_EXIT_CODE

    def _settings_for(self, job: Job) -> EncodeSettings:
        return EncodeSettings(
            disc_format=job.disc_format,
            audio_languages=self.audio_languages,
            subtitle_languages=self.subtitle_languages,
        )

    def _encoded_path_for(self, job: Job) -> Path:
        return (
            self.configuration.staging_root
            / ENCODED_FOLDER_NAME
            / f"job-{job.job_id:05d}"
        )

    def _fail(self, job: Job, failure_reason: str) -> EncodeOutcome:
        self.announce(f"  {failure_reason}")
        self.catalog.mark_failed(job.job_id, failure_reason)
        return EncodeOutcome(message=failure_reason, job=job)


def raw_files_in(staged_path: Path) -> list[Path]:
    """The rips waiting in one staging folder, in the order they were ripped.

    Sorting matters for TV: the folders are named after the title ids, so
    sorting by path keeps episodes in the order they sit on the disc.
    """
    if not staged_path.is_dir():
        return []
    return sorted(item for item in staged_path.rglob("*.mkv") if item.is_file())
