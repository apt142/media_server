import tempfile
import unittest
from pathlib import Path

from media_server.configuration import Configuration
from media_server.encode_worker import EncodeWorker, raw_files_in
from media_server.job_catalog import JobCatalog, JobState, RippedDisc


class FakeHandBrake:
    """Writes a plausible output file instead of spending three hours encoding."""

    ENCODED_FILE_BYTES = 512

    def __init__(self, failing_source_names: tuple[str, ...] = (), exit_code: int = 0):
        self.failing_source_names = failing_source_names
        self.exit_code = exit_code
        self.received_argument_lists: list[list[str]] = []

    def __call__(self, arguments: list[str]) -> int:
        self.received_argument_lists.append(arguments)
        source_file = Path(arguments[arguments.index("--input") + 1])
        output_file = Path(arguments[arguments.index("--output") + 1])

        if source_file.name in self.failing_source_names:
            return 3

        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(b"e" * self.ENCODED_FILE_BYTES)
        return self.exit_code

    @property
    def encoded_count(self) -> int:
        return len(self.received_argument_lists)


class EncodeWorkerTestCase(unittest.TestCase):
    """Shared staging, catalog and stubbed HandBrake. Holds no tests of its own."""

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
        self.handbrake = FakeHandBrake()
        self.announced_lines: list[str] = []

    def _worker(self, handbrake=None) -> EncodeWorker:
        return EncodeWorker(
            configuration=self.configuration,
            catalog=self.catalog,
            run_command=handbrake or self.handbrake,
            announce=self.announced_lines.append,
        )

    def _stage_job(self, raw_file_count: int = 1, **overrides) -> int:
        """A ripped disc sitting in staging, waiting to be transcoded."""
        disc_label = overrides.get("disc_label", "THE_MATRIX")
        staged_path = self.configuration.staging_root / "raw" / disc_label

        for title_id in range(raw_file_count):
            title_path = staged_path / f"title-{title_id:02d}"
            title_path.mkdir(parents=True)
            (title_path / f"title_t{title_id:02d}.mkv").write_bytes(b"r" * 2048)

        fields = {
            "disc_label": disc_label,
            "media_kind": "film",
            "staged_path": staged_path,
            "title": "The Matrix",
            "year": "1999",
            "disc_format": "bluray",
        }
        fields.update(overrides)
        return self.catalog.record_ripped_disc(RippedDisc(**fields))


class EncodingAFilmTests(EncodeWorkerTestCase):
    def test_the_job_is_transcoded_and_waits_for_delivery(self):
        job_id = self._stage_job()

        outcome = self._worker().encode_next_job()

        self.assertTrue(outcome.is_encoded)
        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.ENCODED)

    def test_the_output_is_written_in_the_shape_the_library_expects(self):
        job_id = self._stage_job()

        self._worker().encode_next_job()

        encoded_path = self.catalog.job_with_id(job_id).encoded_path
        self.assertTrue(
            (encoded_path / "Movies/The Matrix (1999)/The Matrix (1999).mp4").is_file()
        )

    def test_a_bluray_is_encoded_with_the_bluray_preset(self):
        self._stage_job(disc_format="bluray")

        self._worker().encode_next_job()

        arguments = self.handbrake.received_argument_lists[0]
        self.assertEqual(
            arguments[arguments.index("--preset") + 1], "Super HQ 1080p30 Surround"
        )
        self.assertEqual(arguments[arguments.index("--encoder-level") + 1], "4.0")

    def test_a_dvd_is_encoded_with_the_dvd_preset(self):
        self._stage_job(disc_format="dvd")

        self._worker().encode_next_job()

        arguments = self.handbrake.received_argument_lists[0]
        self.assertEqual(
            arguments[arguments.index("--preset") + 1], "Super HQ 480p30 Surround"
        )
        self.assertEqual(arguments[arguments.index("--encoder-level") + 1], "3.1")

    def test_handbrake_warnings_still_count_as_a_usable_file(self):
        self._stage_job()
        forgiving_handbrake = FakeHandBrake(exit_code=1)

        outcome = self._worker(forgiving_handbrake).encode_next_job()

        self.assertTrue(outcome.is_encoded)


class EncodingATvDiscTests(EncodeWorkerTestCase):
    def test_every_episode_on_the_disc_is_transcoded(self):
        self._stage_job(
            raw_file_count=3,
            disc_label="FIREFLY_S01_D1",
            media_kind="show",
            title="Firefly",
            year="",
            season_number=1,
        )

        self._worker().encode_next_job()

        self.assertEqual(self.handbrake.encoded_count, 3)

    def test_episodes_are_named_so_plex_can_match_them(self):
        job_id = self._stage_job(
            raw_file_count=2,
            disc_label="FIREFLY_S01_D1",
            media_kind="show",
            title="Firefly",
            year="",
            season_number=1,
        )

        self._worker().encode_next_job()

        encoded_path = self.catalog.job_with_id(job_id).encoded_path
        self.assertTrue(
            (encoded_path / "TV/Firefly/Season 01/Firefly - s01e01.mp4").is_file()
        )
        self.assertTrue(
            (encoded_path / "TV/Firefly/Season 01/Firefly - s01e02.mp4").is_file()
        )

    def test_a_second_disc_carries_on_numbering_from_the_first(self):
        job_id = self._stage_job(
            raw_file_count=2,
            disc_label="FIREFLY_S01_D2",
            media_kind="show",
            title="Firefly",
            year="",
            season_number=1,
            first_episode_number=3,
        )

        self._worker().encode_next_job()

        encoded_path = self.catalog.job_with_id(job_id).encoded_path
        self.assertTrue(
            (encoded_path / "TV/Firefly/Season 01/Firefly - s01e03.mp4").is_file()
        )
        self.assertTrue(
            (encoded_path / "TV/Firefly/Season 01/Firefly - s01e04.mp4").is_file()
        )


class WorkingThroughTheQueueTests(EncodeWorkerTestCase):
    def test_an_empty_queue_says_so_rather_than_failing(self):
        outcome = self._worker().encode_next_job()

        self.assertFalse(outcome.is_encoded)
        self.assertFalse(outcome.had_work_to_do)
        self.assertIn("Nothing waiting", outcome.message)

    def test_jobs_are_taken_oldest_first(self):
        first_job_id = self._stage_job(disc_label="FIRST_DISC")
        self._stage_job(disc_label="SECOND_DISC")

        outcome = self._worker().encode_next_job()

        self.assertEqual(outcome.job.job_id, first_job_id)


class FailedEncodeTests(EncodeWorkerTestCase):
    def test_a_failed_encode_is_recorded_with_its_reason(self):
        job_id = self._stage_job()
        failing_handbrake = FakeHandBrake(failing_source_names=("title_t00.mkv",))

        outcome = self._worker(failing_handbrake).encode_next_job()

        self.assertFalse(outcome.is_encoded)
        job = self.catalog.job_with_id(job_id)
        self.assertEqual(job.state, JobState.FAILED)
        self.assertIn("HandBrake failed", job.failure_reason)

    def test_a_failed_encode_leaves_no_half_finished_output_behind(self):
        self._stage_job(raw_file_count=2)
        failing_handbrake = FakeHandBrake(failing_source_names=("title_t01.mkv",))

        self._worker(failing_handbrake).encode_next_job()

        encoded_root = self.configuration.staging_root / "encoded"
        self.assertEqual(list(encoded_root.rglob("*.mp4")), [])

    def test_a_job_whose_raw_files_vanished_is_marked_failed(self):
        job_id = self._stage_job()
        staged_path = self.catalog.job_with_id(job_id).staged_path
        for raw_file in staged_path.rglob("*.mkv"):
            raw_file.unlink()

        outcome = self._worker().encode_next_job()

        self.assertFalse(outcome.is_encoded)
        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.FAILED)
        self.assertIn("No raw files", outcome.message)


class RawFileOrderingTests(EncodeWorkerTestCase):
    def test_episodes_keep_the_order_they_sit_in_on_the_disc(self):
        job_id = self._stage_job(raw_file_count=3)
        staged_path = self.catalog.job_with_id(job_id).staged_path

        raw_files = raw_files_in(staged_path)

        self.assertEqual(
            [raw_file.name for raw_file in raw_files],
            ["title_t00.mkv", "title_t01.mkv", "title_t02.mkv"],
        )

    def test_a_folder_that_is_not_there_reads_as_no_files(self):
        self.assertEqual(raw_files_in(Path("/nowhere")), [])


if __name__ == "__main__":
    unittest.main()
