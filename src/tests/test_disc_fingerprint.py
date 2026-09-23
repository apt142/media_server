import unittest

from media_server.disc_fingerprint import (
    FINGERPRINT_LENGTH,
    fingerprint_for_disc,
    normalize_disc_label,
)


class DiscFingerprintTests(unittest.TestCase):
    DISC_LABEL = "THE_MATRIX"
    TITLE_LENGTHS_SECONDS = [8160, 300, 180]

    def test_the_same_disc_fingerprints_the_same_way_every_time(self):
        first_scan = fingerprint_for_disc(self.DISC_LABEL, self.TITLE_LENGTHS_SECONDS)
        second_scan = fingerprint_for_disc(self.DISC_LABEL, self.TITLE_LENGTHS_SECONDS)

        self.assertEqual(first_scan, second_scan)

    def test_scan_order_does_not_change_the_fingerprint(self):
        in_scan_order = fingerprint_for_disc(self.DISC_LABEL, [8160, 300, 180])
        in_another_order = fingerprint_for_disc(self.DISC_LABEL, [180, 8160, 300])

        self.assertEqual(in_scan_order, in_another_order)

    def test_two_discs_sharing_a_generic_label_are_told_apart(self):
        first_disc = fingerprint_for_disc("DVD_VIDEO", [5400, 120])
        second_disc = fingerprint_for_disc("DVD_VIDEO", [7020, 240])

        self.assertNotEqual(first_disc, second_disc)

    def test_two_discs_with_the_same_titles_but_different_labels_differ(self):
        first_disc = fingerprint_for_disc("FELLOWSHIP_D1", self.TITLE_LENGTHS_SECONDS)
        second_disc = fingerprint_for_disc("FELLOWSHIP_D2", self.TITLE_LENGTHS_SECONDS)

        self.assertNotEqual(first_disc, second_disc)

    def test_a_disc_with_no_titles_still_fingerprints(self):
        self.assertEqual(
            len(fingerprint_for_disc(self.DISC_LABEL, [])), FINGERPRINT_LENGTH
        )

    def test_the_fingerprint_is_short_enough_to_read_in_a_log(self):
        fingerprint = fingerprint_for_disc(self.DISC_LABEL, self.TITLE_LENGTHS_SECONDS)

        self.assertEqual(len(fingerprint), FINGERPRINT_LENGTH)


class DiscLabelNormalizationTests(unittest.TestCase):
    def test_trivial_label_differences_are_flattened(self):
        cases = [
            ("THE_MATRIX", "THE_MATRIX"),
            ("the matrix", "THE_MATRIX"),
            ("The-Matrix!", "THE_MATRIX"),
            ("  THE  MATRIX  ", "THE_MATRIX"),
            ("THE_MATRIX_", "THE_MATRIX"),
        ]

        for disc_label, expected_label in cases:
            with self.subTest(disc_label=disc_label):
                self.assertEqual(normalize_disc_label(disc_label), expected_label)

    def test_labels_that_differ_only_in_punctuation_fingerprint_alike(self):
        title_lengths = [5400]

        self.assertEqual(
            fingerprint_for_disc("The Matrix", title_lengths),
            fingerprint_for_disc("THE_MATRIX", title_lengths),
        )


if __name__ == "__main__":
    unittest.main()
