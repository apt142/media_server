"""Durable record of every disc moving through the pipeline.

Ripping is bound by the optical drive and transcoding is bound by the CPU, so
the two run as separate workers. This catalog is the handoff between them, and
it lives on disk so a crash, a reboot, or an unplugged library drive never
loses track of work that is part way through.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


class JobState(StrEnum):
    STAGED = "staged"
    ENCODING = "encoding"
    ENCODED = "encoded"
    DELIVERED = "delivered"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


# Work that is still on its way through, and so still occupying staging space.
UNFINISHED_STATES = (JobState.STAGED, JobState.ENCODING, JobState.ENCODED)

# A disc counts as already seen unless the last attempt failed, because a
# failed attempt is exactly the case where re-inserting the disc is the fix.
DUPLICATE_BLOCKING_STATES = (
    JobState.STAGED,
    JobState.ENCODING,
    JobState.ENCODED,
    JobState.DELIVERED,
    JobState.NEEDS_REVIEW,
)


FILM_MEDIA_KIND = "film"
SHOW_MEDIA_KIND = "show"

DVD_DISC_FORMAT = "dvd"
BLURAY_DISC_FORMAT = "bluray"


@dataclass(frozen=True)
class RippedDisc:
    """What came off a disc, ready to be handed to the catalog."""

    disc_label: str
    media_kind: str
    staged_path: Path
    title: str = ""
    year: str = ""
    part_number: int | None = None
    disc_fingerprint: str = ""
    is_needing_review: bool = False

    # Which preset the transcode uses. Encoding a Blu-ray with the 480p preset
    # would throw away most of the picture, and the disc is long gone by then.
    disc_format: str = DVD_DISC_FORMAT

    # Where this disc sits in a season. Episodes are numbered from
    # first_episode_number so that disc two carries on where disc one stopped.
    season_number: int | None = None
    first_episode_number: int = 1
    episode_count: int = 0

    @property
    def initial_state(self) -> JobState:
        if self.is_needing_review:
            return JobState.NEEDS_REVIEW
        return JobState.STAGED

    @property
    def next_episode_number(self) -> int:
        """The episode number a following disc of this season should start at."""
        return self.first_episode_number + self.episode_count


@dataclass(frozen=True)
class Job:
    """One disc, from the moment it is ripped until its files reach the library."""

    job_id: int
    disc_label: str
    media_kind: str
    title: str
    year: str
    part_number: int | None
    state: JobState
    staged_path: Path
    encoded_path: Path | None
    disc_fingerprint: str
    failure_reason: str
    attempt_count: int
    disc_format: str = DVD_DISC_FORMAT
    season_number: int | None = None
    first_episode_number: int = 1
    episode_count: int = 0

    @property
    def is_part_of_split_film(self) -> bool:
        return self.part_number is not None

    @property
    def is_show(self) -> bool:
        return self.media_kind == SHOW_MEDIA_KIND

    @property
    def is_bluray(self) -> bool:
        return self.disc_format == BLURAY_DISC_FORMAT

    def describe_title(self) -> str:
        """The film or show as a person would name it, with no pipeline state."""
        described_title = self.title or self.disc_label
        if self.year:
            described_title = f"{described_title} ({self.year})"
        if self.part_number is None:
            return described_title
        return f"{described_title} part {self.part_number}"

    def describe(self) -> str:
        """A one line summary for logs and status output."""
        return f"{self.describe_title()} [{self.state}]"

    def staged_size_bytes(self) -> int:
        """How much staging disk this job is holding on to right now."""
        total_bytes = _directory_size_bytes(self.staged_path)
        if self.encoded_path is None:
            return total_bytes
        return total_bytes + _directory_size_bytes(self.encoded_path)

    def remove_staged_files(self) -> None:
        """Delete the raw rip and the encoded copy this job is holding.

        Called once the library has the files, and again when a disc is ripped
        a second time and the first attempt's files are sitting in the way.
        """
        for staged_directory in (self.encoded_path, self.staged_path):
            if staged_directory is not None:
                shutil.rmtree(staged_directory, ignore_errors=True)


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS jobs (
        job_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        disc_label      TEXT    NOT NULL,
        media_kind      TEXT    NOT NULL,
        title           TEXT    NOT NULL DEFAULT '',
        year            TEXT    NOT NULL DEFAULT '',
        part_number     INTEGER,
        state           TEXT    NOT NULL,
        staged_path     TEXT    NOT NULL,
        encoded_path    TEXT,
        disc_fingerprint TEXT   NOT NULL DEFAULT '',
        failure_reason  TEXT    NOT NULL DEFAULT '',
        attempt_count   INTEGER NOT NULL DEFAULT 0,
        disc_format     TEXT    NOT NULL DEFAULT 'dvd',
        season_number   INTEGER,
        first_episode_number INTEGER NOT NULL DEFAULT 1,
        episode_count   INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT    NOT NULL,
        updated_at      TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS jobs_by_state ON jobs (state, job_id)",
    "CREATE INDEX IF NOT EXISTS jobs_by_fingerprint ON jobs (disc_fingerprint)",
)


class JobCatalog:
    """Reads and writes the pipeline's job records.

    Opened with isolation_level of None so transactions are explicit: claiming
    work has to be a single atomic step or two workers could take the same disc.
    """

    def __init__(self, catalog_path: Path):
        self.catalog_path = catalog_path
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(catalog_path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._use_write_ahead_logging()
        self._create_schema()

    def _use_write_ahead_logging(self) -> None:
        """Let reading and writing happen at the same time.

        The watcher and the encoder are separate processes sharing this one
        file, and a person runs ``status`` against it while both are working.
        Under SQLite's default rollback journal a writer locks everyone else
        out for the length of its commit; with write-ahead logging readers see
        the last committed state instead and never wait.

        Network filesystems do not support it. SQLite reports back the mode it
        settled on rather than failing, so a staging folder on a share quietly
        keeps the old behaviour rather than refusing to open.
        """
        self._connection.execute("PRAGMA journal_mode=WAL")

    def _create_schema(self) -> None:
        for statement in SCHEMA_STATEMENTS:
            self._connection.execute(statement)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> JobCatalog:
        return self

    def __exit__(self, *_unused_exception_details: object) -> None:
        self.close()

    def record_ripped_disc(self, ripped_disc: RippedDisc) -> int:
        """Add a freshly ripped disc and return its job id."""
        now = _timestamp()
        cursor = self._connection.execute(
            """
            INSERT INTO jobs (
                disc_label, media_kind, title, year, part_number, state,
                staged_path, disc_fingerprint, disc_format, season_number,
                first_episode_number, episode_count, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ripped_disc.disc_label,
                ripped_disc.media_kind,
                ripped_disc.title,
                ripped_disc.year,
                ripped_disc.part_number,
                ripped_disc.initial_state,
                str(ripped_disc.staged_path),
                ripped_disc.disc_fingerprint,
                ripped_disc.disc_format,
                ripped_disc.season_number,
                ripped_disc.first_episode_number,
                ripped_disc.episode_count,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def existing_job_for_disc(self, disc_fingerprint: str) -> Job | None:
        """The job for a disc that has already been through, if there is one.

        A disc with no fingerprint is always treated as new, so an unreadable
        or unscannable disc is never silently skipped as a duplicate.
        """
        if not disc_fingerprint:
            return None

        state_placeholders = ", ".join("?" for _state in DUPLICATE_BLOCKING_STATES)
        row = self._connection.execute(
            f"""
            SELECT * FROM jobs
             WHERE disc_fingerprint = ?
               AND state IN ({state_placeholders})
             ORDER BY job_id DESC
             LIMIT 1
            """,
            (disc_fingerprint, *DUPLICATE_BLOCKING_STATES),
        ).fetchone()
        if row is None:
            return None
        return _job_from_row(row)

    def next_episode_number_for(self, title: str, season_number: int | None) -> int:
        """Where the next disc of this season should start numbering.

        Disc two of a season has no idea it is disc two, so the count comes
        from what has already been recorded rather than from the disc itself.
        """
        row = self._connection.execute(
            """
            SELECT MAX(first_episode_number + episode_count) AS next_episode
              FROM jobs
             WHERE title = ? AND season_number IS ? AND state != ?
            """,
            (title, season_number, JobState.FAILED),
        ).fetchone()
        if row is None or row["next_episode"] is None:
            return 1
        return int(row["next_episode"])

    def is_duplicate_disc(self, disc_fingerprint: str) -> bool:
        """True when this exact disc has already been ripped."""
        return self.existing_job_for_disc(disc_fingerprint) is not None

    def claim_next_for_encoding(self) -> Job | None:
        """Take the oldest staged disc and mark it as encoding.

        Wrapped in an immediate transaction so the select and the update cannot
        be interleaved with another worker doing the same thing.
        """
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY job_id LIMIT 1",
                (JobState.STAGED,),
            ).fetchone()
            if row is None:
                self._connection.execute("ROLLBACK")
                return None

            self._connection.execute(
                """
                UPDATE jobs
                   SET state = ?, attempt_count = attempt_count + 1, updated_at = ?
                 WHERE job_id = ?
                """,
                (JobState.ENCODING, _timestamp(), row["job_id"]),
            )
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

        return self.job_with_id(int(row["job_id"]))

    def mark_encoded(self, job_id: int, encoded_path: Path) -> None:
        """Record a finished transcode that is now waiting for the library drive."""
        self._connection.execute(
            """
            UPDATE jobs
               SET state = ?, encoded_path = ?, updated_at = ?
             WHERE job_id = ?
            """,
            (JobState.ENCODED, str(encoded_path), _timestamp(), job_id),
        )

    def mark_delivered(self, job_id: int) -> None:
        self._set_state(job_id, JobState.DELIVERED)

    def mark_failed(self, job_id: int, failure_reason: str) -> None:
        self._connection.execute(
            """
            UPDATE jobs
               SET state = ?, failure_reason = ?, updated_at = ?
             WHERE job_id = ?
            """,
            (JobState.FAILED, failure_reason, _timestamp(), job_id),
        )

    def return_to_staged(self, job_id: int) -> None:
        """Put a claimed job back so it can be retried after a worker dies."""
        self._set_state(job_id, JobState.STAGED)

    def forget_job(self, job_id: int) -> bool:
        """Drop a job entirely so its disc can be ripped again from scratch."""
        cursor = self._connection.execute(
            "DELETE FROM jobs WHERE job_id = ?", (job_id,)
        )
        return cursor.rowcount > 0

    def _set_state(self, job_id: int, state: JobState) -> None:
        self._connection.execute(
            "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?",
            (state, _timestamp(), job_id),
        )

    def jobs_awaiting_delivery(self) -> list[Job]:
        """Everything transcoded and waiting for the library to become available."""
        return self.jobs_in_state(JobState.ENCODED)

    def jobs_in_state(self, state: JobState) -> list[Job]:
        rows = self._connection.execute(
            "SELECT * FROM jobs WHERE state = ? ORDER BY job_id", (state,)
        ).fetchall()
        return [_job_from_row(row) for row in rows]

    def job_with_id(self, job_id: int) -> Job | None:
        row = self._connection.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return _job_from_row(row)

    def all_jobs(self) -> list[Job]:
        rows = self._connection.execute(
            "SELECT * FROM jobs ORDER BY job_id"
        ).fetchall()
        return [_job_from_row(row) for row in rows]

    def unfinished_jobs(self) -> list[Job]:
        """Everything still on its way through, oldest first."""
        state_placeholders = ", ".join("?" for _state in UNFINISHED_STATES)
        rows = self._connection.execute(
            f"SELECT * FROM jobs WHERE state IN ({state_placeholders}) ORDER BY job_id",
            UNFINISHED_STATES,
        ).fetchall()
        return [_job_from_row(row) for row in rows]

    def jobs_by_state(self) -> dict[JobState, list[Job]]:
        """Every job grouped by where it has got to, oldest first within a state.

        Status reports both the depth of each state and what is sitting in it,
        and those are the same question asked twice, so it asks once.
        """
        grouped_jobs: dict[JobState, list[Job]] = {}
        for job in self.all_jobs():
            grouped_jobs.setdefault(job.state, []).append(job)
        return grouped_jobs

    def staged_bytes(self) -> int:
        """How much disk the unfinished backlog is holding on to."""
        return sum(job.staged_size_bytes() for job in self.unfinished_jobs())


def _job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        job_id=int(row["job_id"]),
        disc_label=row["disc_label"],
        media_kind=row["media_kind"],
        title=row["title"],
        year=row["year"],
        part_number=row["part_number"],
        state=JobState(row["state"]),
        staged_path=Path(row["staged_path"]),
        encoded_path=Path(row["encoded_path"]) if row["encoded_path"] else None,
        disc_fingerprint=row["disc_fingerprint"],
        failure_reason=row["failure_reason"],
        attempt_count=int(row["attempt_count"]),
        disc_format=row["disc_format"],
        season_number=row["season_number"],
        first_episode_number=int(row["first_episode_number"]),
        episode_count=int(row["episode_count"]),
    )


def _directory_size_bytes(directory: Path) -> int:
    if not directory.exists():
        return 0
    if directory.is_file():
        return directory.stat().st_size
    return sum(item.stat().st_size for item in directory.rglob("*") if item.is_file())


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
