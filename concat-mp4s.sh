#!/usr/bin/env bash
# Join every mp4 in a folder, in alphabetical order, into one file.
#
# Usage:
#   ./concat-mp4s.sh                    join the mp4s in the current folder
#   ./concat-mp4s.sh "/path/to/folder"  join the mp4s in that folder
#
# This is the blunt version of join-parts.sh: no name matching, no checking that
# the pieces were encoded the same way. Whatever is in the folder gets stitched
# together in the order `ls` would show it. Use it when you already know the
# files belong together and are in the right order.
#
# The join is a remux, so nothing is re-encoded.
set -euo pipefail

OUTPUT_NAME="output.mp4"
FILE_LIST_NAME="filelist.txt"

usage() {
  cat <<EOF
Usage:
  ./concat-mp4s.sh                    join the mp4s in the current folder
  ./concat-mp4s.sh "/path/to/folder"  join the mp4s in that folder

Every .mp4 in the folder is joined in alphabetical order into ${OUTPUT_NAME},
using ${FILE_LIST_NAME} as the list ffmpeg reads. Nothing is re-encoded.
EOF
}

target_directory="${1:-.}"

if [[ "$target_directory" == "-h" || "$target_directory" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -d "$target_directory" ]]; then
  echo "Not a directory: ${target_directory}"
  exit 1
fi

cd "$target_directory"

# Refusing here rather than overwriting keeps a second run from quietly
# stitching the last result back into itself, and keeps a file you meant to
# keep from being replaced by one you did not.
if [[ -e "$OUTPUT_NAME" ]]; then
  echo "${OUTPUT_NAME} already exists in ${target_directory}. Leaving it alone."
  exit 1
fi

shopt -s nullglob
source_files=(*.mp4)

if [[ "${#source_files[@]}" -lt 2 ]]; then
  echo "Need at least two mp4 files to join. Found ${#source_files[@]} in ${target_directory}."
  exit 1
fi

: > "$FILE_LIST_NAME"
for mp4_file in "${source_files[@]}"; do
  # ffmpeg's concat list quotes with single quotes, so a name like
  # "Ocean's 11.mp4" has to escape its own quote or the list stops early.
  printf "file '%s'\n" "${mp4_file//\'/\'\\\'\'}" >> "$FILE_LIST_NAME"
done

echo "Joining ${#source_files[@]} files into ${OUTPUT_NAME}:"
cat "$FILE_LIST_NAME"

ffmpeg -f concat -safe 0 -i "$FILE_LIST_NAME" -c copy "$OUTPUT_NAME"
