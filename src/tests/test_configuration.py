import tempfile
import unittest
from pathlib import Path

from media_server.configuration import (
    Configuration,
    expand_path,
    parse_setting_line,
    read_settings_file,
)


class ConfigurationLoadingTests(unittest.TestCase):
    STAGING_ROOT_FROM_FILE = "/Volumes/Internal/Rips"
    LIBRARY_ROOT_FROM_FILE = "/Volumes/MediaDrive/Media"
    STAGING_ROOT_FROM_ENVIRONMENT = "/tmp/environment-rips"

    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_root = Path(temporary_directory.name)
        self.configuration_path = self.temporary_root / "config"

    def _write_configuration_file(self, contents: str) -> Path:
        self.configuration_path.write_text(contents)
        return self.configuration_path

    def test_reads_both_roots_from_the_configuration_file(self):
        self._write_configuration_file(
            f"STAGING_ROOT={self.STAGING_ROOT_FROM_FILE}\n"
            f"LIBRARY_ROOT={self.LIBRARY_ROOT_FROM_FILE}\n"
        )

        configuration = Configuration.load(self.configuration_path, environment={})

        self.assertEqual(configuration.staging_root, Path(self.STAGING_ROOT_FROM_FILE))
        self.assertEqual(configuration.library_root, Path(self.LIBRARY_ROOT_FROM_FILE))

    def test_environment_overrides_the_configuration_file(self):
        self._write_configuration_file(
            f"STAGING_ROOT={self.STAGING_ROOT_FROM_FILE}\n"
            f"LIBRARY_ROOT={self.LIBRARY_ROOT_FROM_FILE}\n"
        )

        configuration = Configuration.load(
            self.configuration_path,
            environment={"STAGING_ROOT": self.STAGING_ROOT_FROM_ENVIRONMENT},
        )

        self.assertEqual(
            configuration.staging_root, Path(self.STAGING_ROOT_FROM_ENVIRONMENT)
        )
        self.assertEqual(configuration.library_root, Path(self.LIBRARY_ROOT_FROM_FILE))

    def test_falls_back_to_defaults_when_nothing_is_configured(self):
        configuration = Configuration.load(
            self.temporary_root / "missing-config", environment={}
        )

        self.assertEqual(configuration.staging_root, Path.home() / "Media" / "Rips")
        self.assertEqual(configuration.library_root, Path.home() / "Media")

    def test_expands_a_home_relative_path(self):
        self._write_configuration_file("LIBRARY_ROOT=~/ExternalMedia\n")

        configuration = Configuration.load(self.configuration_path, environment={})

        self.assertEqual(configuration.library_root, Path.home() / "ExternalMedia")


class ConfigurationPathsTests(unittest.TestCase):
    STAGING_ROOT = Path("/Volumes/Internal/Rips")
    LIBRARY_ROOT = Path("/Volumes/MediaDrive/Media")

    def setUp(self):
        self.configuration = Configuration(
            staging_root=self.STAGING_ROOT, library_root=self.LIBRARY_ROOT
        )

    def test_library_folders_hang_off_the_library_root(self):
        self.assertEqual(
            self.configuration.movies_directory, self.LIBRARY_ROOT / "Movies"
        )
        self.assertEqual(self.configuration.tv_directory, self.LIBRARY_ROOT / "TV")
        self.assertEqual(
            self.configuration.files_directory, self.LIBRARY_ROOT / "Files"
        )

    def test_catalog_lives_with_the_rips_so_it_survives_an_unmounted_library(self):
        self.assertEqual(
            self.configuration.catalog_path, self.STAGING_ROOT / "catalog.sqlite3"
        )

    def test_directory_for_media_kind_splits_shows_from_movies(self):
        cases = [
            ("show", self.LIBRARY_ROOT / "TV"),
            ("film", self.LIBRARY_ROOT / "Movies"),
        ]

        for media_kind, expected_directory in cases:
            with self.subTest(media_kind=media_kind):
                self.assertEqual(
                    self.configuration.directory_for_media_kind(media_kind),
                    expected_directory,
                )

    def test_library_is_unavailable_when_the_drive_is_not_mounted(self):
        self.assertFalse(self.configuration.is_library_available())


class SettingsFileParsingTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_root = Path(temporary_directory.name)

    def test_skips_comments_and_blank_lines(self):
        configuration_path = self.temporary_root / "config"
        configuration_path.write_text(
            "# where rips are staged\n"
            "\n"
            "STAGING_ROOT=/tmp/rips\n"
            "   # trailing comment\n"
        )

        settings = read_settings_file(configuration_path)

        self.assertEqual(settings, {"STAGING_ROOT": "/tmp/rips"})

    def test_missing_file_reads_as_no_settings(self):
        settings = read_settings_file(self.temporary_root / "not-there")

        self.assertEqual(settings, {})

    def test_parses_one_line_at_a_time(self):
        cases = [
            ("STAGING_ROOT=/tmp/rips", ("STAGING_ROOT", "/tmp/rips")),
            ('LIBRARY_ROOT="/Volumes/My Drive"', ("LIBRARY_ROOT", "/Volumes/My Drive")),
            ("LIBRARY_ROOT='/Volumes/Other'", ("LIBRARY_ROOT", "/Volumes/Other")),
            ("  STAGING_ROOT = /tmp/spaced  ", ("STAGING_ROOT", "/tmp/spaced")),
            ("# comment", None),
            ("", None),
            ("no-equals-sign", None),
        ]

        for line, expected_setting in cases:
            with self.subTest(line=line):
                self.assertEqual(parse_setting_line(line), expected_setting)


class PathExpansionTests(unittest.TestCase):
    def test_expands_environment_variables_and_home(self):
        self.assertEqual(expand_path("~/Media"), Path.home() / "Media")
        self.assertEqual(expand_path("$HOME/Media"), Path.home() / "Media")
        self.assertEqual(expand_path("/Volumes/Drive"), Path("/Volumes/Drive"))


if __name__ == "__main__":
    unittest.main()
