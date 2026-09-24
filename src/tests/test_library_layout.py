import unittest
from pathlib import Path

from media_server.job_catalog import Job, JobState
from media_server.library_layout import destinations_for


def build_job(**overrides) -> Job:
    """A job with only the fields the layout cares about set."""
    fields = {
        "job_id": 1,
        "disc_label": "THE_MATRIX",
        "media_kind": "film",
        "title": "The Matrix",
        "year": "1999",
        "part_number": None,
        "state": JobState.ENCODING,
        "staged_path": Path("/staging/raw/THE_MATRIX"),
        "encoded_path": None,
        "disc_fingerprint": "abc123",
        "failure_reason": "",
        "attempt_count": 1,
    }
    fields.update(overrides)
    return Job(**fields)


class FilmLayoutTests(unittest.TestCase):
    def test_a_film_lands_in_its_own_folder_named_for_plex(self):
        destinations = destinations_for(build_job(), source_count=1, extension="mp4")

        self.assertEqual(
            destinations,
            [Path("Movies/The Matrix (1999)/The Matrix (1999).mp4")],
        )

    def test_a_film_with_no_year_yet_still_gets_a_sensible_folder(self):
        destinations = destinations_for(
            build_job(year=""), source_count=1, extension="mp4"
        )

        self.assertEqual(destinations, [Path("Movies/The Matrix/The Matrix.mp4")])

    def test_a_split_film_is_named_as_a_part_so_plex_stacks_it(self):
        job = build_job(title="Fellowship", year="2001", part_number=2)

        destinations = destinations_for(job, source_count=1, extension="mkv")

        self.assertEqual(
            destinations, [Path("Movies/Fellowship (2001)/Fellowship (2001) - part2.mkv")]
        )

    def test_both_parts_of_a_split_film_share_one_folder(self):
        first_disc = destinations_for(
            build_job(title="Fellowship", year="2001", part_number=1),
            source_count=1,
            extension="mkv",
        )[0]
        second_disc = destinations_for(
            build_job(title="Fellowship", year="2001", part_number=2),
            source_count=1,
            extension="mkv",
        )[0]

        self.assertEqual(first_disc.parent, second_disc.parent)
        self.assertNotEqual(first_disc.name, second_disc.name)

    def test_a_colon_in_a_title_is_removed_because_it_breaks_smb_shares(self):
        job = build_job(title="Alien: Resurrection", year="1997")

        destinations = destinations_for(job, source_count=1, extension="mp4")

        self.assertEqual(
            destinations,
            [Path("Movies/Alien Resurrection (1997)/Alien Resurrection (1997).mp4")],
        )

    def test_a_double_feature_becomes_two_films_rather_than_one_in_two_parts(self):
        destinations = destinations_for(build_job(), source_count=2, extension="mp4")

        self.assertEqual(len(destinations), 2)
        self.assertEqual(
            destinations[0],
            Path("Movies/The Matrix (1999) - feature1/The Matrix (1999) - feature1.mp4"),
        )
        self.assertEqual(
            destinations[1],
            Path("Movies/The Matrix (1999) - feature2/The Matrix (1999) - feature2.mp4"),
        )

    def test_each_film_on_a_double_feature_gets_its_own_folder(self):
        """Sharing a folder is what makes Plex stack them into one long film."""
        destinations = destinations_for(build_job(), source_count=2, extension="mp4")

        self.assertNotEqual(destinations[0].parent, destinations[1].parent)

    def test_a_film_split_across_discs_still_uses_its_part_number(self):
        split_film = build_job(part_number=2)

        destinations = destinations_for(split_film, source_count=1, extension="mp4")

        self.assertTrue(destinations[0].name.endswith("- part2.mp4"))


class EpisodeLayoutTests(unittest.TestCase):
    def _show_job(self, **overrides) -> Job:
        fields = {
            "media_kind": "show",
            "title": "Firefly",
            "year": "",
            "disc_label": "FIREFLY_S01_D2",
            "season_number": 1,
            "first_episode_number": 1,
        }
        fields.update(overrides)
        return build_job(**fields)

    def test_episodes_are_named_the_way_plex_matches_them(self):
        destinations = destinations_for(
            self._show_job(), source_count=3, extension="mp4"
        )

        self.assertEqual(
            destinations,
            [
                Path("TV/Firefly/Season 01/Firefly - s01e01.mp4"),
                Path("TV/Firefly/Season 01/Firefly - s01e02.mp4"),
                Path("TV/Firefly/Season 01/Firefly - s01e03.mp4"),
            ],
        )

    def test_a_later_disc_carries_on_where_the_previous_one_stopped(self):
        destinations = destinations_for(
            self._show_job(first_episode_number=5), source_count=2, extension="mp4"
        )

        self.assertEqual(
            [destination.name for destination in destinations],
            ["Firefly - s01e05.mp4", "Firefly - s01e06.mp4"],
        )

    def test_a_show_with_no_season_on_the_label_is_filed_as_season_one(self):
        destinations = destinations_for(
            self._show_job(season_number=None), source_count=1, extension="mp4"
        )

        self.assertEqual(
            destinations, [Path("TV/Firefly/Season 01/Firefly - s01e01.mp4")]
        )

    def test_a_double_digit_season_is_padded_consistently(self):
        destinations = destinations_for(
            self._show_job(season_number=12, first_episode_number=10),
            source_count=1,
            extension="mp4",
        )

        self.assertEqual(
            destinations, [Path("TV/Firefly/Season 12/Firefly - s12e10.mp4")]
        )


if __name__ == "__main__":
    unittest.main()
