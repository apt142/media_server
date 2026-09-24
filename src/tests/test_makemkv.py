import tempfile
import unittest
from pathlib import Path

from media_server.makemkv import (
    MakeMkv,
    collapse_repeated_messages,
    duration_to_seconds,
    largest_mkv_in,
    parse_disc_scan,
)
from tests import makemkv_fixtures
from tests.fake_makemkv import FakeMakeMkvCommand


class ScanParsingTests(unittest.TestCase):
    def test_reads_the_label_and_disc_type_off_a_bluray(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.FILM_BLURAY)

        self.assertEqual(disc_scan.disc_label, "THE_MATRIX")
        self.assertEqual(disc_scan.media_type, "Blu-ray disc")
        self.assertTrue(disc_scan.is_bluray)
        self.assertEqual(disc_scan.media_kind, "bluray")

    def test_reads_a_dvd_as_a_dvd(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.TV_DVD)

        self.assertFalse(disc_scan.is_bluray)
        self.assertEqual(disc_scan.media_kind, "dvd")

    def test_collects_every_title_in_disc_order(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.FILM_BLURAY)

        self.assertEqual([title.title_id for title in disc_scan.titles], [0, 1, 2, 3])

    def test_reads_the_details_of_one_title(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.FILM_BLURAY)

        feature = disc_scan.title_with_id(0)
        self.assertEqual(feature.duration, "2:16:17")
        self.assertEqual(feature.length_seconds, 8177)
        self.assertEqual(feature.size_bytes, 30829404160)
        self.assertEqual(feature.source, "00800.mpls")
        self.assertEqual(feature.name, "title_t00.mkv")

    def test_notices_the_title_makemkv_flagged_as_the_feature(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.FILM_BLURAY)

        self.assertTrue(disc_scan.title_with_id(0).is_main_feature)
        self.assertFalse(disc_scan.title_with_id(1).is_main_feature)

    def test_keeps_the_messages_that_explain_a_failure(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.UNREADABLE_DISC)

        self.assertFalse(disc_scan.has_titles)
        self.assertIn("Failed to open disc", disc_scan.messages)

    def test_an_empty_response_reads_as_a_disc_with_nothing_on_it(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.NO_DRIVE_RESPONSE)

        self.assertFalse(disc_scan.has_titles)
        self.assertEqual(disc_scan.disc_label, "")


class FeatureSelectionTests(unittest.TestCase):
    def test_the_flagged_title_wins_even_when_another_runs_longer(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.FILM_BLURAY)

        self.assertEqual(disc_scan.feature_title().title_id, 0)

    def test_the_longest_title_wins_when_nothing_is_flagged(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.DOUBLE_FEATURE_DVD)

        self.assertEqual(disc_scan.feature_title().title_id, 1)

    def test_a_disc_with_no_titles_has_no_feature(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.UNREADABLE_DISC)

        self.assertIsNone(disc_scan.feature_title())

    def test_titles_can_be_narrowed_to_a_length_window(self):
        disc_scan = parse_disc_scan(makemkv_fixtures.TV_DVD)

        episode_titles = disc_scan.titles_lasting_between(900, 3900)

        self.assertEqual([title.title_id for title in episode_titles], [0, 1, 2, 3])

    def test_the_same_disc_always_fingerprints_the_same_way(self):
        first_scan = parse_disc_scan(makemkv_fixtures.FILM_BLURAY)
        second_scan = parse_disc_scan(makemkv_fixtures.FILM_BLURAY)

        self.assertEqual(first_scan.fingerprint(), second_scan.fingerprint())

    def test_two_different_discs_fingerprint_differently(self):
        film = parse_disc_scan(makemkv_fixtures.FILM_BLURAY)
        show = parse_disc_scan(makemkv_fixtures.TV_DVD)

        self.assertNotEqual(film.fingerprint(), show.fingerprint())


class DurationReadingTests(unittest.TestCase):
    def test_reads_the_shapes_makemkv_prints(self):
        cases = [
            ("2:16:17", 8177),
            ("0:44:01", 2641),
            ("44:01", 2641),
            ("0:00:30", 30),
            ("", 0),
            ("unknown", 0),
        ]

        for duration, expected_seconds in cases:
            with self.subTest(duration=duration):
                self.assertEqual(duration_to_seconds(duration), expected_seconds)


class RippingTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.destination = Path(temporary_directory.name) / "title-00"

    def test_a_ripped_title_reports_the_file_it_produced(self):
        makemkv = MakeMkv(run_command=FakeMakeMkvCommand())

        rip_result = makemkv.rip_title(0, self.destination)

        self.assertTrue(rip_result.is_ripped)
        self.assertTrue(rip_result.ripped_file.is_file())
        self.assertEqual(rip_result.ripped_file.suffix, ".mkv")

    def test_a_title_that_will_not_decrypt_reports_nothing(self):
        makemkv = MakeMkv(run_command=FakeMakeMkvCommand(unreadable_title_ids=(0,)))

        rip_result = makemkv.rip_title(0, self.destination)

        self.assertFalse(rip_result.is_ripped)
        self.assertIsNone(rip_result.ripped_file)

    def test_the_reason_a_title_refused_comes_back_with_the_refusal(self):
        fake_command = FakeMakeMkvCommand(
            unreadable_title_ids=(0,),
            rip_output=makemkv_fixtures.REFUSED_TITLE_OUTPUT,
        )
        makemkv = MakeMkv(run_command=fake_command)

        rip_result = makemkv.rip_title(0, self.destination)

        self.assertIn(
            "Failed to save title 0 to file title_t00.mkv", rip_result.messages
        )

    def test_a_scratched_disc_reports_its_errors_once_with_a_count(self):
        fake_command = FakeMakeMkvCommand(
            unreadable_title_ids=(0,),
            rip_output=makemkv_fixtures.DAMAGED_DISC_OUTPUT,
        )
        makemkv = MakeMkv(run_command=fake_command)

        rip_result = makemkv.rip_title(0, self.destination)

        self.assertIn(
            "Error 'Scsi error - MEDIUM ERROR:L-EC UNCORRECTABLE ERROR' occurred "
            "while reading '/BDMV/STREAM/00518.m2ts' at offset '3989962752' (\u00d73)",
            rip_result.messages,
        )

    def test_the_lines_that_say_what_happened_are_not_buried(self):
        fake_command = FakeMakeMkvCommand(
            unreadable_title_ids=(0,),
            rip_output=makemkv_fixtures.DAMAGED_DISC_OUTPUT,
        )
        makemkv = MakeMkv(run_command=fake_command)

        rip_result = makemkv.rip_title(0, self.destination)

        # Nine lines of output, six of them distinct.
        self.assertEqual(len(rip_result.messages), 6)
        self.assertIn("Copy complete. 0 titles saved, 1 failed.", rip_result.messages)

    def test_an_expired_key_is_carried_back_in_makemkvs_own_words(self):
        fake_command = FakeMakeMkvCommand(
            unreadable_title_ids=(0,),
            rip_output=makemkv_fixtures.EXPIRED_KEY_OUTPUT,
        )
        makemkv = MakeMkv(run_command=fake_command)

        rip_result = makemkv.rip_title(0, self.destination)

        self.assertIn(
            "This application version is too old and the evaluation period has expired",
            rip_result.messages,
        )

    def test_a_message_said_once_is_left_alone(self):
        self.assertEqual(
            collapse_repeated_messages(["Failed to open disc"]),
            ["Failed to open disc"],
        )

    def test_repeats_are_counted_even_when_they_alternate(self):
        collapsed = collapse_repeated_messages(
            ["read error", "io error", "read error", "io error", "read error"]
        )

        self.assertEqual(collapsed, ["read error (\u00d73)", "io error (\u00d72)"])

    def test_messages_keep_the_order_they_were_first_said_in(self):
        collapsed = collapse_repeated_messages(
            ["started", "read error", "read error", "gave up"]
        )

        self.assertEqual(collapsed, ["started", "read error (\u00d72)", "gave up"])

    def test_nothing_said_collapses_to_nothing(self):
        self.assertEqual(collapse_repeated_messages([]), [])

    def test_scanning_hands_back_a_parsed_disc(self):
        fake_command = FakeMakeMkvCommand(makemkv_fixtures.FILM_BLURAY)
        makemkv = MakeMkv(run_command=fake_command)

        disc_scan = makemkv.scan_disc()

        self.assertEqual(disc_scan.disc_label, "THE_MATRIX")
        self.assertEqual(fake_command.scanned_count, 1)


class LargestFileTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.directory = Path(temporary_directory.name)

    def test_the_biggest_mkv_wins_over_stray_small_files(self):
        (self.directory / "title_t00.mkv").write_bytes(b"x" * 5000)
        (self.directory / "title_t01.mkv").write_bytes(b"x" * 10)

        self.assertEqual(largest_mkv_in(self.directory).name, "title_t00.mkv")

    def test_a_folder_with_no_mkv_reports_nothing(self):
        (self.directory / "notes.txt").write_text("nothing to see")

        self.assertIsNone(largest_mkv_in(self.directory))


if __name__ == "__main__":
    unittest.main()
