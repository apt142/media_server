"""A stand-in for the makemkvcon command, so tests need no drive and no disc."""

from __future__ import annotations

from pathlib import Path

from media_server.makemkv import CommandResult

RIP_SUBCOMMAND = "mkv"
INFO_SUBCOMMAND = "info"

# What a decrypted title weighs in the tests. Large enough to be the obvious
# winner next to any stray file, small enough to be free to write.
RIPPED_FILE_BYTES = 4096


class FakeMakeMkvCommand:
    """Answers scans from a fixture and writes plausible files for rips.

    Titles listed in ``unreadable_title_ids`` produce nothing, which is how a
    disc with one bad episode behaves in practice.
    """

    def __init__(self, scan_output: str = "", unreadable_title_ids: tuple[int, ...] = ()):
        self.scan_output = scan_output
        self.unreadable_title_ids = unreadable_title_ids
        self.scanned_count = 0
        self.ripped_title_ids: list[int] = []

    def __call__(self, arguments: list[str]) -> CommandResult:
        if INFO_SUBCOMMAND in arguments:
            self.scanned_count += 1
            return CommandResult(exit_code=0, output=self.scan_output)
        if RIP_SUBCOMMAND in arguments:
            return self._rip(arguments)
        return CommandResult(exit_code=0, output="")

    def _rip(self, arguments: list[str]) -> CommandResult:
        title_id = int(arguments[-2])
        destination = Path(arguments[-1])
        self.ripped_title_ids.append(title_id)

        if title_id in self.unreadable_title_ids:
            return CommandResult(exit_code=1, output="")

        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"title_t{title_id:02d}.mkv").write_bytes(
            b"m" * RIPPED_FILE_BYTES
        )
        return CommandResult(exit_code=0, output="")
