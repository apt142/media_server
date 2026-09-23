import tempfile
import unittest
from pathlib import Path
from unittest import mock

from media_server.configuration import Configuration
from media_server.disc_watcher import DiscWatcher, WatchSettings
from media_server.job_catalog import JobCatalog, RippedDisc
from media_server.rip_worker import RipOutcome
from media_server.volume_space import VolumeSpace
from tests.fake_drive import BLURAY_IN_DRIVE, EMPTY_DRIVE, ScriptedDiscDrive

RIPPED = RipOutcome(message="Ripped 1 title(s) as job #1.", job_id=1, is_ripped=True)
DUPLICATE = RipOutcome(message="Already ripped this disc as #1.", job_id=1)
UNREADABLE = RipOutcome(message="No titles found.")

FULL_DISK = VolumeSpace(total_bytes=1024**4, free_bytes=0)
ROOMY_DISK = VolumeSpace(total_bytes=1024**4, free_bytes=500 * 1024**3)


class FakeRipWorker:
    """Stands in for the drive-bound half while the watcher is under test.

    The outcomes are handed out in order, and the last one repeats, so a test
    only describes the discs it cares about.
    """

    def __init__(self, outcomes=(RIPPED,)):
        self.outcomes = list(outcomes)
        self.rip_count = 0

    def rip_disc_in_drive(self) -> RipOutcome:
        outcome = self.outcomes[min(self.rip_count, len(self.outcomes) - 1)]
        self.rip_count += 1
        return outcome


class StoppingSleep:
    """A clock that records what it was asked to wait for, then stops the loop.

    Interrupting is how the service really stops, so the loop tests end the
    same way a person ending it with Ctrl-C would.
    """

    def __init__(self, stop_after_calls: int = 0):
        self.stop_after_calls = stop_after_calls
        self.waited_seconds: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waited_seconds.append(seconds)
        if self.stop_after_calls and len(self.waited_seconds) >= self.stop_after_calls:
            raise KeyboardInterrupt


class DiscWatcherTestCase(unittest.TestCase):
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
        self.announced_lines: list[str] = []
        self.sleep = StoppingSleep()

        # Free space is stubbed for every test, so that a watcher's decisions
        # never depend on how full the disk of the machine running the suite is.
        space_patcher = mock.patch(
            "media_server.disc_watcher.space_at", return_value=ROOMY_DISK
        )
        self.staging_space = space_patcher.start()
        self.addCleanup(space_patcher.stop)

    def _watcher(
        self, statuses, rip_worker=None, maximum_queue_depth=None
    ) -> DiscWatcher:
        self.drive = ScriptedDiscDrive(statuses)
        self.rip_worker = rip_worker or FakeRipWorker()
        return DiscWatcher(
            configuration=self.configuration,
            catalog=self.catalog,
            rip_worker=self.rip_worker,
            drive=self.drive,
            settings=WatchSettings(
                poll_seconds=0.0,
                settle_seconds=0.0,
                maximum_queue_depth=maximum_queue_depth,
            ),
            announce=self.announced_lines.append,
            sleep=self.sleep,
        )

    def _stage_a_disc(self, disc_label: str) -> int:
        """A ripped disc already waiting in the queue."""
        return self.catalog.record_ripped_disc(
            RippedDisc(
                disc_label=disc_label,
                media_kind="film",
                staged_path=self.configuration.staging_root / "raw" / disc_label,
                title=disc_label,
                disc_fingerprint=disc_label,
            )
        )

    @property
    def announced_output(self) -> str:
        return "\n".join(self.announced_lines)


class NoticingADiscTests(DiscWatcherTestCase):
    def test_a_disc_put_in_the_drive_is_ripped(self):
        watcher = self._watcher([BLURAY_IN_DRIVE])

        outcome = watcher.check_once()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(self.rip_worker.rip_count, 1)

    def test_an_empty_drive_is_left_alone(self):
        watcher = self._watcher([EMPTY_DRIVE])

        outcome = watcher.check_once()

        self.assertTrue(outcome.is_idle)
        self.assertEqual(self.rip_worker.rip_count, 0)
        self.assertEqual(self.announced_lines, [])

    def test_the_drive_is_given_a_moment_to_spin_up_before_it_is_read(self):
        watcher = self._watcher([BLURAY_IN_DRIVE])
        watcher.settings = WatchSettings(poll_seconds=5.0, settle_seconds=15.0)

        watcher.check_once()

        self.assertEqual(self.sleep.waited_seconds, [15.0])

    def test_what_was_found_is_written_down_before_the_rip_starts(self):
        watcher = self._watcher([BLURAY_IN_DRIVE])

        watcher.check_once()

        self.assertIn("BD-ROM", self.announced_output)


class OneRipPerDiscTests(DiscWatcherTestCase):
    def test_a_disc_still_sitting_there_is_not_ripped_twice(self):
        watcher = self._watcher([BLURAY_IN_DRIVE])

        for _pass in range(4):
            watcher.check_once()

        self.assertEqual(self.rip_worker.rip_count, 1)

    def test_taking_one_disc_out_and_putting_another_in_rips_again(self):
        watcher = self._watcher([BLURAY_IN_DRIVE, EMPTY_DRIVE, BLURAY_IN_DRIVE])

        for _pass in range(3):
            watcher.check_once()

        self.assertEqual(self.rip_worker.rip_count, 2)

    def test_a_disc_that_will_not_rip_is_not_retried_in_a_loop(self):
        watcher = self._watcher([BLURAY_IN_DRIVE], FakeRipWorker([UNREADABLE]))

        for _pass in range(4):
            watcher.check_once()

        self.assertEqual(self.rip_worker.rip_count, 1)

    def test_a_disc_that_will_not_rip_stays_in_the_drive_to_be_seen(self):
        watcher = self._watcher([BLURAY_IN_DRIVE], FakeRipWorker([UNREADABLE]))

        outcome = watcher.check_once()

        self.assertFalse(outcome.is_ripped)
        self.assertEqual(self.drive.eject_count, 0)
        self.assertIn("Leaving it in the drive", outcome.message)

    def test_a_disc_already_in_the_catalog_counts_as_dealt_with(self):
        watcher = self._watcher([BLURAY_IN_DRIVE], FakeRipWorker([DUPLICATE]))

        outcome = watcher.check_once()

        self.assertFalse(outcome.is_ripped)
        self.assertIn("Already ripped", self.announced_output)


class BackpressureTests(DiscWatcherTestCase):
    def test_a_disc_waits_rather_than_filling_the_staging_disk(self):
        watcher = self._watcher([BLURAY_IN_DRIVE])
        self.staging_space.return_value = FULL_DISK

        outcome = watcher.check_once()

        self.assertTrue(outcome.is_holding)
        self.assertEqual(self.rip_worker.rip_count, 0)
        self.assertIn("Holding this disc", outcome.message)

    def test_a_held_disc_is_ripped_once_the_queue_drains(self):
        watcher = self._watcher([BLURAY_IN_DRIVE])
        self.staging_space.return_value = FULL_DISK
        watcher.check_once()

        self.staging_space.return_value = ROOMY_DISK
        outcome = watcher.check_once()

        self.assertTrue(outcome.is_ripped)
        self.assertEqual(self.rip_worker.rip_count, 1)

    def test_holding_is_said_once_rather_than_on_every_pass(self):
        watcher = self._watcher([BLURAY_IN_DRIVE])
        self.staging_space.return_value = FULL_DISK

        for _pass in range(5):
            watcher.check_once()

        holding_lines = [line for line in self.announced_lines if "Holding" in line]
        self.assertEqual(len(holding_lines), 1)

    def test_a_disc_waits_when_the_queue_is_already_at_its_limit(self):
        self._stage_a_disc("FIRST_DISC")
        self._stage_a_disc("SECOND_DISC")
        watcher = self._watcher([BLURAY_IN_DRIVE], maximum_queue_depth=2)

        outcome = watcher.check_once()

        self.assertTrue(outcome.is_holding)
        self.assertEqual(self.rip_worker.rip_count, 0)
        self.assertIn("2 disc(s) already waiting", outcome.message)

    def test_a_queue_under_the_limit_still_accepts_discs(self):
        self._stage_a_disc("FIRST_DISC")
        watcher = self._watcher([BLURAY_IN_DRIVE], maximum_queue_depth=2)

        outcome = watcher.check_once()

        self.assertTrue(outcome.is_ripped)

    def test_no_limit_means_no_limit(self):
        for disc_number in range(10):
            self._stage_a_disc(f"DISC_{disc_number}")
        watcher = self._watcher([BLURAY_IN_DRIVE])

        outcome = watcher.check_once()

        self.assertTrue(outcome.is_ripped)


class WatchingUntilStoppedTests(DiscWatcherTestCase):
    def test_the_drive_is_looked_at_on_every_pass(self):
        self.sleep = StoppingSleep(stop_after_calls=3)
        watcher = self._watcher([EMPTY_DRIVE])

        with self.assertRaises(KeyboardInterrupt):
            watcher.watch_forever()

        self.assertEqual(self.drive.read_count, 3)

    def test_it_says_where_rips_are_going_before_it_settles_in(self):
        self.sleep = StoppingSleep(stop_after_calls=1)
        watcher = self._watcher([EMPTY_DRIVE])

        with self.assertRaises(KeyboardInterrupt):
            watcher.watch_forever()

        self.assertIn(str(self.configuration.staging_root), self.announced_output)

    def test_it_waits_the_poll_interval_between_looks(self):
        self.sleep = StoppingSleep(stop_after_calls=2)
        watcher = self._watcher([EMPTY_DRIVE])
        watcher.settings = WatchSettings(poll_seconds=5.0)

        with self.assertRaises(KeyboardInterrupt):
            watcher.watch_forever()

        self.assertEqual(self.sleep.waited_seconds, [5.0, 5.0])


if __name__ == "__main__":
    unittest.main()
