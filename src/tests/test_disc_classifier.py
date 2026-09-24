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
