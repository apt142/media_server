import unittest

from media_server.disc_drive import DiscDrive, parse_drutil_status
from tests import drutil_fixtures


class RecordingDrutil:
    """Stands in for the drutil command and remembers how it was called."""

    def __init__(self, status_output: str = ""):
        self.status_output = status_output
        self.received_arguments: list[list[str]] = []

    def __call__(self, arguments: list[str]) -> str:
        self.received_arguments.append(arguments)
        if arguments == ["status"]:
            return self.status_output
        return ""


class DrutilStatusParsingTests(unittest.TestCase):
    def test_reads_presence_and_media_kind_from_drive_output(self):
        cases = [
            ("dvd", drutil_fixtures.DVD_IN_DRIVE, True, "DVD-ROM", "dvd"),
            ("bluray", drutil_fixtures.BLURAY_IN_DRIVE, True, "BD-ROM", "bluray"),
            ("empty", drutil_fixtures.EMPTY_DRIVE, False, "", "dvd"),
            ("no drive", drutil_fixtures.NO_DRIVE_ATTACHED, False, "", "dvd"),
        ]

        for name, status_output, is_present, media_type, media_kind in cases:
            with self.subTest(disc=name):
                status = parse_drutil_status(status_output)

                self.assertEqual(status.is_present, is_present)
                self.assertEqual(status.media_type, media_type)
                self.assertEqual(status.media_kind, media_kind)

    def test_reads_the_device_path_for_a_disc_that_is_present(self):
        status = parse_drutil_status(drutil_fixtures.DVD_IN_DRIVE)

        self.assertEqual(status.device_path, "/dev/disk4")

    def test_book_type_is_not_mistaken_for_the_type_field(self):
        status = parse_drutil_status(drutil_fixtures.BLURAY_IN_DRIVE)

        self.assertEqual(status.media_type, "BD-ROM")
        self.assertTrue(status.is_bluray)

    def test_a_dvd_is_not_reported_as_bluray(self):
        status = parse_drutil_status(drutil_fixtures.DVD_IN_DRIVE)

        self.assertFalse(status.is_bluray)


class DiscDriveTests(unittest.TestCase):
    def test_reports_a_disc_the_drive_is_holding(self):
        drutil = RecordingDrutil(drutil_fixtures.BLURAY_IN_DRIVE)
        drive = DiscDrive(run_command=drutil)

        self.assertTrue(drive.is_disc_present())
        self.assertEqual(drutil.received_arguments, [["status"]])

    def test_reports_an_empty_drive(self):
        drive = DiscDrive(run_command=RecordingDrutil(drutil_fixtures.EMPTY_DRIVE))

        self.assertFalse(drive.is_disc_present())

    def test_eject_asks_the_drive_to_open(self):
        drutil = RecordingDrutil()
        drive = DiscDrive(run_command=drutil)

        drive.eject()

        self.assertEqual(drutil.received_arguments, [["eject"]])


if __name__ == "__main__":
    unittest.main()
