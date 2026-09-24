import unittest

from media_server.encode_service import EncodeService
from media_server.encode_worker import EncodeWorker
from media_server.job_catalog import JobState
from media_server.library_delivery import LibraryDelivery
from tests.test_disc_watcher import StoppingSleep
from tests.test_encode_worker import EncodeWorkerTestCase, FakeHandBrake


class EncodeServiceTestCase(EncodeWorkerTestCase):
    """Reuses the staging and catalog fixture. Holds no tests of its own."""

    def _service(self, handbrake=None) -> EncodeService:
        self.sleep = StoppingSleep()
        return EncodeService(
            configuration=self.configuration,
            catalog=self.catalog,
            encode_worker=EncodeWorker(
                configuration=self.configuration,
                catalog=self.catalog,
                run_command=handbrake or self.handbrake,
                announce=self.announced_lines.append,
            ),
            delivery=LibraryDelivery(self.configuration, self.catalog),
            announce=self.announced_lines.append,
            sleep=self.sleep,
            poll_seconds=0.0,
        )

    def _make_the_library_available(self) -> None:
        self.configuration.library_root.mkdir(parents=True, exist_ok=True)

    @property
    def announced_output(self) -> str:
        return "\n".join(self.announced_lines)


class OnePassTests(EncodeServiceTestCase):
    def test_a_ripped_disc_is_transcoded_and_delivered_in_one_pass(self):
        self._make_the_library_available()
        job_id = self._stage_job()

        service_pass = self._service().run_one_pass()

        self.assertEqual(service_pass.encoded_count, 1)
        self.assertEqual(service_pass.delivered_count, 1)
        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.DELIVERED)

    def test_the_finished_file_lands_in_the_library(self):
        self._make_the_library_available()
        self._stage_job()

        self._service().run_one_pass()

        delivered_file = (
            self.configuration.library_root
            / "Movies/The Matrix (1999)/The Matrix (1999).mp4"
        )
        self.assertTrue(delivered_file.is_file())

    def test_staging_is_cleared_once_the_library_has_it(self):
        self._make_the_library_available()
        self._stage_job()

        self._service().run_one_pass()

        leftover_files = list(self.configuration.staging_root.rglob("*.mkv"))
        self.assertEqual(leftover_files, [])

    def test_an_empty_queue_is_a_quiet_pass(self):
        self._make_the_library_available()

        service_pass = self._service().run_one_pass()

        self.assertFalse(service_pass.had_work_to_do)
        self.assertEqual(self.announced_lines, [])

    def test_the_whole_backlog_goes_through_in_one_pass(self):
        self._make_the_library_available()
        self._stage_job(disc_label="FIRST_DISC")
        self._stage_job(disc_label="SECOND_DISC")

        service_pass = self._service().run_one_pass()

        self.assertEqual(service_pass.encoded_count, 2)
        self.assertEqual(service_pass.delivered_count, 2)


class UnpluggedLibraryTests(EncodeServiceTestCase):
    def test_transcoding_carries_on_while_the_drive_is_away(self):
        job_id = self._stage_job()

        service_pass = self._service().run_one_pass()

        self.assertEqual(service_pass.encoded_count, 1)
        self.assertEqual(service_pass.delivered_count, 0)
        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.ENCODED)

    def test_plugging_the_drive_back_in_flushes_the_backlog(self):
        job_id = self._stage_job()
        service = self._service()
        service.run_one_pass()

        self._make_the_library_available()
        service_pass = service.run_one_pass()

        self.assertEqual(service_pass.encoded_count, 0)
        self.assertEqual(service_pass.delivered_count, 1)
        self.assertEqual(self.catalog.job_with_id(job_id).state, JobState.DELIVERED)

    def test_an_absent_drive_is_mentioned_once_rather_than_every_pass(self):
        self._stage_job()
        service = self._service()

        for _pass in range(5):
            service.run_one_pass()

        holding_lines = [
            line for line in self.announced_lines if "not mounted" in line
        ]
        self.assertEqual(len(holding_lines), 1)


class FailedJobTests(EncodeServiceTestCase):
    def test_a_job_that_will_not_encode_does_not_stop_the_service(self):
        self._make_the_library_available()
        self._stage_job()
        failing_handbrake = FakeHandBrake(failing_source_names=("title_t00.mkv",))

        service_pass = self._service(failing_handbrake).run_one_pass()

        self.assertEqual(service_pass.encoded_count, 0)
        self.assertIn("HandBrake failed", self.announced_output)

    def test_a_failed_job_does_not_block_the_rest_of_the_queue(self):
        self._make_the_library_available()
        self._stage_job(disc_label="BAD_DISC")
        self._stage_job(disc_label="GOOD_DISC", raw_file_count=2)
        picky_handbrake = FakeHandBrake(failing_source_names=("title_t01.mkv",))

        service_pass = self._service(picky_handbrake).run_one_pass()

        self.assertEqual(service_pass.encoded_count, 1)
        self.assertEqual(service_pass.delivered_count, 1)


class LibraryWatchingHandBrake(FakeHandBrake):
    """Notes how much was already in the library each time it was asked to encode."""

    def __init__(self, library_root):
        super().__init__()
        self.library_root = library_root
        self.library_counts: list[int] = []

    def __call__(self, arguments: list[str]) -> int:
        self.library_counts.append(len(list(self.library_root.rglob("*.mp4"))))
        return super().__call__(arguments)


class StagingIsClearedAsItGoesTests(EncodeServiceTestCase):
    """Holding finished films until the whole queue is done is what fills the disk."""

    def test_the_first_job_is_delivered_before_the_second_is_transcoded(self):
        self._make_the_library_available()
        self._stage_job(disc_label="FIRST_DISC")
        self._stage_job(disc_label="SECOND_DISC")
        handbrake = LibraryWatchingHandBrake(self.configuration.library_root)

        self._service(handbrake).run_one_pass()

        self.assertEqual(handbrake.library_counts, [0, 1])

    def test_staging_is_empty_once_the_pass_is_done(self):
        self._make_the_library_available()
        self._stage_job(disc_label="FIRST_DISC")
        self._stage_job(disc_label="SECOND_DISC")

        self._service().run_one_pass()

        self.assertEqual(list(self.configuration.staging_root.rglob("*.mkv")), [])
        self.assertEqual(list(self.configuration.staging_root.rglob("*.mp4")), [])


class ServingUntilStoppedTests(EncodeServiceTestCase):
    def test_the_queue_is_worked_on_every_pass(self):
        self._make_the_library_available()
        service = self._service()
        service.sleep = StoppingSleep(stop_after_calls=3)

        with self.assertRaises(KeyboardInterrupt):
            service.serve_forever()

        self.assertEqual(len(service.sleep.waited_seconds), 3)

    def test_it_says_where_finished_files_are_going_before_it_settles_in(self):
        self._make_the_library_available()
        service = self._service()
        service.sleep = StoppingSleep(stop_after_calls=1)

        with self.assertRaises(KeyboardInterrupt):
            service.serve_forever()

        self.assertIn(str(self.configuration.library_root), self.announced_output)


if __name__ == "__main__":
    unittest.main()
