#!/usr/bin/env bash
# Report whether a ripped file is something a Roku can play directly, or
# something Plex will have to transcode on its behalf.
#
# Plex hides this: a file that a Roku cannot decode still "works", it just gets
# re-encoded on the fly, which is where stuttering and failures come from.
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIRECTORY}/common.sh"

# What a Roku decodes in hardware. Anything else is Plex's problem to solve.
ROKU_VIDEO_CODECS="h264 hevc vp9"
ROKU_AUDIO_CODECS="aac ac3 eac3 mp3 pcm_s16le flac opus vorbis"
ROKU_MAX_H264_LEVEL=42

usage() {
  cat <<EOF
Usage:
  ./scripts/check-rip.sh /path/to/file.mp4 [more files...]
  ./scripts/check-rip.sh              checks everything in the library

Says, for each file, whether a Roku can play it as-is or whether Plex will have
to transcode it. Transcoding is the usual cause of stuttering and playback
failures on a Roku.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

require_ffmpeg_tools() {
  if ! ffprobe_command >/dev/null 2>&1; then
    print_error "ffprobe is missing. Install it with: brew install ffmpeg"
    exit 1
  fi
}

# ffprobe reports H.264 level as an integer: 40 is level 4.0, 41 is 4.1.
describe_level() {
  local level="$1"
  if [[ ! "$level" =~ ^[0-9]+$ || "$level" -le 0 ]]; then
    printf 'unknown'
    return
  fi
  printf '%d.%d' "$((level / 10))" "$((level % 10))"
}

check_one_file() {
  local file_path="$1"
  local ffprobe
  local streams
  local video_codec video_level video_profile video_refs
  local audio_codecs
  local subtitle_codecs
  local -a complaints=()
  ffprobe="$(ffprobe_command)"

  streams="$("$ffprobe" -v error -show_entries \
    stream=codec_type,codec_name,profile,level,refs -of json "$file_path" 2>/dev/null || true)"
  if [[ -z "$streams" ]]; then
    print_error "${file_path}: ffprobe could not read this file"
    return 1
  fi

  video_codec="$(stream_field "$streams" video codec_name)"
  video_profile="$(stream_field "$streams" video profile)"
  video_level="$(stream_field "$streams" video level)"
  video_refs="$(stream_field "$streams" video refs)"
  audio_codecs="$(stream_fields "$streams" audio codec_name)"
  subtitle_codecs="$(stream_fields "$streams" subtitle codec_name)"

  if [[ " $ROKU_VIDEO_CODECS " != *" $video_codec "* ]]; then
    complaints+=("video is ${video_codec}, which a Roku cannot decode")
  fi

  if [[ "$video_codec" == "h264" ]]; then
    if [[ "$video_level" =~ ^[0-9]+$ && "$video_level" -gt "$ROKU_MAX_H264_LEVEL" ]]; then
      complaints+=("H.264 level $(describe_level "$video_level") is above the 4.2 a Roku allows")
    fi
    # Level 4.0 and below permit 4 reference frames at 1080p. More than that is
    # the usual reason a technically-valid file will not play.
    if [[ "$video_refs" =~ ^[0-9]+$ && "$video_refs" -gt 4 ]]; then
      complaints+=("${video_refs} reference frames, more than the 4 a Roku handles at 1080p")
    fi
  fi

  local audio_codec
  for audio_codec in $audio_codecs; do
    if [[ " $ROKU_AUDIO_CODECS " != *" $audio_codec "* ]]; then
      complaints+=("audio track is ${audio_codec}, which a Roku cannot decode")
    fi
  done

  local subtitle_codec
  for subtitle_codec in $subtitle_codecs; do
    if [[ "$subtitle_codec" == "hdmv_pgs_subtitle" || "$subtitle_codec" == "dvd_subtitle" ]]; then
      complaints+=("${subtitle_codec} subtitles are images, so Plex burns them in when switched on")
    fi
  done

  printf '\n%s\n' "$(basename "$file_path")"
  printf '  video       %s %s, level %s, %s ref frames\n' \
    "$video_codec" "${video_profile:-?}" "$(describe_level "$video_level")" "${video_refs:-?}"
  printf '  audio       %s\n' "${audio_codecs:-none}"
  if [[ -n "$subtitle_codecs" ]]; then
    printf '  subtitles   %s\n' "$subtitle_codecs"
  fi

  if [[ "${#complaints[@]}" -eq 0 ]]; then
    printf '  verdict     plays directly on a Roku\n'
    return 0
  fi

  printf '  verdict     Plex will have to transcode this\n'
  local complaint
  for complaint in "${complaints[@]}"; do
    printf '              - %s\n' "$complaint"
  done
  return 1
}

stream_field() {
  python3 -c '
import json
import sys

streams = json.loads(sys.argv[1]).get("streams", [])
for stream in streams:
    if stream.get("codec_type") == sys.argv[2]:
        print(stream.get(sys.argv[3], ""))
        break
' "$1" "$2" "$3"
}

stream_fields() {
  python3 -c '
import json
import sys

streams = json.loads(sys.argv[1]).get("streams", [])
print(" ".join(
    str(stream.get(sys.argv[3], ""))
    for stream in streams
    if stream.get("codec_type") == sys.argv[2]
))
' "$1" "$2" "$3"
}

require_macos
require_ffmpeg_tools

files=("$@")
if [[ "${#files[@]}" -eq 0 ]]; then
  print_step "Checking everything in $(resolved_media_root)"
  while IFS= read -r -d '' found; do
    files+=("$found")
  done < <(find "$(resolved_media_root)" \( -name '*.mp4' -o -name '*.mkv' \) \
    -not -path '*/Rips/*' -print0 2>/dev/null | sort -z)
fi

if [[ "${#files[@]}" -eq 0 ]]; then
  print_error "No .mp4 or .mkv files found. Pass one explicitly."
  exit 1
fi

problem_count=0
for file in "${files[@]}"; do
  check_one_file "$file" || problem_count=$((problem_count + 1))
done

printf '\n'
if [[ "$problem_count" -eq 0 ]]; then
  print_line "All ${#files[@]} file(s) play directly on a Roku."
  exit 0
fi

print_line "${problem_count} of ${#files[@]} file(s) will make Plex transcode."
print_line "Re-ripping those with the current settings should fix it."
exit 1
