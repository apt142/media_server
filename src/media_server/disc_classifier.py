"""Decides whether a disc holds a film or episodes of a show.

The tell is shape, not length. A film disc has one dominant title with assorted
shorter extras around it. An episode disc has several titles of near-identical
length, because episodes of a season run to the same slot.

Signals are weighed against each other rather than applied in order, since any
one of them can be wrong on its own. Firefly's first disc opens with a
feature-length pilot that looks exactly like a film sitting next to two
ordinary episodes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .job_catalog import FILM_MEDIA_KIND, SHOW_MEDIA_KIND
from .makemkv import DiscScan, DiscTitle

# How far apart the two scores must be before the answer is worth trusting
# without a person confirming it.
CONFIDENT_SCORE_MARGIN = 2

LABEL_SEPARATORS = re.compile(r"[_\-.]+")

SEASON_LABEL_PATTERNS = (
    re.compile(r"\bs(?:eason)?\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\bseries\s*(\d{1,2})\b", re.IGNORECASE),
)
DISC_NUMBER_LABEL_PATTERN = re.compile(
    r"\b(?:d|disc|disk)\s*(\d{1,2})\b", re.IGNORECASE
)

DISC_BOOKKEEPING_WORDS = re.compile(
    r"\b(disc|disk|dvd|blu\s?ray|bd|video|ts|season|series|vol(ume)?)\b",
    re.IGNORECASE,
)
# Labels glue the number straight onto the word, as in DISC1, so a plain word
# boundary never fires between the two.
NUMBERED_DISC_CODE = re.compile(
    r"\b(?:disc|disk|season|series|vol(?:ume)?)\s*\d{1,2}\b", re.IGNORECASE
)
SEASON_OR_DISC_CODE = re.compile(r"\b[sd]\d{1,2}\b", re.IGNORECASE)
BARE_NUMBER = re.compile(r"\b\d{1,2}\b")

# Discs advertise their edition in the label, which is never part of the title
# anyone would search for: FELLOWSHIP_EE_D2 is the Fellowship of the Ring.
EDITION_MARKERS = re.compile(
    r"\b(ee|se|extended|special|edition|collectors?|anniversary|"
    r"directors?|cut|unrated|uncut|remastered|theatrical|feature)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DiscVerdict:
    """What the disc looks like, and how sure that is."""

    media_kind: str
    is_confident: bool
    reason: str

    @property
    def is_show(self) -> bool:
        return self.media_kind == SHOW_MEDIA_KIND

    def describe(self) -> str:
        if self.is_show:
            return f"This looks like a TV disc: {self.reason}."
        return f"This looks like a film: {self.reason}."


class DiscClassifier:
    """Weighs the evidence on one disc and returns a verdict."""

    SHORTEST_EPISODE_SECONDS = 900

    # 65 minutes. Episodic television runs to roughly 22, 44 or 60 minute
    # slots, so anything longer is far more likely to be a feature. A ceiling
    # of 75 minutes read a double-feature DVD -- two 72 minute B-movies, a
    # common way old films are reissued -- as a pair of episodes.
    #
    # Feature-length pilots do exist above this line, but they sit next to
    # ordinary episodes that still count, so those discs are unaffected.
    LONGEST_EPISODE_SECONDS = 3900

    SIMILAR_LENGTH_TOLERANCE = 0.12

    def __init__(self, disc_scan: DiscScan):
        self.disc_scan = disc_scan

    def verdict(self) -> DiscVerdict:
        show_score, show_reason = self._show_evidence()
        film_score, film_reason = self._film_evidence()

        if show_score > film_score:
            return DiscVerdict(
                media_kind=SHOW_MEDIA_KIND,
                is_confident=show_score - film_score >= CONFIDENT_SCORE_MARGIN,
                reason=show_reason,
            )
        if film_score > show_score:
            return DiscVerdict(
                media_kind=FILM_MEDIA_KIND,
                is_confident=film_score - show_score >= CONFIDENT_SCORE_MARGIN,
                reason=film_reason,
            )
        return DiscVerdict(
            media_kind=FILM_MEDIA_KIND,
            is_confident=False,
            reason="nothing on the disc clearly says film or show",
        )

    def episode_length_titles(self) -> list[DiscTitle]:
        return self.disc_scan.titles_lasting_between(
            self.SHORTEST_EPISODE_SECONDS, self.LONGEST_EPISODE_SECONDS
        )

    def _show_evidence(self) -> tuple[int, str]:
        episode_titles = self.episode_length_titles()
        score = 0
        reasons = []

        if len(episode_titles) >= 3:
            score += 3
            reasons.append(f"{len(episode_titles)} titles run at episode length")
        elif self._has_matching_pair(episode_titles):
            score += 2
            reasons.append("two titles run to almost exactly the same length")

        disc_label = self.disc_scan.disc_label
        if season_from_disc_label(disc_label):
            score += 2
            reasons.append(f'the label "{disc_label}" carries a season number')
        elif disc_number_from_label(disc_label):
            # Weak on its own. Plenty of films ship as KNIVES_OUT_FEATURE_DISC1,
            # so this should colour the answer without driving it.
            score += 1
            reasons.append(f'the label "{disc_label}" carries a disc number')

        return score, ", and ".join(reasons)

    def _has_matching_pair(self, titles: list[DiscTitle]) -> bool:
        """Two titles within a few percent of each other look like episodes.

        Extras on a film disc vary in length; episodes of a season do not.
        """
        lengths = sorted(title.length_seconds for title in titles)
        for shorter, longer in zip(lengths, lengths[1:]):
            if longer and abs(longer - shorter) / longer <= self.SIMILAR_LENGTH_TOLERANCE:
                return True
        return False

    def _film_evidence(self) -> tuple[int, str]:
        feature_titles = [
            title
            for title in self.disc_scan.titles
            if title.length_seconds > self.LONGEST_EPISODE_SECONDS
        ]
        if not feature_titles:
            return 0, ""

        longest_seconds = max(title.length_seconds for title in feature_titles)
        score = 1
        reasons = [f"the longest title runs {longest_seconds // 60} minutes"]

        if len(self.episode_length_titles()) < 2:
            score += 2
            reasons.append("nothing else on the disc is episode length")

        return score, ", and ".join(reasons)


def spaced_label(disc_label: str) -> str:
    """Underscores are word characters, so word boundaries never fire in FIREFLY_D1."""
    return LABEL_SEPARATORS.sub(" ", disc_label or "")


def season_from_disc_label(disc_label: str) -> int | None:
    """Pull a season number out of a label, or None."""
    spaced = spaced_label(disc_label)
    for pattern in SEASON_LABEL_PATTERNS:
        match = pattern.search(spaced)
        if match:
            return int(match.group(1))
    return None


def disc_number_from_label(disc_label: str) -> int | None:
    """Pull a disc number out of a label, or None.

    This is what tells disc two of an extended edition from disc one, so a
    split film can be joined back together later.
    """
    spaced = spaced_label(disc_label)
    if season_from_disc_label(spaced):
        # A season code and a disc code look alike. Take the disc number only
        # from what is left once the season has been removed, so THE_WIRE_S02
        # is not read as disc 2.
        spaced = SEASON_LABEL_PATTERNS[0].sub(" ", spaced)

    match = DISC_NUMBER_LABEL_PATTERN.search(spaced)
    if match:
        return int(match.group(1))
    return None


def clean_disc_label(disc_label: str) -> str:
    """Turn a volume label into something worth searching for.

    Labels are shouty and full of disc bookkeeping: FIREFLY_D1, THE_WIRE_S02_D3.
    Strip the bookkeeping and hand back the words.
    """
    words = spaced_label(disc_label)
    words = NUMBERED_DISC_CODE.sub(" ", words)
    words = DISC_BOOKKEEPING_WORDS.sub(" ", words)
    words = EDITION_MARKERS.sub(" ", words)
    words = SEASON_OR_DISC_CODE.sub(" ", words)
    words = BARE_NUMBER.sub(" ", words)
    return re.sub(r"\s+", " ", words).strip().title()
