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
# finding ways to hit it in fewer bits, so it buys a smaller file rather than
# a better picture.
#
# Both Super HQ presets ask for "veryslow", which on an 8-core M1 Pro means 5-7
# hours for a Blu-ray. "slow" hits the same RF in 2-3 hours for roughly 5-10%
# more file size, which is the better trade at that size. A DVD is a sixth of
# the pixels and nowhere near those hours, so it gets the preset's answer back.
BLURAY_ENCODER_SPEED = "slow"
DVD_ENCODER_SPEED = "veryslow"

# RF, the constant quality target: lower keeps more of the source and costs
# file size. The Super HQ 480p preset asks for 16, which is conservative for a
# source this small -- a whole DVD is a few gigabytes, so the size a step down
# costs is not the constraint it is on a Blu-ray.
#
# Blu-ray is left on whatever its preset asks for. Nothing is said about it
# here because it has not been looked at the way this has.
DVD_QUALITY = "13"

# x264's "film" tune eases off the deblocking filter, which otherwise reads
# grain and fine texture as compression noise and smooths it away. A DVD of a
# live-action film has grain in it worth keeping; the alternative, "grain",
# keeps more still and costs far more size than the detail is worth here.
DVD_ENCODER_TUNE = "film"

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

# An NTSC DVD of a film is not 30fps video. It is 23.976fps film with 3:2
# pulldown laid over it to reach 29.97, which leaves two frames in every five
# holding fields from two different film frames. The preset spots those as
# interlacing and hands them to decomb, which interpolates a new picture --
# a guess, standing in for an original frame that is still on the disc.
#
# Detelecine reverses the pulldown instead and recovers the film frames
# themselves. It runs ahead of decomb in the filter chain, so by the time comb
# detection looks there is nothing left to find.
#
# HandBrake's note on this filter: it drops the duplicated frames to get back
# to 23.976 "unless you specify a constant framerate", which --cfr does. So the
# output stays 30fps and the duplicates stay in, costing almost nothing to
# encode, and the frames that are left are the real ones.
#
# A DVD that is genuinely 30fps video rather than telecined film has no pulldown
# pattern to find, so this does nothing to it and decomb still handles it.
DETELECINE_FLAG = "--detelecine"

# Lossless Blu-ray audio only fits in MKV, and only survives if "copy" is
# allowed to pass these through rather than re-encoding them.
SURROUND_COPY_MASK = "aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,flac"

MP4_EXTENSION = "mp4"
MKV_EXTENSION = "mkv"

# HandBrakeCLI uses 1 for "finished with warnings". That is still a usable file.
HIGHEST_SUCCESSFUL_EXIT_CODE = 1

# Subtitles from a disc are pictures, not text: PGS on Blu-ray, VobSub on DVD.
# No Roku can draw either, so Plex burns them into the video when one is
# switched on, which turns a direct play into a full transcode for the length
# of the film. That is a fine price for a film you need them for, and a steep
# one for every other disc, so nothing is flagged to come on by itself.
#
# "none" is passed rather than left out, because HandBrake will otherwise pick
# a default of its own and the whole point is that it should not.
NO_DEFAULT_SUBTITLE = "none"


@dataclass(frozen=True)
class EncodeSettings:
    """The choices that turn one raw rip into one playable file."""

    disc_format: str
    audio_languages: str = ""
    is_keeping_subtitles: bool = False
    quality_override: str = ""

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
    def encoder_speed(self) -> str:
        if self.is_bluray:
            return BLURAY_ENCODER_SPEED
        return DVD_ENCODER_SPEED

    @property
    def quality(self) -> str:
        """The RF to aim at, or nothing to leave the preset's own alone."""
        if self.quality_override:
            return self.quality_override
        if self.is_bluray:
            return ""
        return DVD_QUALITY

    @property
    def encoder_tune(self) -> str:
        if self.is_bluray:
            return ""
        return DVD_ENCODER_TUNE

    @property
    def is_keeping_extra_tracks(self) -> bool:
        return bool(self.audio_languages) or self.is_keeping_subtitles

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
            *self._filter_arguments(),
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
        if self.quality:
            arguments += ["--quality", self.quality]
        if self.encoder_speed:
            arguments += ["--encoder-preset", self.encoder_speed]
        if self.encoder_tune:
            arguments += ["--encoder-tune", self.encoder_tune]
        return arguments

    def _filter_arguments(self) -> list[str]:
        """Picture filters beyond what the preset already turns on."""
        if self.is_bluray:
            return []
        return [DETELECINE_FLAG]

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
        if self.is_keeping_subtitles:
            # No language list, which is how HandBrake is told "every track":
            # --all-subtitles takes any language when it has no list to match
            # against. Naming languages would mean guessing at rip time which
            # ones a disc carries, and the cost of guessing wrong is another
            # trip through the drive, against a few megabytes for being wrong
            # the generous way.
            #
            # Which one to read is left to Plex, where it is a menu rather than
            # a re-rip. Nothing is burned into the picture, so that choice
            # stays open rather than being made once, here, forever.
            arguments += [
                "--all-subtitles",
                "--subtitle-default",
                NO_DEFAULT_SUBTITLE,
                "--subtitle-burned",
                "none",
            ]
        return arguments
