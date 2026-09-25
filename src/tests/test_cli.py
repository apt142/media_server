import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from media_server.cli import (
    DELIVERED_JOBS_SHOWN,
    describe_bytes,
    fit_to_width,
    main,
)
from media_server.configuration import (
    LIBRARY_ROOT_KEY,
    STAGING_ROOT_KEY,
    Configuration,
)
from media_server.job_catalog import JobCatalog, JobState, RippedDisc
from media_server.encode_worker import EncodeWorker
from media_server.makemkv import MakeMkv
from tests import makemkv_fixtures
from tests.fake_drive import RecordingDiscDrive
from tests.fake_makemkv import FakeMakeMkvCommand
from tests.test_encode_worker import FakeHandBrake


class CommandLineTestCase(unittest.TestCase):
    """Runs commands against temporary folders. Holds no tests of its own."""

    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_root = Path(temporary_directory.name)

        self.staging_root = self.temporary_root / "staging"
        self.library_root = self.temporary_root / "library"
        self.library_root.mkdir()
        self.configuration_path = self.temporary_root / "config"

        self._ignore_roots_from_the_real_environment()

    def _ignore_roots_from_the_real_environment(self) -> None:
        """Keep a developer's own exported roots from leaking into the tests."""
        environment_without_roots = {
            key: value
            for key, value in os.environ.items()
            if key not in (STAGING_ROOT_KEY, LIBRARY_ROOT_KEY)
        }
        patcher = mock.patch.dict(os.environ, environment_without_roots, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_configuration_file(self) -> None:
        self.configuration_path.write_text(
            f"{STAGING_ROOT_KEY}={self.staging_root}\n"
            f"{LIBRARY_ROOT_KEY}={self.library_root}\n"
        )

    def _run_command(self, *command_arguments: str) -> tuple[int, str]:
        captured_output = io.StringIO()
        with redirect_stdout(captured_output):
            exit_code = main(
                ["--config", str(self.configuration_path), *command_arguments]
            )
        return exit_code, captured_output.getvalue()

    def _seed_job(self, disc_fingerprint: str = "") -> int:
        """Put one ripped disc in the catalog the commands will read."""
        configuration = Configuration(
            staging_root=self.staging_root, library_root=self.library_root
        )
        raw_path = self.staging_root / "raw" / "THE_MATRIX"
        raw_path.mkdir(parents=True)
        (raw_path / "title00.mkv").write_bytes(b"x" * 2048)

        with JobCatalog(configuration.catalog_path) as catalog:
            return catalog.record_ripped_disc(
                RippedDisc(
                    disc_label="THE_MATRIX",
                    media_kind="film",
                    staged_path=raw_path,
                    title="The Matrix",
                    year="1999",
                    disc_fingerprint=disc_fingerprint,
                )
            )


class StatusCommandTests(CommandLineTestCase):
    def test_reports_both_folders_before_anything_has_been_ripped(self):
        self._write_configuration_file()

        exit_code, output = self._run_command("status")

        self.assertEqual(exit_code, 0)
        self.assertIn(str(self.staging_root), output)
        self.assertIn(str(self.library_root), output)
        self.assertIn("Nothing has been ripped yet.", output)

    def test_asking_for_status_does_not_create_a_staging_folder_or_catalog(self):
        self._write_configuration_file()

        self._run_command("status")

        self.assertFalse(self.staging_root.exists())

    def test_says_when_the_library_drive_is_missing(self):
        self.configuration_path.write_text(
            f"{STAGING_ROOT_KEY}={self.staging_root}\n"
            f"{LIBRARY_ROOT_KEY}={self.temporary_root / 'not-mounted'}\n"
        )

        _exit_code, output = self._run_command("status")

        self.assertIn("not mounted", output)

    def test_says_when_the_library_drive_is_present(self):
        self._write_configuration_file()

        _exit_code, output = self._run_command("status")

        self.assertIn("mounted", output)

    def test_reports_disk_space_and_backlog_size(self):
        self._write_configuration_file()
        self._seed_job()

        _exit_code, output = self._run_command("status")

        self.assertIn("GB free", output)
        self.assertIn("Backlog", output)
        self.assertIn("1 disc(s) holding 2.0 KB in staging", output)

    def test_an_unmounted_library_does_not_report_the_internal_disk_space(self):
        self.configuration_path.write_text(
            f"{STAGING_ROOT_KEY}={self.staging_root}\n"
            f"{LIBRARY_ROOT_KEY}={self.temporary_root / 'not-mounted'}\n"
        )

        _exit_code, output = self._run_command("status")

        self.assertIn("not available", output)


class StatusListingTests(CommandLineTestCase):
    """Status names the films in each state, not just how many there are."""

    def _record_film(self, title: str, state: JobState = JobState.STAGED) -> None:
        configuration = Configuration(
            staging_root=self.staging_root, library_root=self.library_root
        )
        raw_path = self.staging_root / "raw" / title.replace(" ", "_")
        raw_path.mkdir(parents=True)

        with JobCatalog(configuration.catalog_path) as catalog:
            job_id = catalog.record_ripped_disc(
                RippedDisc(
                    disc_label=title.upper().replace(" ", "_"),
                    media_kind="film",
                    staged_path=raw_path,
                    title=title,
                )
            )
            if state == JobState.DELIVERED:
                catalog.mark_delivered(job_id)
            if state == JobState.FAILED:
                catalog.mark_failed(job_id, "the disc would not decrypt")

    def test_names_every_film_waiting_to_be_transcoded(self):
        self._write_configuration_file()
        self._record_film("Iron Man")
        self._record_film("Thor")

        _exit_code, output = self._run_command("status")

        self.assertIn("2  staged", output)
        self.assertIn("Iron Man", output)
        self.assertIn("Thor", output)

    def test_a_film_is_listed_under_the_state_it_reached(self):
        self._write_configuration_file()
        self._record_film("Iron Man", state=JobState.FAILED)
        self._record_film("Thor")

        _exit_code, output = self._run_command("status")

        self.assertIn("staged        ripped, waiting to be transcoded\n"
                      "       #2    Thor", output)
        self.assertIn("failed        failed, needs attention\n"
                      "       #1    Iron Man", output)

    def test_a_film_is_named_by_its_disc_label_when_it_has_no_title(self):
        self._write_configuration_file()
        self._seed_job()

        _exit_code, output = self._run_command("status")

        self.assertIn("The Matrix (1999)", output)

    def test_an_old_delivered_film_is_counted_rather_than_named(self):
        self._write_configuration_file()
        delivered_titles = [f"Film {number}" for number in range(1, 8)]
        for title in delivered_titles:
            self._record_film(title, state=JobState.DELIVERED)

        _exit_code, output = self._run_command("status")

        self.assertIn("7  delivered", output)
        self.assertIn("and 2 more", output)
        for recent_title in delivered_titles[-DELIVERED_JOBS_SHOWN:]:
            with self.subTest(title=recent_title):
                self.assertIn(f"{recent_title}\n", output)
        for older_title in delivered_titles[:-DELIVERED_JOBS_SHOWN]:
            with self.subTest(title=older_title):
                self.assertNotIn(f"{older_title}\n", output)


class ConfigCommandTests(CommandLineTestCase):
    def test_shows_the_resolved_folders(self):
        self._write_configuration_file()

        exit_code, output = self._run_command("config")

        self.assertEqual(exit_code, 0)
        self.assertIn(f"{STAGING_ROOT_KEY}={self.staging_root}", output)
        self.assertIn(f"{LIBRARY_ROOT_KEY}={self.library_root}", output)


class InitConfigCommandTests(CommandLineTestCase):
    def test_writes_a_starter_file_that_can_be_read_back(self):
        exit_code, output = self._run_command("init-config")

        self.assertEqual(exit_code, 0)
        self.assertIn("Edit it to point at your drives.", output)
        self.assertIn(STAGING_ROOT_KEY, self.configuration_path.read_text())
        self.assertIn(LIBRARY_ROOT_KEY, self.configuration_path.read_text())

    def test_refuses_to_overwrite_an_existing_file(self):
        self._write_configuration_file()
        original_contents = self.configuration_path.read_text()

        exit_code, output = self._run_command("init-config")

        self.assertEqual(exit_code, 1)
        self.assertIn("already exists", output)
        self.assertEqual(self.configuration_path.read_text(), original_contents)


class DeliverCommandTests(CommandLineTestCase):
    def test_reports_when_there_is_nothing_waiting(self):
        self._write_configuration_file()

        exit_code, output = self._run_command("deliver")

        self.assertEqual(exit_code, 0)
        self.assertIn("Nothing waiting to be delivered.", output)


class QueueCommandTests(CommandLineTestCase):
    def test_says_when_there_is_nothing_queued(self):
        self._write_configuration_file()

        exit_code, output = self._run_command("queue")

        self.assertEqual(exit_code, 0)
        self.assertIn("Nothing in the queue.", output)

    def test_lists_a_waiting_disc_with_its_id_state_and_size(self):
        self._write_configuration_file()
        job_id = self._seed_job()

        exit_code, output = self._run_command("queue")

        self.assertEqual(exit_code, 0)
        self.assertIn(f"#{job_id}", output)
        self.assertIn("The Matrix (1999)", output)
        self.assertIn(JobState.STAGED, output)
        self.assertIn("2.0 KB", output)


class AttentionCommandTests(CommandLineTestCase):
    def test_says_when_nothing_is_stuck(self):
        self._write_configuration_file()
        self._seed_job()

        exit_code, output = self._run_command("attention")

        self.assertEqual(exit_code, 0)
        self.assertIn("Nothing needs attention.", output)

    def test_lists_a_failed_job_with_its_reason(self):
        self._write_configuration_file()
        job_id = self._seed_job()
        configuration = Configuration(
            staging_root=self.staging_root, library_root=self.library_root
        )
        with JobCatalog(configuration.catalog_path) as catalog:
            catalog.mark_failed(job_id, "MakeMKV produced no output")

        _exit_code, output = self._run_command("attention")

        self.assertIn(f"#{job_id}", output)
        self.assertIn("MakeMKV produced no output", output)


class ForgetCommandTests(CommandLineTestCase):
    DISC_FINGERPRINT = "a1b2c3d4e5f60718"

    def test_forgetting_a_job_lets_its_disc_be_ripped_again(self):
        self._write_configuration_file()
        job_id = self._seed_job(disc_fingerprint=self.DISC_FINGERPRINT)

        exit_code, output = self._run_command("forget", str(job_id))

        self.assertEqual(exit_code, 0)
        self.assertIn("can be ripped again", output)

        configuration = Configuration(
            staging_root=self.staging_root, library_root=self.library_root
        )
        with JobCatalog(configuration.catalog_path) as catalog:
            self.assertFalse(catalog.is_duplicate_disc(self.DISC_FINGERPRINT))

    def test_forgetting_an_unknown_job_reports_the_mistake(self):
        self._write_configuration_file()

        exit_code, output = self._run_command("forget", "404")

        self.assertEqual(exit_code, 1)
        self.assertIn("No job #404.", output)


class RipCommandTests(CommandLineTestCase):
    """Drives the rip command with a stubbed MakeMKV and a stubbed drive."""

    def _installed_makemkv(self, scan_output: str) -> MakeMkv:
        # Pointing at this test file is enough to satisfy the installed check.
        return MakeMkv(
            run_command=FakeMakeMkvCommand(scan_output),
            makemkv_command=Path(__file__),
        )

    def _run_with_disc(self, command: str, scan_output: str) -> tuple[int, str]:
        with mock.patch(
            "media_server.cli.MakeMkv",
            return_value=self._installed_makemkv(scan_output),
        ):
            with mock.patch("media_server.rip_worker.DiscDrive", RecordingDiscDrive):
                return self._run_command(*command.split())

    def test_ripping_a_film_queues_it_and_says_so(self):
        self._write_configuration_file()

        exit_code, output = self._run_with_disc("rip", makemkv_fixtures.FILM_BLURAY)

        self.assertEqual(exit_code, 0)
        self.assertIn("The disc is out and the transcode is queued.", output)

    def test_a_ripped_disc_then_shows_up_in_the_queue(self):
        self._write_configuration_file()
        self._run_with_disc("rip", makemkv_fixtures.FILM_BLURAY)

        _exit_code, output = self._run_command("queue")

        self.assertIn("The Matrix", output)

    def test_an_unreadable_disc_reports_a_failure(self):
        self._write_configuration_file()

        exit_code, output = self._run_with_disc("rip", makemkv_fixtures.UNREADABLE_DISC)

        self.assertEqual(exit_code, 1)
        self.assertIn("No titles found", output)

    def test_scanning_describes_the_disc_without_ripping_it(self):
        self._write_configuration_file()

        exit_code, output = self._run_with_disc("scan", makemkv_fixtures.TV_DVD)

        self.assertEqual(exit_code, 0)
        self.assertIn("This looks like a TV disc", output)
        self.assertIn("FIREFLY_S01_D2", output)
        self.assertIn("0:44:01", output)

    def test_scanning_warns_when_the_disc_has_already_been_ripped(self):
        self._write_configuration_file()
        self._run_with_disc("rip", makemkv_fixtures.FILM_BLURAY)

        _exit_code, output = self._run_with_disc("scan", makemkv_fixtures.FILM_BLURAY)

        self.assertIn("already been ripped", output)

    def test_the_same_disc_twice_is_refused_without_the_override(self):
        self._write_configuration_file()
        self._run_with_disc("rip", makemkv_fixtures.FILM_BLURAY)

        _exit_code, output = self._run_with_disc("rip", makemkv_fixtures.FILM_BLURAY)

        self.assertIn("Ejecting without doing it again", output)

    def test_ripping_again_replaces_the_first_attempt(self):
        self._write_configuration_file()
        self._run_with_disc("rip", makemkv_fixtures.FILM_BLURAY)

        exit_code, output = self._run_with_disc(
            "rip --again", makemkv_fixtures.FILM_BLURAY
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("Replacing #1 The Matrix", output)
        self.assertIn("The disc is out and the transcode is queued.", output)

    def test_a_missing_makemkv_is_reported_rather_than_crashing(self):
        self._write_configuration_file()
        missing_makemkv = MakeMkv(makemkv_command=Path("/nowhere/makemkvcon"))

        with mock.patch("media_server.cli.MakeMkv", return_value=missing_makemkv):
            exit_code, output = self._run_command("rip")

        self.assertEqual(exit_code, 1)
        self.assertIn("MakeMKV is not installed", output)


class EncodeCommandTests(CommandLineTestCase):
    def _run_encode(self, *extra_arguments: str) -> tuple[int, str]:
        def build_worker(configuration, catalog, **_unused_settings):
            return EncodeWorker(
                configuration,
                catalog,
                run_command=FakeHandBrake(),
                announce=lambda _line: None,
            )

        # The full-queue path builds its worker inside the service, so both
        # places that construct one have to be stubbed.
        with mock.patch("media_server.cli.is_handbrake_installed", return_value=True):
            with mock.patch("media_server.cli.EncodeWorker", build_worker):
                with mock.patch(
                    "media_server.encode_service.EncodeWorker", build_worker
                ):
                    return self._run_command("encode", *extra_arguments)

    def _state_of(self, job_id: int) -> JobState:
        configuration = Configuration(
            staging_root=self.staging_root, library_root=self.library_root
        )
        with JobCatalog(configuration.catalog_path) as catalog:
            return catalog.job_with_id(job_id).state

    def test_nothing_ripped_yet_says_so(self):
        self._write_configuration_file()

        exit_code, output = self._run_encode()

        self.assertEqual(exit_code, 0)
        self.assertIn("Nothing has been ripped yet.", output)

    def test_a_staged_job_is_transcoded_and_delivered_in_one_go(self):
        self._write_configuration_file()
        job_id = self._seed_job()

        exit_code, output = self._run_encode()

        self.assertEqual(exit_code, 0)
        self.assertIn("delivered The Matrix (1999)", output)
        self.assertEqual(self._state_of(job_id), JobState.DELIVERED)

    def test_the_finished_file_lands_in_the_library(self):
        self._write_configuration_file()
        self._seed_job()

        self._run_encode()

        self.assertTrue(
            (
                self.library_root / "Movies/The Matrix (1999)/The Matrix (1999).mp4"
            ).is_file()
        )

    def test_an_absent_library_holds_the_job_rather_than_losing_it(self):
        self._write_configuration_file()
        job_id = self._seed_job()
        shutil.rmtree(self.library_root)

        exit_code, output = self._run_encode()

        self.assertEqual(exit_code, 0)
        self.assertIn("not mounted", output)
        self.assertEqual(self._state_of(job_id), JobState.ENCODED)

    def test_an_absent_library_names_the_path_it_looked_for(self):
        """A plugged-in drive with no library folder looks the same as no drive."""
        self._write_configuration_file()
        self._seed_job()
        shutil.rmtree(self.library_root)

        _exit_code, output = self._run_encode()

        self.assertIn(str(self.library_root), output)

    def test_work_left_over_from_a_held_delivery_goes_out_on_the_next_run(self):
        self._write_configuration_file()
        job_id = self._seed_job()
        shutil.rmtree(self.library_root)
        self._run_encode()

        self.library_root.mkdir()
        _exit_code, output = self._run_encode()

        self.assertIn("delivered The Matrix (1999)", output)
        self.assertEqual(self._state_of(job_id), JobState.DELIVERED)

    def test_a_missing_handbrake_is_reported_rather_than_crashing(self):
        self._write_configuration_file()

        with mock.patch(
            "media_server.cli.is_handbrake_installed", return_value=False
        ):
            exit_code, output = self._run_command("encode")

        self.assertEqual(exit_code, 1)
        self.assertIn("HandBrakeCLI is not installed", output)


class TitleFittingTests(unittest.TestCase):
    def test_a_short_title_is_left_alone(self):
        self.assertEqual(fit_to_width("Toy Story (1995)", 40), "Toy Story (1995)")

    def test_a_long_title_is_trimmed_to_the_column_width(self):
        long_title = "The Lord of the Rings The Fellowship of the Ring (2001) part 1"

        fitted_title = fit_to_width(long_title, 40)

        self.assertEqual(len(fitted_title), 40)
        self.assertTrue(fitted_title.endswith("\u2026"))


class ByteDescriptionTests(unittest.TestCase):
    def test_renders_sizes_the_way_a_person_would_say_them(self):
        cases = [
            (0, "0.0 B"),
            (512, "512.0 B"),
            (2048, "2.0 KB"),
            (5 * 1024 * 1024, "5.0 MB"),
            (40 * 1024**3, "40.0 GB"),
            (2 * 1024**4, "2.0 TB"),
        ]

        for byte_count, expected_description in cases:
            with self.subTest(byte_count=byte_count):
                self.assertEqual(describe_bytes(byte_count), expected_description)


if __name__ == "__main__":
    unittest.main()
