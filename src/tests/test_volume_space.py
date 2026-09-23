import tempfile
import unittest
from pathlib import Path

from media_server.volume_space import (
    BYTES_PER_GIGABYTE,
    LOW_SPACE_GIGABYTES,
    VolumeSpace,
    nearest_existing_directory,
    space_at,
)


class VolumeSpaceTests(unittest.TestCase):
    TOTAL_BYTES = 100 * BYTES_PER_GIGABYTE

    def test_reports_how_much_of_the_volume_is_used(self):
        volume = VolumeSpace(
            total_bytes=self.TOTAL_BYTES, free_bytes=25 * BYTES_PER_GIGABYTE
        )

        self.assertEqual(volume.used_bytes, 75 * BYTES_PER_GIGABYTE)
        self.assertEqual(volume.used_percent, 75.0)
        self.assertEqual(volume.free_gigabytes, 25.0)

    def test_an_empty_volume_does_not_divide_by_zero(self):
        volume = VolumeSpace(total_bytes=0, free_bytes=0)

        self.assertEqual(volume.used_percent, 0.0)

    def test_room_is_measured_against_the_free_space(self):
        volume = VolumeSpace(total_bytes=self.TOTAL_BYTES, free_bytes=1000)

        self.assertTrue(volume.has_room_for(1000))
        self.assertFalse(volume.has_room_for(1001))

    def test_a_nearly_full_staging_disk_reads_as_low(self):
        cases = [
            (LOW_SPACE_GIGABYTES - 1, True),
            (LOW_SPACE_GIGABYTES + 1, False),
        ]

        for free_gigabytes, is_low in cases:
            with self.subTest(free_gigabytes=free_gigabytes):
                volume = VolumeSpace(
                    total_bytes=self.TOTAL_BYTES,
                    free_bytes=int(free_gigabytes * BYTES_PER_GIGABYTE),
                )

                self.assertEqual(volume.is_low, is_low)

    def test_a_missing_volume_says_so(self):
        volume = VolumeSpace(total_bytes=0, free_bytes=0, is_present=False)

        self.assertEqual(volume.describe(), "not available")


class MeasuringRealFoldersTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_root = Path(temporary_directory.name)

    def test_measures_the_volume_a_folder_lives_on(self):
        volume = space_at(self.temporary_root)

        self.assertTrue(volume.is_present)
        self.assertGreater(volume.total_bytes, 0)

    def test_a_staging_folder_that_does_not_exist_yet_measures_its_parent(self):
        not_created_yet = self.temporary_root / "staging" / "rips"

        volume = space_at(not_created_yet)

        self.assertTrue(volume.is_present)
        self.assertEqual(volume.total_bytes, space_at(self.temporary_root).total_bytes)

    def test_finds_the_nearest_folder_that_exists(self):
        self.assertEqual(
            nearest_existing_directory(self.temporary_root / "a" / "b" / "c"),
            self.temporary_root,
        )


if __name__ == "__main__":
    unittest.main()
