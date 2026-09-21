#!/usr/bin/env bash
# Look at whatever disc is in the drive, decide whether it is a film or a TV
# disc, and hand it to the ripper that knows how to deal with it.
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIRECTORY}/common.sh"

SHOW_LOOKUP_COMMAND="${SCRIPT_DIRECTORY}/show_lookup.py"
MOVIE_RIPPER="${SCRIPT_DIRECTORY}/rip-dvd.sh"
SHOW_RIPPER="${SCRIPT_DIRECTORY}/rip-shows.sh"

forced_kind=""
is_listing_only=0

usage() {
  cat <<EOF
Usage:
  ./rip.sh [--movie|--tv] [options passed through to the ripper]

Reads the disc, works out whether it holds a film or episodes of a show, then
runs ./rip-dvd.sh or ./rip-shows.sh accordingly. Anything else you pass is
handed to whichever one it picks.

It decides from the shape of the disc. A film disc has one dominant title with
shorter extras around it. An episode disc has several titles of near-identical
length. Season and disc numbering on the label counts too.

  --movie                Skip the guess, treat it as a film
  --tv                   Skip the guess, treat it as a TV disc
  --what-is-it           Say what it thinks the disc is and stop
  -h, --help             Show this help

Examples:
  ./rip.sh                          decide, then rip
  ./rip.sh --what-is-it             just tell me
  ./rip.sh --subtitle-langs eng     decide, then rip keeping English subtitles
  ./rip.sh --tv --season 2          override the guess

Only rip discs you own.
EOF
}

passthrough_arguments=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --movie)
      forced_kind="film"
      shift
      ;;
    --tv)
      forced_kind="show"
      shift
      ;;
    --what-is-it)
      is_listing_only=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      passthrough_arguments+=("$1")
      shift
      ;;
  esac
done

require_macos
require_not_root

if [[ ! -x "$MAKE_MKV_COMMAND" ]]; then
  print_error "MakeMKV is not installed. Run ./setup.sh on the server Mac first."
  exit 1
fi

run_ripper() {
  local ripper="$1"
  # An empty array under `set -u` would expand to an unbound variable on the
  # bash 3.2 that ships with macOS, so only pass arguments when there are some.
  if [[ "${#passthrough_arguments[@]}" -eq 0 ]]; then
    exec "$ripper"
  fi
  exec "$ripper" "${passthrough_arguments[@]}"
}

if [[ "$forced_kind" == "film" ]]; then
  run_ripper "$MOVIE_RIPPER"
fi
if [[ "$forced_kind" == "show" ]]; then
  run_ripper "$SHOW_RIPPER"
fi

print_step "Reading the disc"
disc_label="$(read_disc_label || true)"
verdict="$(makemkv_title_table <<< "$(scan_disc_info)" \
  | "$SHOW_LOOKUP_COMMAND" classify --label "$disc_label")"

disc_kind="$(cut -f1 <<< "$verdict")"
confidence="$(cut -f2 <<< "$verdict")"
reason="$(cut -f3 <<< "$verdict")"

if [[ "$disc_kind" == "show" ]]; then
  print_line "This looks like a TV disc: ${reason}."
else
  print_line "This looks like a film: ${reason}."
fi

if [[ "$confidence" != "confident" ]]; then
  print_line "That is a guess rather than a certainty. Use --movie or --tv to overrule it."
fi

if [[ "$is_listing_only" -eq 1 ]]; then
  exit 0
fi

if [[ "$disc_kind" == "show" ]]; then
  print_line "Handing it to rip-shows.sh."
  run_ripper "$SHOW_RIPPER"
fi

print_line "Handing it to rip-dvd.sh."
run_ripper "$MOVIE_RIPPER"
