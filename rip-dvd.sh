#!/usr/bin/env bash
# Rip an owned DVD or Blu-ray from the USB drive, convert to H.264, file for Plex.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

MINIMUM_TITLE_LENGTH_SECONDS=300
is_keeping_raw_rip=0
is_copying_without_encode=0
is_accepting_first_lookup=0
is_min_length_explicit=0
is_listing_titles=0
is_ripping_all_titles=0
SELECTED_TITLE_ID=""
MOVIE_TITLE=""
MOVIE_YEAR=""

usage() {
  cat <<EOF
Usage:
  ./rip-dvd.sh ["Movie Title"] [year]

Movies only. TV discs are handled by ./rip-shows.sh, which identifies the show
and its episodes rather than guessing from the disc label.

Reads the disc in the USB drive with MakeMKV, then converts with HandBrake.
DVDs use "${DVD_HANDBRAKE_PRESET}", Blu-rays use "${BLURAY_HANDBRAKE_PRESET}".

Movies keep the title MakeMKV flags as the main feature, or the longest one if
nothing is flagged, and file it as:
  ${MOVIES_DIRECTORY}/Movie Title (Year)/Movie Title (Year).mp4

If you omit the name, the script reads the disc label and looks it up in iTunes.

  --min-length SECONDS   Ignore shorter titles (default: ${MINIMUM_TITLE_LENGTH_SECONDS})
  --list                 Print the titles on the disc and stop. Use this when the
                         wrong feature got ripped, then re-run with --title N.
  --title N              Rip MakeMKV title N instead of guessing
  --all                  Decrypt every title to ${RIPS_DIRECTORY}/raw and stop.
                         No encoding, no library filing. Sort them out yourself.
  --preset NAME          HandBrake preset, overriding the disc-type default
  --keep-raw             Leave the MakeMKV .mkv files in ${RIPS_DIRECTORY}/raw
  --direct               Skip HandBrake. Copy decrypted MPEG-2 as .mkv
  --yes                  Use the first lookup match without asking
  -h, --help             Show this help

Only rip discs you own.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tv|--season|--episode)
      print_error "TV ripping moved to ./rip-shows.sh, which identifies episodes properly."
      exit 1
      ;;
    --list)
      is_listing_titles=1
      shift
      ;;
    --all)
      is_ripping_all_titles=1
      shift
      ;;
    --title)
      SELECTED_TITLE_ID="${2:-}"
      if [[ ! "$SELECTED_TITLE_ID" =~ ^[0-9]+$ ]]; then
        print_error "--title needs a title number from --list"
        exit 1
      fi
      shift 2
      ;;
    --preset)
      HANDBRAKE_PRESET="${2:-}"
      if [[ -z "$HANDBRAKE_PRESET" ]]; then
        print_error "--preset needs a HandBrake preset name"
        exit 1
      fi
      shift 2
      ;;
    --min-length)
      MINIMUM_TITLE_LENGTH_SECONDS="${2:-}"
      is_min_length_explicit=1
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
    --yes)
      is_accepting_first_lookup=1
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

plex_movie_folder_name() {
  if [[ -n "$MOVIE_YEAR" ]]; then
    printf '%s (%s)' "$MOVIE_TITLE" "$MOVIE_YEAR"
    return
  fi
  printf '%s' "$MOVIE_TITLE"
}


search_query_from_disc_label() {
  python3 -c '
import re
import sys

label = sys.argv[1].replace("_", " ")
label = re.sub(r"\s+", " ", label).strip()
label = re.sub(r"[\s]+s\d+\s*d\d+\s*$", "", label, flags=re.I)
label = re.sub(r"\s*(disc\s*\d+|d\d+|s\d+|season\s*\d+|dvd video|dvd)\s*$", "", label, flags=re.I)
print(label.strip(" -"))
' "$1"
}

search_itunes_movies() {
  python3 -c '
import json
import sys
import urllib.parse
import urllib.request

query = sys.argv[1]
if not query:
    raise SystemExit(0)
url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
    {
        "term": query,
        "media": "movie",
        "entity": "movie",
        "limit": "8",
        "country": "us",
    }
)
with urllib.request.urlopen(url, timeout=20) as response:
    payload = json.load(response)

seen = set()
for item in payload.get("results", []):
    name = item.get("trackName") or item.get("collectionName") or ""
    year = (item.get("releaseDate") or "")[:4]
    if not name or not year.isdigit():
        continue
    key = (name, year)
    if key in seen:
        continue
    seen.add(key)
    print(f"{name}\t{year}")
' "$1"
}

print_title_table() {
  local disc_info
  disc_info="$(scan_disc_info)"

  printf '\n%-6s %-10s %-9s %-14s %-6s %s\n' "TITLE" "LENGTH" "SIZE" "SOURCE" "FLAG" "NAME"
  makemkv_title_table <<< "$disc_info" \
    | while IFS=$'\t' read -r title_id seconds duration size_bytes source flag name; do
        printf '%-6s %-10s %-9s %-14s %-6s %s\n' \
          "$title_id" "$duration" "$(human_gigabytes "$size_bytes")" "$source" "$flag" "$name"
      done
  printf '\nRip one of these with: ./rip-dvd.sh --title N\n'
}

# Prefer MakeMKV's main-feature flag, then the longest title. Length beats file
# size: a commentary or bonus-view cut runs as long as the movie but carries more
# audio, so "biggest file" lands on the wrong one.
select_movie_title_id() {
  local disc_info="$1"
  makemkv_title_table <<< "$disc_info" | python3 -c '
import sys

rows = []
for line in sys.stdin:
    fields = line.rstrip("\n").split("\t")
    if len(fields) < 7:
        continue
    rows.append((int(fields[0]), int(fields[1]), fields[5]))

if not rows:
    raise SystemExit(0)

flagged = [row for row in rows if row[2] == "main"]
pool = flagged if flagged else rows
print(max(pool, key=lambda row: row[1])[0])
'
}

apply_lookup_choice() {
  local choice="$1"
  local -a titles=("${@:2}")
  local selected=""

  if [[ "$choice" =~ ^[0-9]+$ ]]; then
    local index=$((choice - 1))
    if [[ "$index" -lt 0 || "$index" -ge "${#titles[@]}" ]]; then
      print_error "Not a listed number: ${choice}"
      exit 1
    fi
    selected="${titles[$index]}"
  else
    selected="$choice"
  fi

  if [[ "$selected" =~ ^(.*)[[:space:]]+([[:digit:]]{4})$ ]]; then
    MOVIE_TITLE="${BASH_REMATCH[1]}"
    MOVIE_YEAR="${BASH_REMATCH[2]}"
    return
  fi

  MOVIE_TITLE="$selected"
  MOVIE_YEAR=""
}

lookup_title_and_year() {
  local disc_label
  local search_query
  local matches
  local -a titles=()
  local index=1
  local choice=""

  print_step "Looking up title and year from the disc label"
  disc_label="$(read_disc_label)"
  if [[ -z "$disc_label" ]]; then
    print_error "MakeMKV did not report a disc name. Is a disc in the USB drive?"
    exit 1
  fi

  search_query="$(search_query_from_disc_label "$disc_label")"
  print_line "Disc label: ${disc_label}"
  print_line "Search:     ${search_query}"

  matches="$(search_itunes_movies "$search_query" || true)"
  if [[ -n "$matches" ]]; then
    print_line "Matches:"
    while IFS=$'\t' read -r name year; do
      print_line "  ${index}. ${name} (${year})"
      titles+=("${name} ${year}")
      index=$((index + 1))
    done <<< "$matches"
  else
    print_line "No iTunes movie match for that label."
  fi

  if [[ "$is_accepting_first_lookup" -eq 1 ]]; then
    if [[ "${#titles[@]}" -eq 0 ]]; then
      print_error "Lookup found nothing. Re-run with: ./rip-dvd.sh \"Movie Title\" 1999"
      exit 1
    fi
    apply_lookup_choice "1" "${titles[@]}"
    print_line "Using ${MOVIE_TITLE} (${MOVIE_YEAR})"
    return
  fi

  if [[ "${#titles[@]}" -gt 0 ]]; then
    read -r -p "Number, or type Title Year: " choice </dev/tty
    choice="${choice:-1}"
    apply_lookup_choice "$choice" "${titles[@]}"
  else
    read -r -p "Movie title: " MOVIE_TITLE </dev/tty
    read -r -p "Year (optional): " MOVIE_YEAR </dev/tty
  fi

  if [[ -z "$MOVIE_TITLE" ]]; then
    print_error "Need a title to name the Plex folder."
    exit 1
  fi

  print_line "Using ${MOVIE_TITLE}${MOVIE_YEAR:+ (${MOVIE_YEAR})}"
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

rip_single_title() {
  local title_id="$1"
  local destination="$2"
  mkdir -p "$destination"

  print_step "Reading title ${title_id} with MakeMKV"
  print_line "This can take 20–40 minutes. Leave the USB drive plugged in."

  "$MAKE_MKV_COMMAND" --minlength=1 -r --decrypt mkv disc:0 "$title_id" "$destination"
}

describe_title() {
  local disc_info="$1"
  local wanted_id="$2"
  makemkv_title_table <<< "$disc_info" \
    | awk -F'\t' -v wanted="$wanted_id" '$1 == wanted { printf "%s, %s, %s", $3, $5, $7 }'
}

title_is_flagged_main() {
  local disc_info="$1"
  local wanted_id="$2"
  makemkv_title_table <<< "$disc_info" \
    | awk -F'\t' -v wanted="$wanted_id" '$1 == wanted && $6 == "main" { found = 1 } END { exit !found }'
}

rip_movie() {
  local library_name
  local raw_directory
  local output_directory
  local output_file
  local raw_mkv
  local output_extension="mp4"

  library_name="$(plex_movie_folder_name)"
  raw_directory="${RIPS_DIRECTORY}/raw/${library_name}"
  output_directory="${MOVIES_DIRECTORY}/${library_name}"
  if [[ "$is_copying_without_encode" -eq 1 ]]; then
    output_extension="mkv"
  fi
  output_file="${output_directory}/${library_name}.${output_extension}"

  if [[ -e "$output_file" ]]; then
    print_error "Already exists: ${output_file}"
    exit 1
  fi

  local disc_info
  local title_id
  disc_info="$(scan_disc_info)"

  title_id="$SELECTED_TITLE_ID"
  if [[ -z "$title_id" ]]; then
    title_id="$(select_movie_title_id "$disc_info")"
    if [[ -z "$title_id" ]]; then
      print_error "No titles on the disc. Is a disc in the USB drive? Is MakeMKV registered?"
      exit 1
    fi
    if ! title_is_flagged_main "$disc_info" "$title_id"; then
      print_line "MakeMKV did not flag a main feature, so this is the longest title."
      print_line "If that turns out to be a commentary or bonus cut, run ./rip-dvd.sh --list and pick with --title N."
    fi
  fi

  print_line "Title ${title_id}: $(describe_title "$disc_info" "$title_id")"
  rip_single_title "$title_id" "$raw_directory"

  raw_mkv="$(largest_file_in_directory "$raw_directory")"
  if [[ -z "$raw_mkv" ]]; then
    print_error "MakeMKV did not produce an .mkv for title ${title_id}."
    exit 1
  fi
  if [[ "$is_copying_without_encode" -eq 1 ]]; then
    copy_title "$raw_mkv" "$output_file"
  else
    print_step "Converting with ${HANDBRAKE_PRESET}"
    convert_title "$raw_mkv" "$output_file"
  fi

  if [[ "$is_keeping_raw_rip" -eq 0 ]]; then
    print_step "Removing the raw MakeMKV files (pass --keep-raw to keep them)"
    rm -rf "$raw_directory"
  fi

  print_line ""
  print_line "Done: ${output_file}"
}

all_titles_destination() {
  local label
  label="${MOVIE_TITLE:-$(read_disc_label || true)}"
  label="${label:-Disc}"
  printf '%s/raw/%s' "$RIPS_DIRECTORY" "$(sanitize_file_component "$label")"
}

# Decrypt everything and stop. No title guessing, no encoding, no library filing.
rip_all_titles() {
  local disc_info
  local destination
  local rows=()
  local row
  local ripped_count=0
  local failed_count=0

  disc_info="$(scan_disc_info)"
  print_title_table

  while IFS= read -r row; do
    [[ -n "$row" ]] && rows+=("$row")
  done <<< "$(makemkv_title_table <<< "$disc_info")"

  if [[ "${#rows[@]}" -eq 0 ]]; then
    print_error "No titles on the disc. Is a disc in the USB drive? Is MakeMKV registered?"
    exit 1
  fi

  destination="$(all_titles_destination)"
  mkdir -p "$destination"

  local wanted=()
  local total_bytes=0
  for row in "${rows[@]}"; do
    local seconds size_bytes
    seconds="$(cut -f2 <<< "$row")"
    size_bytes="$(cut -f4 <<< "$row")"
    if [[ "$seconds" -lt "$MINIMUM_TITLE_LENGTH_SECONDS" ]]; then
      continue
    fi
    wanted+=("$row")
    total_bytes=$((total_bytes + size_bytes))
  done

  if [[ "${#wanted[@]}" -eq 0 ]]; then
    print_error "Every title is under ${MINIMUM_TITLE_LENGTH_SECONDS}s. Lower it with --min-length."
    exit 1
  fi

  print_step "Ripping ${#wanted[@]} title(s) to ${destination}"
  print_line "Needs about $(human_gigabytes "$total_bytes"), and $(free_gigabytes_at "$destination") GB is free."
  print_line "Titles under ${MINIMUM_TITLE_LENGTH_SECONDS}s are skipped. Change that with --min-length."

  if [[ "$is_accepting_first_lookup" -eq 0 ]]; then
    local answer
    read -r -p "Continue? [y/N] " answer </dev/tty
    if [[ ! "$answer" =~ ^[Yy] ]]; then
      print_line "Stopped."
      exit 0
    fi
  fi

  for row in "${wanted[@]}"; do
    local title_id duration flag staging produced target suffix status
    title_id="$(cut -f1 <<< "$row")"
    duration="$(cut -f3 <<< "$row")"
    flag="$(cut -f6 <<< "$row")"

    suffix=""
    if [[ "$flag" == "main" ]]; then
      suffix=" - main"
    fi
    target="${destination}/t$(padded_two_digits "$title_id") - $(duration_for_file_name "$duration")${suffix}.mkv"

    if [[ -e "$target" ]]; then
      print_line "Already have $(basename "$target"), skipping."
      continue
    fi

    print_step "Title ${title_id} (${duration})"
    staging="${destination}/.title-${title_id}"
    rm -rf "$staging"
    mkdir -p "$staging"

    set +e
    "$MAKE_MKV_COMMAND" --minlength=1 -r --decrypt mkv disc:0 "$title_id" "$staging" </dev/null
    status=$?
    set -e

    produced="$(find "$staging" -type f -name '*.mkv' | head -1)"
    if [[ "$status" -ne 0 || -z "$produced" ]]; then
      print_error "Title ${title_id} failed, moving on to the next one."
      failed_count=$((failed_count + 1))
      rm -rf "$staging"
      continue
    fi

    mv "$produced" "$target"
    rm -rf "$staging"
    ripped_count=$((ripped_count + 1))
  done

  print_line ""
  print_step "Ripped ${ripped_count} title(s) to ${destination}"
  if [[ "$failed_count" -gt 0 ]]; then
    print_line "${failed_count} title(s) failed. Decoy playlists on protected discs often do."
  fi
  ls -lhS "$destination" | tail -n +2
  print_line ""
  print_line "Nothing was added to Plex. Play them, find the keeper, then either:"
  print_line "  ./rip-dvd.sh --title N \"Movie Title\" 1999     (re-rip and encode properly)"
  print_line "  mv the file into ~/Media/Movies/Movie Title (Year)/ as-is"
}

require_macos
require_not_root

if [[ ! -x "$MAKE_MKV_COMMAND" ]]; then
  print_error "MakeMKV is not installed. Run ./setup.sh on the server Mac first."
  exit 1
fi

if [[ "$is_listing_titles" -eq 1 ]]; then
  print_title_table
  exit 0
fi

if [[ "$is_ripping_all_titles" -eq 1 ]]; then
  rip_all_titles
  exit 0
fi

# Only the encoding paths need HandBrake. --list and --all never touch it.
if [[ "$is_copying_without_encode" -eq 0 ]] && ! handbrake_command >/dev/null; then
  print_error "HandBrakeCLI is not installed. Run ./setup.sh on the server Mac first."
  exit 1
fi

if [[ "$is_copying_without_encode" -eq 0 ]]; then
  choose_handbrake_preset
fi

if [[ -z "$MOVIE_TITLE" ]]; then
  lookup_title_and_year
fi
rip_movie

print_line "Plex should pick it up on the next library scan. If not: library → More → Scan Library Files."
