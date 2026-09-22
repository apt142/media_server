#!/usr/bin/env bash
# Rip an owned TV disc (DVD or Blu-ray), work out which episodes are on it,
# and file them where Plex expects.
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIRECTORY}/common.sh"

SHOW_LOOKUP_COMMAND="${SCRIPT_DIRECTORY}/show_lookup.py"

SHOW_NAME=""
SHOW_ID=""
SEASON_NUMBER=""
STARTING_EPISODE=""
EPISODE_ORDER="auto"
MINIMUM_EPISODE_SECONDS=900
is_listing_only=0
# Ripping a disc is a walk-away job, so every guess stands on its own unless
# --ask says otherwise. The one thing it will not do is invent a show or season
# it could not work out; that stops with an error instead.
is_asking_before_choices=0
is_keeping_raw_rip=0
is_copying_without_encode=0

usage() {
  cat <<EOF
Usage:
  ./rip-shows.sh [--show "Show Name"] [--season N] [--episode N]

Rips every episode on a TV disc in one pass. Works with DVD and Blu-ray, and
picks the HandBrake preset from whichever it finds.

Identifying the disc happens in layers, most reliable first:

  1. Whatever you pass on the command line.
  2. Episodes already in ${TV_DIRECTORY}, to continue a part-finished season.
  3. The season and disc number printed on the disc label.
  4. Episode lengths matched against the season, which pins down the disc when
     one episode runs long or short.
  5. Asking you.

Nothing is ripped until you have seen the episode list and agreed to it.

  --show "Name"          Skip the disc-label guess and search for this show
  --season N             Season on this disc
  --episode N            First episode on this disc, when the guess is wrong
  --order aired|dvd      Episode order. Defaults to DVD order when the show has
                         one published, because that is the order on the disc.
  --list                 Show the disc titles and the episode plan, rip nothing
  --min-length SECONDS   Ignore titles shorter than this (default: ${MINIMUM_EPISODE_SECONDS})
  --preset NAME          HandBrake preset, overriding the disc-type default
  --quality RF           Override the preset's quality. Lower is better and
                         bigger (DVD default 16, Blu-ray 18).
  --speed NAME           x264 effort (default slow). Quality is set by
                         --quality, not this, so "veryslow" does not look
                         better than the default -- it takes 2-3x as long to
                         reach the same quality in a slightly smaller file.
  --audio-langs LIST     Keep every audio track in these languages, e.g.
                         eng,spa. Forces an .mkv so surround audio survives.
  --subtitle-langs LIST  Keep subtitles in these languages, switched off by
                         default. Forces an .mkv.
  --keep-raw             Leave the MakeMKV .mkv files in ${RIPS_DIRECTORY}/raw
  --direct               Skip HandBrake and keep the decrypted .mkv
  --ask                  Stop and confirm each guess, and the episode plan,
                         instead of just going. Without this the rip runs
                         unattended.
  --no-eject             Leave the disc in the drive when it finishes
  -h, --help             Show this help

Only rip discs you own.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --show)
      SHOW_NAME="${2:-}"
      if [[ -z "$SHOW_NAME" ]]; then
        print_error "--show needs a show name"
        exit 1
      fi
      shift 2
      ;;
    --season)
      SEASON_NUMBER="${2:-}"
      if [[ ! "$SEASON_NUMBER" =~ ^[0-9]+$ ]]; then
        print_error "--season needs a number"
        exit 1
      fi
      shift 2
      ;;
    --episode)
      STARTING_EPISODE="${2:-}"
      if [[ ! "$STARTING_EPISODE" =~ ^[0-9]+$ ]]; then
        print_error "--episode needs a number"
        exit 1
      fi
      shift 2
      ;;
    --order)
      EPISODE_ORDER="${2:-}"
      if [[ "$EPISODE_ORDER" != "aired" && "$EPISODE_ORDER" != "dvd" ]]; then
        print_error "--order takes aired or dvd"
        exit 1
      fi
      shift 2
      ;;
    --list)
      is_listing_only=1
      shift
      ;;
    --min-length)
      MINIMUM_EPISODE_SECONDS="${2:-}"
      if [[ ! "$MINIMUM_EPISODE_SECONDS" =~ ^[0-9]+$ ]]; then
        print_error "--min-length needs a number of seconds"
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
    --audio-langs)
      AUDIO_LANGUAGES="${2:-}"
      if [[ -z "$AUDIO_LANGUAGES" ]]; then
        print_error "--audio-langs needs a list like eng,spa,fra"
        exit 1
      fi
      shift 2
      ;;
    --subtitle-langs)
      SUBTITLE_LANGUAGES="${2:-}"
      if [[ -z "$SUBTITLE_LANGUAGES" ]]; then
        print_error "--subtitle-langs needs a list like eng,spa"
        exit 1
      fi
      shift 2
      ;;
    --quality)
      VIDEO_QUALITY_OVERRIDE="${2:-}"
      if [[ ! "$VIDEO_QUALITY_OVERRIDE" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        print_error "--quality needs an RF number, lower being better (try 18)"
        exit 1
      fi
      shift 2
      ;;
    --speed)
      ENCODER_SPEED="${2:-}"
      case "$ENCODER_SPEED" in
        ultrafast|superfast|veryfast|faster|fast|medium|slow|slower|veryslow|placebo) ;;
        *)
          print_error "--speed needs an x264 preset name, e.g. slow or veryslow"
          exit 1
          ;;
      esac
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
    --ask)
      is_asking_before_choices=1
      shift
      ;;
    --no-eject)
      is_ejecting_when_done=0
      shift
      ;;
    --yes)
      # Accepting every guess is the default now. Accepted so older commands and
      # notes keep working.
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      print_error "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

ask_for_line() {
  local question="$1"
  local answer
  read -r -p "$question" answer </dev/tty
  printf '%s' "$answer"
}

# --- Working out which show this is ---------------------------------------

# Presents TVmaze matches and sets SHOW_ID / SHOW_NAME. Returns 1 when the
# search found nothing, so the caller can ask for a better name.
choose_show_from_search() {
  local query="$1"
  local matches
  local match_count

  matches="$("$SHOW_LOOKUP_COMMAND" search "$query")" || return 1
  matches="$(grep -v '^$' <<< "$matches" || true)"
  if [[ -z "$matches" ]]; then
    return 1
  fi

  match_count="$(wc -l <<< "$matches" | tr -d ' ')"
  if [[ "$match_count" -eq 1 || "$is_asking_before_choices" -eq 0 ]]; then
    SHOW_ID="$(head -1 <<< "$matches" | cut -f1)"
    SHOW_NAME="$(head -1 <<< "$matches" | cut -f2)"
    print_line "Show: ${SHOW_NAME}"
    return 0
  fi

  print_step "Which show is this?"
  local line_number=1
  while IFS=$'\t' read -r show_id show_name premiered network average_runtime; do
    printf '  %d) %s (%s, %s, ~%s min)\n' "$line_number" "$show_name" "$premiered" "$network" "$average_runtime"
    line_number=$((line_number + 1))
  done <<< "$matches"
  printf '  0) none of these\n'

  local choice
  choice="$(ask_for_line 'Number: ')"
  if [[ ! "$choice" =~ ^[0-9]+$ || "$choice" -lt 1 ]]; then
    return 1
  fi

  SHOW_ID="$(sed -n "${choice}p" <<< "$matches" | cut -f1)"
  SHOW_NAME="$(sed -n "${choice}p" <<< "$matches" | cut -f2)"
  if [[ -z "$SHOW_ID" ]]; then
    return 1
  fi
  print_line "Show: ${SHOW_NAME}"
}

identify_show() {
  if [[ -n "$SHOW_NAME" ]]; then
    choose_show_from_search "$SHOW_NAME" && return 0
    print_error "Nothing on TVmaze matches \"${SHOW_NAME}\"."
    exit 1
  fi

  local guessed_name
  guessed_name="$(cut -f1 <<< "$DISC_LABEL_FACTS")"
  if [[ -n "$guessed_name" ]]; then
    print_line "Disc label reads \"${DISC_LABEL}\", which looks like \"${guessed_name}\"."
    choose_show_from_search "$guessed_name" && return 0
    print_line "That did not match anything on TVmaze."
  else
    print_line "The disc label (\"${DISC_LABEL}\") says nothing useful about the show."
  fi

  if [[ "$is_asking_before_choices" -eq 0 ]]; then
    print_error "Cannot work out which show this disc is."
    print_line "Name it:      ./rip-shows.sh --show \"Show Name\""
    print_line "Or search:    ./rip-shows.sh --ask"
    exit 1
  fi

  while true; do
    local typed_name
    typed_name="$(ask_for_line 'Show name: ')"
    if [[ -z "$typed_name" ]]; then
      print_error "Need a show name."
      exit 1
    fi
    choose_show_from_search "$typed_name" && return 0
    print_line "No match. Try a different spelling."
  done
}

# --- Season, ordering, and where this disc starts --------------------------

identify_season() {
  if [[ -n "$SEASON_NUMBER" ]]; then
    return
  fi

  SEASON_NUMBER="$(cut -f2 <<< "$DISC_LABEL_FACTS")"
  if [[ -n "$SEASON_NUMBER" ]]; then
    print_line "Season ${SEASON_NUMBER}, from the disc label."
    return
  fi

  local seasons
  seasons="$("$SHOW_LOOKUP_COMMAND" seasons --show-id "$SHOW_ID" --order "$EPISODE_ORDER" 2>/dev/null || true)"
  if [[ "$(grep -c . <<< "$seasons")" -eq 1 ]]; then
    SEASON_NUMBER="$(cut -f1 <<< "$seasons")"
    print_line "${SHOW_NAME} only ran one season, so this is season ${SEASON_NUMBER}."
    return
  fi

  if [[ -n "$seasons" ]]; then
    print_step "${SHOW_NAME} has these seasons"
    while IFS=$'\t' read -r season episode_count; do
      [[ -z "$season" ]] && continue
      printf '  season %-3s %s episodes\n' "$season" "$episode_count"
    done <<< "$seasons"
  fi

  if [[ "$is_asking_before_choices" -eq 0 ]]; then
    print_error "Cannot tell which season this disc is."
    print_line "Say so:    ./rip-shows.sh --season N"
    print_line "Or pick:   ./rip-shows.sh --ask"
    exit 1
  fi

  SEASON_NUMBER="$(ask_for_line 'Season number: ')"
  if [[ ! "$SEASON_NUMBER" =~ ^[0-9]+$ ]]; then
    print_error "Need a numeric season."
    exit 1
  fi
}

# The disc is a DVD or Blu-ray, so its own running order is the one to follow.
# Shows that shipped out of broadcast order (Firefly) publish that separately.
resolve_episode_order() {
  if [[ "$EPISODE_ORDER" != "auto" ]]; then
    return
  fi

  if "$SHOW_LOOKUP_COMMAND" has-dvd-order "$SHOW_ID" >/dev/null 2>&1; then
    EPISODE_ORDER="dvd"
    print_line "This show publishes a DVD running order, so that is what the episodes are numbered by."
    print_line "Set the show to DVD Order in Plex so its metadata lines up."
    return
  fi
  EPISODE_ORDER="aired"
}

season_directory() {
  printf '%s/%s/Season %s' "$TV_DIRECTORY" "$(sanitize_file_component "$SHOW_NAME")" "$(padded_two_digits "$SEASON_NUMBER")"
}

# The most reliable answer to "which disc is this" is what you already ripped.
highest_episode_already_in_library() {
  local directory
  directory="$(season_directory)"
  [[ -d "$directory" ]] || return 0

  local season_padded
  season_padded="$(padded_two_digits "$SEASON_NUMBER")"
  find "$directory" -type f -name '*.m*' \
    | sed -n "s/.*[sS]${season_padded}[eE]\([0-9][0-9]*\).*/\1/p" \
    | sort -n \
    | tail -1
}

decide_starting_episode() {
  if [[ -n "$STARTING_EPISODE" ]]; then
    print_line "Starting at episode ${STARTING_EPISODE}, because you said so."
    return
  fi

  local last_ripped
  last_ripped="$(highest_episode_already_in_library)"
  if [[ -n "$last_ripped" ]]; then
    STARTING_EPISODE=$((10#$last_ripped + 1))
    print_line "Season ${SEASON_NUMBER} already has episodes up to ${last_ripped}, so this disc starts at ${STARTING_EPISODE}."
    return
  fi

  local disc_number
  disc_number="$(cut -f3 <<< "$DISC_LABEL_FACTS")"
  if [[ "$disc_number" == "1" ]]; then
    STARTING_EPISODE=1
    print_line "Disc 1 by the label, so this starts at episode 1."
  fi
}

# --- Building and showing the plan -----------------------------------------

build_episode_plan() {
  local plan_arguments=(
    plan
    --show-id "$SHOW_ID"
    --season "$SEASON_NUMBER"
    --order "$EPISODE_ORDER"
    --min-length "$MINIMUM_EPISODE_SECONDS"
  )
  if [[ -n "$STARTING_EPISODE" ]]; then
    plan_arguments+=(--start-episode "$STARTING_EPISODE")
  fi

  makemkv_title_table <<< "$(scan_disc_info)" | "$SHOW_LOOKUP_COMMAND" "${plan_arguments[@]}"
}

print_plan() {
  local plan="$1"
  local summary
  local confidence
  local note

  summary="$(grep '^#plan' <<< "$plan")"
  confidence="$(cut -f2 <<< "$summary")"
  note="$(cut -f3 <<< "$summary")"

  print_step "Episodes on this disc"
  while IFS=$'\t' read -r title_id duration season episode episode_name; do
    [[ "$title_id" == "#plan" ]] && continue
    if [[ -z "$season" ]]; then
      printf '  title %-3s %-10s  (no episode left to match)\n' "$title_id" "$duration"
      continue
    fi
    printf '  title %-3s %-10s  ->  s%se%s  %s\n' \
      "$title_id" "$duration" "$(padded_two_digits "$season")" "$(padded_two_digits "$episode")" "$episode_name"
  done <<< "$plan"

  print_line ""
  if [[ "$confidence" == "confident" ]]; then
    print_line "Confident: ${note}."
  else
    print_line "NOT confident: ${note}."
    if [[ "$is_asking_before_choices" -eq 0 ]]; then
      # Going ahead regardless: renaming afterwards is cheap, and a wrong name
      # is easier to spot in the library than a disc that never got ripped.
      print_line "Ripping anyway. Check the names above afterwards, and if the disc"
      print_line "starts somewhere else re-run with --episode N. Use --ask to be consulted."
    else
      print_line "Check the names above. If the disc starts somewhere else, use --episode N."
    fi
  fi
}

confirm_plan() {
  if [[ "$is_asking_before_choices" -eq 0 ]]; then
    return
  fi

  local answer
  answer="$(ask_for_line 'Rip these? [y/N] ')"
  if [[ ! "$answer" =~ ^[Yy] ]]; then
    print_line "Stopped. Nothing was ripped."
    exit 0
  fi
}

# --- Ripping ----------------------------------------------------------------

episode_file_name() {
  local season="$1"
  local episode="$2"
  local episode_name="$3"
  local extension="$4"
  local stem

  stem="$(sanitize_file_component "$SHOW_NAME") - s$(padded_two_digits "$season")e$(padded_two_digits "$episode")"
  if [[ -n "$episode_name" ]]; then
    stem="${stem} - $(sanitize_file_component "$episode_name")"
  fi
  printf '%s.%s' "$stem" "$extension"
}

rip_one_episode() {
  local title_id="$1"
  local output_file="$2"
  local staging_directory="$3"
  local produced_file
  local makemkv_status

  rm -rf "$staging_directory"
  mkdir -p "$staging_directory"

  set +e
  "$MAKE_MKV_COMMAND" --minlength=1 -r --decrypt mkv disc:0 "$title_id" "$staging_directory" </dev/null
  makemkv_status=$?
  set -e

  produced_file="$(find "$staging_directory" -type f -name '*.mkv' | head -1)"
  if [[ "$makemkv_status" -ne 0 || -z "$produced_file" ]]; then
    print_error "Title ${title_id} would not decrypt, skipping it."
    rm -rf "$staging_directory"
    return 1
  fi

  if [[ "$is_copying_without_encode" -eq 1 ]]; then
    copy_title "$produced_file" "$output_file"
  else
    convert_title "$produced_file" "$output_file"
  fi
  rm -rf "$staging_directory"
}

rip_planned_episodes() {
  local plan="$1"
  local output_directory
  local raw_directory
  local output_extension
  local ripped_count=0
  local skipped_count=0

  output_extension="$(output_container_extension)"
  if [[ "$is_copying_without_encode" -eq 1 ]]; then
    output_extension="mkv"
  fi

  output_directory="$(season_directory)"
  raw_directory="${RIPS_DIRECTORY}/raw/$(sanitize_file_component "$SHOW_NAME") S$(padded_two_digits "$SEASON_NUMBER")"
  mkdir -p "$output_directory"

  while IFS=$'\t' read -r title_id duration season episode episode_name; do
    [[ "$title_id" == "#plan" ]] && continue
    if [[ -z "$season" ]]; then
      print_line "Title ${title_id} has no episode to match, skipping."
      skipped_count=$((skipped_count + 1))
      continue
    fi

    local output_file
    output_file="${output_directory}/$(episode_file_name "$season" "$episode" "$episode_name" "$output_extension")"
    if [[ -e "$output_file" ]]; then
      print_line "Already have $(basename "$output_file"), skipping."
      skipped_count=$((skipped_count + 1))
      continue
    fi

    print_step "s$(padded_two_digits "$season")e$(padded_two_digits "$episode") ${episode_name} (${duration})"
    # Each episode is encoded and its raw file deleted before the next starts,
    # so it is one episode's worth of space that has to be there, not the disc's.
    require_free_space_for "$(title_size_bytes "$(scan_disc_info)" "$title_id")" "$raw_directory"
    if rip_one_episode "$title_id" "$output_file" "${raw_directory}/title-${title_id}"; then
      ripped_count=$((ripped_count + 1))
    fi
  done <<< "$plan"

  if [[ "$is_keeping_raw_rip" -eq 0 ]]; then
    rm -rf "$raw_directory"
  fi

  print_line ""
  print_step "Ripped ${ripped_count} episode(s) to ${output_directory}"
  if [[ "$skipped_count" -gt 0 ]]; then
    print_line "Skipped ${skipped_count}."
  fi
  print_line "In Plex: the TV library -> Scan Library Files."
  if [[ "$EPISODE_ORDER" == "dvd" ]]; then
    print_line "These are numbered in DVD order. Set the show to DVD Order in Plex or the titles will look shuffled."
  fi
}

# --- Main -------------------------------------------------------------------

require_macos
require_not_root

if [[ ! -x "$MAKE_MKV_COMMAND" ]]; then
  print_error "MakeMKV is not installed. Run ./setup.sh on the server Mac first."
  exit 1
fi

if [[ ! -x "$SHOW_LOOKUP_COMMAND" ]]; then
  print_error "Missing ${SHOW_LOOKUP_COMMAND}. It ships alongside this script."
  exit 1
fi

print_step "Reading the disc"
DISC_LABEL="$(read_disc_label || true)"
DISC_LABEL_FACTS="$("$SHOW_LOOKUP_COMMAND" label "$DISC_LABEL")"

identify_show
resolve_episode_order
identify_season
decide_starting_episode

EPISODE_PLAN="$(build_episode_plan)"

if [[ "$is_listing_only" -eq 1 ]]; then
  print_plan "$EPISODE_PLAN"
  print_line ""
  print_line "Nothing was ripped. Drop --list to go ahead."
  exit 0
fi

if [[ "$is_copying_without_encode" -eq 0 ]]; then
  if ! handbrake_command >/dev/null; then
    print_error "HandBrakeCLI is not installed. Run ./setup.sh on the server Mac first."
    exit 1
  fi
  choose_handbrake_preset
fi

print_plan "$EPISODE_PLAN"
confirm_plan
rip_planned_episodes "$EPISODE_PLAN"
eject_disc
