"""Reading discs with MakeMKV, and decrypting titles off them.

This is the fast, drive-bound half of the pipeline. It decrypts a title to a
raw MKV and does no re-encoding at all, so the disc can be ejected in twenty to
forty minutes rather than the two to three hours a transcode takes.

MakeMKV's robot mode (``-r``) prints one record per line. The three that matter:

    CINFO:<attribute>,<code>,"<value>"          about the disc
    TINFO:<title>,<attribute>,<code>,"<value>"  about one title on the disc
    MSG:<code>,<flags>,<count>,"<message>",...  something it wants to tell you

The MSG lines are the reason a failed disc used to look like an empty one:
missing keys, Java trouble and unreadable sectors are all reported there rather
than through the exit code.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .disc_fingerprint import fingerprint_for_disc

MAKEMKV_COMMAND = Path(
    "/Applications/MakeMKV.app/Contents/MacOS/makemkvcon"
)

# Every title is reported, however short, and the selection rules further down
# decide what is worth keeping. Filtering here would hide extras from the
# classifier, which needs to see the whole shape of the disc.
MINIMUM_LENGTH_ARGUMENT = "--minlength=1"
DISC_ARGUMENT = "disc:0"

BLURAY_MARKER = "blu-ray"

DISC_RECORD_PATTERN = re.compile(r'^CINFO:(\d+),\d+,"(.*)"\s*$')
TITLE_RECORD_PATTERN = re.compile(r'^TINFO:(\d+),(\d+),\d+,"(.*)"\s*$')
MESSAGE_PATTERN = re.compile(r'^MSG:\d+,\d+,\d+,"(.*?)",')

# CINFO attribute numbers.
DISC_TYPE_ATTRIBUTE = 1
DISC_NAME_ATTRIBUTE = 2
DISC_ALTERNATE_NAME_ATTRIBUTE = 30
DISC_VOLUME_ATTRIBUTE = 32

# TINFO attribute numbers.
TITLE_NAME_ATTRIBUTE = 2
TITLE_DURATION_ATTRIBUTE = 9
TITLE_SIZE_ATTRIBUTE = 11
TITLE_SOURCE_ATTRIBUTE = 16
TITLE_FILE_NAME_ATTRIBUTE = 27

MAIN_FEATURE_MARKER = "mainfeature"

BLURAY_MEDIA_KIND = "bluray"
DVD_MEDIA_KIND = "dvd"


@dataclass(frozen=True)
class RipResult:
    """What decrypting one title produced, and what MakeMKV said about it."""

    ripped_file: Path | None = None
    messages: tuple[str, ...] = ()

    @property
    def is_ripped(self) -> bool:
        return self.ripped_file is not None


@dataclass(frozen=True)
class DiscTitle:
    """One title on the disc: a film, an episode, a trailer, a menu loop."""

    title_id: int
    length_seconds: int
    duration: str = ""
    size_bytes: int = 0
    source: str = ""
    name: str = ""
    is_main_feature: bool = False

    def describe(self) -> str:
        """A one line summary, skipping the fields discs often leave blank."""
        described_parts = [f"title {self.title_id}", self.duration or "unknown length"]
        if self.size_bytes:
            described_parts.append(f"{self.size_bytes / 1024**3:.1f} GB")
        if self.source:
            described_parts.append(self.source)
        if self.is_main_feature:
            described_parts.append("main feature")
        if self.name:
            described_parts.append(self.name)
        return ", ".join(described_parts)


@dataclass(frozen=True)
class DiscScan:
    """Everything one pass of ``makemkvcon info`` told us about the disc."""

    disc_label: str = ""
    media_type: str = ""
    titles: list[DiscTitle] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    @property
    def is_bluray(self) -> bool:
        return BLURAY_MARKER in self.media_type.lower()

    @property
    def media_kind(self) -> str:
        if self.is_bluray:
            return BLURAY_MEDIA_KIND
        return DVD_MEDIA_KIND

    @property
    def has_titles(self) -> bool:
        return bool(self.titles)

    def fingerprint(self) -> str:
        """The identity used to recognise this disc if it is inserted again."""
        return fingerprint_for_disc(
            self.disc_label, [title.length_seconds for title in self.titles]
        )

    def feature_title(self) -> DiscTitle | None:
        """The film itself.

        MakeMKV flags the real feature when its playlist detection works, and
        that beats any guess. Otherwise the longest title wins. Length beats
        file size here: a commentary or bonus-angle cut runs as long as the
        film but carries more audio, so "biggest file" picks the wrong one.
        """
        if not self.titles:
            return None

        flagged_titles = [title for title in self.titles if title.is_main_feature]
        candidate_titles = flagged_titles or self.titles
        return max(candidate_titles, key=lambda title: title.length_seconds)

    def feature_titles(self, minimum_length_seconds: int) -> list[DiscTitle]:
        """Every title long enough to be a film, in the order they sit on the disc.

        A double feature puts two films on one disc, and taking only the
        longest loses the other silently. Taking both sometimes picks up a
        commentary cut as well, which is a cheap file to delete next to a film
        that never got ripped at all.
        """
        return [
            title
            for title in self.titles
            if title.length_seconds >= minimum_length_seconds
        ]

    def titles_lasting_between(
        self, shortest_seconds: int, longest_seconds: int
    ) -> list[DiscTitle]:
        """Titles inside a length window, in the order they sit on the disc."""
        return [
            title
            for title in self.titles
            if shortest_seconds <= title.length_seconds <= longest_seconds
        ]

    def title_with_id(self, title_id: int) -> DiscTitle | None:
        for title in self.titles:
            if title.title_id == title_id:
                return title
        return None

    def total_size_bytes(self, titles: list[DiscTitle]) -> int:
        return sum(title.size_bytes for title in titles)


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str

    @property
    def is_successful(self) -> bool:
        return self.exit_code == 0


def run_makemkv(arguments: list[str]) -> CommandResult:
    """Run makemkvcon and hand back what it said.

    A failing exit code is not treated as an exception, because MakeMKV reports
    most real problems through its MSG lines while still exiting zero, and
    sometimes exits non-zero having produced a perfectly good file.
    """
    try:
        completed_process = subprocess.run(
            [str(MAKEMKV_COMMAND), *arguments],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except OSError as command_error:
        return CommandResult(exit_code=127, output=str(command_error))
    return CommandResult(
        exit_code=completed_process.returncode, output=completed_process.stdout
    )


class MakeMkv:
    """Scans discs and decrypts titles off them.

    The command runner is injected so tests can describe a disc's responses
    without a drive, a disc, or MakeMKV being installed.
    """

    def __init__(self, run_command=run_makemkv, makemkv_command: Path = MAKEMKV_COMMAND):
        self.run_command = run_command
        self.makemkv_command = makemkv_command

    def is_installed(self) -> bool:
        return self.makemkv_command.exists()

    def scan_disc(self) -> DiscScan:
        """Read the disc's label, type and titles.

        Scanning a Blu-ray is slow, so callers are expected to hold on to the
        result rather than asking twice.
        """
        result = self.run_command(["-r", MINIMUM_LENGTH_ARGUMENT, "info", DISC_ARGUMENT])
        return parse_disc_scan(result.output)

    def rip_title(self, title_id: int, destination: Path) -> RipResult:
        """Decrypt one title into a folder, and say how it went.

        A failure is reported rather than raised because a TV disc should carry
        on to the next episode when one title refuses to decrypt. MakeMKV's own
        messages come back with the result: the exit code says almost nothing,
        and the reason a title would not decrypt is only ever in the MSG lines.
        """
        destination.mkdir(parents=True, exist_ok=True)
        result = self.run_command(
            [
                MINIMUM_LENGTH_ARGUMENT,
                "-r",
                "--decrypt",
                "mkv",
                DISC_ARGUMENT,
                str(title_id),
                str(destination),
            ]
        )
        return RipResult(
            ripped_file=largest_mkv_in(destination),
            messages=tuple(collapse_repeated_messages(parse_messages(result.output))),
        )


def largest_mkv_in(directory: Path) -> Path | None:
    """The biggest .mkv under a folder.

    MakeMKV sometimes leaves small stray files beside the real one, so size is
    what separates the title from the debris.
    """
    mkv_files = [item for item in directory.rglob("*.mkv") if item.is_file()]
    if not mkv_files:
        return None
    return max(mkv_files, key=lambda mkv_file: mkv_file.stat().st_size)


def parse_disc_scan(scan_output: str) -> DiscScan:
    """Turn robot-mode output into a DiscScan."""
    disc_values = parse_disc_values(scan_output)
    return DiscScan(
        disc_label=choose_disc_label(disc_values),
        media_type=disc_values.get(DISC_TYPE_ATTRIBUTE, ""),
        titles=parse_titles(scan_output),
        messages=collapse_repeated_messages(parse_messages(scan_output)),
    )


def parse_disc_values(scan_output: str) -> dict[int, str]:
    disc_values: dict[int, str] = {}
    for line in scan_output.splitlines():
        match = DISC_RECORD_PATTERN.match(line)
        if match:
            disc_values[int(match.group(1))] = match.group(2)
    return disc_values


def choose_disc_label(disc_values: dict[int, str]) -> str:
    """The disc's name, falling back through the fields discs actually fill in."""
    for attribute in (
        DISC_NAME_ATTRIBUTE,
        DISC_ALTERNATE_NAME_ATTRIBUTE,
        DISC_VOLUME_ATTRIBUTE,
    ):
        label = disc_values.get(attribute, "").strip()
        if label:
            return label
    return ""


def parse_titles(scan_output: str) -> list[DiscTitle]:
    """Collect the TINFO records into one DiscTitle per title, in disc order."""
    title_values: dict[int, dict[int, str]] = {}
    main_feature_title_ids: set[int] = set()

    for line in scan_output.splitlines():
        match = TITLE_RECORD_PATTERN.match(line)
        if not match:
            continue

        title_id = int(match.group(1))
        attribute = int(match.group(2))
        value = match.group(3)
        title_values.setdefault(title_id, {})[attribute] = value
        if MAIN_FEATURE_MARKER in value.replace("_", "").lower():
            main_feature_title_ids.add(title_id)

    return [
        build_title(title_id, title_values[title_id], title_id in main_feature_title_ids)
        for title_id in sorted(title_values)
    ]


def build_title(
    title_id: int, values: dict[int, str], is_main_feature: bool
) -> DiscTitle:
    duration = values.get(TITLE_DURATION_ATTRIBUTE, "")
    return DiscTitle(
        title_id=title_id,
        length_seconds=duration_to_seconds(duration),
        duration=duration,
        size_bytes=whole_number(values.get(TITLE_SIZE_ATTRIBUTE, "")),
        source=values.get(TITLE_SOURCE_ATTRIBUTE, ""),
        name=values.get(TITLE_FILE_NAME_ATTRIBUTE, "")
        or values.get(TITLE_NAME_ATTRIBUTE, ""),
        is_main_feature=is_main_feature,
    )


def parse_messages(scan_output: str) -> list[str]:
    """The human-readable notes MakeMKV printed, including its failures."""
    messages = []
    for line in scan_output.splitlines():
        match = MESSAGE_PATTERN.match(line)
        if match:
            messages.append(match.group(1))
    return messages


def collapse_repeated_messages(messages: list[str]) -> list[str]:
    """Fold something MakeMKV said many times into one line and a count.

    A disc with a bad patch reports the same read error once per retry, which
    runs to dozens of identical lines and buries the ones that say what
    actually happened. Unattended, it buries them in a log nobody rereads.

    Counting every occurrence rather than only consecutive runs matters here:
    a failing read alternates between two errors, so consecutive folding would
    still leave a screen of them.
    """
    counts: dict[str, int] = {}
    for message in messages:
        counts[message] = counts.get(message, 0) + 1

    return [
        message if count == 1 else f"{message} (\u00d7{count})"
        for message, count in counts.items()
    ]


def duration_to_seconds(duration: str) -> int:
    """Read MakeMKV's ``h:mm:ss`` (or ``mm:ss``) into a count of seconds."""
    if not duration:
        return 0

    seconds = 0
    for piece in duration.split(":"):
        if not piece.strip().isdigit():
            return 0
        seconds = seconds * 60 + int(piece)
    return seconds


def whole_number(value: str) -> int:
    """MakeMKV leaves numeric fields blank rather than zero on some discs."""
    if not value.isdigit():
        return 0
    return int(value)
