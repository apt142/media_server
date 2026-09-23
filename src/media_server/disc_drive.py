"""Detects what is in the optical drive, and ejects it when the rip is done.

Disc presence is read from ``drutil status`` rather than from mount events.
Mount events are tempting because macOS publishes them through Disk
Arbitration, but encrypted Blu-rays frequently never present a mountable
volume, so the discs that matter most would go unnoticed. ``drutil`` reports
what the hardware sees whether or not the disc can be mounted.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

DRUTIL_COMMAND = "drutil"
NO_MEDIA_MARKER = "no media"
BLURAY_MEDIA_PREFIX = "BD"

DVD_MEDIA_KIND = "dvd"
BLURAY_MEDIA_KIND = "bluray"

# drutil prints several label/value pairs per line, padded into columns. Each
# pattern is anchored to the start of a line so that "Media Type" and "Book
# Type" cannot be mistaken for the plain "Type" field, and each value stops at
# the column padding before the next label.
MEDIA_TYPE_PATTERN = re.compile(r"^\s*Media Type:\s*(.+?)(?:\s{2,}|$)", re.MULTILINE)
TYPE_PATTERN = re.compile(r"^\s*Type:\s*(.+?)(?:\s{2,}|$)", re.MULTILINE)
DEVICE_PATH_PATTERN = re.compile(r"\bName:\s*(\S+)")


@dataclass(frozen=True)
class DiscStatus:
    """What the drive currently reports."""

    is_present: bool
    media_type: str = ""
    device_path: str = ""

    @property
    def is_bluray(self) -> bool:
        return self.media_type.upper().startswith(BLURAY_MEDIA_PREFIX)

    @property
    def media_kind(self) -> str:
        """The word the rip settings are chosen by."""
        if self.is_bluray:
            return BLURAY_MEDIA_KIND
        return DVD_MEDIA_KIND

    def describe(self) -> str:
        if not self.is_present:
            return "No disc in the drive."
        return f"{self.media_type or 'Unknown disc'} in the drive."


DISC_ABSENT = DiscStatus(is_present=False)


def run_drutil(arguments: list[str]) -> str:
    """Run drutil and return its output, or an empty string when it fails."""
    try:
        completed_process = subprocess.run(
            [DRUTIL_COMMAND, *arguments],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed_process.stdout


class DiscDrive:
    """The optical drive, as far as the pipeline needs to know about it.

    The command runner is injected so tests can describe a drive's responses
    without needing a real drive or a real disc.
    """

    def __init__(self, run_command=run_drutil):
        self.run_command = run_command

    def read_status(self) -> DiscStatus:
        """Ask the drive what it is holding right now."""
        return parse_drutil_status(self.run_command(["status"]))

    def is_disc_present(self) -> bool:
        return self.read_status().is_present

    def eject(self) -> None:
        """Send the disc back out. The drive is free for the next one immediately."""
        self.run_command(["eject"])


def parse_drutil_status(status_output: str) -> DiscStatus:
    """Read drive status text into a DiscStatus.

    An empty response means no drive is attached at all, which reads the same
    as an empty drive to everything downstream.
    """
    if not status_output.strip():
        return DISC_ABSENT
    if NO_MEDIA_MARKER in status_output.lower():
        return DISC_ABSENT

    media_type = first_match(MEDIA_TYPE_PATTERN, status_output) or first_match(
        TYPE_PATTERN, status_output
    )
    if not media_type:
        return DISC_ABSENT

    return DiscStatus(
        is_present=True,
        media_type=media_type,
        device_path=first_match(DEVICE_PATH_PATTERN, status_output),
    )


def first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    if match is None:
        return ""
    return match.group(1).strip()
