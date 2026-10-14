"""A stand-in for the Wikidata lookup, so tests reach no web service."""

from __future__ import annotations

from media_server.film_lookup import FilmMatch


class FakeFilmLookup:
    """Answers from a table of titles to years.

    A title that is not in the table has no match, which is the ordinary case
    for a disc whose label says nothing a film is called. Tests that do not
    care about years can build one with an empty table and get no year, which
    is what the library did before any of this existed.
    """

    def __init__(self, years_by_title: dict[str, str] | None = None):
        self.years_by_title = years_by_title or {}
        self.titles_asked_about: list[str] = []

    def best_match(self, search_term: str) -> FilmMatch | None:
        self.titles_asked_about.append(search_term)
        year = self.years_by_title.get(search_term)
        if year is None:
            return None
        return FilmMatch(name=search_term, year=year, similarity=1.0)
