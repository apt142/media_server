import tempfile
import unittest
from pathlib import Path
from unittest import mock

from media_server.configuration import Configuration
from media_server.job_catalog import JobCatalog, JobState
from media_server.makemkv import MakeMkv
from media_server.file_names import safe_file_component
from media_server.makemkv import DiscTitle
from media_server.rip_worker import RipSettings, RipWorker, without_repeated_features
from media_server.volume_space import VolumeSpace
from tests import makemkv_fixtures
from tests.fake_drive import RecordingDiscDrive
from tests.fake_film_lookup import FakeFilmLookup
from tests.fake_makemkv import FakeMakeMkvCommand


class RipWorkerTestCase(unittest.TestCase):
    """Shared staging, catalog and stubbed drive. Holds no tests of its own."""

    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary_root = Path(temporary_directory.name)

        self.configuration = Configuration(
            staging_root=temporary_root / "staging",
            library_root=temporary_root / "library",
        )
        self.catalog = JobCatalog(self.configuration.catalog_path)
        self.addCleanup(self.catalog.close)
        self.drive = RecordingDiscDrive()
        self.announced_lines: list[str] = []
        # Empty by default, so a test that says nothing about years gets none
        # and reaches no web service doing it.
        self.film_lookup = FakeFilmLookup()

    def _worker(
        self,
        scan_output: str,
        unreadable_title_ids: tuple[int, ...] = (),
        rip_output: str = "",
        settings: RipSettings | None = None,
        is_copy_refused: bool = False,
        copy_output: str = "",
    ) -> RipWorker:
        self.fake_command = FakeMakeMkvCommand(
            scan_output,
            unreadable_title_ids,
            rip_output,
            is_copy_refused=is_copy_refused,
            copy_output=copy_output,
        )
        return RipWorker(
            configuration=self.configuration,
            catalog=self.catalog,
            makemkv=MakeMkv(run_command=self.fake_command),
            drive=self.drive,
            announce=self.announced_lines.append,
            settings=settings,
            film_lookup=self.film_lookup,
        )


class RippingAFilmTests(RipWorkerTestCase):
    def test_only_the_feature_is_ripped(self):
        outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(self.fake_command.ripped_title_ids, [0])
        self.assertEqual(len(outcome.ripped_files), 1)

    def test_the_job_is_queued_for_transcoding(self):
        outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertEqual(job.state, JobState.STAGED)
        self.assertEqual(job.media_kind, "film")
        self.assertEqual(job.title, "The Matrix")
        self.assertEqual(job.disc_label, "THE_MATRIX")

    def test_a_film_whose_extras_match_each_other_is_still_ripped_as_a_film(self):
        """The Untouchables delivered two featurettes and left the film behind."""
        outcome = self._worker(
            makemkv_fixtures.FILM_WITH_SIMILAR_EXTRAS
        ).rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertEqual(job.media_kind, "film")
        self.assertEqual(job.title, "The Untouchables")
        self.assertEqual(self.fake_command.ripped_title_ids, [0])

    def test_the_disc_is_ejected_as_soon_as_the_rip_is_done(self):
        self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        self.assertTrue(self.drive.has_ejected)

    def test_the_raw_files_land_in_staging(self):
        outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertTrue(job.staged_path.is_dir())
        self.assertTrue(job.staged_path.is_relative_to(self.configuration.staging_root))

    def test_a_split_film_remembers_which_disc_it_was(self):
        outcome = self._worker(makemkv_fixtures.SPLIT_FILM_BLURAY).rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertEqual(job.part_number, 2)
        self.assertTrue(job.is_part_of_split_film)


class DoubleFeatureTests(RipWorkerTestCase):
    """Two films on one disc. Missing one is far worse than ripping one extra."""

    def test_both_films_are_ripped(self):
        outcome = self._worker(makemkv_fixtures.DOUBLE_FEATURE_DVD).rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(self.fake_command.ripped_title_ids, [0, 1])
        self.assertEqual(len(outcome.ripped_files), 2)

    def test_it_says_it_found_more_than_one_film(self):
        self._worker(makemkv_fixtures.DOUBLE_FEATURE_DVD).rip_disc_in_drive()

        self.assertIn("double feature", "\n".join(self.announced_lines))

    def test_main_feature_only_takes_one_of_them(self):
        worker = self._worker(
            makemkv_fixtures.DOUBLE_FEATURE_DVD,
            settings=RipSettings(is_main_feature_only=True),
        )

        outcome = worker.rip_disc_in_drive()

        self.assertEqual(len(outcome.ripped_files), 1)

    def test_a_longer_threshold_can_rule_the_shorter_film_out(self):
        """Both run about 72 minutes, so 75 leaves neither at feature length."""
        worker = self._worker(
            makemkv_fixtures.DOUBLE_FEATURE_DVD,
            settings=RipSettings(minimum_feature_seconds=75 * 60),
        )

        outcome = worker.rip_disc_in_drive()

        self.assertEqual(len(outcome.ripped_files), 1)

    def test_a_single_feature_disc_is_unaffected(self):
        outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        self.assertEqual(self.fake_command.ripped_title_ids, [0])

    def test_a_short_film_still_gets_ripped_when_nothing_reaches_feature_length(self):
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY,
            settings=RipSettings(minimum_feature_seconds=10 * 3600),
        )

        outcome = worker.rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(len(outcome.ripped_files), 1)


class RippingATvDiscTests(RipWorkerTestCase):
    def test_every_episode_is_ripped_and_the_extras_are_left_behind(self):
        outcome = self._worker(makemkv_fixtures.TV_DVD).rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(self.fake_command.ripped_title_ids, [0, 1, 2, 3])

    def test_the_job_is_recorded_as_a_show(self):
        outcome = self._worker(makemkv_fixtures.TV_DVD).rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertEqual(job.media_kind, "show")
        self.assertEqual(job.title, "Firefly")

    def test_a_disc_number_on_a_tv_disc_is_not_treated_as_a_film_part(self):
        outcome = self._worker(makemkv_fixtures.TV_DVD).rip_disc_in_drive()

        self.assertIsNone(self.catalog.job_with_id(outcome.job_id).part_number)

    def test_a_feature_length_pilot_is_ripped_with_the_episodes(self):
        """Firefly opens on an 88 minute episode. Skipping it would shift every
        episode after it up a number and deliver the disc mislabelled."""
        outcome = self._worker(
            makemkv_fixtures.TV_DVD_WITH_LONG_PILOT
        ).rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(self.fake_command.ripped_title_ids, [0, 1, 2])

    def test_the_pilot_is_counted_so_the_next_disc_numbers_from_after_it(self):
        outcome = self._worker(
            makemkv_fixtures.TV_DVD_WITH_LONG_PILOT
        ).rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertEqual(job.episode_count, 3)
        self.assertEqual(job.first_episode_number, 1)

    def test_taking_a_long_title_as_an_episode_is_said_out_loud(self):
        self._worker(makemkv_fixtures.TV_DVD_WITH_LONG_PILOT).rip_disc_in_drive()

        announced_output = "\n".join(self.announced_lines)
        self.assertIn("double-length episode", announced_output)

    def test_a_play_all_title_is_not_ripped_on_top_of_the_episodes(self):
        outcome = self._worker(
            makemkv_fixtures.TV_DVD_WITH_PLAY_ALL
        ).rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(self.fake_command.ripped_title_ids, [1, 2, 3, 4])

    def test_one_bad_episode_does_not_cost_the_rest_of_the_disc(self):
        worker = self._worker(makemkv_fixtures.TV_DVD, unreadable_title_ids=(1,))

        outcome = worker.rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(len(outcome.ripped_files), 3)


class DuplicateDiscTests(RipWorkerTestCase):
    def test_a_disc_already_ripped_is_ejected_untouched(self):
        first_outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        second_worker = self._worker(makemkv_fixtures.FILM_BLURAY)
        second_outcome = second_worker.rip_disc_in_drive()

        self.assertFalse(second_outcome.is_ripped)
        self.assertTrue(second_outcome.is_duplicate)
        self.assertEqual(second_outcome.job_id, first_outcome.job_id)
        self.assertEqual(self.fake_command.ripped_title_ids, [])

    def test_the_duplicate_message_names_the_job_it_already_has(self):
        self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        second_outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        self.assertIn("Already ripped", second_outcome.message)
        self.assertIn("The Matrix", second_outcome.message)

    def test_a_duplicate_is_still_ejected(self):
        self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()
        self.drive.eject_count = 0

        self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        self.assertTrue(self.drive.has_ejected)

    def test_two_different_discs_are_both_ripped(self):
        self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        second_outcome = self._worker(makemkv_fixtures.TV_DVD).rip_disc_in_drive()

        self.assertTrue(second_outcome.is_ripped)

    def test_the_duplicate_message_says_how_to_override_it(self):
        self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        second_outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        self.assertIn("--again", second_outcome.message)


class RippingADiscAgainTests(RipWorkerTestCase):
    """--again is for a first attempt that was not good enough to keep."""

    ALLOW_RERIP = RipSettings(is_rerip_allowed=True)

    def _rip_again(self, scan_output: str = makemkv_fixtures.FILM_BLURAY):
        return self._worker(scan_output, settings=self.ALLOW_RERIP).rip_disc_in_drive()

    def test_a_disc_already_ripped_is_ripped_over_the_top(self):
        self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        second_outcome = self._rip_again()

        self.assertTrue(second_outcome.is_ripped)
        self.assertEqual(self.fake_command.ripped_title_ids, [0])

    def test_the_first_attempt_is_forgotten_rather_than_left_as_a_second_job(self):
        first_outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        second_outcome = self._rip_again()

        self.assertIsNone(self.catalog.job_with_id(first_outcome.job_id))
        self.assertEqual(len(self.catalog.all_jobs()), 1)
        self.assertNotEqual(second_outcome.job_id, first_outcome.job_id)

    def test_the_first_attempts_files_do_not_pile_up_in_the_new_rip(self):
        first_outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()
        stale_file = self.catalog.job_with_id(first_outcome.job_id).staged_path
        (stale_file / "left-over.mkv").write_bytes(b"from the first attempt")

        second_outcome = self._rip_again()

        staged_path = self.catalog.job_with_id(second_outcome.job_id).staged_path
        self.assertFalse((staged_path / "left-over.mkv").exists())

    def test_it_says_which_job_it_is_replacing(self):
        self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()
        self.announced_lines.clear()

        self._rip_again()

        announced_output = "\n".join(self.announced_lines)
        self.assertIn("Replacing #1 The Matrix", announced_output)

    def test_a_job_being_transcoded_right_now_is_left_alone(self):
        first_outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()
        self.catalog.claim_next_for_encoding()

        second_outcome = self._rip_again()

        self.assertFalse(second_outcome.is_ripped)
        self.assertIn("transcoded right now", second_outcome.message)
        self.assertIsNotNone(self.catalog.job_with_id(first_outcome.job_id))

    def test_the_files_of_a_job_being_transcoded_are_not_deleted(self):
        first_outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()
        staged_path = self.catalog.job_with_id(first_outcome.job_id).staged_path
        self.catalog.claim_next_for_encoding()

        self._rip_again()

        self.assertTrue(staged_path.is_dir())

    def test_a_disc_that_has_never_been_seen_is_ripped_as_normal(self):
        outcome = self._rip_again()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(len(self.catalog.all_jobs()), 1)


class ForcingTheKindOfDiscTests(RipWorkerTestCase):
    """For the box set whose label says nothing and whose episodes run long."""

    AS_SHOW = RipSettings(forced_media_kind="show")
    AS_FILM = RipSettings(forced_media_kind="film")

    def test_a_miniseries_is_read_as_a_film_when_nothing_is_forced(self):
        """The disc itself gives no reason to think these are episodes."""
        outcome = self._worker(makemkv_fixtures.MINISERIES_DVD).rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertEqual(job.media_kind, "film")

    def test_forcing_a_show_records_it_as_a_show(self):
        worker = self._worker(makemkv_fixtures.MINISERIES_DVD, settings=self.AS_SHOW)

        outcome = worker.rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertEqual(job.media_kind, "show")

    def test_forcing_a_show_finds_episodes_that_run_past_feature_length(self):
        worker = self._worker(makemkv_fixtures.MINISERIES_DVD, settings=self.AS_SHOW)

        outcome = worker.rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(self.fake_command.ripped_title_ids, [1, 2, 3])

    def test_forcing_a_show_still_leaves_the_play_all_title_behind(self):
        """Being told it is a show is not permission to deliver it twice."""
        worker = self._worker(makemkv_fixtures.MINISERIES_DVD, settings=self.AS_SHOW)

        worker.rip_disc_in_drive()

        self.assertNotIn(0, self.fake_command.ripped_title_ids)

    def test_forcing_a_film_on_a_tv_disc_records_it_as_a_film(self):
        worker = self._worker(makemkv_fixtures.TV_DVD, settings=self.AS_FILM)

        outcome = worker.rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertEqual(job.media_kind, "film")

    def test_episodes_that_are_all_long_are_not_each_called_the_odd_one_out(self):
        """Every episode running past feature length is the disc, not a decision."""
        worker = self._worker(makemkv_fixtures.MINISERIES_DVD, settings=self.AS_SHOW)

        worker.rip_disc_in_drive()

        announced_output = "\n".join(self.announced_lines)
        self.assertNotIn("double-length episode", announced_output)

    def test_being_told_is_said_out_loud_rather_than_hedged(self):
        worker = self._worker(makemkv_fixtures.MINISERIES_DVD, settings=self.AS_SHOW)

        worker.rip_disc_in_drive()

        announced_output = "\n".join(self.announced_lines)
        self.assertIn("Treating this as a TV disc because you said so.", announced_output)
        self.assertNotIn("That is a guess", announced_output)


class NamingTheDiscTests(RipWorkerTestCase):
    """Volume labels are abbreviated, misspelled, or just DVD_VIDEO."""

    def test_a_given_title_is_used_instead_of_the_label(self):
        worker = self._worker(
            makemkv_fixtures.MINISERIES_DVD,
            settings=RipSettings(
                forced_media_kind="show", forced_title="Band of Brothers"
            ),
        )

        outcome = worker.rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertEqual(job.title, "Band of Brothers")

    def test_a_given_title_works_on_a_film_too(self):
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY,
            settings=RipSettings(forced_title="The Matrix"),
        )

        outcome = worker.rip_disc_in_drive()

        self.assertEqual(self.catalog.job_with_id(outcome.job_id).title, "The Matrix")

    def test_a_given_season_is_used_instead_of_the_label(self):
        worker = self._worker(
            makemkv_fixtures.TV_DVD, settings=RipSettings(forced_season_number=4)
        )

        outcome = worker.rip_disc_in_drive()

        self.assertEqual(self.catalog.job_with_id(outcome.job_id).season_number, 4)

    def test_a_given_season_beats_one_the_label_disagrees_with(self):
        """The fixture label says S01, which is the sort of thing that is wrong."""
        worker = self._worker(
            makemkv_fixtures.TV_DVD, settings=RipSettings(forced_season_number=2)
        )

        outcome = worker.rip_disc_in_drive()

        self.assertEqual(self.catalog.job_with_id(outcome.job_id).season_number, 2)

    def test_the_naming_is_said_out_loud(self):
        worker = self._worker(
            makemkv_fixtures.MINISERIES_DVD,
            settings=RipSettings(
                forced_media_kind="show",
                forced_title="Band of Brothers",
                forced_season_number=1,
            ),
        )

        worker.rip_disc_in_drive()

        self.assertIn(
            'Filing it under "Band of Brothers", season 1.', self.announced_lines
        )

    def test_a_season_given_for_a_film_is_reported_rather_than_dropped(self):
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY, settings=RipSettings(forced_season_number=2)
        )

        worker.rip_disc_in_drive()

        announced_output = "\n".join(self.announced_lines)
        self.assertIn("Ignoring --season", announced_output)

    def test_the_next_disc_carries_on_numbering_despite_different_capitals(self):
        """One disc named off its label, the next typed by hand."""
        first = self._worker(makemkv_fixtures.TV_DVD).rip_disc_in_drive()
        self.assertEqual(self.catalog.job_with_id(first.job_id).title, "Firefly")

        second_worker = self._worker(
            makemkv_fixtures.TV_DVD_WITH_LONG_PILOT,
            settings=RipSettings(forced_title="FIREFLY", forced_season_number=1),
        )
        second = second_worker.rip_disc_in_drive()

        job = self.catalog.job_with_id(second.job_id)
        self.assertEqual(job.first_episode_number, 5)


class UnreadableDiscTests(RipWorkerTestCase):
    def test_a_disc_with_no_titles_is_reported_not_ripped(self):
        outcome = self._worker(makemkv_fixtures.UNREADABLE_DISC).rip_disc_in_drive()

        self.assertFalse(outcome.is_ripped)
        self.assertIsNone(outcome.job_id)
        self.assertIn("No titles found", outcome.message)

    def test_makemkv_messages_are_passed_on_so_the_failure_can_be_read(self):
        self._worker(makemkv_fixtures.UNREADABLE_DISC).rip_disc_in_drive()

        self.assertIn("  Failed to open disc", self.announced_lines)

    def test_an_expired_key_is_named_rather_than_blamed_on_the_drive(self):
        """The key expiring looks exactly like an empty drive from here."""
        outcome = self._worker(makemkv_fixtures.EXPIRED_KEY_SCAN).rip_disc_in_drive()

        self.assertFalse(outcome.is_ripped)
        self.assertIn("key has expired", outcome.message)
        self.assertNotIn("drive may be empty", outcome.message)

    def test_a_disc_where_every_title_fails_records_no_job(self):
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY, unreadable_title_ids=(0, 1, 2, 3)
        )

        outcome = worker.rip_disc_in_drive()

        self.assertFalse(outcome.is_ripped)
        self.assertEqual(self.catalog.all_jobs(), [])
        self.assertIn("no usable files", outcome.message)

    def test_what_makemkv_said_about_a_refusal_is_passed_on(self):
        """The failure message promises the reason is above it, so it has to be."""
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY,
            unreadable_title_ids=(0,),
            rip_output=makemkv_fixtures.REFUSED_TITLE_OUTPUT,
        )

        worker.rip_disc_in_drive()

        self.assertIn(
            "    Failed to save title 0 to file title_t00.mkv", self.announced_lines
        )

    def test_an_expired_key_says_so_rather_than_just_would_not_decrypt(self):
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY,
            unreadable_title_ids=(0,),
            rip_output=makemkv_fixtures.EXPIRED_KEY_OUTPUT,
        )

        worker.rip_disc_in_drive()

        announced_output = "\n".join(self.announced_lines)
        self.assertIn("evaluation period has expired", announced_output)

    def test_a_drive_that_stopped_answering_is_named_as_the_cause(self):
        """The old message blamed a key or an unreadable disc, neither of which
        is what happens when the drive drops off the bus mid-rip."""
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY,
            unreadable_title_ids=(0,),
            rip_output=makemkv_fixtures.DRIVE_DROPOUT_OUTPUT,
        )

        outcome = worker.rip_disc_in_drive()

        self.assertIn("stopped answering", outcome.message)
        self.assertNotIn("expired key", outcome.message)

    def test_a_damaged_disc_is_named_as_the_cause(self):
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY,
            unreadable_title_ids=(0,),
            rip_output=makemkv_fixtures.DAMAGED_DISC_OUTPUT,
        )

        outcome = worker.rip_disc_in_drive()

        self.assertIn("damaged patch", outcome.message)

    def test_a_failure_nobody_recognises_still_points_at_the_messages(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY, unreadable_title_ids=(0,))

        outcome = worker.rip_disc_in_drive()

        self.assertIn("messages are above", outcome.message)

    def test_a_failed_rip_leaves_nothing_behind_in_staging(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY, unreadable_title_ids=(0,))

        worker.rip_disc_in_drive()

        staged_files = list(self.configuration.staging_root.rglob("*.mkv"))
        self.assertEqual(staged_files, [])


class NotEnoughRoomTests(RipWorkerTestCase):
    def test_a_disc_is_not_ripped_when_staging_is_full(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY)
        full_disk = VolumeSpace(total_bytes=1024**4, free_bytes=0)

        with mock.patch("media_server.rip_worker.space_at", return_value=full_disk):
            outcome = worker.rip_disc_in_drive()

        self.assertFalse(outcome.is_ripped)
        self.assertIn("Not enough room", outcome.message)
        self.assertEqual(self.fake_command.ripped_title_ids, [])

    def test_the_shortfall_is_explained_before_anything_spins(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY)
        full_disk = VolumeSpace(total_bytes=1024**4, free_bytes=0)

        with mock.patch("media_server.rip_worker.space_at", return_value=full_disk):
            worker.rip_disc_in_drive()

        announced_output = "\n".join(self.announced_lines)
        self.assertIn("needs about 37 GB", announced_output)
        self.assertIn("free  0.0 GB in staging", announced_output)


class TryingHarderTests(RipWorkerTestCase):
    """--try-harder copies the whole disc and reads the titles out of the copy."""

    TRYING_HARDER = RipSettings(is_trying_harder=True)

    def test_the_disc_is_copied_before_anything_is_ripped(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY, settings=self.TRYING_HARDER)

        outcome = worker.rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(len(self.fake_command.copied_destinations), 1)

    def test_the_titles_come_out_of_the_copy_rather_than_the_drive(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY, settings=self.TRYING_HARDER)

        worker.rip_disc_in_drive()

        self.assertTrue(
            all(source.startswith("file:") for source in self.fake_command.ripped_sources)
        )

    def test_the_disc_is_still_identified_from_the_drive(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY, settings=self.TRYING_HARDER)

        outcome = worker.rip_disc_in_drive()

        job = self.catalog.job_with_id(outcome.job_id)
        self.assertEqual(job.title, "The Matrix")
        self.assertEqual(self.fake_command.scanned_sources[0], "disc:0")

    def test_the_copy_is_deleted_once_the_titles_are_out_of_it(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY, settings=self.TRYING_HARDER)

        worker.rip_disc_in_drive()

        copy_path = self.fake_command.copied_destinations[0]
        self.assertFalse(copy_path.exists())

    def test_the_copy_is_deleted_even_when_no_title_would_rip(self):
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY,
            unreadable_title_ids=(0,),
            settings=self.TRYING_HARDER,
        )

        outcome = worker.rip_disc_in_drive()

        self.assertFalse(outcome.is_ripped)
        self.assertFalse(self.fake_command.copied_destinations[0].exists())

    def test_a_refused_copy_is_reported_rather_than_ripped_around(self):
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY,
            settings=self.TRYING_HARDER,
            is_copy_refused=True,
            copy_output=makemkv_fixtures.DRIVE_DROPOUT_OUTPUT,
        )

        outcome = worker.rip_disc_in_drive()

        self.assertFalse(outcome.is_ripped)
        self.assertIn("Could not copy the disc", outcome.message)
        self.assertIn("stopped answering", outcome.message)
        self.assertEqual(self.fake_command.ripped_title_ids, [])

    def test_an_ordinary_rip_never_copies_the_disc(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY)

        worker.rip_disc_in_drive()

        self.assertEqual(self.fake_command.copied_destinations, [])
        self.assertEqual(self.fake_command.ripped_sources, ["disc:0"])


class MultiAngleDiscTests(RipWorkerTestCase):
    """A disc that lists its angles as titles holds one film, not several.

    The Star Wars box set shelved A New Hope three times, and Plex stacked the
    parts into a six hour film that played through and started again.
    """

    def test_the_film_is_taken_once(self):
        outcome = self._worker(makemkv_fixtures.MULTI_ANGLE_DVD).rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(self.fake_command.ripped_title_ids, [0])
        self.assertEqual(len(outcome.ripped_files), 1)

    def test_it_says_why_it_took_only_one(self):
        self._worker(makemkv_fixtures.MULTI_ANGLE_DVD).rip_disc_in_drive()

        announced_output = "\n".join(self.announced_lines)
        self.assertIn("same film more than once", announced_output)

    def test_a_real_double_feature_still_gets_both_films(self):
        """The guard against repeats must not cost the case it sits next to."""
        outcome = self._worker(
            makemkv_fixtures.DOUBLE_FEATURE_DVD
        ).rip_disc_in_drive()

        self.assertEqual(self.fake_command.ripped_title_ids, [0, 1])
        self.assertEqual(len(outcome.ripped_files), 2)


class RepeatedFeatureTests(unittest.TestCase):
    """Repeats match on the exact second and the exact byte, as angles do."""

    def _title(self, title_id: int, length_seconds: int, size_bytes: int) -> DiscTitle:
        return DiscTitle(
            title_id=title_id, length_seconds=length_seconds, size_bytes=size_bytes
        )

    def test_every_copy_beyond_the_first_is_dropped(self):
        angles = [self._title(index, 7449, 7301444403) for index in range(3)]

        kept = without_repeated_features(angles)

        self.assertEqual([title.title_id for title in kept], [0])

    def test_same_length_but_a_different_size_keeps_both(self):
        """This used to keep the larger, which threw films away.

        A commentary cut runs the film's exact length and is bigger for the
        extra audio track, so "keep the largest" reliably kept the commentary
        and dropped the film. Nothing in a scan tells the two apart, so the
        sorting is left to whoever watches them.
        """
        titles = [
            self._title(0, 7449, 7_000_000_000),
            self._title(1, 7449, 7_301_444_403),
        ]

        kept = without_repeated_features(titles)

        self.assertEqual([title.title_id for title in kept], [0, 1])

    def test_titles_of_different_lengths_are_all_kept_in_disc_order(self):
        titles = [
            self._title(0, 4324, 3_000_000_000),
            self._title(1, 4360, 3_100_000_000),
        ]

        kept = without_repeated_features(titles)

        self.assertEqual([title.title_id for title in kept], [0, 1])


class FolderNamingTests(unittest.TestCase):
    def test_characters_that_break_folders_and_smb_shares_are_removed(self):
        cases = [
            ("THE_MATRIX", "THE_MATRIX"),
            ("Alien: Resurrection", "Alien Resurrection"),
            ("AC/DC Live", "AC DC Live"),
            ("  spaced  out  ", "spaced out"),
            ("trailing.", "trailing"),
            # What a label mastered in another encoding decodes to.
            ("CAF\ufffd SOCIETY", "CAF SOCIETY"),
            ("BROKEN\ufffd", "BROKEN"),
        ]

        for disc_label, expected_name in cases:
            with self.subTest(disc_label=disc_label):
                self.assertEqual(safe_file_component(disc_label), expected_name)


class CommentaryCutTests(RipWorkerTestCase):
    """A commentary cut runs the film's exact length, so both get ripped."""

    def test_both_the_film_and_its_commentary_cut_are_taken(self):
        """Nothing in a scan says which is which, so the choice is not made here."""
        worker = self._worker(makemkv_fixtures.FILM_WITH_COMMENTARY_CUT)

        outcome = worker.rip_disc_in_drive()

        self.assertEqual(len(outcome.ripped_files), 2)

    def test_the_longer_file_does_not_displace_the_film(self):
        """Keeping the larger of two same-length titles kept the commentary."""
        worker = self._worker(makemkv_fixtures.FILM_WITH_COMMENTARY_CUT)

        worker.rip_disc_in_drive()

        self.assertEqual(self.fake_command.ripped_title_ids, [0, 1])

    def test_a_title_matching_to_the_byte_is_still_only_taken_once(self):
        """The multi-angle disc must keep working: it matches on size as well."""
        worker = self._worker(makemkv_fixtures.MULTI_ANGLE_DVD)

        outcome = worker.rip_disc_in_drive()

        self.assertEqual(len(outcome.ripped_files), 1)


class UnfinishedCopyTests(RipWorkerTestCase):
    """A copy MakeMKV calls "done" while missing files is not worth ripping."""

    TRYING_HARDER = RipSettings(is_trying_harder=True)

    def _rip_with_part_copied_disc(self):
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY,
            settings=self.TRYING_HARDER,
            copy_output=makemkv_fixtures.UNFINISHED_COPY_OUTPUT,
        )
        return worker.rip_disc_in_drive()

    def test_nothing_is_ripped_out_of_a_copy_that_lost_a_file(self):
        """The film is what goes missing, so what is left would pass for a disc."""
        outcome = self._rip_with_part_copied_disc()

        self.assertFalse(outcome.is_ripped)
        self.assertIsNone(outcome.job_id)

    def test_the_reason_names_the_missing_files_rather_than_the_copy_failing(self):
        outcome = self._rip_with_part_copied_disc()

        self.assertIn("missing files", outcome.message)
        self.assertIn("rescue-disc.sh", outcome.message)

    def test_the_part_copy_is_deleted_rather_than_left_to_be_found(self):
        self._rip_with_part_copied_disc()

        self.assertFalse(self.fake_command.copied_destinations[0].exists())

    def test_a_copy_that_never_happened_is_not_described_as_part_copied(self):
        """Both end the rip, but only one leaves something that looks usable.

        A copy that failed outright also says "Failed to save title", so the
        two read alike unless the difference is looked for deliberately.
        """
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY,
            settings=self.TRYING_HARDER,
            is_copy_refused=True,
            copy_output=makemkv_fixtures.DRIVE_DROPOUT_OUTPUT,
        )

        outcome = worker.rip_disc_in_drive()

        self.assertIn("Could not copy the disc", outcome.message)
        self.assertNotIn("missing files", outcome.message)


class StaleStagingTests(RipWorkerTestCase):
    """What an unfinished rip leaves behind must not reach the encoder.

    Staging is named after the disc, so every attempt at one disc lands in the
    same folder, and the encoder transcodes everything it finds there.
    """

    def _wreckage_no_job_knows_about(self) -> Path:
        """Leave a truncated file behind with nothing recorded against it.

        This is what a rip that died with the drive leaves: files on disk and
        no job. --again cannot help, because clearing staging that way needs a
        job to clear it for, and an attempt that never finished has none.
        """
        outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()
        staged_path = next((self.configuration.staging_root / "raw").iterdir())
        truncated = staged_path / "title-04" / "Leftover_t04.mkv"
        truncated.parent.mkdir(parents=True, exist_ok=True)
        truncated.write_bytes(b"half a file from a drive that went away")
        self.catalog.forget_job(outcome.job_id)
        return truncated

    def test_a_file_from_an_earlier_attempt_is_cleared_before_ripping(self):
        truncated = self._wreckage_no_job_knows_about()

        outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()

        self.assertTrue(outcome.is_ripped)
        self.assertFalse(truncated.exists())

    def test_the_rip_that_just_happened_is_the_only_thing_staged(self):
        """What the encoder transcodes is everything in the folder, not a list."""
        self._wreckage_no_job_knows_about()

        outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_in_drive()
        staged_path = next((self.configuration.staging_root / "raw").iterdir())

        self.assertEqual(
            sorted(staged_path.rglob("*.mkv")), sorted(outcome.ripped_files)
        )


class RipFromImageTests(RipWorkerTestCase):
    """--from-image reads a rescued image and queues it like any other disc."""

    def _image_file(self) -> Path:
        image_file = self.configuration.staging_root / "images" / "Fury Road.iso"
        image_file.parent.mkdir(parents=True, exist_ok=True)
        image_file.write_bytes(b"not really an image, but it is a file")
        return image_file

    def test_everything_is_read_from_the_image_and_not_the_drive(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY)
        image_file = self._image_file()

        worker.rip_disc_image(image_file)

        asked = self.fake_command.scanned_sources + self.fake_command.ripped_sources
        self.assertTrue(asked)
        self.assertTrue(all(source == f"iso:{image_file}" for source in asked))

    def test_the_image_reaches_the_encode_queue_like_any_other_disc(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY)

        outcome = worker.rip_disc_image(self._image_file())

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(self.catalog.job_with_id(outcome.job_id).title, "The Matrix")

    def test_the_drive_is_left_shut(self):
        """Whatever is in the drive is not what was read, so it stays put."""
        worker = self._worker(makemkv_fixtures.FILM_BLURAY)

        outcome = worker.rip_disc_image(self._image_file())

        self.assertTrue(outcome.is_ripped)
        self.assertFalse(self.drive.has_ejected)
        self.assertNotIn("The disc is out", outcome.message)

    def test_a_disc_already_ripped_does_not_eject_the_one_in_the_drive(self):
        """The duplicate path ejects too, and has to be just as careful."""
        worker = self._worker(makemkv_fixtures.FILM_BLURAY)
        image_file = self._image_file()
        worker.rip_disc_image(image_file)

        outcome = self._worker(makemkv_fixtures.FILM_BLURAY).rip_disc_image(image_file)

        self.assertTrue(outcome.is_duplicate)
        self.assertFalse(self.drive.has_ejected)

    def test_an_image_that_is_not_there_is_said_plainly(self):
        worker = self._worker(makemkv_fixtures.FILM_BLURAY)
        missing = self.configuration.staging_root / "images" / "Nothing.iso"

        outcome = worker.rip_disc_image(missing)

        self.assertFalse(outcome.is_ripped)
        self.assertIn(str(missing), outcome.message)
        self.assertEqual(self.fake_command.scanned_sources, [])

    def test_a_title_lost_to_a_hole_is_reported_and_the_rest_still_comes(self):
        """A rescued image has gaps, so some titles will refuse. Say which."""
        worker = self._worker(makemkv_fixtures.TV_DVD, unreadable_title_ids=(1,))

        outcome = worker.rip_disc_image(self._image_file())

        self.assertTrue(outcome.is_ripped)
        self.assertIn(
            "title 1 would not decrypt, skipping it",
            "\n".join(self.announced_lines),
        )


if __name__ == "__main__":
    unittest.main()
