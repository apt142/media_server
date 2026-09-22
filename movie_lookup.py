#!/usr/bin/env python3
"""Work out which film is on a disc, from its volume label.

Wikidata is the source. It needs no API key, which keeps setup to "run the
script", and it knows about films from every country rather than whatever one
storefront happens to sell.

The obvious choice would have been Apple's iTunes Search API, which this
replaced. That endpoint still answers, and still returns HTTP 200, but as of
2026 it reports zero results for media=movie and media=tvShow while continuing
to serve music. A lookup that silently finds nothing is worse than no lookup, so
it had to go.
"""

import argparse
import difflib
import json
import re
import sys
import time
import urllib.parse
import urllib.request

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Wikidata asks for a descriptive User-Agent and throttles anything without one.
USER_AGENT = "media-server-ripper/1.0 (personal DVD library tool)"

# "instance of" values that mean a single watchable film. Film *series* (Q24856)
# is deliberately absent: "The Matrix series" is not a thing you can rip.
FILM_TYPES = {
    "Q11424",     # film
    "Q24869",     # feature film
    "Q202866",    # animated film
    "Q20650540",  # anime film
    "Q93204",     # documentary film
    "Q506240",    # television film
    "Q226730",    # silent film
    "Q1261214",   # short film
}

# Wikidata has a long tail of film subtypes, and missing one loses the film
# silently: Spirited Away is an "anime film", which no reasonable whitelist
# would have guessed. The one-line description is a cheap second opinion, and it
# already comes back with the search results.
# Singular on purpose. A single film is "a 1999 science fiction film"; a
# collection is "1999-present films directed by the Wachowskis", which is how
# "The Matrix series" used to pass for a film.
DESCRIBES_A_FILM = re.compile(r"\bfilm\b", re.I)

DESCRIBES_SOMETHING_ELSE = re.compile(
    r"\b(episode|album|song|single|soundtrack|video game|"
    r"television series|tv series|book|novel|play|musical|"
    r"films|series|franchise|film festival|film studio|filmmaker|"
    r"film director|production company|band|character)\b",
    re.I,
)

INSTANCE_OF = "P31"
PUBLICATION_DATE = "P577"


class WikidataClient:
    def __init__(self, request_timeout=20, retry_count=4):
        self.request_timeout = request_timeout
        self.retry_count = retry_count

    def _get(self, parameters):
        url = WIKIDATA_API + "?" + urllib.parse.urlencode(parameters)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        # Wikidata answers 429 when it is busy, and says how long to wait. Being
        # patient here is the difference between a named film and a folder
        # called "KNIVES_OUT", and a rip takes hours anyway.
        last_attempt = self.retry_count - 1
        for attempt in range(self.retry_count):
            try:
                with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code != 429 or attempt == last_attempt:
                    raise
                retry_after = error.headers.get("Retry-After")
                time.sleep(int(retry_after) if retry_after else 5 * (attempt + 1))
        return {}

    def search(self, term, limit=15):
        """Matching entities as (id, label, description), in Wikidata's order."""
        payload = self._get({
            "action": "wbsearchentities", "search": term, "language": "en",
            "uselang": "en", "type": "item", "format": "json", "limit": str(limit),
        })
        return [
            (hit["id"], hit.get("label", ""), hit.get("description", ""))
            for hit in payload.get("search", [])
        ]

    def claims_for(self, entity_ids):
        """Claims only. Labels come from the search results instead.

        wbgetentities does not apply language fallback, so items whose English
        label is inherited rather than stored come back unlabelled: the actual
        Toy Story is one, which is how "Toy Story 4" used to win. The search
        endpoint does apply fallback, so its labels are the ones to trust.
        """
        if not entity_ids:
            return {}
        payload = self._get({
            "action": "wbgetentities", "ids": "|".join(entity_ids),
            "props": "claims", "format": "json",
        })
        return {
            entity_id: entity.get("claims", {})
            for entity_id, entity in payload.get("entities", {}).items()
        }


def _claim_values(claims, property_id):
    for claim in claims.get(property_id, []):
        snak = claim.get("mainsnak", {})
        if snak.get("datavalue"):
            yield snak["datavalue"]["value"]


def _release_year(claims):
    """Earliest publication date, since re-releases carry later ones."""
    years = []
    for value in _claim_values(claims, PUBLICATION_DATE):
        time_value = value.get("time", "") if isinstance(value, dict) else ""
        match = re.match(r"^[+-](\d{4})", time_value)
        if match:
            years.append(match.group(1))
    return min(years) if years else ""


def is_film(claims, description=""):
    if DESCRIBES_SOMETHING_ELSE.search(description):
        return False

    types = {
        value.get("id") for value in _claim_values(claims, INSTANCE_OF)
        if isinstance(value, dict)
    }
    if types & FILM_TYPES:
        return True

    return bool(DESCRIBES_A_FILM.search(description))


def find_films(term, client=None):
    """Films matching `term`, best match first.

    Wikidata's own search ranking is built for general knowledge lookups, so it
    happily puts a stage adaptation above the film it was adapted from. Ordering
    by how closely the title matches what the disc said fixes that.
    """
    if not term:
        return []

    client = client or WikidataClient()
    hits = client.search(term)
    claims_by_id = client.claims_for([entity_id for entity_id, _, _ in hits])

    films = []
    for position, (entity_id, name, description) in enumerate(hits):
        claims = claims_by_id.get(entity_id, {})
        if not name or not is_film(claims, description):
            continue
        films.append({
            "name": name,
            "year": _release_year(claims),
            "similarity": _title_similarity(term, name),
            "search_rank": position,
        })

    films.sort(key=lambda film: (-film["similarity"], film["search_rank"]))
    return films


def _title_similarity(term, name):
    """How close two titles are, ignoring case, punctuation and a leading article."""
    return difflib.SequenceMatcher(None, _comparable(term), _comparable(name)).ratio()


def _comparable(text):
    text = re.sub(r"[^\w\s]", " ", text.lower())
    text = re.sub(r"^(the|a|an)\s+", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def clean_disc_label(label):
    """Turn a volume label into something worth searching for.

    Disc labels are upper case, underscored, and padded with the studio's
    bookkeeping: KNIVES_OUT_FEATURE_DISC1 wants to become "KNIVES OUT".
    """
    text = re.sub(r"[_.\-]+", " ", label)
    text = re.sub(r"\s+", " ", text).strip()

    # Bookkeeping that studios append. Repeated because labels stack several of
    # them: "..._16X9_FEATURE_DISC1".
    junk = (
        r"disc\s*\d+|d\d+|side\s*[ab]|s\d+|season\s*\d+|"
        r"feature|main|movie|film|widescreen|fullscreen|ws|fs|"
        r"16x9|4x3|dvd\s*video|dvdvideo|dvd|bluray|blu\s*ray|bd\d*|ntsc|pal|"
        r"disc|vol\s*\d+"
    )
    for _ in range(4):
        shortened = re.sub(rf"\s+({junk})\s*$", "", text, flags=re.I)
        if shortened == text:
            break
        text = shortened

    return text.strip(" -")


def print_tsv(fields):
    print("\t".join(str(field) for field in fields))


def run_search(arguments):
    films = find_films(arguments.query)
    for film in films[: arguments.limit]:
        print_tsv([film["name"], film["year"]])


def run_label(arguments):
    print(clean_disc_label(arguments.label))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search", help="find films matching a title")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=8)
    search.set_defaults(handler=run_search)

    label = commands.add_parser("label", help="turn a disc label into a search term")
    label.add_argument("--label", required=True)
    label.set_defaults(handler=run_label)

    arguments = parser.parse_args()
    try:
        arguments.handler(arguments)
    except urllib.error.URLError as error:
        print(f"lookup failed: {error}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
