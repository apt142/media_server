"""Turns whatever is in the drive into a staged job, then ejects the disc.

This is the drive-bound half of the pipeline and it deliberately stops once the
raw files are on disk. Popping the disc out at that point is what lets a stack
of discs be fed through in an afternoon while the transcodes catch up overnight.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from .configuration import Configuration
from .disc_classifier import (
    DiscClassifier,
    DiscVerdict,
    clean_disc_label,
    disc_number_from_label,
    season_from_disc_label,
    verdict_asked_for,
)
from .disc_drive import DiscDrive
from .file_names import safe_file_component
from .job_catalog import Job, JobCatalog, JobState, RippedDisc
from .makemkv import DiscScan, DiscTitle, MakeMkv, RipResult, explain_failure
from .volume_space import space_at

RAW_FOLDER_NAME = "raw"

# Whole-disc copies, kept only for as long as the titles are being read out of
# one. Separate from the raw rips so the encoder never finds a copy and tries
# to treat it as work.
COPY_FOLDER_NAME = "disc-copies"

# The raw rip and the encode it feeds live side by side until the transcode
# finishes, so the peak need is more than the titles themselves.
SPACE_HEADROOM_MULTIPLIER = 1.3

# Anything running longer than the longest plausible episode is a feature
# rather than an extra. Borrowing the classifier's own ceiling keeps the two
# definitions from drifting apart.
FEATURE_LENGTH_SECONDS = DiscClassifier.LONGEST_EPISODE_SECONDS

# How far past its neighbours an episode has to run before it is worth
# remarking on. A double-length pilot clears this easily; the ordinary spread
# between a 42 and a 44 minute episode does not.
NOTABLY_LONGER_MULTIPLE = 1.5


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

    # Override the film-or-show reading of the disc. Empty means let the disc
    # speak for itself, which is right nearly always; this is for the box set
    # whose label says nothing and whose episodes run to film length.
    forced_media_kind: str = ""

    # What to file the disc under, when the volume label is no help. Labels are
    # abbreviated, misspelled and sometimes just DVD_VIDEO, and nothing later
    # in the pipeline can recover a name the disc never carried.
    forced_title: str = ""

    # Which season this disc holds. Only a label that says so can be trusted
    # to know, and plenty do not, so a disc of season two would otherwise be
    # filed as season one and overwrite it episode for episode.
    forced_season_number: int | None = None

    # Copy the whole disc first and take the titles out of the copy, rather
    # than reading them off the disc. For the rare disc that stops the drive
    # part way through; it wants room for the entire disc, so it is asked for
    # rather than assumed.
    is_trying_harder: bool = False

    @property
    def is_media_kind_forced(self) -> bool:
        return bool(self.forced_media_kind)

    @property
    def minimum_feature_minutes(self) -> int:
        return self.minimum_feature_seconds // 60


@dataclass
class RipAttempt:
    """What came off a disc, and what MakeMKV said about what did not."""

    ripped_files: list[Path] = field(default_factory=list)
    refusal_messages: list[str] = field(default_factory=list)


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

        verdict = self._verdict_for(disc_scan)
        self._announce_verdict(verdict)
        self._announce_naming(verdict)

        if self.settings.is_trying_harder:
            return self._rip_through_copy(disc_scan, verdict)
        return self._select_and_rip(disc_scan, verdict, disc_scan, self.makemkv)

    def _select_and_rip(
        self,
        disc_scan: DiscScan,
        verdict: DiscVerdict,
        title_scan: DiscScan,
        reader: MakeMkv,
    ) -> RipOutcome:
        """Choose what is worth keeping out of ``title_scan`` and read it.

        The disc is identified and named from ``disc_scan``, which always comes
        off the drive, while the titles come from wherever ``reader`` is
        pointed. Those are the same scan ordinarily and differ only when the
        disc has been copied first, where the label still has to match the one
        the catalogue already knows.
        """
        titles_to_rip = self._titles_to_rip(title_scan, verdict)
        if not titles_to_rip:
            return RipOutcome(message="Nothing on this disc looks worth ripping.")

        self._announce_multiple_features(verdict, titles_to_rip)
        self._announce_long_episodes(verdict, titles_to_rip)

        if not self._has_room_for(title_scan.total_size_bytes(titles_to_rip)):
            return RipOutcome(message=self._no_room_message(titles_to_rip))

        return self._rip_and_record(disc_scan, verdict, titles_to_rip, reader)

    def _rip_through_copy(
        self, disc_scan: DiscScan, verdict: DiscVerdict
    ) -> RipOutcome:
        """Copy the whole disc, then take the titles out of the copy.

        The copy goes once the titles are out of it. Keeping it would double
        what every rip costs in staging, and the only thing it is good for is
        the rip that just happened.
        """
        if not self._has_room_for_copy(disc_scan):
            return RipOutcome(
                message="Not enough room in staging to copy the whole disc."
            )

        copy_path = self._copy_path_for(disc_scan)
        self.announce(
            "Copying the whole disc before ripping it. This takes longer than "
            "a normal rip and needs room for the entire disc."
        )
        backup = self.makemkv.back_up_disc(copy_path)
        for message in backup.messages:
            self.announce(f"  {message}")

        if not backup.is_backed_up:
            shutil.rmtree(copy_path, ignore_errors=True)
            return RipOutcome(
                message=(
                    "Could not copy the disc. "
                    f"{explain_failure(backup.messages)}"
                )
            )

        try:
            reader = self.makemkv.reading_from(copy_path)
            self.announce("Copied. Reading the titles out of the copy.")
            return self._select_and_rip(
                disc_scan, verdict, reader.scan_disc(), reader
            )
        finally:
            shutil.rmtree(copy_path, ignore_errors=True)

    def _verdict_for(self, disc_scan: DiscScan) -> DiscVerdict:
        """What the disc holds: what it looks like, unless told otherwise."""
        if self.settings.is_media_kind_forced:
            return verdict_asked_for(self.settings.forced_media_kind)
        return DiscClassifier(disc_scan).verdict()

    def _announce_verdict(self, verdict: DiscVerdict) -> None:
        if self.settings.is_media_kind_forced:
            self.announce(f"Treating this as {verdict.kind_name} because you said so.")
            return

        self.announce(verdict.describe())
        if not verdict.is_confident:
            self.announce("That is a guess rather than a certainty.")

    def _announce_naming(self, verdict: DiscVerdict) -> None:
        """Say what the disc is being filed as when that did not come off the label.

        A season given for a disc being ripped as a film has nowhere to go, so
        it is said out loud rather than dropped. Quietly ignoring an argument
        someone typed is how a box set ends up in the Movies folder with nobody
        able to say why.
        """
        if self.settings.forced_season_number is not None and not verdict.is_show:
            self.announce(
                "Ignoring --season, because this disc is being ripped as a film."
            )

        filed_as = self._describe_forced_naming(verdict)
        if filed_as:
            self.announce(f"Filing it under {filed_as}.")

    def _describe_forced_naming(self, verdict: DiscVerdict) -> str:
        described = []
        if self.settings.forced_title:
            described.append(f'"{self.settings.forced_title}"')
        if verdict.is_show and self.settings.forced_season_number is not None:
            described.append(f"season {self.settings.forced_season_number}")
        return ", ".join(described)

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
            return DiscClassifier(disc_scan).episodes_to_rip()
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

    def _announce_long_episodes(
        self, verdict: DiscVerdict, titles_to_rip: list[DiscTitle]
    ) -> None:
        """Say when one episode is much longer than the others on the disc.

        This is the judgement most worth showing, because the alternative was
        silently dropping it and shifting every episode number after it.

        What makes it worth saying is standing out from its neighbours, not
        passing a fixed length. A miniseries where every episode runs past
        feature length has no odd one out, and announcing all of them would
        report a decision nobody made.
        """
        if not verdict.is_show or len(titles_to_rip) < 2:
            return

        typical_seconds = median(title.length_seconds for title in titles_to_rip)
        for title in titles_to_rip:
            if title.length_seconds > typical_seconds * NOTABLY_LONGER_MULTIPLE:
                self.announce(
                    f"Title {title.title_id} runs {title.length_seconds // 60} "
                    "minutes, well past the others. Taking it as a "
                    "double-length episode rather than skipping it."
                )

    def _no_room_message(self, titles_to_rip: list[DiscTitle]) -> str:
        """Point at the way out when it was the extra features that did not fit."""
        if len(titles_to_rip) < 2:
            return "Not enough room in staging to rip this disc."
        return (
            f"Not enough room in staging for {len(titles_to_rip)} titles. "
            "Let the queue drain, or use --main-feature-only to take just one."
        )

    def _has_room_for(
        self,
        needed_bytes: int,
        purpose: str = "for the raw rip and the encode that follows",
    ) -> bool:
        staging_space = space_at(self.configuration.staging_root)
        needed_with_headroom = int(needed_bytes * SPACE_HEADROOM_MULTIPLIER)
        if staging_space.has_room_for(needed_with_headroom):
            return True

        self.announce(
            f"  needs about {needed_with_headroom / 1024**3:.0f} GB, {purpose}"
        )
        self.announce(f"  free  {staging_space.free_gigabytes:.1f} GB in staging")
        return False

    def _rip_and_record(
        self,
        disc_scan: DiscScan,
        verdict: DiscVerdict,
        titles_to_rip: list[DiscTitle],
        reader: MakeMkv,
    ) -> RipOutcome:
        staged_path = self._staged_path_for(disc_scan)
        attempt = self._rip_titles(titles_to_rip, staged_path, reader)

        if not attempt.ripped_files:
            shutil.rmtree(staged_path, ignore_errors=True)
            return RipOutcome(
                message=(
                    "MakeMKV produced no usable files. "
                    f"{explain_failure(attempt.refusal_messages)}"
                )
            )

        job_id = self.catalog.record_ripped_disc(
            self._ripped_disc_for(
                disc_scan, verdict, staged_path, len(attempt.ripped_files)
            )
        )
        self.drive.eject()

        return RipOutcome(
            message=(
                f"Ripped {len(attempt.ripped_files)} title(s) as job #{job_id}. "
                "The disc is out and the transcode is queued."
            ),
            job_id=job_id,
            is_ripped=True,
            ripped_files=attempt.ripped_files,
        )

    def _rip_titles(
        self, titles_to_rip: list[DiscTitle], staged_path: Path, reader: MakeMkv
    ) -> RipAttempt:
        """Decrypt each title, carrying on past any that refuse.

        One bad episode should not cost the rest of the disc, so a title that
        will not decrypt is reported and skipped rather than ending the run.

        What MakeMKV said about the refusals is carried back out. When nothing
        at all came off the disc, those messages are the only thing that knows
        why.
        """
        attempt = RipAttempt()
        for title in titles_to_rip:
            self.announce(f"Reading {title.describe()}")
            title_path = staged_path / f"title-{title.title_id:02d}"

            rip_result = reader.rip_title(title.title_id, title_path)
            if not rip_result.is_ripped:
                self._announce_refusal(title, rip_result)
                attempt.refusal_messages.extend(rip_result.messages)
                shutil.rmtree(title_path, ignore_errors=True)
                continue
            attempt.ripped_files.append(rip_result.ripped_file)

        return attempt

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
        title = self.settings.forced_title or clean_disc_label(
            disc_scan.disc_label, is_show=verdict.is_show
        )
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
        if self.settings.forced_season_number is not None:
            return self.settings.forced_season_number
        return season_from_disc_label(disc_scan.disc_label)

    def _part_number_for(
        self, disc_scan: DiscScan, verdict: DiscVerdict
    ) -> int | None:
        """A disc number on a film is a part; on a TV disc it is just bookkeeping."""
        if verdict.is_show:
            return None
        return disc_number_from_label(disc_scan.disc_label)

    def _copy_path_for(self, disc_scan: DiscScan) -> Path:
        """Where the whole-disc copy goes while it is being read out of.

        Beside the raw rips rather than inside them, because the encoder walks
        the raw folder looking for work and a half-written disc copy is not it.
        """
        folder_name = safe_file_component(disc_scan.disc_label) or "unlabelled"
        return (
            self.configuration.staging_root
            / COPY_FOLDER_NAME
            / f"{folder_name}-{disc_scan.fingerprint()}"
        )

    def _has_room_for_copy(self, disc_scan: DiscScan) -> bool:
        """Room for the whole disc and for the titles taken out of it at once.

        The copy is deleted as soon as the titles are out, but both exist
        together in the middle, and that is the moment that has to fit. The
        disc's own size is estimated from its titles, which is the only measure
        a scan offers.
        """
        copy_bytes = disc_scan.total_size_bytes(disc_scan.titles)
        biggest_title_bytes = max(
            (title.size_bytes for title in disc_scan.titles), default=0
        )
        return self._has_room_for(
            copy_bytes + biggest_title_bytes,
            purpose="for the disc copy, the rip taken from it, and the encode",
        )

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
