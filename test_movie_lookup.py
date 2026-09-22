#!/usr/bin/env python3
"""Tests for movie_lookup.py. No network: the client is stubbed."""

import unittest

from movie_lookup import (
    _title_similarity,
    clean_disc_label,
    find_films,
    is_film,
)


def claims(instance_of=(), published=()):
    built = {}
    if instance_of:
        built["P31"] = [
            {"mainsnak": {"datavalue": {"value": {"id": type_id}}}}
            for type_id in instance_of
        ]
    if published:
        built["P577"] = [
            {"mainsnak": {"datavalue": {"value": {"time": f"+{year}-01-01T00:00:00Z"}}}}
            for year in published
        ]
    return built


class StubClient:
    """Stands in for Wikidata. Takes the shape its real methods return."""

    def __init__(self, entities):
        self.entities = entities

    def search(self, term, limit=15):
        return [
            (entity_id, entity["label"], entity.get("description", ""))
            for entity_id, entity in self.entities.items()
        ]

    def claims_for(self, entity_ids):
        return {
            entity_id: self.entities[entity_id].get("claims", {})
            for entity_id in entity_ids
        }


FILM = "Q11424"
ANIMATED_FILM = "Q202866"
ANIME_FILM = "Q20650540"
ALBUM = "Q482994"
TV_EPISODE = "Q21191270"


class CleanDiscLabelTests(unittest.TestCase):
    """Volume labels are shouty and padded with the studio's bookkeeping."""

    def test_underscores_become_spaces(self):
        self.assertEqual(clean_disc_label("KNIVES_OUT"), "KNIVES OUT")

    def test_strips_format_markers(self):
        self.assertEqual(clean_disc_label("THE_MATRIX_WS"), "THE MATRIX")
        self.assertEqual(clean_disc_label("DIE_HARD_BD25"), "DIE HARD")

    def test_strips_several_stacked_markers(self):
        self.assertEqual(
            clean_disc_label("BLADE_RUNNER_2049_FEATURE_DISC1"), "BLADE RUNNER 2049"
        )
        self.assertEqual(clean_disc_label("TOY_STORY_16X9_DVD_VIDEO"), "TOY STORY")

    def test_keeps_a_trailing_number_that_is_part_of_the_title(self):
        """2049 is the film, not a disc number."""
        self.assertEqual(clean_disc_label("BLADE_RUNNER_2049"), "BLADE RUNNER 2049")

    def test_leaves_an_unhelpful_label_alone_rather_than_emptying_it(self):
        self.assertEqual(clean_disc_label("LOGICAL_VOLUME_ID"), "LOGICAL VOLUME ID")


class IsFilmTests(unittest.TestCase):
    """Wikidata has a long tail of film subtypes; the description is a backstop."""

    def test_a_plain_film_type_counts(self):
        self.assertTrue(is_film(claims(instance_of=[FILM]), ""))

    def test_an_unlisted_film_subtype_is_caught_by_the_description(self):
        """Spirited Away is an 'anime film' and was being dropped."""
        self.assertTrue(
            is_film(claims(instance_of=["Q99999999"]), "2001 anime film by Hayao Miyazaki")
        )

    def test_an_album_is_not_a_film(self):
        self.assertFalse(is_film(claims(instance_of=[ALBUM]), "album by Joe Hisaishi"))

    def test_a_television_episode_is_not_a_film(self):
        self.assertFalse(
            is_film(claims(instance_of=[TV_EPISODE]), "episode of Unforgettable (S1 E11)")
        )

    def test_a_film_series_is_not_something_you_can_rip(self):
        self.assertFalse(is_film({}, "American film series"))

    def test_a_collection_described_in_the_plural_is_not_a_film(self):
        """"The Matrix series" is described as "1999-present films ..."."""
        self.assertFalse(
            is_film({}, "1999-present films directed by The Wachowskis")
        )

    def test_a_media_franchise_is_not_a_film(self):
        self.assertFalse(is_film({}, "science fiction action media franchise"))

    def test_an_ordinary_film_description_still_passes(self):
        self.assertTrue(
            is_film({}, "1999 American science fiction action thriller film")
        )

    def test_a_film_festival_is_not_a_film(self):
        self.assertFalse(is_film({}, "annual film festival in Utah"))

    def test_the_description_cannot_override_a_real_film_type(self):
        """A film whose description mentions a series is still a film."""
        self.assertFalse(is_film(claims(instance_of=[FILM]), "film series"))


class FindFilmsTests(unittest.TestCase):
    def test_exact_title_wins_over_a_sequel_listed_first(self):
        """Wikidata ranked Toy Story 4 above Toy Story."""
        client = StubClient({
            "Q1": {"label": "Toy Story 4", "description": "2019 film",
                   "claims": claims([ANIMATED_FILM], ["2019"])},
            "Q2": {"label": "Toy Story", "description": "1995 film",
                   "claims": claims([ANIMATED_FILM], ["1995"])},
        })

        films = find_films("TOY STORY", client=client)

        self.assertEqual(films[0]["name"], "Toy Story")
        self.assertEqual(films[0]["year"], "1995")

    def test_the_film_beats_a_stage_adaptation_of_it(self):
        client = StubClient({
            "Q1": {"label": "Spirited Away: Live On Stage", "description": "2023 film",
                   "claims": claims([FILM], ["2023"])},
            "Q2": {"label": "Spirited Away", "description": "2001 anime film",
                   "claims": claims([ANIME_FILM], ["2001"])},
        })

        films = find_films("SPIRITED AWAY", client=client)

        self.assertEqual(films[0]["name"], "Spirited Away")

    def test_non_films_are_left_out_entirely(self):
        client = StubClient({
            "Q1": {"label": "Spirited Away", "description": "album by Joe Hisaishi",
                   "claims": claims([ALBUM], ["2001"])},
            "Q2": {"label": "Spirited Away", "description": "2001 anime film",
                   "claims": claims([ANIME_FILM], ["2001"])},
        })

        films = find_films("SPIRITED AWAY", client=client)

        self.assertEqual(len(films), 1)
        self.assertEqual(films[0]["year"], "2001")

    def test_earliest_publication_date_wins_over_a_rerelease(self):
        client = StubClient({
            "Q1": {"label": "Blade Runner", "description": "1982 film",
                   "claims": claims([FILM], ["2007", "1982"])},
        })

        self.assertEqual(find_films("BLADE RUNNER", client=client)[0]["year"], "1982")

    def test_a_film_with_no_known_date_still_comes_back(self):
        client = StubClient({
            "Q1": {"label": "Some Obscure Film", "description": "film",
                   "claims": claims([FILM])},
        })

        films = find_films("SOME OBSCURE FILM", client=client)

        self.assertEqual(films[0]["year"], "")

    def test_an_empty_query_asks_wikidata_nothing(self):
        self.assertEqual(find_films("", client=None), [])


class TitleSimilarityTests(unittest.TestCase):
    def test_case_and_punctuation_do_not_matter(self):
        self.assertEqual(_title_similarity("KNIVES OUT", "Knives Out"), 1.0)

    def test_a_leading_article_does_not_matter(self):
        self.assertEqual(_title_similarity("MATRIX", "The Matrix"), 1.0)

    def test_a_sequel_scores_below_the_original(self):
        self.assertGreater(
            _title_similarity("TOY STORY", "Toy Story"),
            _title_similarity("TOY STORY", "Toy Story 4"),
        )


if __name__ == "__main__":
    unittest.main()
