import unittest
from pathlib import Path

from media_server.encode_settings import (
    BLURAY_PRESET,
    DVD_PRESET,
    ENCODER_PROFILE,
    EncodeSettings,
)


class PresetChoiceTests(unittest.TestCase):
    def test_each_disc_format_gets_its_own_preset_level_and_rate(self):
        cases = [
            ("dvd", DVD_PRESET, "3.1", "--cfr"),
            ("bluray", BLURAY_PRESET, "4.0", "--pfr"),
        ]

        for disc_format, preset, encoder_level, rate_flag in cases:
            with self.subTest(disc_format=disc_format):
                settings = EncodeSettings(disc_format=disc_format)

                self.assertEqual(settings.preset, preset)
                self.assertEqual(settings.encoder_level, encoder_level)
                self.assertEqual(settings.rate_flag, rate_flag)


class ContainerChoiceTests(unittest.TestCase):
    def test_mp4_is_the_default_because_it_starts_playing_sooner(self):
        settings = EncodeSettings(disc_format="bluray")

        self.assertEqual(settings.container_extension, "mp4")
        self.assertIn("--optimize", settings.command_arguments(Path("in"), Path("out")))

    def test_keeping_subtitles_forces_mkv(self):
        settings = EncodeSettings(disc_format="bluray", subtitle_languages="eng")

        self.assertEqual(settings.container_extension, "mkv")

    def test_keeping_extra_audio_forces_mkv(self):
        settings = EncodeSettings(disc_format="bluray", audio_languages="eng,fra")

        self.assertEqual(settings.container_extension, "mkv")


class RokuCompatibilityTests(unittest.TestCase):
    """The Roku refuses streams past High profile at the level for the size."""

    def test_profile_and_level_are_pinned_rather_than_left_to_the_preset(self):
        settings = EncodeSettings(disc_format="bluray")

        arguments = settings.command_arguments(Path("in.mkv"), Path("out.mp4"))

        self.assertIn("--encoder-profile", arguments)
        self.assertEqual(arguments[arguments.index("--encoder-profile") + 1], ENCODER_PROFILE)
        self.assertEqual(arguments[arguments.index("--encoder-level") + 1], "4.0")

    def test_a_dvd_is_held_to_the_lower_level(self):
        settings = EncodeSettings(disc_format="dvd")

        arguments = settings.command_arguments(Path("in.mkv"), Path("out.mp4"))

        self.assertEqual(arguments[arguments.index("--encoder-level") + 1], "3.1")

    def test_the_speed_preset_is_slow_rather_than_veryslow(self):
        settings = EncodeSettings(disc_format="bluray")

        arguments = settings.command_arguments(Path("in.mkv"), Path("out.mp4"))

        self.assertEqual(arguments[arguments.index("--encoder-preset") + 1], "slow")


class CommandArgumentTests(unittest.TestCase):
    SOURCE_FILE = Path("/staging/raw/title_t00.mkv")
    OUTPUT_FILE = Path("/staging/encoded/The Matrix (1999).mp4")

    def test_the_input_and_output_are_passed_through(self):
        settings = EncodeSettings(disc_format="bluray")

        arguments = settings.command_arguments(self.SOURCE_FILE, self.OUTPUT_FILE)

        self.assertEqual(arguments[arguments.index("--input") + 1], str(self.SOURCE_FILE))
        self.assertEqual(
            arguments[arguments.index("--output") + 1], str(self.OUTPUT_FILE)
        )

    def test_a_quality_override_is_only_sent_when_asked_for(self):
        without_override = EncodeSettings(disc_format="bluray").command_arguments(
            self.SOURCE_FILE, self.OUTPUT_FILE
        )
        with_override = EncodeSettings(
            disc_format="bluray", quality_override="18"
        ).command_arguments(self.SOURCE_FILE, self.OUTPUT_FILE)

        self.assertNotIn("--quality", without_override)
        self.assertEqual(with_override[with_override.index("--quality") + 1], "18")

    def test_subtitles_are_carried_but_left_switched_off(self):
        settings = EncodeSettings(disc_format="bluray", subtitle_languages="eng")

        arguments = settings.command_arguments(self.SOURCE_FILE, self.OUTPUT_FILE)

        self.assertIn("--all-subtitles", arguments)
        self.assertEqual(arguments[arguments.index("--subtitle-burned") + 1], "none")
        self.assertEqual(arguments[arguments.index("--subtitle-default") + 1], "none")

    def test_lossless_audio_is_copied_rather_than_re_encoded(self):
        settings = EncodeSettings(disc_format="bluray", audio_languages="eng")

        arguments = settings.command_arguments(self.SOURCE_FILE, self.OUTPUT_FILE)

        copy_mask = arguments[arguments.index("--audio-copy-mask") + 1]
        self.assertIn("truehd", copy_mask)
        self.assertIn("dtshd", copy_mask)


if __name__ == "__main__":
    unittest.main()
