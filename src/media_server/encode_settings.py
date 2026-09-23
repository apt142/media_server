"""What to ask HandBrake for, and why.

The settings here are the ones the shell scripts arrived at the hard way, and
the comments are the reasons rather than the values.
"""

from __future__ import annotations

from dataclasses import dataclass

from .job_catalog import BLURAY_DISC_FORMAT

# Both presets are the "Super HQ" family so a Blu-ray is treated at least as
# carefully as a DVD. The plain "HQ 1080p30 Surround" preset is RF 20 on x264
# "slow", against RF 16 "veryslow" for DVDs, which encodes the higher quality
# source less carefully than the lower quality one.
DVD_PRESET = "Super HQ 480p30 Surround"
BLURAY_PRESET = "Super HQ 1080p30 Surround"

# How hard x264 works, which is a separate thing from how good the result
# looks. RF is the quality target; this only decides how long x264 spends
# finding ways to hit it in fewer bits.
#
# Both Super HQ presets ask for "veryslow", which on an 8-core M1 Pro means 5-7
# hours for a Blu-ray. "slow" hits the same RF in 2-3 hours for roughly 5-10%
# more file size, so it is the better default on this hardware.
ENCODER_SPEED = "slow"

# Roku decodes H.264 in hardware and is strict about it: High profile, level
# 4.0 at 1080p and 3.1 at 480p. Go past that -- most easily by letting x264
# keep more reference frames than the level allows -- and the Roku refuses the
# stream, so Plex falls back to transcoding or playback fails outright.
#
# The presets do set these, but they are pinned rather than trusted, because
# overriding one video setting on the command line can leave HandBrake applying
# its own defaults for the rest.
ENCODER_PROFILE = "high"
DVD_ENCODER_LEVEL = "3.1"
BLURAY_ENCODER_LEVEL = "4.0"

# DVDs carry telecine flags that produce the timestamps Roku stalls on, so they
# get a constant rate. Blu-ray video is already constant, and forcing 30 there
# would only duplicate frames on a 24fps film.
DVD_RATE_FLAG = "--cfr"
BLURAY_RATE_FLAG = "--pfr"

# Lossless Blu-ray audio only fits in MKV, and only survives if "copy" is
# allowed to pass these through rather than re-encoding them.
SURROUND_COPY_MASK = "aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,flac"

MP4_EXTENSION = "mp4"
MKV_EXTENSION = "mkv"

# HandBrakeCLI uses 1 for "finished with warnings". That is still a usable file.
HIGHEST_SUCCESSFUL_EXIT_CODE = 1


@dataclass(frozen=True)
class EncodeSettings:
    """The choices that turn one raw rip into one playable file."""

    disc_format: str
    audio_languages: str = ""
    subtitle_languages: str = ""
    quality_override: str = ""
    encoder_speed: str = ENCODER_SPEED

    @property
    def is_bluray(self) -> bool:
        return self.disc_format == BLURAY_DISC_FORMAT

    @property
    def preset(self) -> str:
        if self.is_bluray:
            return BLURAY_PRESET
        return DVD_PRESET

    @property
    def encoder_level(self) -> str:
        if self.is_bluray:
            return BLURAY_ENCODER_LEVEL
        return DVD_ENCODER_LEVEL

    @property
    def rate_flag(self) -> str:
        if self.is_bluray:
            return BLURAY_RATE_FLAG
        return DVD_RATE_FLAG

    @property
    def is_keeping_extra_tracks(self) -> bool:
        return bool(self.audio_languages or self.subtitle_languages)

    @property
    def container_extension(self) -> str:
        """MP4 cannot carry bitmap subtitles, nor TrueHD and DTS-HD.

        Asking for either means the output has to be MKV. Plex and Roku play
        both, so MP4 is only the default because it starts playing sooner.
        """
        if self.is_keeping_extra_tracks:
            return MKV_EXTENSION
        return MP4_EXTENSION

    def command_arguments(self, source_file, output_file) -> list[str]:
        """The full HandBrakeCLI argument list for one file."""
        return [
            "--input",
            str(source_file),
            "--output",
            str(output_file),
            "--preset",
            self.preset,
            self.rate_flag,
            *self._container_arguments(),
            *self._video_arguments(),
            *self._track_arguments(),
        ]

    def _container_arguments(self) -> list[str]:
        if self.is_keeping_extra_tracks:
            return ["--format", "av_mkv"]
        # --optimize moves the MP4 index to the front so playback starts sooner.
        return ["--format", "av_mp4", "--optimize"]

    def _video_arguments(self) -> list[str]:
        # Profile and level go first so nothing later can quietly widen them.
        arguments = [
            "--encoder-profile",
            ENCODER_PROFILE,
            "--encoder-level",
            self.encoder_level,
        ]
        if self.quality_override:
            arguments += ["--quality", self.quality_override]
        if self.encoder_speed:
            arguments += ["--encoder-preset", self.encoder_speed]
        return arguments

    def _track_arguments(self) -> list[str]:
        arguments = []
        if self.audio_languages:
            arguments += [
                "--all-audio",
                "--audio-lang-list",
                self.audio_languages,
                "--audio-copy-mask",
                SURROUND_COPY_MASK,
                "--audio-fallback",
                "ac3",
            ]
        if self.subtitle_languages:
            # Carry the subtitles but leave them switched off, so nothing is
            # burned into the picture and Plex can offer them as a choice.
            arguments += [
                "--all-subtitles",
                "--subtitle-lang-list",
                self.subtitle_languages,
                "--subtitle-default",
                "none",
                "--subtitle-burned",
                "none",
            ]
        return arguments
