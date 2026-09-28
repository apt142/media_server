import unittest

from media_server.disc_classifier import (
    DiscClassifier,
    clean_disc_label,
    disc_number_from_label,
    season_from_disc_label,
)
from media_server.makemkv import parse_disc_scan
from tests import makemkv_fixtures


class DiscVerdictTests(unittest.TestCase):
    def test_reads_a_disc_by_the_shape_of_its_titles(self):
        cases = [
            ("film Blu-ray", makemkv_fixtures.FILM_BLURAY, "film", True),
            ("TV DVD", makemkv_fixtures.TV_DVD, "show", True),
            ("double feature", makemkv_fixtures.DOUBLE_FEATURE_DVD, "film", True),
            ("split film", makemkv_fixtures.SPLIT_FILM_BLURAY, "film", True),
        ]

        for name, scan_output, media_kind, is_confident in cases:
            with self.subTest(disc=name):
                verdict = DiscClassifier(parse_disc_scan(scan_output)).verdict()

                self.assertEqual(verdict.media_kind, media_kind)
                self.assertEqual(verdict.is_confident, is_confident)

    def test_a_double_feature_is_not_mistaken_for_two_episodes(self):
        verdict = DiscClassifier(
            parse_disc_scan(makemkv_fixtures.DOUBLE_FEATURE_DVD)
        ).verdict()

        self.assertFalse(verdict.is_show)
        self.assertIn("longest title runs 72 minutes", verdict.reason)

    def test_a_tv_disc_explains_itself(self):
        verdict = DiscClassifier(parse_disc_scan(makemkv_fixtures.TV_DVD)).verdict()

        self.assertTrue(verdict.is_show)
        self.assertIn("4 titles run at episode length", verdict.reason)
        self.assertIn("season number", verdict.reason)

    def test_a_disc_with_nothing_on_it_is_not_confident_either_way(self):
        verdict = DiscClassifier(
            parse_disc_scan(makemkv_fixtures.UNREADABLE_DISC)
        ).verdict()

        self.assertFalse(verdict.is_confident)
        self.assertIn("nothing on the disc", verdict.reason)

    def test_the_verdict_reads_as_a_sentence(self):
        verdict = DiscClassifier(parse_disc_scan(makemkv_fixtures.TV_DVD)).verdict()

        self.assertTrue(verdict.describe().startswith("This looks like a TV disc:"))

    def test_only_episode_length_titles_count_as_episodes(self):
        classifier = DiscClassifier(parse_disc_scan(makemkv_fixtures.TV_DVD))

        episode_titles = classifier.episode_length_titles()

        self.assertEqual([title.title_id for title in episode_titles], [0, 1, 2, 3])


class EpisodesToRipTests(unittest.TestCase):
    """A double-length pilot is still an episode. A "play all" is not."""

    def _episodes_on(self, scan_output: str) -> list:
        return DiscClassifier(parse_disc_scan(scan_output)).episodes_to_rip()

    def test_a_feature_length_pilot_is_taken_as_an_episode(self):
        episodes = self._episodes_on(makemkv_fixtures.TV_DVD_WITH_LONG_PILOT)

        self.assertEqual([title.title_id for title in episodes], [0, 1, 2])

    def test_the_pilot_comes_first_so_the_numbering_starts_at_it(self):
        episodes = self._episodes_on(makemkv_fixtures.TV_DVD_WITH_LONG_PILOT)

        self.assertEqual(episodes[0].duration, "1:26:26")

    def test_a_disc_whose_label_carries_no_season_is_still_read_as_a_show(self):
        """FIREFLY_D1 has only a disc number to go on, which scores weakly."""
        verdict = DiscClassifier(
            parse_disc_scan(makemkv_fixtures.TV_DVD_WITH_LONG_PILOT)
        ).verdict()

        self.assertEqual(verdict.media_kind, "show")
        self.assertTrue(verdict.is_confident)

    def test_a_play_all_title_is_left_behind(self):
        episodes = self._episodes_on(makemkv_fixtures.TV_DVD_WITH_PLAY_ALL)

        self.assertEqual([title.title_id for title in episodes], [1, 2, 3, 4])

    def test_an_ordinary_disc_is_unaffected(self):
        episodes = self._episodes_on(makemkv_fixtures.TV_DVD)

        self.assertEqual([title.title_id for title in episodes], [0, 1, 2, 3])

    def test_the_shape_of_the_disc_is_still_read_from_ordinary_episodes(self):
        """Loosening what gets ripped must not loosen the film-or-show call."""
        classifier = DiscClassifier(
            parse_disc_scan(makemkv_fixtures.DOUBLE_FEATURE_DVD)
        )

        self.assertEqual(classifier.verdict().media_kind, "film")
        self.assertEqual(classifier.episode_length_titles(), [])


class DiscLabelReadingTests(unittest.TestCase):
    def test_pulls_a_season_number_out_of_a_label(self):
        cases = [
            ("FIREFLY_S01_D2", 1),
            ("THE_WIRE_SEASON_3", 3),
            ("SOME_SHOW_SERIES_2", 2),
            ("THE_MATRIX", None),
            ("", None),
        ]

        for disc_label, expected_season in cases:
            with self.subTest(disc_label=disc_label):
                self.assertEqual(season_from_disc_label(disc_label), expected_season)

    def test_pulls_a_disc_number_out_of_a_label(self):
        cases = [
            ("FELLOWSHIP_EE_D2", 2),
            ("KNIVES_OUT_FEATURE_DISC1", 1),
            ("FIREFLY_S01_D2", 2),
            ("THE_MATRIX", None),
            ("BLADE_RUNNER_2049", None),
        ]

        for disc_label, expected_disc_number in cases:
            with self.subTest(disc_label=disc_label):
                self.assertEqual(
                    disc_number_from_label(disc_label), expected_disc_number
                )

    def test_strips_disc_bookkeeping_down_to_the_words(self):
        cases = [
            ("FIREFLY_S01_D2", "Firefly"),
            ("THE_MATRIX", "The Matrix"),
            ("TOY_STORY_DVD_VIDEO", "Toy Story"),
            ("THE_WIRE_S02_D3", "The Wire"),
            ("FELLOWSHIP_EE_D2", "Fellowship"),
            ("KNIVES_OUT_FEATURE_DISC1", "Knives Out"),
            ("BLADE_RUNNER_DIRECTORS_CUT", "Blade Runner"),
        ]

        for disc_label, expected_title in cases:
            with self.subTest(disc_label=disc_label):
                self.assertEqual(clean_disc_label(disc_label), expected_title)


class SequelNumberTests(unittest.TestCase):
    """A number in a film's name is the name. In a show's it is the disc."""

    def test_a_films_number_is_part_of_its_title(self):
        cases = [
            ("IRON_MAN_2", "Iron Man 2"),
            ("OCEANS_11", "Oceans 11"),
            ("TOY_STORY_3", "Toy Story 3"),
            ("THOR", "Thor"),
        ]

        for disc_label, expected_title in cases:
            with self.subTest(disc_label=disc_label):
                self.assertEqual(clean_disc_label(disc_label), expected_title)

    def test_disc_bookkeeping_is_still_stripped_from_a_film(self):
        cases = [
            ("FELLOWSHIP_EE_D2", "Fellowship"),
            ("KNIVES_OUT_FEATURE_DISC1", "Knives Out"),
        ]

        for disc_label, expected_title in cases:
            with self.subTest(disc_label=disc_label):
                self.assertEqual(clean_disc_label(disc_label), expected_title)

    def test_a_trailing_number_on_a_show_is_read_as_a_disc_number(self):
        self.assertEqual(clean_disc_label("FIREFLY 2", is_show=True), "Firefly")

    def test_a_show_that_is_only_a_number_keeps_it(self):
        self.assertEqual(clean_disc_label("24", is_show=True), "24")


if __name__ == "__main__":
    unittest.main()
