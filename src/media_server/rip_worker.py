"""Turns whatever is in the drive into a staged job, then ejects the disc.

This is the drive-bound half of the pipeline and it deliberately stops once the
raw files are on disk. Popping the disc out at that point is what lets a stack
of discs be fed through in an afternoon while the transcodes catch up overnight.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .configuration import Configuration
from .disc_classifier import (
    DiscClassifier,
    DiscVerdict,
    clean_disc_label,
    disc_number_from_label,
    season_from_disc_label,
)
from .disc_drive import DiscDrive
from .file_names import safe_file_component
from .job_catalog import Job, JobCatalog, JobState, RippedDisc
from .makemkv import DiscScan, DiscTitle, MakeMkv, RipResult
from .volume_space import space_at

RAW_FOLDER_NAME = "raw"

# The raw rip and the encode it feeds live side by side until the transcode
# finishes, so the peak need is more than the titles themselves.
SPACE_HEADROOM_MULTIPLIER = 1.3

# Anything running longer than the longest plausible episode is a feature
# rather than an extra. Borrowing the classifier's own ceiling keeps the two
# definitions from drifting apart.
FEATURE_LENGTH_SECONDS = DiscClassifier.LONGEST_EPISODE_SECONDS


@dataclass(frozen=True)
class RipSettings:
    """What to take off a film disc."""

    minimum_feature_seconds: int = FEATURE_LENGTH_SECONDS
    is_main_feature_only: bool = False

    # Rip a disc that has already been through, throwing away what the first
    # attempt produced. Off by default: feeding a stack of discs through means
    # putting the same one back in by mistake, and doing nothing is the right
    # answer to that far more often than ripping it twice is.
    is_rerip_allowed: bool = False

    @property
    def minimum_feature_minutes(self) -> int:
        return self.minimum_feature_seconds // 60


@dataclass
class RipOutcome:
    """What happened to one disc."""

    message: str
    job_id: int | None = None
    is_ripped: bool = False
    ripped_files: list[Path] = field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        return not self.is_ripped and self.job_id is not None


class RipWorker:
    """Scans one disc, decrypts what is worth keeping, and records the job.

    MakeMKV is the authority on whether a disc is readable, so this does not
    ask the drive first. A disc that will not scan reports that plainly rather
    than being mistaken for an empty drive.
    """

    def __init__(
        self,
        configuration: Configuration,
        catalog: JobCatalog,
        makemkv: MakeMkv | None = None,
        drive: DiscDrive | None = None,
        announce=print,
        settings: RipSettings | None = None,
    ):
        self.configuration = configuration
        self.catalog = catalog
        self.makemkv = makemkv or MakeMkv()
        self.drive = drive or DiscDrive()
        self.announce = announce
        self.settings = settings or RipSettings()

    def rip_disc_in_drive(self) -> RipOutcome:
        """Read the disc, rip it, record it, and eject it."""
        disc_scan = self.makemkv.scan_disc()
        self._announce_makemkv_messages(disc_scan)

        if not disc_scan.has_titles:
            return RipOutcome(
                message="No titles found. The drive may be empty, or the disc may not be readable."
            )

        self.announce(f'Disc label: "{disc_scan.disc_label or "unlabelled"}"')

        already_ripped = self.catalog.existing_job_for_disc(disc_scan.fingerprint())
        if already_ripped is not None:
            blocked = self._handle_disc_seen_before(already_ripped)
            if blocked is not None:
                return blocked

        verdict = DiscClassifier(disc_scan).verdict()
        self.announce(verdict.describe())
        if not verdict.is_confident:
            self.announce("That is a guess rather than a certainty.")

        titles_to_rip = self._titles_to_rip(disc_scan, verdict)
        if not titles_to_rip:
            return RipOutcome(message="Nothing on this disc looks worth ripping.")

        self._announce_multiple_features(verdict, titles_to_rip)

        if not self._has_room_for(disc_scan.total_size_bytes(titles_to_rip)):
            return RipOutcome(message=self._no_room_message(titles_to_rip))

        return self._rip_and_record(disc_scan, verdict, titles_to_rip)

    def _handle_disc_seen_before(self, earlier_job: Job) -> RipOutcome | None:
        """Decide what to do about a disc that has already been through.

        None means the way is clear and the rip should carry on.
        """
        if not self.settings.is_rerip_allowed:
            self.drive.eject()
            return RipOutcome(
                message=(
                    f"Already ripped this disc as #{earlier_job.job_id} "
                    f"{earlier_job.describe_title()}. Ejecting without doing it "
                    "again. Use --again to rip it over the top."
                ),
                job_id=earlier_job.job_id,
            )

        if earlier_job.state == JobState.ENCODING:
            # The encoder is reading these files right now, and deleting them
            # out from under it would fail the transcode rather than redo it.
            return RipOutcome(
                message=(
                    f"#{earlier_job.job_id} {earlier_job.describe_title()} is being "
                    "transcoded right now. Let it finish, or stop the encoder, "
                    "before ripping this disc again."
                ),
                job_id=earlier_job.job_id,
            )

        self._discard_earlier_rip(earlier_job)
        return None

    def _discard_earlier_rip(self, earlier_job: Job) -> None:
        """Clear the first attempt out of the way of the second.

        The record goes as well as the files. Leaving it would mean the new rip
        landing in the same folder as the old one's titles, and the disc still
        counting as a duplicate the next time it goes in the drive.

        Anything already delivered to the library is left alone. The new rip
        writes to the same paths and replaces it as it lands.
        """
        self.announce(
            f"Replacing #{earlier_job.job_id} {earlier_job.describe_title()}, "
            f"which was {earlier_job.state}."
        )
        earlier_job.remove_staged_files()
        self.catalog.forget_job(earlier_job.job_id)

    def _titles_to_rip(
        self, disc_scan: DiscScan, verdict: DiscVerdict
    ) -> list[DiscTitle]:
        """Every episode on a TV disc, or every film on a film disc."""
        if verdict.is_show:
            return DiscClassifier(disc_scan).episode_length_titles()
        return self._film_titles(disc_scan)

    def _film_titles(self, disc_scan: DiscScan) -> list[DiscTitle]:
        """Every feature-length title, not only the longest one.

        Double features are common and nothing on the disc reliably separates
        two films from one film and its commentary cut. Taking both costs a
        file somebody deletes in a second; taking one costs a film that was
        never ripped and will not be noticed until it is wanted.

        Falling back to the single best guess matters for a short film, where
        nothing on the disc reaches feature length at all.
        """
        if not self.settings.is_main_feature_only:
            feature_titles = disc_scan.feature_titles(
                self.settings.minimum_feature_seconds
            )
            if feature_titles:
                return feature_titles

        single_feature = disc_scan.feature_title()
        if single_feature is None:
            return []
        return [single_feature]

    def _announce_multiple_features(
        self, verdict: DiscVerdict, titles_to_rip: list[DiscTitle]
    ) -> None:
        if verdict.is_show or len(titles_to_rip) < 2:
            return
        self.announce(
            f"{len(titles_to_rip)} titles run past "
            f"{self.settings.minimum_feature_minutes} minutes, so this looks like a "
            "double feature. Ripping all of them."
        )

    def _no_room_message(self, titles_to_rip: list[DiscTitle]) -> str:
        """Point at the way out when it was the extra features that did not fit."""
        if len(titles_to_rip) < 2:
            return "Not enough room in staging to rip this disc."
        return (
            f"Not enough room in staging for {len(titles_to_rip)} titles. "
            "Let the queue drain, or use --main-feature-only to take just one."
        )

    def _has_room_for(self, needed_bytes: int) -> bool:
        staging_space = space_at(self.configuration.staging_root)
        needed_with_headroom = int(needed_bytes * SPACE_HEADROOM_MULTIPLIER)
        if staging_space.has_room_for(needed_with_headroom):
            return True

        self.announce(
            f"  needs about {needed_with_headroom / 1024**3:.0f} GB, "
            f"for the raw rip and the encode that follows"
        )
        self.announce(f"  free  {staging_space.free_gigabytes:.1f} GB in staging")
        return False

    def _rip_and_record(
        self,
        disc_scan: DiscScan,
        verdict: DiscVerdict,
        titles_to_rip: list[DiscTitle],
    ) -> RipOutcome:
        staged_path = self._staged_path_for(disc_scan)
        ripped_files = self._rip_titles(titles_to_rip, staged_path)

        if not ripped_files:
            shutil.rmtree(staged_path, ignore_errors=True)
            return RipOutcome(
                message=(
                    "MakeMKV produced no usable files. Its messages are above; "
                    "an expired key, missing Java on a Blu-ray, or an "
                    "unreadable disc are the usual causes."
                )
            )

        job_id = self.catalog.record_ripped_disc(
            self._ripped_disc_for(disc_scan, verdict, staged_path, len(ripped_files))
        )
        self.drive.eject()

        return RipOutcome(
            message=(
                f"Ripped {len(ripped_files)} title(s) as job #{job_id}. "
                "The disc is out and the transcode is queued."
            ),
            job_id=job_id,
            is_ripped=True,
            ripped_files=ripped_files,
        )

    def _rip_titles(
        self, titles_to_rip: list[DiscTitle], staged_path: Path
    ) -> list[Path]:
        """Decrypt each title, carrying on past any that refuse.

        One bad episode should not cost the rest of the disc, so a title that
        will not decrypt is reported and skipped rather than ending the run.
        """
        ripped_files = []
        for title in titles_to_rip:
            self.announce(f"Reading {title.describe()}")
            title_path = staged_path / f"title-{title.title_id:02d}"

            rip_result = self.makemkv.rip_title(title.title_id, title_path)
            if not rip_result.is_ripped:
                self._announce_refusal(title, rip_result)
                shutil.rmtree(title_path, ignore_errors=True)
                continue
            ripped_files.append(rip_result.ripped_file)

        return ripped_files

    def _announce_refusal(self, title: DiscTitle, rip_result: RipResult) -> None:
        """Pass on what MakeMKV said about a title it would not decrypt.

        Without this the failure is just "would not decrypt", which is the one
        thing the person already knows. The reason is always in the messages.
        """
        self.announce(f"  title {title.title_id} would not decrypt, skipping it")
        for message in rip_result.messages:
            self.announce(f"    {message}")

    def _ripped_disc_for(
        self,
        disc_scan: DiscScan,
        verdict: DiscVerdict,
        staged_path: Path,
        episode_count: int,
    ) -> RippedDisc:
        title = clean_disc_label(disc_scan.disc_label, is_show=verdict.is_show)
        season_number = self._season_number_for(disc_scan, verdict)

        return RippedDisc(
            disc_label=disc_scan.disc_label,
            media_kind=verdict.media_kind,
            staged_path=staged_path,
            title=title,
            part_number=self._part_number_for(disc_scan, verdict),
            disc_fingerprint=disc_scan.fingerprint(),
            disc_format=disc_scan.media_kind,
            season_number=season_number,
            first_episode_number=self.catalog.next_episode_number_for(
                title, season_number
            ),
            episode_count=episode_count,
        )

    def _season_number_for(
        self, disc_scan: DiscScan, verdict: DiscVerdict
    ) -> int | None:
        if not verdict.is_show:
            return None
        return season_from_disc_label(disc_scan.disc_label)

    def _part_number_for(
        self, disc_scan: DiscScan, verdict: DiscVerdict
    ) -> int | None:
        """A disc number on a film is a part; on a TV disc it is just bookkeeping."""
        if verdict.is_show:
            return None
        return disc_number_from_label(disc_scan.disc_label)

    def _staged_path_for(self, disc_scan: DiscScan) -> Path:
        """Where this disc's raw files go.

        The fingerprint is part of the folder name so two discs sharing a
        generic label cannot land on top of each other.
        """
        folder_name = safe_file_component(disc_scan.disc_label) or "unlabelled"
        return (
            self.configuration.staging_root
            / RAW_FOLDER_NAME
            / f"{folder_name}-{disc_scan.fingerprint()}"
        )

    def _announce_makemkv_messages(self, disc_scan: DiscScan) -> None:
        for message in disc_scan.messages:
            self.announce(f"  {message}")
