"""Turning titles into names that survive a filesystem and an SMB share."""

from __future__ import annotations

import re

# Colons are the awkward one. Wikidata returns plenty of titles with them, and
# they break SMB for Windows clients as well as being forbidden by Plex.
UNSAFE_FILE_CHARACTERS = re.compile(r"[/\\:]+")
REPEATED_WHITESPACE = re.compile(r"\s+")


def safe_file_component(name: str) -> str:
    """Strip the characters that break folder and file names."""
    without_separators = UNSAFE_FILE_CHARACTERS.sub(" ", name)
    return REPEATED_WHITESPACE.sub(" ", without_separators).strip(" .")
