import tempfile
import unittest
from pathlib import Path
from unittest import mock

from media_server.configuration import Configuration
from media_server.job_catalog import JobCatalog, JobState
from media_server.makemkv import MakeMkv
from media_server.file_names import safe_file_component
from media_server.rip_worker import RipWorker
from media_server.volume_space import VolumeSpace
from tests import makemkv_fixtures
from tests.fake_makemkv import FakeMakeMkvCommand, RecordingDiscDrive


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

    def _worker(
        self, scan_output: str, unreadable_title_ids: tuple[int, ...] = ()
    ) -> RipWorker:
        self.fake_command = FakeMakeMkvCommand(scan_output, unreadable_title_ids)
        return RipWorker(
            configuration=self.configuration,
            catalog=self.catalog,
            makemkv=MakeMkv(run_command=self.fake_command),
            drive=self.drive,
            announce=self.announced_lines.append,
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


class UnreadableDiscTests(RipWorkerTestCase):
    def test_a_disc_with_no_titles_is_reported_not_ripped(self):
        outcome = self._worker(makemkv_fixtures.UNREADABLE_DISC).rip_disc_in_drive()

        self.assertFalse(outcome.is_ripped)
        self.assertIsNone(outcome.job_id)
        self.assertIn("No titles found", outcome.message)

    def test_makemkv_messages_are_passed_on_so_the_failure_can_be_read(self):
        self._worker(makemkv_fixtures.UNREADABLE_DISC).rip_disc_in_drive()

        self.assertIn("  Failed to open disc", self.announced_lines)

    def test_a_disc_where_every_title_fails_records_no_job(self):
        worker = self._worker(
            makemkv_fixtures.FILM_BLURAY, unreadable_title_ids=(0, 1, 2, 3)
        )

        outcome = worker.rip_disc_in_drive()

        self.assertFalse(outcome.is_ripped)
        self.assertEqual(self.catalog.all_jobs(), [])
        self.assertIn("no usable files", outcome.message)

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


class FolderNamingTests(unittest.TestCase):
    def test_characters_that_break_folders_and_smb_shares_are_removed(self):
        cases = [
            ("THE_MATRIX", "THE_MATRIX"),
            ("Alien: Resurrection", "Alien Resurrection"),
            ("AC/DC Live", "AC DC Live"),
            ("  spaced  out  ", "spaced out"),
            ("trailing.", "trailing"),
        ]

        for disc_label, expected_name in cases:
            with self.subTest(disc_label=disc_label):
                self.assertEqual(safe_file_component(disc_label), expected_name)


if __name__ == "__main__":
    unittest.main()
