import unittest
import urllib.error

from media_server.film_lookup import (
    FilmLookup,
    FilmMatch,
    earliest_release_year,
    is_match_trustworthy,
    is_year_plausible,
)

FILM_TYPE = "Q11424"


def wikidata_entry(entity_id, label, description, type_id=FILM_TYPE, release_dates=()):
    """One search hit and its claims, shaped the way Wikidata returns them."""
    return {
        "hit": {"id": entity_id, "label": label, "description": description},
        "claims": {
            "P31": [{"mainsnak": {"datavalue": {"value": {"id": type_id}}}}],
            "P577": [
                {"mainsnak": {"datavalue": {"value": {"time": date}}}}
                for date in release_dates
            ],
        },
    }


class FakeWikidata:
    """Answers both calls the lookup makes, from a list of entries."""

    def __init__(self, entries=()):
        self.entries = list(entries)
        self.search_terms = []

    def __call__(self, parameters):
        if parameters["action"] == "wbsearchentities":
            self.search_terms.append(parameters["search"])
            return {"search": [entry["hit"] for entry in self.entries]}
        return {
            "entities": {
                entry["hit"]["id"]: {"claims": entry["claims"]}
                for entry in self.entries
            }
        }


class YearPlausibilityTests(unittest.TestCase):
    def test_only_years_a_film_could_carry_are_accepted(self):
        cases = [
            ("1977", True),
            ("1888", True),
            ("1887", False),
            ("0012", False),
            ("", False),
            ("19x7", False),
            ("3000", False),
        ]

        for year, is_accepted in cases:
            with self.subTest(year=year):
                self.assertEqual(is_year_plausible(year), is_accepted)


class MatchTrustTests(unittest.TestCase):
    """Resemblance alone is not the test, because discs say less than Wikidata."""

    def test_a_disc_naming_part_of_a_longer_title_is_trusted(self):
        """A_NEW_HOPE scores 0.41 against Wikidata's label, and is still right."""
        match = FilmMatch(
            name="Star Wars: Episode IV - A New Hope", year="1977", similarity=0.41
        )

        self.assertTrue(is_match_trustworthy("A NEW HOPE", match))

    def test_a_close_title_is_trusted_without_containing_it(self):
        match = FilmMatch(name="The Untouchables", year="1987", similarity=1.0)

        self.assertTrue(is_match_trustworthy("THE UNTOUCHABLES", match))

    def test_a_match_with_no_year_is_no_use(self):
        """Firefly matches its own name exactly and is a series, so has none."""
        match = FilmMatch(name="Firefly", year="", similarity=1.0)

        self.assertFalse(is_match_trustworthy("FIREFLY", match))

    def test_an_unrelated_film_is_refused(self):
        match = FilmMatch(name="Daughters of Destiny", year="2017", similarity=0.2)

        self.assertFalse(is_match_trustworthy("THE UNTOUCHABLES", match))


class ReleaseYearTests(unittest.TestCase):
    def test_the_earliest_date_wins_because_re_releases_carry_later_ones(self):
        claims = {
            "P577": [
                {"mainsnak": {"datavalue": {"value": {"time": "+1997-01-31T00:00:00Z"}}}},
                {"mainsnak": {"datavalue": {"value": {"time": "+1977-05-25T00:00:00Z"}}}},
            ]
        }

        self.assertEqual(earliest_release_year(claims), "1977")

    def test_no_date_at_all_is_an_empty_year(self):
        self.assertEqual(earliest_release_year({"P577": []}), "")


class BestMatchTests(unittest.TestCase):
    def test_the_film_a_label_names_is_found_with_its_year(self):
        wikidata = FakeWikidata(
            [
                wikidata_entry(
                    "Q17738",
                    "Star Wars: Episode IV - A New Hope",
                    "1977 film by George Lucas",
                    release_dates=("+1977-05-25T00:00:00Z",),
                )
            ]
        )

        match = FilmLookup(fetch=wikidata).best_match("A NEW HOPE")

        self.assertEqual(match.year, "1977")
        self.assertEqual(wikidata.search_terms, ["A NEW HOPE"])

    def test_the_closest_title_wins_over_wikidata_own_ranking(self):
        """Wikidata ranks for general knowledge and puts sequels high."""
        wikidata = FakeWikidata(
            [
                wikidata_entry(
                    "Q83495",
                    "The Matrix Reloaded",
                    "2003 film",
                    release_dates=("+2003-05-15T00:00:00Z",),
                ),
                wikidata_entry(
                    "Q83495b",
                    "The Matrix",
                    "1999 film",
                    release_dates=("+1999-03-31T00:00:00Z",),
                ),
            ]
        )

        match = FilmLookup(fetch=wikidata).best_match("THE MATRIX")

        self.assertEqual(match.name, "The Matrix")
        self.assertEqual(match.year, "1999")

    def test_a_television_series_is_not_offered_as_a_film(self):
        wikidata = FakeWikidata(
            [
                wikidata_entry(
                    "Q671107",
                    "Firefly",
                    "American space Western television series",
                    type_id="Q5398426",
                )
            ]
        )

        self.assertIsNone(FilmLookup(fetch=wikidata).best_match("FIREFLY"))

    def test_a_label_matching_nothing_gives_no_year(self):
        self.assertIsNone(FilmLookup(fetch=FakeWikidata()).best_match("DVD VIDEO"))

    def test_an_empty_label_is_not_looked_up_at_all(self):
        wikidata = FakeWikidata()

        self.assertIsNone(FilmLookup(fetch=wikidata).best_match("   "))
        self.assertEqual(wikidata.search_terms, [])


class LookupFailureTests(unittest.TestCase):
    """A staged rip must not be lost because a web service was unhappy."""

    def _refusing_lookup(self, error: Exception) -> FilmLookup:
        def refuse(parameters):
            raise error

        return FilmLookup(fetch=refuse)

    def test_no_failure_reaches_the_caller(self):
        cases = [
            ("no network", urllib.error.URLError("unreachable")),
            ("timed out", TimeoutError("too slow")),
            ("not json", ValueError("bad payload")),
            ("unexpected shape", KeyError("id")),
            ("socket gone", OSError("closed")),
        ]

        for name, error in cases:
            with self.subTest(failure=name):
                lookup = self._refusing_lookup(error)

                self.assertIsNone(lookup.best_match("THE MATRIX"))


if __name__ == "__main__":
    unittest.main()
