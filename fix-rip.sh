#!/usr/bin/env bash
# Repair a rip that stalls on the Roku: remux timestamps first, re-encode if that is not enough.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

is_reencoding=0
INPUT_FILE=""

usage() {
  cat <<EOF
Usage: ./fix-rip.sh [--reencode] "/path/to/Movie (Year).mp4"

Fixes a title that plays a few seconds, stalls, then speed-plays with no
audio. That is almost always a timestamp / variable-frame-rate problem in
that file, not the network.

Default: remux. Copies the video and first audio track into a new MP4
with rebuilt timestamps. Takes seconds. Try this first.

  --reencode   Run HandBrake again with a constant frame rate. Use this
               if remux still stalls on the Roku. Takes as long as a rip.

The original file is renamed to *.stalled.mp4 next to the fixed one.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reencode)
      is_reencoding=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    --*)
      print_error "Unknown argument: $1"
      usage
      exit 1
      ;;
    *)
      if [[ -n "$INPUT_FILE" ]]; then
        print_error "Unexpected argument: $1"
        usage
        exit 1
      fi
      INPUT_FILE="$1"
      shift
      ;;
  esac
done

require_ffmpeg() {
  if ffmpeg_command >/dev/null; then
    return
  fi
  print_step "Installing ffmpeg (needed to inspect and remux)"
  brew install ffmpeg
}

print_stream_summary() {
  local media_file="$1"
  local probe
  probe="$(ffprobe_command)"

  print_step "What's in this file"
  "$probe" -hide_banner "$media_file" 2>&1 | grep -E 'Input #|Duration:|Stream #'

  local video_rate
  local video_average_rate
  local video_duration
  local audio_duration
  video_rate="$("$probe" -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$media_file")"
  video_average_rate="$("$probe" -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of csv=p=0 "$media_file")"
  video_duration="$("$probe" -v error -select_streams v:0 -show_entries stream=duration -of csv=p=0 "$media_file")"
  audio_duration="$("$probe" -v error -select_streams a:0 -show_entries stream=duration -of csv=p=0 "$media_file")"

  print_line "  video frame rate (nominal)  ${video_rate}"
  print_line "  video frame rate (average)  ${video_average_rate}"
  print_line "  video duration              ${video_duration}"
  print_line "  audio duration              ${audio_duration}"

  if [[ "$video_rate" != "$video_average_rate" ]]; then
    print_line "  This looks variable-frame-rate. Roku often stalls, then catches up with no sound."
  fi
}

backup_path_for() {
  local media_file="$1"
  local directory
  local base
  directory="$(dirname "$media_file")"
  base="$(basename "$media_file")"
  printf '%s/%s.stalled.%s' "$directory" "${base%.*}" "${base##*.}"
}

remux_copy() {
  local source_file="$1"
  local destination_file="$2"
  local ffmpeg
  ffmpeg="$(ffmpeg_command)"

  print_step "Remuxing video + first audio with rebuilt timestamps"
  "$ffmpeg" -hide_banner -y \
    -fflags +genpts+discardcorrupt \
    -i "$source_file" \
    -map 0:v:0 -map 0:a:0 \
    -c copy \
    -sn \
    -movflags +faststart \
    "$destination_file"
}

reencode_constant_frame_rate() {
  local source_file="$1"
  local destination_file="$2"
  local handbrake
  handbrake="$(handbrake_command)"

  print_step "Re-encoding at a constant frame rate"
  print_line "Same Super HQ DVD preset as a new rip, plus --cfr so the Roku gets a steady clock."
  "$handbrake" \
    --input "$source_file" \
    --output "$destination_file" \
    --preset "Super HQ 480p30 Surround" \
    --format av_mp4 \
    --cfr \
    --optimize
}

replace_with_fixed_file() {
  local original_file="$1"
  local fixed_file="$2"
  local backup_file
  backup_file="$(backup_path_for "$original_file")"

  if [[ -e "$backup_file" ]]; then
    print_error "Backup already exists: ${backup_file}"
    print_error "Move that aside if you want to run this again."
    exit 1
  fi

  mv "$original_file" "$backup_file"
  mv "$fixed_file" "$original_file"
  print_line "Kept the old file as ${backup_file}"
}

require_macos
require_not_root

if [[ -z "$INPUT_FILE" ]]; then
  usage
  exit 1
fi

if [[ ! -f "$INPUT_FILE" ]]; then
  print_error "No file at ${INPUT_FILE}"
  exit 1
fi

if [[ "$is_reencoding" -eq 1 ]] && ! handbrake_command >/dev/null; then
  print_error "HandBrakeCLI is not installed. Run ./setup.sh on the server Mac first."
  exit 1
fi

require_ffmpeg
print_stream_summary "$INPUT_FILE"

temporary_file="${INPUT_FILE}.fixing.${RANDOM}"
cleanup_temporary_file() {
  rm -f "$temporary_file"
}
trap cleanup_temporary_file EXIT

print_line "Stop playback on the Roku before this replaces the file."

if [[ "$is_reencoding" -eq 1 ]]; then
  reencode_constant_frame_rate "$INPUT_FILE" "$temporary_file"
else
  remux_copy "$INPUT_FILE" "$temporary_file"
fi

replace_with_fixed_file "$INPUT_FILE" "$temporary_file"
trap - EXIT

print_line ""
print_line "Done. In Plex: Movies → Scan Library Files, then play this title on the Roku."
if [[ "$is_reencoding" -eq 0 ]]; then
  print_line "If it still stalls: ./fix-rip.sh --reencode \"${INPUT_FILE}\""
fi
