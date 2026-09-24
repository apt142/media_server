"""Turning titles into names that survive a filesystem and an SMB share."""

from __future__ import annotations

import re

# Colons are the awkward one. Wikidata returns plenty of titles with them, and
# they break SMB for Windows clients as well as being forbidden by Plex.
UNSAFE_FILE_CHARACTERS = re.compile(r"[/\\:]+")
REPEATED_WHITESPACE = re.compile(r"\s+")

# A disc label is raw bytes, and a disc mastered in some other encoding decodes
# to replacement characters. Those are legal in a file name and useless in one,
# so they come out rather than travelling into the library.
UNREADABLE_CHARACTERS = re.compile(r"[\ufffd\x00-\x1f\x7f]+")


def safe_file_component(name: str) -> str:
    """Strip the characters that break folder and file names."""
    readable = UNREADABLE_CHARACTERS.sub("", name)
    without_separators = UNSAFE_FILE_CHARACTERS.sub(" ", readable)
    return REPEATED_WHITESPACE.sub(" ", without_separators).strip(" .")
