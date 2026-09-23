"""Command line entry point for the disc pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from .configuration import (
    DEFAULT_CONFIGURATION_PATH,
    LIBRARY_ROOT_KEY,
    STAGING_ROOT_KEY,
    Configuration,
)
from .disc_classifier import DiscClassifier
from .encode_worker import EncodeWorker, is_handbrake_installed
from .job_catalog import JobCatalog, JobState
from .library_delivery import LibraryDelivery
from .makemkv import MakeMkv
from .rip_worker import RipWorker
from .volume_space import LOW_SPACE_GIGABYTES, VOLUME_MISSING, VolumeSpace, space_at

SAMPLE_CONFIGURATION = f"""\
# Where rips are staged while they wait to be transcoded. This disk takes the
# churn, so give it room for the backlog rather than for the whole library.
{STAGING_ROOT_KEY}={{staging_root}}

# Where finished files are delivered. Point this at the external drive. When it
# is not mounted, finished jobs wait here until it comes back.
{LIBRARY_ROOT_KEY}={{library_root}}
"""

QUEUE_TITLE_WIDTH = 40

MAKEMKV_MISSING_MESSAGE = (
    "MakeMKV is not installed. Run ./setup.sh on the server Mac first."
)
HANDBRAKE_MISSING_MESSAGE = (
    "HandBrakeCLI is not installed. Run ./setup.sh on the server Mac first."
)

STATE_DESCRIPTIONS = {
    JobState.STAGED: "ripped, waiting to be transcoded",
    JobState.ENCODING: "being transcoded now",
    JobState.ENCODED: "transcoded, waiting for the library drive",
    JobState.DELIVERED: "finished and delivered",
    JobState.FAILED: "failed, needs attention",
    JobState.NEEDS_REVIEW: "could not be identified, needs a decision",
}


class PipelineCommands:
    """Runs the commands against one configuration and its catalog."""

    def __init__(self, configuration: Configuration):
        self.configuration = configuration
        self._catalog: JobCatalog | None = None

    @property
    def has_catalog(self) -> bool:
        """False until a disc has actually been ripped.

        Commands that only read check this first, so that asking for status
        never creates a staging folder and a database as a side effect.
        """
        return self.configuration.catalog_path.exists()

    @property
    def catalog(self) -> JobCatalog:
        """Opened on first use, because opening it creates it."""
        if self._catalog is None:
            self._catalog = JobCatalog(self.configuration.catalog_path)
        return self._catalog

    def close(self) -> None:
        if self._catalog is None:
            return
        self._catalog.close()

    def show_status(self) -> int:
        """Print the folders, the disk space on each, and the queue depth."""
        self._print_folders()
        print()
        if not self.has_catalog:
            print("Nothing has been ripped yet.")
            return 0

        self._print_backlog()
        print()
        self._print_queue()
        return 0

    def _print_folders(self) -> None:
        staging_space = space_at(self.configuration.staging_root)
        library_space = self._library_space()

        print("Folders")
        print(f"  Staging : {self.configuration.staging_root}")
        print(f"            {staging_space.describe()}")
        print(
            f"  Library : {self.configuration.library_root}"
            f"  ({self._describe_library_availability()})"
        )
        print(f"            {library_space.describe()}")

        if staging_space.is_low:
            print()
            print(
                f"  Staging is under {LOW_SPACE_GIGABYTES:.0f} GB free. "
                "Let the queue drain before adding more discs."
            )

    def _library_space(self) -> VolumeSpace:
        """An unmounted drive reports nothing.

        Measuring it directly would walk up past the empty mount point and
        report the internal disk's free space, which would be a lie.
        """
        if not self.configuration.is_library_available():
            return VOLUME_MISSING
        return space_at(self.configuration.library_root)

    def _describe_library_availability(self) -> str:
        if self.configuration.is_library_available():
            return "mounted"
        return "not mounted, deliveries are on hold"

    def _print_backlog(self) -> None:
        unfinished_jobs = self.catalog.unfinished_jobs()
        backlog_bytes = self.catalog.staged_bytes()
        print("Backlog")
        print(
            f"  {len(unfinished_jobs)} disc(s) holding "
            f"{describe_bytes(backlog_bytes)} in staging"
        )

    def _print_queue(self) -> None:
        counts = self.catalog.count_by_state()
        if not counts:
            print("Queue is empty.")
            return

        print("Queue")
        for state, description in STATE_DESCRIPTIONS.items():
            job_count = counts.get(state, 0)
            if job_count:
                print(f"  {job_count:>3}  {state:<13} {description}")

    def show_queue(self) -> int:
        """List the discs still on their way through, oldest first."""
        if not self.has_catalog:
            print("Nothing in the queue.")
            return 0

        unfinished_jobs = self.catalog.unfinished_jobs()
        if not unfinished_jobs:
            print("Nothing in the queue.")
            return 0

        for job in unfinished_jobs:
            fitted_title = fit_to_width(job.describe_title(), QUEUE_TITLE_WIDTH)
            print(
                f"  #{job.job_id:<4} {fitted_title:<{QUEUE_TITLE_WIDTH}} "
                f"{job.state:<10} {describe_bytes(job.staged_size_bytes()):>10}"
            )
        print()
        print(
            f"  {len(unfinished_jobs)} disc(s), "
            f"{describe_bytes(self.catalog.staged_bytes())} in staging"
        )
        return 0

    def show_attention_list(self) -> int:
        """List jobs that stopped and need a person to look at them."""
        if not self.has_catalog:
            print("Nothing needs attention.")
            return 0

        stopped_jobs = self.catalog.jobs_in_state(
            JobState.FAILED
        ) + self.catalog.jobs_in_state(JobState.NEEDS_REVIEW)
        if not stopped_jobs:
            print("Nothing needs attention.")
            return 0

        for job in stopped_jobs:
            print(f"  #{job.job_id:<4} {job.describe()}")
            if job.failure_reason:
                print(f"        {job.failure_reason}")
        return 0

    def rip_disc_in_drive(self) -> int:
        """Rip whatever is in the drive, then eject it."""
        makemkv = MakeMkv()
        if not makemkv.is_installed():
            print(MAKEMKV_MISSING_MESSAGE)
            return 1

        outcome = RipWorker(self.configuration, self.catalog, makemkv).rip_disc_in_drive()
        print(outcome.message)
        if outcome.is_ripped or outcome.is_duplicate:
            return 0
        return 1

    def scan_disc(self) -> int:
        """Say what the disc looks like and what is on it, without ripping."""
        makemkv = MakeMkv()
        if not makemkv.is_installed():
            print(MAKEMKV_MISSING_MESSAGE)
            return 1

        disc_scan = makemkv.scan_disc()
        for message in disc_scan.messages:
            print(f"  {message}")

        if not disc_scan.has_titles:
            print("No titles found. The drive may be empty, or the disc unreadable.")
            return 1

        print(f'Label      : {disc_scan.disc_label or "unlabelled"}')
        print(f"Disc type  : {disc_scan.media_type or 'unknown'}")
        print(f"Fingerprint: {disc_scan.fingerprint()}")
        print()
        print(DiscClassifier(disc_scan).verdict().describe())
        print()
        print_title_table(disc_scan)

        if self.has_catalog and self.catalog.is_duplicate_disc(disc_scan.fingerprint()):
            print()
            print("This disc has already been ripped. 'rip' would eject it untouched.")
        return 0

    def encode_queue(self, is_encoding_one: bool) -> int:
        """Transcode the backlog, or just the next job in it."""
        if not is_handbrake_installed():
            print(HANDBRAKE_MISSING_MESSAGE)
            return 1
        if not self.has_catalog:
            print("Nothing waiting to be transcoded.")
            return 0

        worker = EncodeWorker(self.configuration, self.catalog)
        if is_encoding_one:
            print(worker.encode_next_job().message)
            return 0

        outcomes = worker.encode_until_queue_is_empty()
        if not outcomes:
            print("Nothing waiting to be transcoded.")
            return 0

        for outcome in outcomes:
            print(outcome.message)
        return 0

    def deliver_waiting_jobs(self) -> int:
        """Move everything that finished transcoding onto the library drive."""
        if not self.has_catalog:
            print("Nothing waiting to be delivered.")
            return 0

        delivery = LibraryDelivery(self.configuration, self.catalog)
        delivery.remove_abandoned_partial_files()
        report = delivery.deliver_waiting_jobs()

        print(report.describe())
        for job in report.delivered_jobs:
            print(f"  delivered {job.describe_title()}")
        return 0

    def forget_job(self, job_id: int) -> int:
        """Drop a job so its disc stops counting as a duplicate."""
        if not self.has_catalog:
            print(f"No job #{job_id}.")
            return 1

        job = self.catalog.job_with_id(job_id)
        if job is None:
            print(f"No job #{job_id}.")
            return 1

        self.catalog.forget_job(job_id)
        print(f"Forgot #{job_id} {job.describe_title()}. That disc can be ripped again.")
        return 0

    def show_configuration(self) -> int:
        print(f"{STAGING_ROOT_KEY}={self.configuration.staging_root}")
        print(f"{LIBRARY_ROOT_KEY}={self.configuration.library_root}")
        return 0


def write_sample_configuration(
    configuration_path: Path, configuration: Configuration
) -> int:
    """Create a starter config file, refusing to clobber one that already exists."""
    if configuration_path.exists():
        print(f"{configuration_path} already exists. Leaving it alone.")
        return 1

    configuration_path.parent.mkdir(parents=True, exist_ok=True)
    configuration_path.write_text(
        SAMPLE_CONFIGURATION.format(
            staging_root=configuration.staging_root,
            library_root=configuration.library_root,
        )
    )
    print(f"Wrote {configuration_path}. Edit it to point at your drives.")
    return 0


def print_title_table(disc_scan) -> None:
    """The titles on the disc, the way ./rip-dvd.sh --list shows them."""
    print(f"{'TITLE':<6} {'LENGTH':<10} {'SIZE':<9} {'SOURCE':<14} {'FLAG':<6} NAME")
    for title in disc_scan.titles:
        main_feature_flag = "main" if title.is_main_feature else "-"
        print(
            f"{title.title_id:<6} {title.duration:<10} "
            f"{describe_bytes(title.size_bytes):<9} {title.source:<14} "
            f"{main_feature_flag:<6} {title.name}"
        )


def fit_to_width(text: str, width: int) -> str:
    """Trim a long title so the queue listing stays in columns."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "\u2026"


def describe_bytes(byte_count: int) -> str:
    """Render a size the way a person would say it out loud."""
    size = float(byte_count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-server",
        description="Rip, transcode, and deliver discs without babysitting them.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIGURATION_PATH,
        help=f"Path to the configuration file (default: {DEFAULT_CONFIGURATION_PATH})",
    )

    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("rip", help="Rip the disc in the drive, then eject it")
    subcommands.add_parser("scan", help="Say what the disc is, without ripping it")

    encode_command = subcommands.add_parser(
        "encode", help="Transcode the ripped backlog into playable files"
    )
    encode_command.add_argument(
        "--one",
        action="store_true",
        help="Transcode only the next job, rather than the whole queue",
    )
    subcommands.add_parser("status", help="Folders, disk space, and queue depth")
    subcommands.add_parser("queue", help="List the discs still on their way through")
    subcommands.add_parser("attention", help="List jobs that stopped and need a look")
    subcommands.add_parser("deliver", help="Move finished files to the library drive")
    subcommands.add_parser("config", help="Show the resolved folders")
    subcommands.add_parser(
        "init-config", help="Write a starter configuration file to edit"
    )

    forget_command = subcommands.add_parser(
        "forget", help="Drop a job so its disc can be ripped again"
    )
    forget_command.add_argument("job_id", type=int, help="The job id shown by 'queue'")
    return parser


def main(argument_values: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argument_values)
    configuration = Configuration.load(arguments.config)

    if arguments.command == "init-config":
        return write_sample_configuration(arguments.config, configuration)

    commands = PipelineCommands(configuration)
    try:
        return run_command(arguments, commands)
    finally:
        commands.close()


def run_command(arguments: argparse.Namespace, commands: PipelineCommands) -> int:
    if arguments.command == "rip":
        return commands.rip_disc_in_drive()
    if arguments.command == "scan":
        return commands.scan_disc()
    if arguments.command == "encode":
        return commands.encode_queue(arguments.one)
    if arguments.command == "status":
        return commands.show_status()
    if arguments.command == "queue":
        return commands.show_queue()
    if arguments.command == "attention":
        return commands.show_attention_list()
    if arguments.command == "deliver":
        return commands.deliver_waiting_jobs()
    if arguments.command == "forget":
        return commands.forget_job(arguments.job_id)
    return commands.show_configuration()
