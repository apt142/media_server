import tempfile
import unittest
from pathlib import Path

from media_server.job_catalog import JobCatalog, JobState, RippedDisc


class JobCatalogTestCase(unittest.TestCase):
    """Shared catalog setup. Holds no tests of its own."""

    DISC_LABEL = "THE_MATRIX"
    MEDIA_KIND = "film"
    TITLE = "The Matrix"
    YEAR = "1999"

    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.staging_root = Path(temporary_directory.name)
        self.catalog = JobCatalog(self.staging_root / "catalog.sqlite3")
        self.addCleanup(self.catalog.close)

    def _record_disc(
        self,
        disc_label: str = "",
        part_number: int | None = None,
        disc_fingerprint: str = "",
    ) -> int:
        label = disc_label or self.DISC_LABEL
        return self.catalog.record_ripped_disc(
            RippedDisc(
                disc_label=label,
                media_kind=self.MEDIA_KIND,
                staged_path=self.staging_root / label,
                title=self.TITLE,
                year=self.YEAR,
                part_number=part_number,
                disc_fingerprint=disc_fingerprint,
            )
        )


class RecordingDiscsTests(JobCatalogTestCase):
    def test_a_ripped_disc_starts_out_staged(self):
        job_id = self._record_disc()

        job = self.catalog.job_with_id(job_id)

        self.assertEqual(job.state, JobState.STAGED)
        self.assertEqual(job.disc_label, self.DISC_LABEL)
        self.assertEqual(job.title, self.TITLE)
        self.assertEqual(job.year, self.YEAR)
        self.assertEqual(job.attempt_count, 0)

    def test_a_disc_needing_review_is_held_out_of_the_queue(self):
        job_id = self.catalog.record_ripped_disc(
            RippedDisc(
                disc_label="UNKNOWN_DISC",
                media_kind=self.MEDIA_KIND,
                staged_path=self.staging_root / "UNKNOWN_DISC",
                is_needing_review=True,
            )
        )

        self.assertEqual(
            self.catalog.job_with_id(job_id).state, JobState.NEEDS_REVIEW
        )
        self.assertIsNone(self.catalog.claim_next_for_encoding())

    def test_an_unknown_job_id_reads_as_nothing(self):
        self.assertIsNone(self.catalog.job_with_id(404))

    def test_a_split_film_remembers_which_disc_it_came_from(self):
        job_id = self._record_disc(disc_label="FELLOWSHIP_D2", part_number=2)

        job = self.catalog.job_with_id(job_id)

        self.assertEqual(job.part_number, 2)
        self.assertTrue(job.is_part_of_split_film)


class ClaimingWorkTests(JobCatalogTestCase):
    def test_claiming_takes_the_oldest_staged_disc(self):
        first_job_id = self._record_disc(disc_label="FIRST_DISC")
        self._record_disc(disc_label="SECOND_DISC")

        claimed_job = self.catalog.claim_next_for_encoding()

        self.assertEqual(claimed_job.job_id, first_job_id)
        self.assertEqual(claimed_job.state, JobState.ENCODING)
        self.assertEqual(claimed_job.attempt_count, 1)

    def test_a_claimed_disc_is_not_handed_out_twice(self):
        self._record_disc(disc_label="FIRST_DISC")
        self._record_disc(disc_label="SECOND_DISC")

        first_claim = self.catalog.claim_next_for_encoding()
        second_claim = self.catalog.claim_next_for_encoding()

        self.assertNotEqual(first_claim.job_id, second_claim.job_id)

    def test_claiming_an_empty_queue_returns_nothing(self):
        self.assertIsNone(self.catalog.claim_next_for_encoding())

    def test_returning_a_job_puts_it_back_in_the_queue(self):
        job_id = self._record_disc()
        self.catalog.claim_next_for_encoding()

        self.catalog.return_to_staged(job_id)

        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.STAGED)
        self.assertEqual(self.catalog.claim_next_for_encoding().job_id, job_id)


class JobProgressTests(JobCatalogTestCase):
    ENCODED_DIRECTORY_NAME = "encoded"

    def test_a_finished_transcode_waits_for_delivery(self):
        job_id = self._record_disc()
        encoded_path = self.staging_root / self.ENCODED_DIRECTORY_NAME

        self.catalog.mark_encoded(job_id, encoded_path)

        job = self.catalog.job_with_id(job_id)
        self.assertEqual(job.state, JobState.ENCODED)
        self.assertEqual(job.encoded_path, encoded_path)
        self.assertEqual(
            [waiting.job_id for waiting in self.catalog.jobs_awaiting_delivery()],
            [job_id],
        )

    def test_a_delivered_job_stops_waiting(self):
        job_id = self._record_disc()
        self.catalog.mark_encoded(job_id, self.staging_root / "encoded")

        self.catalog.mark_delivered(job_id)

        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.DELIVERED)
        self.assertEqual(self.catalog.jobs_awaiting_delivery(), [])

    def test_a_failed_job_keeps_the_reason(self):
        job_id = self._record_disc()

        self.catalog.mark_failed(job_id, "MakeMKV produced no output")

        job = self.catalog.job_with_id(job_id)
        self.assertEqual(job.state, JobState.FAILED)
        self.assertEqual(job.failure_reason, "MakeMKV produced no output")

    def test_counts_report_the_depth_of_each_state(self):
        self._record_disc(disc_label="FIRST_DISC")
        self._record_disc(disc_label="SECOND_DISC")
        claimed_job = self.catalog.claim_next_for_encoding()
        self.catalog.mark_encoded(claimed_job.job_id, self.staging_root / "encoded")

        counts = self.catalog.count_by_state()

        self.assertEqual(counts[JobState.STAGED], 1)
        self.assertEqual(counts[JobState.ENCODED], 1)
        self.assertNotIn(JobState.FAILED, counts)


class JobDescriptionTests(JobCatalogTestCase):
    def test_describes_a_film_with_its_year(self):
        job_id = self._record_disc()

        self.assertEqual(
            self.catalog.job_with_id(job_id).describe(), "The Matrix (1999) [staged]"
        )

    def test_describes_which_part_a_split_film_is(self):
        job_id = self._record_disc(disc_label="FELLOWSHIP_D2", part_number=2)

        self.assertEqual(
            self.catalog.job_with_id(job_id).describe(),
            "The Matrix (1999) part 2 [staged]",
        )

    def test_falls_back_to_the_disc_label_when_there_is_no_title(self):
        job_id = self.catalog.record_ripped_disc(
            RippedDisc(
                disc_label="UNKNOWN_DISC",
                media_kind=self.MEDIA_KIND,
                staged_path=self.staging_root / "UNKNOWN_DISC",
            )
        )

        self.assertEqual(
            self.catalog.job_with_id(job_id).describe(), "UNKNOWN_DISC [staged]"
        )


class DuplicateDiscTests(JobCatalogTestCase):
    DISC_FINGERPRINT = "a1b2c3d4e5f60718"
    OTHER_FINGERPRINT = "0918273645aabbcc"

    def test_a_disc_that_has_been_ripped_is_recognised_again(self):
        self._record_disc(disc_fingerprint=self.DISC_FINGERPRINT)

        self.assertTrue(self.catalog.is_duplicate_disc(self.DISC_FINGERPRINT))

    def test_a_disc_that_has_never_been_seen_is_not_a_duplicate(self):
        self._record_disc(disc_fingerprint=self.DISC_FINGERPRINT)

        self.assertFalse(self.catalog.is_duplicate_disc(self.OTHER_FINGERPRINT))

    def test_a_disc_stays_a_duplicate_after_it_has_been_delivered(self):
        job_id = self._record_disc(disc_fingerprint=self.DISC_FINGERPRINT)
        self.catalog.mark_encoded(job_id, self.staging_root / "encoded")
        self.catalog.mark_delivered(job_id)

        self.assertTrue(self.catalog.is_duplicate_disc(self.DISC_FINGERPRINT))

    def test_a_failed_disc_can_be_ripped_again(self):
        job_id = self._record_disc(disc_fingerprint=self.DISC_FINGERPRINT)
        self.catalog.mark_failed(job_id, "MakeMKV produced no output")

        self.assertFalse(self.catalog.is_duplicate_disc(self.DISC_FINGERPRINT))

    def test_a_disc_with_no_fingerprint_is_never_treated_as_a_duplicate(self):
        self._record_disc(disc_fingerprint="")

        self.assertFalse(self.catalog.is_duplicate_disc(""))

    def test_the_matching_job_is_reported_so_it_can_be_named(self):
        job_id = self._record_disc(disc_fingerprint=self.DISC_FINGERPRINT)

        existing_job = self.catalog.existing_job_for_disc(self.DISC_FINGERPRINT)

        self.assertEqual(existing_job.job_id, job_id)
        self.assertEqual(existing_job.title, self.TITLE)

    def test_forgetting_a_job_lets_its_disc_be_ripped_again(self):
        job_id = self._record_disc(disc_fingerprint=self.DISC_FINGERPRINT)

        self.assertTrue(self.catalog.forget_job(job_id))

        self.assertFalse(self.catalog.is_duplicate_disc(self.DISC_FINGERPRINT))
        self.assertIsNone(self.catalog.job_with_id(job_id))

    def test_forgetting_a_job_that_is_not_there_reports_nothing_was_dropped(self):
        self.assertFalse(self.catalog.forget_job(404))


class BacklogSizeTests(JobCatalogTestCase):
    RAW_RIP_BYTES = 100
    ENCODED_BYTES = 25

    def setUp(self):
        super().setUp()
        self.raw_path = self.staging_root / "raw"
        self.raw_path.mkdir()
        (self.raw_path / "title00.mkv").write_bytes(b"a" * self.RAW_RIP_BYTES)

        self.encoded_path = self.staging_root / "encoded"
        self.encoded_path.mkdir()
        (self.encoded_path / "film.mkv").write_bytes(b"b" * self.ENCODED_BYTES)

    def test_backlog_counts_the_raw_rip_and_the_encoded_copy_together(self):
        job_id = self.catalog.record_ripped_disc(
            RippedDisc("DISC", "film", self.raw_path)
        )
        self.catalog.mark_encoded(job_id, self.encoded_path)

        self.assertEqual(
            self.catalog.staged_bytes(), self.RAW_RIP_BYTES + self.ENCODED_BYTES
        )

    def test_a_delivered_job_stops_counting_against_the_backlog(self):
        job_id = self.catalog.record_ripped_disc(
            RippedDisc("DISC", "film", self.raw_path)
        )
        self.catalog.mark_encoded(job_id, self.encoded_path)

        self.catalog.mark_delivered(job_id)

        self.assertEqual(self.catalog.staged_bytes(), 0)


class CatalogPersistenceTests(JobCatalogTestCase):
    def test_jobs_survive_reopening_the_catalog(self):
        job_id = self._record_disc()
        catalog_path = self.catalog.catalog_path
        self.catalog.close()

        with JobCatalog(catalog_path) as reopened_catalog:
            self.assertEqual(
                reopened_catalog.job_with_id(job_id).disc_label, self.DISC_LABEL
            )


if __name__ == "__main__":
    unittest.main()
