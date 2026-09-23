import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from media_server.configuration import Configuration
from media_server.job_catalog import JobCatalog, JobState, RippedDisc
from media_server.library_delivery import PARTIAL_SUFFIX, LibraryDelivery


class LibraryDeliveryTestCase(unittest.TestCase):
    """Shared staging and library setup. Holds no tests of its own."""

    DISC_LABEL = "THE_MATRIX"
    RELATIVE_MOVIE_PATH = Path("Movies/The Matrix (1999)/The Matrix (1999).mkv")
    MOVIE_CONTENTS = b"pretend this is an encoded film"

    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary_root = Path(temporary_directory.name)

        self.staging_root = temporary_root / "staging"
        self.library_root = temporary_root / "library"
        self.staging_root.mkdir()
        self.library_root.mkdir()

        self.configuration = Configuration(
            staging_root=self.staging_root, library_root=self.library_root
        )
        self.catalog = JobCatalog(self.configuration.catalog_path)
        self.addCleanup(self.catalog.close)
        self.delivery = LibraryDelivery(self.configuration, self.catalog)

    def _stage_encoded_job(self) -> int:
        """A job whose rip and transcode are both finished and on disk."""
        raw_path = self.staging_root / "raw" / self.DISC_LABEL
        raw_path.mkdir(parents=True)
        (raw_path / "title00.mkv").write_bytes(b"pretend this is a raw rip")

        encoded_path = self.staging_root / "encoded" / self.DISC_LABEL
        movie_file = encoded_path / self.RELATIVE_MOVIE_PATH
        movie_file.parent.mkdir(parents=True)
        movie_file.write_bytes(self.MOVIE_CONTENTS)

        job_id = self.catalog.record_ripped_disc(
            RippedDisc(
                disc_label=self.DISC_LABEL,
                media_kind="film",
                staged_path=raw_path,
                title="The Matrix",
                year="1999",
            )
        )
        self.catalog.mark_encoded(job_id, encoded_path)
        return job_id


class SuccessfulDeliveryTests(LibraryDeliveryTestCase):
    def test_finished_files_land_in_the_library_with_their_layout_intact(self):
        self._stage_encoded_job()

        report = self.delivery.deliver_waiting_jobs()

        delivered_file = self.library_root / self.RELATIVE_MOVIE_PATH
        self.assertTrue(report.has_deliveries)
        self.assertTrue(delivered_file.is_file())
        self.assertEqual(delivered_file.read_bytes(), self.MOVIE_CONTENTS)

    def test_staged_copies_are_cleared_once_the_library_has_them(self):
        job_id = self._stage_encoded_job()
        job = self.catalog.job_with_id(job_id)

        self.delivery.deliver_waiting_jobs()

        self.assertFalse(job.staged_path.exists())
        self.assertFalse(job.encoded_path.exists())

    def test_a_delivered_job_is_recorded_as_done(self):
        job_id = self._stage_encoded_job()

        self.delivery.deliver_waiting_jobs()

        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.DELIVERED)
        self.assertEqual(self.catalog.jobs_awaiting_delivery(), [])

    def test_no_partial_files_are_left_behind(self):
        self._stage_encoded_job()

        self.delivery.deliver_waiting_jobs()

        leftover_partials = list(self.library_root.rglob(f"*{PARTIAL_SUFFIX}"))
        self.assertEqual(leftover_partials, [])


class UnavailableLibraryTests(LibraryDeliveryTestCase):
    def test_jobs_are_held_when_the_drive_is_not_mounted(self):
        job_id = self._stage_encoded_job()
        self.configuration.library_root = self.library_root / "not-mounted"

        report = self.delivery.deliver_waiting_jobs()

        self.assertFalse(report.is_library_available)
        self.assertEqual([held.job_id for held in report.held_jobs], [job_id])

    def test_a_held_job_keeps_its_files_and_waits_for_the_next_pass(self):
        job_id = self._stage_encoded_job()
        job = self.catalog.job_with_id(job_id)
        self.configuration.library_root = self.library_root / "not-mounted"

        self.delivery.deliver_waiting_jobs()

        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.ENCODED)
        self.assertTrue(job.staged_path.exists())
        self.assertTrue(job.encoded_path.exists())

    def test_a_remounted_drive_delivers_the_backlog(self):
        self._stage_encoded_job()
        self.configuration.library_root = self.library_root / "not-mounted"
        self.delivery.deliver_waiting_jobs()

        self.configuration.library_root = self.library_root
        report = self.delivery.deliver_waiting_jobs()

        self.assertTrue(report.has_deliveries)
        self.assertTrue((self.library_root / self.RELATIVE_MOVIE_PATH).is_file())


class InterruptedDeliveryTests(LibraryDeliveryTestCase):
    def test_a_copy_that_fails_leaves_the_library_clean_and_the_job_waiting(self):
        job_id = self._stage_encoded_job()

        with mock.patch.object(shutil, "copy2", side_effect=OSError("drive vanished")):
            report = self.delivery.deliver_waiting_jobs()

        self.assertEqual([held.job_id for held in report.held_jobs], [job_id])
        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.ENCODED)
        self.assertFalse((self.library_root / self.RELATIVE_MOVIE_PATH).exists())
        self.assertEqual(list(self.library_root.rglob(f"*{PARTIAL_SUFFIX}")), [])

    def test_a_job_is_held_when_the_library_is_too_full(self):
        job_id = self._stage_encoded_job()
        full_drive = mock.Mock(free=0)

        with mock.patch.object(shutil, "disk_usage", return_value=full_drive):
            report = self.delivery.deliver_waiting_jobs()

        self.assertEqual([held.job_id for held in report.held_jobs], [job_id])
        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.ENCODED)

    def test_abandoned_partial_files_are_cleaned_up(self):
        abandoned_partial = (
            self.library_root / "Movies" / f"Half A Film.mkv{PARTIAL_SUFFIX}"
        )
        abandoned_partial.parent.mkdir(parents=True)
        abandoned_partial.write_bytes(b"half a film")

        removed_files = self.delivery.remove_abandoned_partial_files()

        self.assertEqual(removed_files, [abandoned_partial])
        self.assertFalse(abandoned_partial.exists())


class MissingOutputTests(LibraryDeliveryTestCase):
    def test_a_job_whose_encoded_output_vanished_is_marked_failed(self):
        job_id = self._stage_encoded_job()
        shutil.rmtree(self.catalog.job_with_id(job_id).encoded_path)

        self.delivery.deliver_waiting_jobs()

        job = self.catalog.job_with_id(job_id)
        self.assertEqual(job.state, JobState.FAILED)
        self.assertIn("missing", job.failure_reason)

    def test_a_job_with_an_empty_output_directory_is_marked_failed(self):
        job_id = self._stage_encoded_job()
        encoded_path = self.catalog.job_with_id(job_id).encoded_path
        shutil.rmtree(encoded_path)
        encoded_path.mkdir(parents=True)

        self.delivery.deliver_waiting_jobs()

        job = self.catalog.job_with_id(job_id)
        self.assertEqual(job.state, JobState.FAILED)
        self.assertIn("empty", job.failure_reason)


if __name__ == "__main__":
    unittest.main()
