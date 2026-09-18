#!/usr/bin/env bash
# Rip an owned DVD or Blu-ray from the USB drive, convert to H.264, file for Plex.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

MINIMUM_TITLE_LENGTH_SECONDS=300
TV_MINIMUM_TITLE_LENGTH_SECONDS=900
# Picked from the disc type at run time. Encoding a Blu-ray with the 480p preset
# would throw away most of the picture.
DVD_HANDBRAKE_PRESET="Super HQ 480p30 Surround"
BLURAY_HANDBRAKE_PRESET="HQ 1080p30 Surround"
HANDBRAKE_PRESET=""
# DVDs carry telecine flags that produce the timestamps Roku stalls on, so they
# get a constant rate. Blu-ray video is already constant; forcing 30 there would
# only duplicate frames on a 24fps film.
HANDBRAKE_RATE_FLAG="--cfr"
is_keeping_raw_rip=0
is_copying_without_encode=0
is_accepting_first_lookup=0
is_min_length_explicit=0
is_ripping_tv=0
is_listing_titles=0
is_ripping_all_titles=0
SELECTED_TITLE_ID=""
MOVIE_TITLE=""
MOVIE_YEAR=""
SHOW_NAME=""
SEASON_NUMBER=""
STARTING_EPISODE=""
ITUNES_COLLECTION_ID=""

usage() {
  cat <<EOF
Usage:
  ./rip-dvd.sh ["Movie Title"] [year]
  ./rip-dvd.sh --tv ["Show Name"] [--season N] [--episode N]

Reads the disc in the USB drive with MakeMKV, then converts with HandBrake.
DVDs use "${DVD_HANDBRAKE_PRESET}", Blu-rays use "${BLURAY_HANDBRAKE_PRESET}".

Movies keep the title MakeMKV flags as the main feature, or the longest one if
nothing is flagged, and file it as:
  ${MOVIES_DIRECTORY}/Movie Title (Year)/Movie Title (Year).mp4

TV (--tv) keeps every title longer than the minimum (15 minutes by default),
asks for show / season / first episode on this disc, and files them as:
  ${TV_DIRECTORY}/Show Name/Season 01/Show Name - s01e01 - Episode.mp4

If you omit the name, the script reads the disc label and looks it up in
iTunes (movies, or TV seasons with --tv).

  --tv                   Rip every episode-length title into the TV library
  --season N             Season number (TV)
  --episode N            First episode number on this disc (TV, default: 1).
                         Use this for disc 2+ of a season, not to rip one episode.
  --min-length SECONDS   Ignore shorter titles (movie default: ${MINIMUM_TITLE_LENGTH_SECONDS}; TV default: ${TV_MINIMUM_TITLE_LENGTH_SECONDS})
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
    --tv)
      is_ripping_tv=1
      shift
      ;;
    --season)
      SEASON_NUMBER="${2:-}"
      if [[ -z "$SEASON_NUMBER" ]]; then
        print_error "--season needs a number"
        exit 1
      fi
      shift 2
      ;;
    --episode)
      STARTING_EPISODE="${2:-}"
      if [[ -z "$STARTING_EPISODE" ]]; then
        print_error "--episode needs a number"
        exit 1
      fi
      shift 2
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

sanitize_file_component() {
  python3 -c '
import re
import sys

name = sys.argv[1]
name = re.sub(r"[/\\\\:]", " - ", name)
name = re.sub(r"\s+", " ", name).strip(" .")
print(name)
' "$1"
}

padded_two_digits() {
  printf '%02d' "$1"
}

# MakeMKV reports real problems (missing key, Java, unreadable sectors) as MSG
# lines on stdout. Hiding those is why a failed disc used to look like an empty one.
print_makemkv_messages() {
  python3 -c '
import re
import sys

for line in sys.stdin.read().splitlines():
    match = re.match(r"^MSG:\d+,\d+,\d+,\"(.*?)\",", line)
    if match:
        print("  " + match.group(1))
'
}

# Scanning a Blu-ray is slow, so keep the first result for the rest of the run.
DISC_INFO_CACHE=""
is_disc_scanned=0

scan_disc_info() {
  if [[ "$is_disc_scanned" -eq 0 ]]; then
    DISC_INFO_CACHE="$("$MAKE_MKV_COMMAND" -r --minlength=1 info disc:0)" || true
    is_disc_scanned=1
    printf '%s' "$DISC_INFO_CACHE" | print_makemkv_messages >&2
  fi
  printf '%s' "$DISC_INFO_CACHE"
}

# CINFO:1 is the disc type, e.g. "Blu-ray disc" or "DVD disc".
disc_media_type() {
  scan_disc_info \
    | sed -n 's/^CINFO:1,[0-9]*,"\(.*\)"[[:space:]]*$/\1/p' \
    | tail -1
}

choose_handbrake_preset() {
  local media_type
  media_type="$(disc_media_type)"

  if [[ "$media_type" == *[Bb]lu-ray* ]]; then
    HANDBRAKE_RATE_FLAG="--pfr"
    HANDBRAKE_PRESET="${HANDBRAKE_PRESET:-$BLURAY_HANDBRAKE_PRESET}"
    print_step "Blu-ray detected, encoding with \"${HANDBRAKE_PRESET}\""
    print_line "Leave about 30 GB free for the raw rip. The encode takes hours, not minutes."
  else
    HANDBRAKE_RATE_FLAG="--cfr"
    HANDBRAKE_PRESET="${HANDBRAKE_PRESET:-$DVD_HANDBRAKE_PRESET}"
    print_step "DVD detected, encoding with \"${HANDBRAKE_PRESET}\""
  fi
}

read_disc_label() {
  local disc_info
  disc_info="$(scan_disc_info)"
  python3 -c '
import re
import sys

text = sys.stdin.read()
title = ""
volume = ""
for line in text.splitlines():
    match = re.match(r"^CINFO:2,\d+,\"(.*)\"\s*$", line)
    if match:
        title = match.group(1)
        continue
    match = re.match(r"^CINFO:30,\d+,\"(.*)\"\s*$", line)
    if match and not title:
        title = match.group(1)
        continue
    match = re.match(r"^CINFO:32,\d+,\"(.*)\"\s*$", line)
    if match:
        volume = match.group(1)
print(title or volume)
' <<< "$disc_info"
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

season_number_from_disc_label() {
  python3 -c '
import re
import sys

label = sys.argv[1].replace("_", " ")
match = re.search(r"(?:season|s)\s*0*(\d+)", label, flags=re.I)
print(match.group(1) if match else "")
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

search_itunes_tv_seasons() {
  python3 -c '
import json
import re
import sys
import urllib.parse
import urllib.request

query = sys.argv[1]
if not query:
    raise SystemExit(0)
url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
    {
        "term": query,
        "media": "tvShow",
        "entity": "tvSeason",
        "limit": "8",
        "country": "us",
    }
)
with urllib.request.urlopen(url, timeout=20) as response:
    payload = json.load(response)

seen = set()
for item in payload.get("results", []):
    show_name = item.get("artistName") or ""
    collection_name = item.get("collectionName") or ""
    collection_id = item.get("collectionId") or ""
    year = (item.get("releaseDate") or "")[:4]
    season_match = re.search(r"season\s+(\d+)", collection_name, flags=re.I)
    season_number = season_match.group(1) if season_match else ""
    if not show_name or not collection_id:
        continue
    key = (show_name, collection_name)
    if key in seen:
        continue
    seen.add(key)
    print(f"{collection_id}\t{show_name}\t{season_number}\t{collection_name}\t{year}")
' "$1"
}

list_itunes_episodes() {
  local collection_id="$1"
  if [[ -z "$collection_id" ]]; then
    return
  fi
  python3 -c '
import json
import sys
import urllib.parse
import urllib.request

collection_id = sys.argv[1]
url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(
    {"id": collection_id, "entity": "tvEpisode", "limit": "200"}
)
with urllib.request.urlopen(url, timeout=20) as response:
    payload = json.load(response)

for item in payload.get("results", []):
    if item.get("kind") != "tv-episode":
        continue
    number = item.get("trackNumber")
    name = item.get("trackName") or ""
    millis = item.get("trackTimeMillis") or 0
    if not number or not name:
        continue
    seconds = int(millis) // 1000 if millis else 0
    print(f"{number}\t{seconds}\t{name}")
' "$collection_id"
}

list_makemkv_titles() {
  python3 -c '
import re
import sys

text = sys.stdin.read()
durations = {}
for line in text.splitlines():
    match = re.match(r"^TINFO:(\d+),(\d+),\d+,\"(.*)\"\s*$", line)
    if not match:
        continue
    title_id = int(match.group(1))
    attribute = int(match.group(2))
    value = match.group(3)
    if attribute != 9:
        continue
    parts = [int(piece) for piece in value.split(":")]
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    durations[title_id] = seconds
for title_id in sorted(durations):
    print(f"{title_id}\t{durations[title_id]}")
'
}

# One row per title: id, seconds, duration, bytes, source playlist, main-feature
# flag, name. When MakeMKV's Java playlist detection works it comments the real
# feature with FPL_MainFeature, which beats any size or length guess.
makemkv_title_table() {
  python3 -c '
import re
import sys

titles = {}
for line in sys.stdin.read().splitlines():
    match = re.match(r"^TINFO:(\d+),(\d+),\d+,\"(.*)\"\s*$", line)
    if not match:
        continue
    title_id = int(match.group(1))
    attribute = int(match.group(2))
    value = match.group(3)
    title = titles.setdefault(title_id, {"main": False, "values": {}})
    title["values"][attribute] = value
    if "mainfeature" in value.replace("_", "").lower():
        title["main"] = True


def duration_seconds(text):
    try:
        parts = [int(piece) for piece in text.split(":")]
    except ValueError:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


for title_id in sorted(titles):
    values = titles[title_id]["values"]
    duration = values.get(9, "")
    source = values.get(16, "")
    name = values.get(27, "") or values.get(2, "")
    try:
        size_bytes = int(values.get(11, "0"))
    except ValueError:
        size_bytes = 0
    flag = "main" if titles[title_id]["main"] else "-"
    print(f"{title_id}\t{duration_seconds(duration)}\t{duration}\t{size_bytes}\t{source}\t{flag}\t{name}")
'
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

human_gigabytes() {
  local bytes="$1"
  if [[ ! "$bytes" =~ ^[0-9]+$ || "$bytes" -eq 0 ]]; then
    printf '?'
    return
  fi
  awk -v bytes="$bytes" 'BEGIN { printf "%.1f GB", bytes / 1073741824 }'
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

select_tv_title_ids() {
  local min_seconds="$1"
  python3 -c '
import statistics
import sys

min_seconds = int(sys.argv[1])
rows = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    title_id, seconds = line.split("\t")
    seconds = int(seconds)
    if seconds >= min_seconds:
        rows.append((int(title_id), seconds))

if not rows:
    raise SystemExit(0)

typical_pool = [seconds for _, seconds in rows if seconds <= 55 * 60]
if len(typical_pool) >= 2:
    typical_seconds = statistics.median(typical_pool)
else:
    typical_seconds = statistics.median([seconds for _, seconds in rows])

# Keep regular episodes and double-length pilots. Drop "Play All" bundles.
limit_seconds = typical_seconds * 2.5
for title_id, seconds in rows:
    if seconds <= limit_seconds:
        print(f"{title_id}\t{seconds}")
' "$min_seconds"
}

assign_episode_names_by_runtime() {
  python3 -c '
import sys

title_count = int(sys.argv[1])
starting_episode = int(sys.argv[2])
title_seconds = [int(value) for value in sys.argv[3:3 + title_count]]
episode_blob = sys.argv[3 + title_count] if len(sys.argv) > 3 + title_count else ""

episodes = []
for line in episode_blob.split("\n"):
    line = line.strip()
    if not line:
        continue
    number, seconds, name = line.split("\t", 2)
    episodes.append({"number": int(number), "seconds": int(seconds), "name": name, "is_used": False})

def closest_unused_name(runtime):
    best = None
    best_delta = None
    for episode in episodes:
        if episode["is_used"] or episode["seconds"] <= 0:
            continue
        delta = abs(episode["seconds"] - runtime) / runtime
        if best_delta is None or delta < best_delta:
            best = episode
            best_delta = delta
    if best is None or best_delta > 0.3:
        return ""
    best["is_used"] = True
    return best["name"]

for index, runtime in enumerate(title_seconds):
    episode_number = starting_episode + index
    name = closest_unused_name(runtime) if runtime else ""
    print(f"{episode_number}\t{name}")
' "$@"
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

apply_tv_season_choice() {
  local choice="$1"
  local -a rows=("${@:2}")
  local selected=""
  local collection_id
  local show_name
  local season_from_match

  if [[ "$choice" =~ ^[0-9]+$ ]]; then
    local index=$((choice - 1))
    if [[ "$index" -lt 0 || "$index" -ge "${#rows[@]}" ]]; then
      print_error "Not a listed number: ${choice}"
      exit 1
    fi
    selected="${rows[$index]}"
    IFS=$'\t' read -r collection_id show_name season_from_match _ <<< "$selected"
    SHOW_NAME="$show_name"
    ITUNES_COLLECTION_ID="$collection_id"
    if [[ -z "$SEASON_NUMBER" && -n "$season_from_match" ]]; then
      SEASON_NUMBER="$season_from_match"
    fi
    return
  fi

  SHOW_NAME="$choice"
}

lookup_tv_show_and_season() {
  local disc_label
  local search_query
  local matches
  local -a rows=()
  local index=1
  local choice=""
  local guessed_season=""

  print_step "Looking up TV show from the disc label"
  disc_label="$(read_disc_label)"
  if [[ -z "$disc_label" ]]; then
    print_error "MakeMKV did not report a disc name. Is a disc in the USB drive?"
    exit 1
  fi

  search_query="$(search_query_from_disc_label "$disc_label")"
  guessed_season="$(season_number_from_disc_label "$disc_label")"
  print_line "Disc label: ${disc_label}"
  print_line "Search:     ${search_query}"

  matches="$(search_itunes_tv_seasons "$search_query" || true)"
  if [[ -n "$matches" ]]; then
    print_line "Matches:"
    while IFS=$'\t' read -r collection_id show_name season_from_match collection_name year; do
      print_line "  ${index}. ${collection_name}${year:+ (${year})}"
      rows+=("${collection_id}"$'\t'"${show_name}"$'\t'"${season_from_match}"$'\t'"${collection_name}")
      index=$((index + 1))
    done <<< "$matches"
  else
    print_line "No iTunes TV match for that label."
  fi

  if [[ "$is_accepting_first_lookup" -eq 1 ]]; then
    if [[ "${#rows[@]}" -eq 0 ]]; then
      print_error "Lookup found nothing. Re-run with: ./rip-dvd.sh --tv \"Show Name\" --season 1"
      exit 1
    fi
    apply_tv_season_choice "1" "${rows[@]}"
  elif [[ "${#rows[@]}" -gt 0 ]]; then
    read -r -p "Number, or type show name: " choice </dev/tty
    choice="${choice:-1}"
    apply_tv_season_choice "$choice" "${rows[@]}"
  else
    read -r -p "Show name: " SHOW_NAME </dev/tty
  fi

  if [[ -z "$SHOW_NAME" ]]; then
    print_error "Need a show name to name the Plex folder."
    exit 1
  fi

  if [[ -z "$SEASON_NUMBER" && -n "$guessed_season" ]]; then
    SEASON_NUMBER="$guessed_season"
    print_line "Season from disc label: ${SEASON_NUMBER}"
  fi

  if [[ -z "$SEASON_NUMBER" ]]; then
    if [[ "$is_accepting_first_lookup" -eq 1 ]]; then
      print_error "Need --season N for unattended TV rips when the match has no season."
      exit 1
    fi
    read -r -p "Season number: " SEASON_NUMBER </dev/tty
  fi

  if [[ -z "$SEASON_NUMBER" || ! "$SEASON_NUMBER" =~ ^[0-9]+$ ]]; then
    print_error "Need a numeric season."
    exit 1
  fi

  if [[ -z "$STARTING_EPISODE" ]]; then
    STARTING_EPISODE=1
    print_line "Numbering this disc from e01. Pass --episode N if it is a later disc in the season."
  fi

  if [[ ! "$STARTING_EPISODE" =~ ^[0-9]+$ ]]; then
    print_error "Need a numeric starting episode."
    exit 1
  fi

  print_line "Using ${SHOW_NAME}  s$(padded_two_digits "$SEASON_NUMBER")e$(padded_two_digits "$STARTING_EPISODE")+"
}

prompt_tv_season_if_needed() {
  local disc_label
  disc_label="$(read_disc_label || true)"
  if [[ -z "$SEASON_NUMBER" && -n "$disc_label" ]]; then
    SEASON_NUMBER="$(season_number_from_disc_label "$disc_label")"
  fi
  if [[ -z "$SEASON_NUMBER" ]]; then
    if [[ "$is_accepting_first_lookup" -eq 1 ]]; then
      print_error "Need --season N when using --yes with a show name."
      exit 1
    fi
    read -r -p "Season number: " SEASON_NUMBER </dev/tty
  fi
  if [[ -z "$SEASON_NUMBER" || ! "$SEASON_NUMBER" =~ ^[0-9]+$ ]]; then
    print_error "Need a numeric season. Pass --season N."
    exit 1
  fi
  if [[ -z "$STARTING_EPISODE" ]]; then
    STARTING_EPISODE=1
    print_line "Numbering this disc from e01. Pass --episode N if it is a later disc in the season."
  fi
  if [[ ! "$STARTING_EPISODE" =~ ^[0-9]+$ ]]; then
    print_error "Need a numeric starting episode."
    exit 1
  fi
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

mkv_files_in_disc_order() {
  local directory="$1"
  find "$directory" -type f -name '*.mkv' | LC_ALL=C sort -V
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

copy_title() {
  local raw_mkv="$1"
  local output_file="$2"
  mkdir -p "$(dirname "$output_file")"
  cp "$raw_mkv" "$output_file"
}

convert_title() {
  local raw_mkv="$1"
  local output_file="$2"
  local handbrake
  local handbrake_status
  handbrake="$(handbrake_command)"

  mkdir -p "$(dirname "$output_file")"
  set +e
  "$handbrake" \
    --input "$raw_mkv" \
    --output "$output_file" \
    --preset "$HANDBRAKE_PRESET" \
    --format av_mp4 \
    "$HANDBRAKE_RATE_FLAG" \
    --optimize
  handbrake_status=$?
  set -e
  # HandBrakeCLI uses 1 for "finished with warnings". That is still a usable file.
  if [[ "$handbrake_status" -gt 1 ]]; then
    print_error "HandBrake failed (exit ${handbrake_status}) on ${raw_mkv}"
    exit 1
  fi
}

plex_tv_file_name() {
  local show_name="$1"
  local season_padded="$2"
  local episode_padded="$3"
  local episode_title="$4"
  if [[ -n "$episode_title" ]]; then
    printf '%s - s%se%s - %s' "$show_name" "$season_padded" "$episode_padded" "$episode_title"
    return
  fi
  printf '%s - s%se%s' "$show_name" "$season_padded" "$episode_padded"
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

duration_for_file_name() {
  awk -F: '{
    if (NF == 3 && $1 + 0 > 0) { printf "%dh%02dm%02ds", $1, $2, $3 }
    else if (NF == 3) { printf "%dm%02ds", $2, $3 }
    else if (NF == 2) { printf "%dm%02ds", $1, $2 }
    else { printf "%s", $0 }
  }' <<< "$1"
}

# Walk up to the nearest directory that exists. Trimming with ${path%/*} always
# shortens the string, so this cannot spin the way a dirname call can.
free_gigabytes_at() {
  local path="$1"
  local parent
  while [[ ! -d "$path" ]]; do
    parent="${path%/*}"
    if [[ -z "$parent" || "$parent" == "$path" ]]; then
      parent="/"
    fi
    path="$parent"
  done
  df -k "$path" | awk 'NR == 2 { printf "%.1f", $4 / 1048576 }'
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

fill_itunes_collection_id_if_needed() {
  local matches
  local collection_id
  local show_name
  local season_from_match
  if [[ -n "$ITUNES_COLLECTION_ID" ]]; then
    return
  fi
  matches="$(search_itunes_tv_seasons "$SHOW_NAME" || true)"
  [[ -n "$matches" ]] || return
  while IFS=$'\t' read -r collection_id show_name season_from_match _ _; do
    if [[ "$season_from_match" == "$SEASON_NUMBER" ]]; then
      ITUNES_COLLECTION_ID="$collection_id"
      return
    fi
  done <<< "$matches"
}

rip_tv() {
  local show_name
  local season_padded
  local raw_directory
  local output_directory
  local output_extension="mp4"
  local disc_info
  local all_titles
  local selected_rows
  local title_id
  local title_seconds
  local -a title_ids=()
  local -a title_runtimes=()
  local -a ripped_files=()
  local files_before
  local raw_mkv
  local itunes_episodes
  local assignments
  local episode_number
  local episode_title
  local episode_padded
  local file_stem
  local output_file
  local index=0
  local file_count

  show_name="$(sanitize_file_component "$SHOW_NAME")"
  season_padded="$(padded_two_digits "$SEASON_NUMBER")"
  raw_directory="${RIPS_DIRECTORY}/raw/${show_name}/Season ${season_padded}"
  output_directory="${TV_DIRECTORY}/${show_name}/Season ${season_padded}"
  if [[ "$is_copying_without_encode" -eq 1 ]]; then
    output_extension="mkv"
  fi

  print_step "Scanning the disc for episode-length titles"
  disc_info="$(scan_disc_info)"
  all_titles="$(list_makemkv_titles <<< "$disc_info")"
  selected_rows="$(select_tv_title_ids "$MINIMUM_TITLE_LENGTH_SECONDS" <<< "$all_titles")"
  if [[ -z "$selected_rows" ]]; then
    print_error "No titles were at least ${MINIMUM_TITLE_LENGTH_SECONDS}s. Try a lower --min-length."
    exit 1
  fi

  print_line "Keeping these MakeMKV titles (Play All / shorts dropped):"
  while IFS=$'\t' read -r title_id title_seconds; do
    [[ -n "$title_id" ]] || continue
    title_ids+=("$title_id")
    title_runtimes+=("$title_seconds")
    print_line "  title ${title_id}  $(printf '%d:%02d' $((title_seconds / 60)) $((title_seconds % 60)))"
  done <<< "$selected_rows"

  file_count="${#title_ids[@]}"
  print_line "Numbering ${file_count} episode(s) from s${season_padded}e$(padded_two_digits "$STARTING_EPISODE") in disc order."
  print_line "Names are matched by runtime (so Firefly's two-hour Serenity does not steal Train Job's number)."

  mkdir -p "$raw_directory"
  for title_id in "${title_ids[@]}"; do
    print_line "MakeMKV title ${title_id}"
    files_before="$(mkv_files_in_disc_order "$raw_directory")"
    "$MAKE_MKV_COMMAND" --minlength=1 -r --decrypt mkv disc:0 "$title_id" "$raw_directory"
    raw_mkv=""
    if [[ -z "$files_before" ]]; then
      IFS= read -r raw_mkv < <(mkv_files_in_disc_order "$raw_directory") || true
    else
      IFS= read -r raw_mkv < <(comm -13 <(printf '%s\n' "$files_before") <(mkv_files_in_disc_order "$raw_directory")) || true
    fi
    if [[ -z "$raw_mkv" ]]; then
      print_error "MakeMKV did not create a new file for title ${title_id}."
      exit 1
    fi
    ripped_files+=("$raw_mkv")
  done

  if [[ "${#ripped_files[@]}" -ne "$file_count" ]]; then
    print_line "Warning: expected ${file_count} files, found ${#ripped_files[@]}. Using disc order of new files."
    if [[ "${#ripped_files[@]}" -eq 0 ]]; then
      print_error "MakeMKV did not produce episode .mkv files."
      exit 1
    fi
  fi

  itunes_episodes="$(list_itunes_episodes "$ITUNES_COLLECTION_ID" || true)"
  assignments="$(assign_episode_names_by_runtime "$file_count" "$STARTING_EPISODE" "${title_runtimes[@]}" "${itunes_episodes}")"

  print_step "Converting ${#ripped_files[@]} episode(s) with ${HANDBRAKE_PRESET}"
  index=0
  while IFS=$'\t' read -r episode_number episode_title; do
    raw_mkv="${ripped_files[$index]}"
    episode_padded="$(padded_two_digits "$episode_number")"
    if [[ -n "$episode_title" ]]; then
      episode_title="$(sanitize_file_component "$episode_title")"
    fi
    file_stem="$(plex_tv_file_name "$show_name" "$season_padded" "$episode_padded" "$episode_title")"
    output_file="${output_directory}/${file_stem}.${output_extension}"
    print_line "s${season_padded}e${episode_padded}${episode_title:+ ${episode_title}}: ${raw_mkv}"
    if [[ -e "$output_file" ]]; then
      print_line "Already exists, skipping: ${output_file}"
      index=$((index + 1))
      continue
    fi
    if [[ "$is_copying_without_encode" -eq 1 ]]; then
      copy_title "$raw_mkv" "$output_file"
    else
      convert_title "$raw_mkv" "$output_file"
    fi
    index=$((index + 1))
  done <<< "$assignments"

  if [[ "$is_keeping_raw_rip" -eq 0 ]]; then
    print_step "Removing the raw MakeMKV files (pass --keep-raw to keep them)"
    rm -rf "$raw_directory"
  fi

  print_line ""
  print_line "Done: ${output_directory}"
  print_line "Plex often uses aired order for Firefly. If names look right but Plex shows The Train Job first, set the show's episode order to DVD."
}

require_macos
require_not_root

if [[ "$is_ripping_tv" -eq 1 && "$is_min_length_explicit" -eq 0 ]]; then
  MINIMUM_TITLE_LENGTH_SECONDS="$TV_MINIMUM_TITLE_LENGTH_SECONDS"
fi

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

if [[ "$is_ripping_tv" -eq 1 ]]; then
  if [[ -n "$MOVIE_TITLE" ]]; then
    SHOW_NAME="$MOVIE_TITLE"
    prompt_tv_season_if_needed
  else
    lookup_tv_show_and_season
  fi
  fill_itunes_collection_id_if_needed
  rip_tv
else
  if [[ -z "$MOVIE_TITLE" ]]; then
    lookup_title_and_year
  fi
  rip_movie
fi

print_line "Plex should pick it up on the next library scan. If not: library → More → Scan Library Files."
