#!/usr/bin/env bash
# Join a film that was ripped from several discs into one file.
#
# Plex can play "stacked" part files, but its own documentation recommends
# against relying on it: thumbnails, chapter images and subtitle selection all
# degrade, and not every client plays them. One real file avoids all of that.
#
# The join is a remux, so nothing is re-encoded and nothing is lost. It runs at
# disk speed rather than encode speed.
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIRECTORY}/common.sh"

MKVMERGE_FORMULA="mkvtoolnix"
is_keeping_parts=0

usage() {
  cat <<EOF
Usage:
  ./join-parts.sh                     join every split film in the library
  ./join-parts.sh "/path/to/Movie (2001)"
                                      join one film's folder

Looks for files named "Movie (Year) - part1.mkv", "- part2.mkv" and so on, and
merges them into a single "Movie (Year).mkv".

Nothing is re-encoded. The parts are removed once the join is verified.

  --keep-parts           Leave the part files in place afterwards
  -h, --help             Show this help
EOF
}

folders=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-parts)
      is_keeping_parts=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      folders+=("$1")
      shift
      ;;
  esac
done

require_mkvmerge() {
  if command -v mkvmerge >/dev/null 2>&1; then
    return
  fi
  print_step "Installing MKVToolNix, which does the joining"
  brew install "$MKVMERGE_FORMULA"
}

# Parts in disc order. The sort has to be on the part number as a number: sorted
# as text, part10 lands between part1 and part2 and the film gets spliced
# together in the wrong order, which nothing downstream would catch.
parts_in_order() {
  python3 -c '
import os
import re
import sys

folder = sys.argv[1]
part_pattern = re.compile(r" - part(\d+)\.(?:mkv|mp4)$", re.I)

numbered = []
for name in sorted(os.listdir(folder)):
    match = part_pattern.search(name)
    if match:
        numbered.append((int(match.group(1)), os.path.join(folder, name)))

for _, path in sorted(numbered):
    sys.stdout.write(path + "\0")
' "$1"
}

base_name_for_folder() {
  local folder="$1"
  basename "$folder"
}

# Two parts only splice cleanly if they were encoded the same way. They always
# are when both came from this ripper, but a mismatch here would produce a file
# that plays for one disc and then falls apart, so it is worth the check.
stream_signature() {
  local file_path="$1"
  local ffprobe
  ffprobe="$(ffprobe_command)"
  "$ffprobe" -v error -select_streams v:0 \
    -show_entries stream=codec_name,width,height,pix_fmt \
    -of csv=p=0 "$file_path" 2>/dev/null
}

total_bytes_of() {
  local -a files=("$@")
  local total=0
  local file_path
  for file_path in "${files[@]}"; do
    total=$((total + $(stat -f%z "$file_path")))
  done
  printf '%s' "$total"
}

join_one_folder() {
  local folder="$1"
  local base_name
  local -a parts=()
  local first_signature=""
  local signature
  local output_file
  local part

  base_name="$(base_name_for_folder "$folder")"
  while IFS= read -r -d '' found; do
    parts+=("$found")
  done < <(parts_in_order "$folder")

  if [[ "${#parts[@]}" -lt 2 ]]; then
    return 2
  fi

  print_step "${base_name}: joining ${#parts[@]} parts"
  for part in "${parts[@]}"; do
    signature="$(stream_signature "$part")"
    if [[ -z "$first_signature" ]]; then
      first_signature="$signature"
    elif [[ "$signature" != "$first_signature" ]]; then
      print_error "${base_name}: parts were not encoded the same way, refusing to join."
      print_line "  $(basename "${parts[0]}"): ${first_signature}"
      print_line "  $(basename "$part"): ${signature}"
      print_line "Re-rip them with the same settings, then try again."
      return 1
    fi
    print_line "  $(basename "$part")"
  done

  output_file="${folder}/${base_name}.mkv"
  if [[ -e "$output_file" ]]; then
    print_error "${base_name}: ${output_file} already exists, leaving it alone."
    return 1
  fi

  require_free_space_for "$(total_bytes_of "${parts[@]}")" "$folder"

  # mkvmerge's "+" appends rather than muxing side by side, which is what a film
  # split across discs needs.
  local -a merge_arguments=("${parts[0]}")
  for part in "${parts[@]:1}"; do
    merge_arguments+=("+" "$part")
  done

  if ! mkvmerge -o "$output_file" "${merge_arguments[@]}" >/dev/null; then
    print_error "${base_name}: mkvmerge failed."
    rm -f "$output_file"
    return 1
  fi

  if ! joined_length_looks_right "$output_file" "${parts[@]}"; then
    print_error "${base_name}: the joined file is not as long as the parts, keeping everything."
    rm -f "$output_file"
    return 1
  fi

  print_line "  -> $(basename "$output_file")"

  if [[ "$is_keeping_parts" -eq 0 ]]; then
    for part in "${parts[@]}"; do
      rm -f "$part"
    done
    print_line "  removed the part files"
  fi
  return 0
}

# The join is only trustworthy if the result runs as long as its pieces. A
# silent truncation would otherwise look like a success.
joined_length_looks_right() {
  local joined="$1"
  shift
  local -a parts=("$@")
  local ffprobe
  local joined_seconds
  local expected_seconds=0
  local part
  ffprobe="$(ffprobe_command)"

  joined_seconds="$(duration_seconds_of "$joined")"
  for part in "${parts[@]}"; do
    expected_seconds="$(awk -v a="$expected_seconds" -v b="$(duration_seconds_of "$part")" \
      'BEGIN { print a + b }')"
  done

  awk -v joined="$joined_seconds" -v expected="$expected_seconds" \
    'BEGIN { exit !(expected > 0 && joined >= expected - 5) }'
}

duration_seconds_of() {
  local file_path="$1"
  local ffprobe
  ffprobe="$(ffprobe_command)"
  "$ffprobe" -v error -show_entries format=duration -of csv=p=0 "$file_path" 2>/dev/null || printf '0'
}

require_macos
require_mkvmerge

if ! ffprobe_command >/dev/null 2>&1; then
  print_error "ffprobe is missing. Install it with: brew install ffmpeg"
  exit 1
fi

if [[ "${#folders[@]}" -eq 0 ]]; then
  print_step "Looking for split films in $(resolved_media_root)"
  # One entry per film, not one per part.
  while IFS= read -r found; do
    [[ -n "$found" ]] && folders+=("$found")
  done < <(find "${MOVIES_DIRECTORY}" -type f -name '* - part[0-9]*' -exec dirname {} \; 2>/dev/null | sort -u)
fi

if [[ "${#folders[@]}" -eq 0 ]]; then
  print_line "No split films found. Nothing to do."
  exit 0
fi

joined_count=0
failed_count=0
for folder in "${folders[@]}"; do
  set +e
  join_one_folder "$folder"
  status=$?
  set -e
  case "$status" in
    0) joined_count=$((joined_count + 1)) ;;
    1) failed_count=$((failed_count + 1)) ;;
  esac
done

print_line ""
if [[ "$joined_count" -gt 0 ]]; then
  print_step "Joined ${joined_count} film(s)"
  print_line "In Plex: the movie library -> More -> Scan Library Files."
fi
if [[ "$failed_count" -gt 0 ]]; then
  print_error "${failed_count} film(s) could not be joined. See above."
  exit 1
fi
