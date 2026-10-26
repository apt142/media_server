#!/usr/bin/env bash
# Stop Plex Media Server, start it again, and wait until it answers.
#
# Plex keeps its library in a SQLite database and writes to it constantly:
# metadata, watch state, thumbnails. Killing it mid-write is the usual way a
# library database ends up corrupt, so this asks it to quit and gives it time
# to close the database properly. Forcing it is a separate decision, behind
# --force, and even that waits for the polite request to fail first.
#
# Run this on the server itself. Plex is not on the Mac you sit at.
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "${SCRIPT_DIRECTORY}/common.sh"

PLEX_APPLICATION="Plex Media Server"
PLEX_APPLICATION_PATH="${PLEX_APPLICATION_PATH:-/Applications/${PLEX_APPLICATION}.app}"
# Plex answers /identity without a token, so this says "the server is up"
# without needing to log in as anybody.
PLEX_IDENTITY_URL="http://127.0.0.1:32400/identity"

# Long enough for a big library to finish writing and close cleanly. A server
# mid-scan is the slow case, and cutting it short is the thing being avoided.
SECONDS_TO_WAIT_FOR_QUIT=45
# Starting is quicker than stopping, but a cold start still reopens the
# database and reads the library before it will answer.
SECONDS_TO_WAIT_FOR_START=60

is_forcing=0

usage() {
  cat <<EOF
Usage:
  ./scripts/restart-plex.sh            quit Plex, start it again, wait for it
  ./scripts/restart-plex.sh --force    kill it if it will not quit on its own

Run this on the server, not on the Mac you sit at.

Restarting is rarely the answer to "my new rip is not showing up". Nothing in
this pipeline tells Plex when a file lands, and a restart does not make it
look. Use Scan Library Files in the Plex web interface for that.
EOF
}

read_arguments() {
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      -h|--help) usage; exit 0 ;;
      --force) is_forcing=1; shift ;;
      *) print_error "Unknown option: $1"; usage; exit 1 ;;
    esac
  done
}

# The Mac this is typed on is usually not the Mac Plex runs on, and the error
# for that should say so rather than leaving someone to wonder why a restart
# did nothing.
require_plex_installed() {
  if [[ -d "$PLEX_APPLICATION_PATH" ]]; then
    return
  fi
  print_error "Plex Media Server is not installed on this Mac."
  print_line "This has to run on the server itself. Reach it with Screen Sharing"
  print_line "or ssh, then run it there."
  exit 1
}

# -x matches the process name exactly. A looser match would find this script,
# and the Plex helpers that come and go beside the server.
is_plex_running() {
  pgrep -x "$PLEX_APPLICATION" >/dev/null 2>&1
}

is_plex_answering() {
  curl --silent --fail --max-time 5 "$PLEX_IDENTITY_URL" >/dev/null 2>&1
}

# Returns 1 when the wait runs out, so the caller decides what that means:
# failing to stop is a problem, while failing to start quickly may not be.
wait_until() {
  local condition="$1"
  local seconds_to_wait="$2"
  local waited=0

  while [[ "$waited" -lt "$seconds_to_wait" ]]; do
    if "$condition"; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

is_plex_stopped() {
  ! is_plex_running
}

stop_plex() {
  if ! is_plex_running; then
    print_line "Plex was not running."
    return
  fi

  print_step "Asking Plex to quit"
  # A quit it refuses is not fatal here: the wait below is what decides
  # whether it actually went, and an unscriptable Plex still answers kill.
  osascript -e "quit app \"${PLEX_APPLICATION}\"" >/dev/null 2>&1 || true

  if wait_until is_plex_stopped "$SECONDS_TO_WAIT_FOR_QUIT"; then
    print_line "Plex has stopped."
    return
  fi

  if [[ "$is_forcing" -ne 1 ]]; then
    print_error "Plex is still running after ${SECONDS_TO_WAIT_FOR_QUIT}s."
    print_line "It may be part way through a library scan. Leave it a while longer,"
    print_line "or run this again with --force to kill it."
    exit 1
  fi

  print_step "Killing Plex, because --force was asked for"
  print_line "If its library database was mid-write, expect it to repair on startup."
  pkill -x "$PLEX_APPLICATION" || true
  if ! wait_until is_plex_stopped "$SECONDS_TO_WAIT_FOR_QUIT"; then
    print_error "Plex will not stop even when killed. A reboot is the next step."
    exit 1
  fi
  print_line "Plex has stopped."
}

start_plex() {
  print_step "Starting Plex"
  open -a "$PLEX_APPLICATION"

  if wait_until is_plex_answering "$SECONDS_TO_WAIT_FOR_START"; then
    local host_name
    host_name="$(bonjour_name 2>/dev/null || echo localhost)"
    print_step "Plex is up and answering on port 32400"
    print_line "  http://${host_name}:32400/web"
    return
  fi

  # Started but not answering is worth saying plainly. It is usually a big
  # library still opening, and usually comes good on its own.
  print_error "Plex started but has not answered in ${SECONDS_TO_WAIT_FOR_START}s."
  print_line "A large library can take longer than this to open. Check it with:"
  print_line "  curl -sI ${PLEX_IDENTITY_URL}"
  exit 1
}

main() {
  require_macos
  require_not_root
  read_arguments "$@"
  require_plex_installed

  stop_plex
  start_plex
}

main "$@"
