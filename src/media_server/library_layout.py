"""Where a finished file belongs inside the library.

Paths are built relative to the library root rather than absolute, so the
encoder can write the whole shape into staging and delivery can mirror it onto
the external drive without either of them knowing about the other.

The names follow Plex's own conventions, because Plex matches on them:

    Movies/The Matrix (1999)/The Matrix (1999).mp4
    Movies/Fellowship (2001)/Fellowship (2001) - part2.mp4
    TV/Firefly/Season 01/Firefly - s01e03.mp4
"""

from __future__ import annotations

from pathlib import Path

from .file_names import safe_file_component
from .job_catalog import Job

MOVIES_FOLDER = "Movies"
TV_FOLDER = "TV"

DEFAULT_SEASON_NUMBER = 1
UNTITLED_NAME = "Untitled"


def destinations_for(job: Job, source_count: int, extension: str) -> list[Path]:
    """One library-relative path per file the encoder is about to write."""
    if job.is_show:
        return [
            episode_destination(job, job.first_episode_number + offset, extension)
            for offset in range(source_count)
        ]
    return film_destinations(job, source_count, extension)


def film_destinations(job: Job, source_count: int, extension: str) -> list[Path]:
    """A film is normally one file, but a disc can hold more than one film.

    A double feature is the common case, and the two are not parts of one
    film. Each gets its own folder so Plex treats them as the separate movies
    they are rather than stacking them into a four hour Iron Man.

    A film genuinely split across discs is a different shape: one title per
    disc, with the part number coming from the disc label rather than from how
    many files came off one disc.
    """
    if source_count <= 1:
        return [film_destination(job, extension)]
    return [
        separate_feature_destination(job, extension, feature_number=offset + 1)
        for offset in range(source_count)
    ]


def separate_feature_destination(
    job: Job, extension: str, feature_number: int
) -> Path:
    """One of several films found on the same disc, until it is given a name.

    Numbered in disc order, which is what makes them tellable apart: the
    person renaming them can play a few seconds of each and knows which is
    which. Both the folder and the file carry the number so a rename of the
    folder alone cannot leave Plex matching on a stale name.
    """
    folder_name = f"{film_folder_name(job)} - feature{feature_number}"
    return Path(MOVIES_FOLDER) / folder_name / f"{folder_name}.{extension}"


def film_destination(
    job: Job, extension: str, part_number: int | None = None
) -> Path:
    folder_name = film_folder_name(job)
    part = part_number or job.part_number

    file_stem = folder_name
    if part is not None:
        file_stem = f"{folder_name} - part{part}"
    return Path(MOVIES_FOLDER) / folder_name / f"{file_stem}.{extension}"


def film_folder_name(job: Job) -> str:
    """"Title (Year)", or just the title when the year is not known yet."""
    title = safe_file_component(job.title) or UNTITLED_NAME
    if not job.year:
        return title
    return f"{title} ({job.year})"


def episode_destination(job: Job, episode_number: int, extension: str) -> Path:
    show_name = safe_file_component(job.title) or UNTITLED_NAME
    season_number = job.season_number or DEFAULT_SEASON_NUMBER
    episode_code = f"s{season_number:02d}e{episode_number:02d}"

    return (
        Path(TV_FOLDER)
        / show_name
        / f"Season {season_number:02d}"
        / f"{show_name} - {episode_code}.{extension}"
    )
