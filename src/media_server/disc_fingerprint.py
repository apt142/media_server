"""A stable identity for a disc, so the same one is never ripped twice.

Disc labels alone are not enough to tell discs apart. Plenty of discs are
stamped with something generic like ``DVD_VIDEO``, and a season box set often
uses the same label on every disc in the box. Pairing the label with the shape
of the titles on the disc separates those cases: two different discs almost
never carry the same set of title lengths, while the same disc scanned twice
always produces the same ones.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

FINGERPRINT_LENGTH = 16
NON_IDENTIFYING_CHARACTERS = re.compile(r"[^A-Z0-9]+")


def fingerprint_for_disc(
    disc_label: str, title_lengths_seconds: Iterable[int]
) -> str:
    """Build the fingerprint used to recognise a disc that has been seen before."""
    identifying_parts = [normalize_disc_label(disc_label)]
    identifying_parts.extend(
        str(title_length) for title_length in sorted(title_lengths_seconds)
    )
    identity = "|".join(identifying_parts)
    return hashlib.sha256(identity.encode()).hexdigest()[:FINGERPRINT_LENGTH]


def normalize_disc_label(disc_label: str) -> str:
    """Flatten punctuation and case so trivial label differences do not matter."""
    upper_cased_label = disc_label.upper()
    return NON_IDENTIFYING_CHARACTERS.sub("_", upper_cased_label).strip("_")
