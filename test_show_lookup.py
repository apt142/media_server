#!/usr/bin/env python3
"""Tests for the disc identification logic in show_lookup.py.

Run with: python3 -m unittest test_show_lookup -v

Nothing here touches the network. TvMazeClient is the only part that does, and
these tests feed episode lists in directly instead of calling it.
"""

import io
import unittest

from show_lookup import (
    DiscTitles,
    EpisodeAlignment,
    clean_disc_label,
    disc_number_from_label,
    season_from_disc_label,
)


def episode(season, number, name, runtime):
    return {"season": season, "number": number, "name": name, "runtime": runtime}


def title(title_id, seconds, size_bytes=1_000_000_000):
    return {
        "title_id": title_id,
        "seconds": seconds,
        "duration": f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}",
        "bytes": size_bytes,
    }


class CleanDiscLabelTests(unittest.TestCase):
    """Volume labels are the weakest signal, so parsing them must not overreach."""

    def test_strips_disc_numbering(self):
        self.assertEqual(clean_disc_label("FIREFLY_D1"), "Firefly")

    def test_strips_season_and_disc_numbering(self):
        self.assertEqual(clean_disc_label("THE_WIRE_S02_D3"), "The Wire")

    def test_strips_spelled_out_bookkeeping(self):
        self.assertEqual(clean_disc_label("BREAKING_BAD_SEASON_1_DISC_2"), "Breaking Bad")

    def test_generic_label_yields_nothing_to_search(self):
        self.assertEqual(clean_disc_label("DVD_VIDEO"), "")

    def test_empty_label_is_handled(self):
        self.assertEqual(clean_disc_label(""), "")


class SeasonFromDiscLabelTests(unittest.TestCase):
    def test_reads_padded_season(self):
        self.assertEqual(season_from_disc_label("THE_WIRE_S02_D3"), 2)

    def test_reads_spelled_out_season(self):
        self.assertEqual(season_from_disc_label("BREAKING_BAD_SEASON_1_DISC_2"), 1)

    def test_returns_none_when_absent(self):
        self.assertIsNone(season_from_disc_label("FIREFLY_D1"))


class DiscNumberFromLabelTests(unittest.TestCase):
    def test_reads_short_form(self):
        self.assertEqual(disc_number_from_label("FIREFLY_D1"), 1)

    def test_reads_spelled_out_disc(self):
        self.assertEqual(disc_number_from_label("BREAKING_BAD_SEASON_1_DISC_2"), 2)

    def test_returns_none_when_absent(self):
        self.assertIsNone(disc_number_from_label("DVD_VIDEO"))


class DiscTitlesFromTsvTests(unittest.TestCase):
    def test_reads_the_makemkv_table(self):
        table = "0\t2640\t0:44:00\t1600000000\t00800.mpls\t-\ttitle_t00.mkv\n"

        titles = DiscTitles.from_tsv(io.StringIO(table)).titles

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0]["title_id"], 0)
        self.assertEqual(titles[0]["seconds"], 2640)

    def test_skips_the_plan_header_and_blank_lines(self):
        table = "#plan\tconfident\tnote\t3\n\n0\t2640\t0:44:00\t1600000000\n"

        titles = DiscTitles.from_tsv(io.StringIO(table)).titles

        self.assertEqual(len(titles), 1)


class EpisodeTitleSelectionTests(unittest.TestCase):
    """A TV disc carries more than its episodes; only the episodes should survive."""

    FORTY_FOUR_MINUTES = 2640
    EXPECTED_EPISODE_SECONDS = 2640

    def test_keeps_plain_episodes(self):
        disc = DiscTitles([title(0, 2640), title(1, 2610), title(2, 2650)])

        kept = disc.episode_titles(self.EXPECTED_EPISODE_SECONDS, 900)

        self.assertEqual([entry["title_id"] for entry in kept], [0, 1, 2])

    def test_drops_the_play_all_bundle(self):
        disc = DiscTitles([title(0, 2640), title(1, 2610), title(2, 2650), title(3, 7900)])

        kept = disc.episode_titles(self.EXPECTED_EPISODE_SECONDS, 900)

        self.assertEqual([entry["title_id"] for entry in kept], [0, 1, 2])

    def test_drops_short_extras(self):
        disc = DiscTitles([title(0, 2640), title(1, 2610), title(2, 250)])

        kept = disc.episode_titles(self.EXPECTED_EPISODE_SECONDS, 900)

        self.assertEqual([entry["title_id"] for entry in kept], [0, 1])

    def test_drops_repeated_playlists_with_identical_size(self):
        disc = DiscTitles([
            title(0, 2640, size_bytes=1_600_000_000),
            title(1, 2640, size_bytes=1_600_000_000),
            title(2, 2610, size_bytes=1_580_000_000),
        ])

        kept = disc.episode_titles(self.EXPECTED_EPISODE_SECONDS, 900)

        self.assertEqual([entry["title_id"] for entry in kept], [0, 2])

    def test_keeps_same_length_episodes_that_differ_in_size(self):
        """Two episodes can genuinely run the same time; only a byte-for-byte
        repeat is a duplicate playlist."""
        disc = DiscTitles([
            title(0, 2640, size_bytes=1_600_000_000),
            title(1, 2640, size_bytes=1_712_000_000),
        ])

        kept = disc.episode_titles(self.EXPECTED_EPISODE_SECONDS, 900)

        self.assertEqual([entry["title_id"] for entry in kept], [0, 1])

    def test_keeps_a_double_length_premiere(self):
        disc = DiscTitles([title(0, 7320), title(1, 2640), title(2, 2610)])

        kept = disc.episode_titles(self.EXPECTED_EPISODE_SECONDS, 900)

        self.assertEqual([entry["title_id"] for entry in kept], [0, 1, 2])


class EpisodeAlignmentTests(unittest.TestCase):
    """Placing a disc within a season, and being honest when it cannot be placed."""

    # Firefly in DVD order: a double-length premiere, then uniform episodes.
    FIREFLY_DVD_SEASON = [
        episode(1, 1, "Serenity", 120),
        episode(1, 2, "The Train Job", 60),
        episode(1, 3, "Bushwhacked", 60),
        episode(1, 4, "Shindig", 60),
        episode(1, 5, "Safe", 60),
        episode(1, 6, "Our Mrs. Reynolds", 60),
    ]

    UNIFORM_SEASON = [episode(2, number, f"Episode {number}", 60) for number in range(1, 8)]

    def test_double_length_premiere_pins_the_first_disc(self):
        disc = [title(0, 7320), title(1, 2640), title(2, 2610)]

        start, is_confident, _ = EpisodeAlignment(self.FIREFLY_DVD_SEASON, disc).best_start()

        self.assertEqual(start, 0)
        self.assertTrue(is_confident)

    def test_uniform_episodes_cannot_be_placed(self):
        disc = [title(0, 2640), title(1, 2610), title(2, 2650)]

        _, is_confident, note = EpisodeAlignment(self.UNIFORM_SEASON, disc).best_start()

        self.assertFalse(is_confident)
        self.assertIn("about the same length", note)

    def test_a_disc_holding_the_whole_season_is_certain(self):
        disc = [title(index, 2640) for index in range(len(self.UNIFORM_SEASON))]

        start, is_confident, note = EpisodeAlignment(self.UNIFORM_SEASON, disc).best_start()

        self.assertEqual(start, 0)
        self.assertTrue(is_confident)
        self.assertIn("whole season", note)

    def test_supplied_start_is_used(self):
        disc = [title(0, 2640), title(1, 2610), title(2, 2650)]

        start, is_confident, _ = EpisodeAlignment(self.UNIFORM_SEASON, disc).best_start(preferred_start=3)

        self.assertEqual(start, 3)
        self.assertTrue(is_confident)

    def test_supplied_start_that_contradicts_the_lengths_is_flagged(self):
        """Told to start at episode 4, but the double-length premiere says episode 1."""
        disc = [title(0, 7320), title(1, 2640), title(2, 2610)]

        start, is_confident, note = EpisodeAlignment(self.FIREFLY_DVD_SEASON, disc).best_start(preferred_start=3)

        self.assertEqual(start, 3)
        self.assertFalse(is_confident)
        self.assertIn("starts at episode 1", note)

    def test_supplied_start_that_cannot_fit_is_rejected(self):
        """Episode 6 of a 6-episode season leaves no room for three titles."""
        disc = [title(0, 7320), title(1, 2640), title(2, 2610)]

        _, is_confident, note = EpisodeAlignment(self.FIREFLY_DVD_SEASON, disc).best_start(preferred_start=5)

        self.assertFalse(is_confident)
        self.assertIn("leaves no room", note)

    def test_more_titles_than_episodes_is_reported(self):
        disc = [title(index, 2640) for index in range(9)]

        _, is_confident, note = EpisodeAlignment(self.UNIFORM_SEASON, disc).best_start()

        self.assertFalse(is_confident)
        self.assertIn("only lists", note)

    def test_empty_disc_does_not_raise(self):
        _, is_confident, _ = EpisodeAlignment(self.UNIFORM_SEASON, []).best_start()

        self.assertFalse(is_confident)


if __name__ == "__main__":
    unittest.main()
