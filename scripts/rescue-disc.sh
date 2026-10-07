#!/usr/bin/env bash
# Copy a disc that hangs the drive, one pass per run, then rip from the copy.
#
# Usage:
#   ./scripts/rescue-disc.sh "Red Sparrow"            read forwards from where it stopped
#   ./scripts/rescue-disc.sh --reverse "Red Sparrow"  read backwards from the end
#   ./scripts/rescue-disc.sh --from 15G "Red Sparrow" start past a spot that hangs the drive
#
# MakeMKV reads a title in one go, so a disc that stops the drive part way
# through loses everything read so far and starts from the beginning next time.
# It then hits the same place and fails again, which is why trying repeatedly
# gets nowhere.
#
# ddrescue keeps a map of what it already has, so each run continues rather than
# restarts. This script runs exactly one pass and then stops, because the drive
# this was written for does not come back on its own: it needs unplugging and
# plugging in again, and no script can do that part.
#
# The intended sequence for a disc with one bad spot is two runs:
#
#   1. a forward pass, which stops where the trouble is
#   2. a reverse pass, which fills in everything after it
#
# What is left is the bad region itself, which stays as zeros in the image. A
# hole of a hundred megabytes is a few seconds of glitched video in a film that
# otherwise would not have been ripped at all.
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "${script_directory}/common.sh"

# Optical media is addressed in 2048 byte sectors. Telling ddrescue so stops it
# reading across a sector boundary and losing a good sector to a bad neighbour.
SECTOR_SIZE=2048

is_reading_backwards=0
start_position=""
disc_name=""

usage() {
  cat <<EOF
Usage:
  ./scripts/rescue-disc.sh "Red Sparrow"             read forwards from where it stopped
  ./scripts/rescue-disc.sh --reverse "Red Sparrow"   read backwards from the end
  ./scripts/rescue-disc.sh --from 15G "Red Sparrow"  start past a spot that hangs the drive

Runs one pass, then stops. Progress is kept in a map beside the image, so the
next run carries on instead of starting over. Run it again after plugging the
drive back in.

Images land in ${RIPS_DIRECTORY}/images.
EOF
}

read_arguments() {
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      -h|--help) usage; exit 0 ;;
      --reverse) is_reading_backwards=1; shift ;;
      --from)
        start_position="${2:-}"
        if [[ -z "$start_position" ]]; then
          print_error "--from needs a position, such as --from 15G"
          exit 1
        fi
        shift 2
        ;;
      -*) print_error "Unknown option: $1"; usage; exit 1 ;;
      *) disc_name="$1"; shift ;;
    esac
  done
}

require_ddrescue() {
  if command -v ddrescue >/dev/null 2>&1; then
    return
  fi
  print_error "ddrescue is not installed."
  print_line "  brew install ddrescue"
  exit 1
}

# drutil prints the node as "Name: /dev/disk4". The raw node is what gets read:
# /dev/rdiskN skips the buffer cache, which matters when the whole point is
# knowing exactly which bytes came off the disc.
optical_device_node() {
  local buffered_node
  buffered_node="$(drutil status 2>/dev/null | sed -n 's/.*Name: *\([^ ]*\).*/\1/p' | tail -1)"
  [[ -n "$buffered_node" ]] || return 1
  printf '%s\n' "${buffered_node/\/dev\/disk//dev/rdisk}"
}

disc_size_bytes() {
  local node="$1"
  diskutil info "${node/rdisk/disk}" 2>/dev/null \
    | sed -n 's/.*(\([0-9][0-9]*\) Bytes).*/\1/p' \
    | tail -1
}

# An image is a byte for byte copy, so the disc's own size is the whole of it.
# common.sh asks for a third again on top, because there a rip is followed by an
# encode; nothing follows this one.
require_room_for_image() {
  local needed_bytes="$1"
  local destination="$2"
  local free_gigabytes

  free_gigabytes="$(free_gigabytes_at "$destination")"
  if awk -v needed="$needed_bytes" -v free_space="$free_gigabytes" \
       'BEGIN { exit !(needed / 1073741824 <= free_space) }'; then
    return 0
  fi

  print_error "Not enough room for the image."
  print_line "  needs    $(human_gigabytes "$needed_bytes")"
  print_line "  free     ${free_gigabytes} GB where ${destination} lives"
  exit 1
}

# The first line of a map that is not a comment is the position ddrescue had
# reached. That is where the drive gave up, which is the one number worth
# knowing when deciding what to do next.
stopped_position_bytes() {
  local map_file="$1"
  local hex_position
  [[ -f "$map_file" ]] || return 0
  hex_position="$(awk '!/^#/ {print $1; exit}' "$map_file")"
  [[ -n "$hex_position" ]] || return 0
  printf '%d\n' "$hex_position" 2>/dev/null || true
}

ddrescue_direction_flags() {
  if [[ "$is_reading_backwards" -eq 1 ]]; then
    printf '%s\n' "--reverse"
  fi
  if [[ -n "$start_position" ]]; then
    printf '%s\n' "--input-position=${start_position}"
  fi
}

describe_pass() {
  if [[ "$is_reading_backwards" -eq 1 ]]; then
    printf 'backwards from the end'
    return
  fi
  if [[ -n "$start_position" ]]; then
    printf 'forwards from %s' "$start_position"
    return
  fi
  printf 'forwards from where the last run stopped'
}

print_what_to_do_next() {
  local map_file="$1"
  local stopped_bytes
  stopped_bytes="$(stopped_position_bytes "$map_file")"

  print_line ""
  if [[ -n "$stopped_bytes" && "$stopped_bytes" -gt 0 ]]; then
    print_line "The drive stopped $(human_gigabytes "$stopped_bytes") in."
  fi
  print_line ""
  print_line "Unplug the drive, plug it back in, then:"
  if [[ "$is_reading_backwards" -eq 1 ]]; then
    print_line "  the reverse pass has met the bad region from the other side."
    print_line "  Whatever is still missing is the damage itself. Rip the image as it stands."
  else
    print_line "  ./scripts/rescue-disc.sh --reverse \"${disc_name}\""
    print_line ""
    print_line "That fills in everything past the bad spot, working back from the end."
  fi
}

main() {
  require_macos
  require_not_root
  read_arguments "$@"
  require_ddrescue

  local device_node
  if ! device_node="$(optical_device_node)" || [[ ! -e "$device_node" ]]; then
    print_error "No optical drive with a disc in it."
    print_line "If the drive hung on the last pass, unplug it and plug it back in."
    exit 1
  fi

  if [[ -z "$disc_name" ]]; then
    disc_name="$(read_disc_label)"
  fi
  disc_name="$(sanitize_file_component "$disc_name")"
  if [[ -z "$disc_name" ]]; then
    print_error "The disc has no label. Pass a name: ./scripts/rescue-disc.sh \"Red Sparrow\""
    exit 1
  fi

  local image_directory="${RIPS_DIRECTORY}/images"
  local image_file="${image_directory}/${disc_name}.iso"
  local map_file="${image_directory}/${disc_name}.map"
  mkdir -p "$image_directory"

  # The map is what makes the next run a continuation rather than a restart. An
  # image without one is either finished or came from somewhere else, and
  # writing over it would destroy a copy that may have taken hours to get.
  if [[ -e "$image_file" && ! -e "$map_file" ]]; then
    print_error "${image_file} already exists and has no map beside it."
    print_line "Nothing here can tell how much of it is good, so it is being left alone."
    print_line "Move it aside to start over."
    exit 1
  fi

  local size_bytes
  size_bytes="$(disc_size_bytes "$device_node")"
  if [[ -n "$size_bytes" ]]; then
    print_line "Disc holds $(human_gigabytes "$size_bytes")"
    require_room_for_image "$size_bytes" "$image_directory"
  fi

  local direction_flags=()
  while IFS= read -r flag; do
    direction_flags+=("$flag")
  done < <(ddrescue_direction_flags)

  print_step "Reading ${device_node} $(describe_pass)"
  print_line "Stop with Ctrl-C whenever you like. The next run carries on from here."
  print_line ""

  local ddrescue_status=0
  set +e
  # --no-scrape skips the slow sector-by-sector retry. On a drive that hangs
  # rather than reporting a bad sector, scraping only buys more chances to hang.
  sudo ddrescue --block-size="$SECTOR_SIZE" --no-scrape \
    "${direction_flags[@]}" \
    "$device_node" "$image_file" "$map_file"
  ddrescue_status=$?
  set -e

  if [[ "$ddrescue_status" -eq 0 ]]; then
    print_step "Got the whole disc into ${image_file}"
    print_line ""
    print_line "Now rip from the image rather than the disc:"
    print_line "  \"${MAKE_MKV_COMMAND}\" mkv iso:\"${image_file}\" all \"${RIPS_DIRECTORY}/raw\""
    exit 0
  fi

  print_step "The pass ended early, which is expected on this disc"
  print_what_to_do_next "$map_file"
  print_line ""
  print_line "When both passes are done, rip whatever was recovered:"
  print_line "  \"${MAKE_MKV_COMMAND}\" mkv iso:\"${image_file}\" all \"${RIPS_DIRECTORY}/raw\""
  exit 1
}

main "$@"
