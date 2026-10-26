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
        settings = EncodeSettings(disc_format="bluray", is_keeping_subtitles=True)

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

    def test_an_override_beats_the_quality_a_dvd_would_otherwise_get(self):
        settings = EncodeSettings(disc_format="dvd", quality_override="20")

        arguments = settings.command_arguments(self.SOURCE_FILE, self.OUTPUT_FILE)

        self.assertEqual(arguments[arguments.index("--quality") + 1], "20")


class DvdPictureTests(unittest.TestCase):
    """A DVD is a small, awkward source and gets treated as one."""

    SOURCE_FILE = Path("/staging/raw/title-00/title_t00.mkv")
    OUTPUT_FILE = Path("/staging/encoded/job-00001/The Matrix (1999).mp4")

    def _arguments_for(self, disc_format: str) -> list[str]:
        return EncodeSettings(disc_format=disc_format).command_arguments(
            self.SOURCE_FILE, self.OUTPUT_FILE
        )

    def test_a_dvd_is_detelecined_so_film_frames_are_recovered_not_guessed(self):
        """Without this, decomb interpolates two frames in every five.

        An NTSC DVD of a film is 23.976fps with 3:2 pulldown over it. The
        preset reads the mixed frames as interlacing and has decomb invent a
        picture, when the original frame is still on the disc.
        """
        self.assertIn("--detelecine", self._arguments_for("dvd"))

    def test_a_bluray_is_left_alone(self):
        """Blu-ray video is already progressive and already the film's rate."""
        arguments = self._arguments_for("bluray")

        self.assertNotIn("--detelecine", arguments)
        self.assertNotIn("--encoder-tune", arguments)
        self.assertNotIn("--quality", arguments)

    def test_a_dvd_aims_at_a_higher_quality_than_its_preset_asks_for(self):
        arguments = self._arguments_for("dvd")

        self.assertEqual(arguments[arguments.index("--quality") + 1], "13")

    def test_a_dvd_keeps_grain_rather_than_deblocking_it_away(self):
        arguments = self._arguments_for("dvd")

        self.assertEqual(arguments[arguments.index("--encoder-tune") + 1], "film")

    def test_a_dvd_is_encoded_as_slowly_as_its_preset_wants(self):
        """Speed buys file size, not picture, and a DVD is cheap to encode.

        The Blu-ray case is the one that cannot afford veryslow: 5-7 hours
        against 2-3, for 5-10% of file size.
        """
        self.assertEqual(self._encoder_speed_for("dvd"), "veryslow")
        self.assertEqual(self._encoder_speed_for("bluray"), "slow")

    def _encoder_speed_for(self, disc_format: str) -> str:
        arguments = self._arguments_for(disc_format)
        return arguments[arguments.index("--encoder-preset") + 1]

    def test_every_subtitle_track_is_taken_whatever_its_language(self):
        """No language list is how HandBrake is told to take any language.

        Naming languages would mean guessing at rip time what a disc carries,
        and being wrong means putting the disc back in the drive.
        """
        settings = EncodeSettings(disc_format="bluray", is_keeping_subtitles=True)

        arguments = settings.command_arguments(self.SOURCE_FILE, self.OUTPUT_FILE)

        self.assertIn("--all-subtitles", arguments)
        self.assertNotIn("--subtitle-lang-list", arguments)

    def test_no_subtitle_is_switched_on_by_itself(self):
        """A switched-on track costs a transcode on every Roku, every time.

        Disc subtitles are pictures, which no Roku can draw, so Plex burns a
        default track into the video for the length of the film. "none" is
        said rather than left out, because HandBrake picks its own otherwise.
        """
        settings = EncodeSettings(disc_format="bluray", is_keeping_subtitles=True)

        arguments = settings.command_arguments(self.SOURCE_FILE, self.OUTPUT_FILE)

        self.assertEqual(arguments[arguments.index("--subtitle-default") + 1], "none")
        self.assertEqual(arguments[arguments.index("--subtitle-burned") + 1], "none")

    def test_lossless_audio_is_copied_rather_than_re_encoded(self):
        settings = EncodeSettings(disc_format="bluray", audio_languages="eng")

        arguments = settings.command_arguments(self.SOURCE_FILE, self.OUTPUT_FILE)

        copy_mask = arguments[arguments.index("--audio-copy-mask") + 1]
        self.assertIn("truehd", copy_mask)
        self.assertIn("dtshd", copy_mask)


if __name__ == "__main__":
    unittest.main()
