#!/usr/bin/env bash
# Rip an owned DVD from the USB optical drive, convert to H.264, drop it in Movies.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

MINIMUM_TITLE_LENGTH_SECONDS=300
HANDBRAKE_PRESET="Super HQ 480p30 Surround"
is_keeping_raw_rip=0
is_copying_without_encode=0
MOVIE_TITLE=""
MOVIE_YEAR=""

usage() {
  cat <<EOF
Usage: ./rip-dvd.sh "Movie Title" [year]

Reads the disc in the USB drive with MakeMKV, then converts the main title
with HandBrake's Super HQ 480p preset (slow x264, DVD deinterlace). That
is meant to look like the disc, in a file the Roku can play directly.

  ${MOVIES_DIRECTORY}/Movie Title (Year)/Movie Title (Year).mp4

  --min-length SECONDS   Ignore titles shorter than this (default: ${MINIMUM_TITLE_LENGTH_SECONDS})
  --keep-raw             Leave the MakeMKV .mkv in ${RIPS_DIRECTORY}/raw
  --direct               Skip HandBrake. Copy the decrypted DVD stream as
                         .mkv. Closest to the disc; Plex will transcode it
                         for the Roku (fine for 480p on an M1).
  -h, --help             Show this help

Only rip discs you own.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --min-length)
      MINIMUM_TITLE_LENGTH_SECONDS="${2:-}"
      if [[ -z "$MINIMUM_TITLE_LENGTH_SECONDS" ]]; then
        print_error "--min-length needs a number of seconds"
        exit 1
      fi
      shift 2
      ;;
    --keep-raw)
      is_keeping_raw_rip=1
      shift
      ;;
    --direct)
      is_copying_without_encode=1
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
      if [[ -z "$MOVIE_TITLE" ]]; then
        MOVIE_TITLE="$1"
      elif [[ -z "$MOVIE_YEAR" ]]; then
        MOVIE_YEAR="$1"
      else
        print_error "Unexpected argument: $1"
        usage
        exit 1
      fi
      shift
      ;;
  esac
done

plex_folder_name() {
  if [[ -n "$MOVIE_YEAR" ]]; then
    printf '%s (%s)' "$MOVIE_TITLE" "$MOVIE_YEAR"
    return
  fi
  printf '%s' "$MOVIE_TITLE"
}

largest_file_in_directory() {
  local directory="$1"
  local largest_path=""
  local largest_size=0
  local file_path
  local file_size

  while IFS= read -r -d '' file_path; do
    file_size="$(stat -f '%z' "$file_path")"
    if [[ "$file_size" -gt "$largest_size" ]]; then
      largest_size="$file_size"
      largest_path="$file_path"
    fi
  done < <(find "$directory" -type f -name '*.mkv' -print0)

  printf '%s' "$largest_path"
}

rip_disc() {
  local destination="$1"
  mkdir -p "$destination"

  print_step "Reading the disc with MakeMKV"
  print_line "This can take 20–40 minutes. Leave the USB drive plugged in."

  "$MAKE_MKV_COMMAND" --minlength="$MINIMUM_TITLE_LENGTH_SECONDS" -r --decrypt mkv disc:0 all "$destination"
}

copy_main_title() {
  local raw_mkv="$1"
  local output_file="$2"

  print_step "Copying the decrypted DVD stream (no re-encode)"
  mkdir -p "$(dirname "$output_file")"
  cp "$raw_mkv" "$output_file"
}

convert_main_title() {
  local raw_mkv="$1"
  local output_file="$2"
  local handbrake
  handbrake="$(handbrake_command)"

  print_step "Converting with ${HANDBRAKE_PRESET}"
  print_line "Slow x264 at DVD resolution. This is the quality pass; the MakeMKV read was only decryption."

  mkdir -p "$(dirname "$output_file")"

  "$handbrake" \
    --input "$raw_mkv" \
    --output "$output_file" \
    --preset "$HANDBRAKE_PRESET" \
    --format av_mp4 \
    --cfr \
    --optimize
}

require_macos
require_not_root

if [[ -z "$MOVIE_TITLE" ]]; then
  usage
  exit 1
fi

if [[ ! -x "$MAKE_MKV_COMMAND" ]]; then
  print_error "MakeMKV is not installed. Run ./setup.sh on the server Mac first."
  exit 1
fi

if [[ "$is_copying_without_encode" -eq 0 ]] && ! handbrake_command >/dev/null; then
  print_error "HandBrakeCLI is not installed. Run ./setup.sh on the server Mac first."
  exit 1
fi

library_name="$(plex_folder_name)"
raw_directory="${RIPS_DIRECTORY}/raw/${library_name}"
output_directory="${MOVIES_DIRECTORY}/${library_name}"
output_file="${output_directory}/${library_name}.mp4"
if [[ "$is_copying_without_encode" -eq 1 ]]; then
  output_file="${output_directory}/${library_name}.mkv"
fi

if [[ -e "$output_file" ]]; then
  print_error "Already exists: ${output_file}"
  exit 1
fi

rip_disc "$raw_directory"

raw_mkv="$(largest_file_in_directory "$raw_directory")"
if [[ -z "$raw_mkv" ]]; then
  print_error "MakeMKV did not produce an .mkv. Is a disc in the USB drive? Is MakeMKV registered?"
  exit 1
fi

print_line "Main title: ${raw_mkv}"
if [[ "$is_copying_without_encode" -eq 1 ]]; then
  copy_main_title "$raw_mkv" "$output_file"
else
  convert_main_title "$raw_mkv" "$output_file"
fi

if [[ "$is_keeping_raw_rip" -eq 0 ]]; then
  print_step "Removing the raw MakeMKV files (pass --keep-raw to keep them)"
  rm -rf "$raw_directory"
fi

print_line ""
print_line "Done: ${output_file}"
print_line "Plex should pick it up on the next library scan. If not: Plex → More → Scan Library Files."
