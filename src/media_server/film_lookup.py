"""Ask Wikidata which film a disc holds, so the library can carry a year.

A disc label is all the pipeline otherwise knows, and "A_NEW_HOPE" tells Plex
nothing it can match on. A year turns the folder into "A New Hope (1977)",
which matches on the first try.

Wikidata is the source because it needs no API key and knows films from every
country rather than whatever one storefront happens to sell. The same choice,
and the same reasoning, as ``scripts/movie_lookup.py`` in the older pipeline.

Nothing here is allowed to fail a rip. A disc that is already decrypted and
staged must not be lost because a web service was slow, so every failure path
ends in "no year" rather than an exception.
"""

from __future__ import annotations

import difflib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Wikidata asks for a descriptive User-Agent and throttles anything without one.
USER_AGENT = "media-server-ripper/1.0 (personal DVD library tool)"

# A rip takes hours, so waiting a few seconds is cheap. Waiting a minute is
# not: the disc is already staged and the only thing outstanding is a nicety.
REQUEST_TIMEOUT_SECONDS = 10

INSTANCE_OF = "P31"
PUBLICATION_DATE = "P577"

# "instance of" values that mean a single watchable film. Film *series*
# (Q24856) is deliberately absent: "The Matrix series" is not a thing to rip.
FILM_TYPES = frozenset(
    {
        "Q11424",  # film
        "Q24869",  # feature film
        "Q202866",  # animated film
        "Q20650540",  # anime film
        "Q93204",  # documentary film
        "Q506240",  # television film
        "Q226730",  # silent film
        "Q1261214",  # short film
    }
)

# Wikidata has a long tail of film subtypes, and missing one loses the film
# silently: Spirited Away is an "anime film", which no reasonable list would
# have guessed. The one-line description is a cheap second opinion and it
# already comes back with the search results.
DESCRIBES_A_FILM = re.compile(r"\bfilm\b", re.I)

DESCRIBES_SOMETHING_ELSE = re.compile(
    r"\b(episode|album|song|single|soundtrack|video game|"
    r"television series|tv series|book|novel|play|musical|"
    r"films|series|franchise|film festival|film studio|filmmaker|"
    r"film director|production company|band|character)\b",
    re.I,
)

# Close enough to accept on resemblance alone. Most discs match their film
# exactly and score 1.0; this leaves room for a stray subtitle or an accent.
CLOSE_ENOUGH_SIMILARITY = 0.6

# The earliest year a film could carry. Anything before this is a data error
# rather than a film, and a folder called "(0012)" is worse than no year.
FIRST_FILM_YEAR = 1888


@dataclass(frozen=True)
class FilmMatch:
    """One film Wikidata believes the disc holds."""

    name: str
    year: str
    similarity: float


def comparable(text: str) -> str:
    """Lower case, punctuation gone, no leading article, for comparing titles."""
    text = re.sub(r"[^\w\s]", " ", text.lower())
    text = re.sub(r"^(the|a|an)\s+", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def title_similarity(search_term: str, name: str) -> float:
    return difflib.SequenceMatcher(None, comparable(search_term), comparable(name)).ratio()


def is_year_plausible(year: str) -> bool:
    """Films run from 1888 to a little ahead of now, and nothing else counts."""
    if not year.isdigit():
        return False
    return FIRST_FILM_YEAR <= int(year) <= date.today().year + 2


def is_match_trustworthy(search_term: str, match: FilmMatch) -> bool:
    """Whether this match is worth writing into a folder name.

    Resemblance alone is not enough, because the disc often says less than
    Wikidata does. "A NEW HOPE" against "Star Wars: Episode IV - A New Hope"
    scores 0.41, which no sensible threshold would accept, and it is
    nonetheless exactly right.

    What settles it is that the disc's words appear inside the film's title.
    Containment is the strong signal; the similarity score is there for the
    other direction, where the disc says more than the film does.
    """
    if not is_year_plausible(match.year):
        return False
    if match.similarity >= CLOSE_ENOUGH_SIMILARITY:
        return True

    disc_words = comparable(search_term)
    return bool(disc_words) and disc_words in comparable(match.name)


def fetch_wikidata(parameters: dict[str, str]) -> dict:
    """One call to Wikidata, or an empty answer if it could not be made."""
    url = f"{WIKIDATA_API}?{urllib.parse.urlencode(parameters)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.load(response)


class FilmLookup:
    """Finds the film a disc label names, and the year it came out.

    The network call is handed in so tests can answer it without reaching the
    internet, and so a caller with no network can pass something that says so.
    """

    def __init__(self, fetch=fetch_wikidata):
        self.fetch = fetch

    def best_match(self, search_term: str) -> FilmMatch | None:
        """The film this label most likely names, or None if nothing fits.

        None covers every kind of disappointment equally: no match, a match
        with no release date, a service that would not answer. The caller does
        the same thing in all of those cases, which is to carry on without a
        year.
        """
        if not search_term.strip():
            return None

        try:
            matches = self._films_matching(search_term)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, OSError):
            # Named rather than caught blind, but the list is wide on purpose:
            # a staged rip is not worth losing to any of them.
            return None

        for match in matches:
            if is_match_trustworthy(search_term, match):
                return match
        return None

    def _films_matching(self, search_term: str) -> list[FilmMatch]:
        """Films Wikidata offers for this label, closest title first.

        Wikidata ranks for general knowledge, so it will happily put a stage
        adaptation above the film it was adapted from. Sorting by how closely
        the title matches the disc puts the film back on top.
        """
        hits = self._search(search_term)
        claims_by_id = self._claims_for([entity_id for entity_id, _, _ in hits])

        matches = []
        for position, (entity_id, name, description) in enumerate(hits):
            claims = claims_by_id.get(entity_id, {})
            if not name or not is_film(claims, description):
                continue
            matches.append(
                (
                    position,
                    FilmMatch(
                        name=name,
                        year=earliest_release_year(claims),
                        similarity=title_similarity(search_term, name),
                    ),
                )
            )

        matches.sort(key=lambda found: (-found[1].similarity, found[0]))
        return [match for _, match in matches]

    def _search(self, search_term: str) -> list[tuple[str, str, str]]:
        payload = self.fetch(
            {
                "action": "wbsearchentities",
                "search": search_term,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "format": "json",
                "limit": "15",
            }
        )
        return [
            (hit["id"], hit.get("label", ""), hit.get("description", ""))
            for hit in payload.get("search", [])
        ]

    def _claims_for(self, entity_ids: list[str]) -> dict[str, dict]:
        """Claims only. Labels come from the search results instead.

        wbgetentities applies no language fallback, so an item whose English
        label is inherited rather than stored comes back unlabelled. The real
        Toy Story is one of those, which is how "Toy Story 4" used to win.
        """
        if not entity_ids:
            return {}
        payload = self.fetch(
            {
                "action": "wbgetentities",
                "ids": "|".join(entity_ids),
                "props": "claims",
                "format": "json",
            }
        )
        return {
            entity_id: entity.get("claims", {})
            for entity_id, entity in payload.get("entities", {}).items()
        }


def claim_values(claims: dict, property_id: str):
    for claim in claims.get(property_id, []):
        snak = claim.get("mainsnak", {})
        if snak.get("datavalue"):
            yield snak["datavalue"]["value"]


def earliest_release_year(claims: dict) -> str:
    """The first publication date, since re-releases carry later ones."""
    years = []
    for value in claim_values(claims, PUBLICATION_DATE):
        time_value = value.get("time", "") if isinstance(value, dict) else ""
        match = re.match(r"^[+-](\d{4})", time_value)
        if match:
            years.append(match.group(1))
    return min(years) if years else ""


def is_film(claims: dict, description: str = "") -> bool:
    if DESCRIBES_SOMETHING_ELSE.search(description):
        return False

    types = {
        value.get("id")
        for value in claim_values(claims, INSTANCE_OF)
        if isinstance(value, dict)
    }
    if types & FILM_TYPES:
        return True

    return bool(DESCRIBES_A_FILM.search(description))
