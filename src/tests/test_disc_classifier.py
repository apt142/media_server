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


class FilmWithSimilarExtrasTests(unittest.TestCase):
    """A feature outweighs extras that happen to run to a similar length.

    The Untouchables ripped as a season: its two featurettes are within a few
    percent of each other, which read as a pair of episodes, and the film was
    then too long to be one and was left on the disc.
    """

    def setUp(self):
        self.classifier = DiscClassifier(
            parse_disc_scan(makemkv_fixtures.FILM_WITH_SIMILAR_EXTRAS)
        )

    def test_the_disc_is_read_as_a_film(self):
        verdict = self.classifier.verdict()

        self.assertFalse(verdict.is_show)
        self.assertIn("longest title runs 119 minutes", verdict.reason)

    def test_the_extras_are_what_made_it_look_like_a_season(self):
        episode_titles = self.classifier.episode_length_titles()

        self.assertEqual([title.title_id for title in episode_titles], [1, 2])

    def test_a_play_all_is_still_not_mistaken_for_a_feature(self):
        """The towering title on a TV disc runs the length of all the episodes."""
        cases = [
            ("two episodes and a play-all", makemkv_fixtures.TV_DVD_WITH_PLAY_ALL),
            ("a double-length pilot", makemkv_fixtures.TV_DVD_WITH_LONG_PILOT),
        ]

        for name, scan_output in cases:
            with self.subTest(disc=name):
                verdict = DiscClassifier(parse_disc_scan(scan_output)).verdict()

                self.assertTrue(verdict.is_show)

    def test_a_miniseries_of_feature_length_episodes_still_needs_telling(self):
        """Not a regression: no signal on that disc says television.

        Every episode runs past the episode ceiling, so there is nothing to
        separate it from a disc of films. It is why --as-show exists, and it
        is asserted here so this change cannot be mistaken for having fixed it.
        """
        verdict = DiscClassifier(
            parse_disc_scan(makemkv_fixtures.MINISERIES_DVD)
        ).verdict()

        self.assertFalse(verdict.is_show)


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

    def test_episodes_longer_than_a_feature_are_still_found(self):
        """Nothing on a miniseries disc sits in the ordinary episode window."""
        episodes = self._episodes_on(makemkv_fixtures.MINISERIES_DVD)

        self.assertEqual([title.title_id for title in episodes], [1, 2, 3])

    def test_the_typical_episode_is_the_median_not_the_longest(self):
        """A maximum would be raised by the play-all it is meant to exclude."""
        episodes = self._episodes_on(makemkv_fixtures.MINISERIES_DVD)

        self.assertNotIn("3:30:00", [title.duration for title in episodes])

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
