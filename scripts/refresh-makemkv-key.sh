#!/usr/bin/env bash
# Fetch MakeMKV's current beta key and register it.
#
# MakeMKV is free while it is in beta, on a key that is posted publicly and
# rotates roughly every two months. When it lapses MakeMKV stops ripping, and
# what you see is a rip that failed rather than a sentence about registration.
#
# The work is done by ./media-server, which has the parsing and the tests. This
# is here so the chore is findable alongside the other scripts rather than only
# as a subcommand you have to already know about.
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIRECTORY}/common.sh"

PROJECT_ROOT="$(cd "${SCRIPT_DIRECTORY}/.." && pwd)"
LAUNCHER="${PROJECT_ROOT}/media-server"

usage() {
  cat <<EOF
Usage:
  ./scripts/refresh-makemkv-key.sh

Reads the current beta key from the MakeMKV forum and registers it, unless the
key already installed is the same one.

MakeMKV rewrites its settings file when it exits, so an edit made while it is
running would be undone. If MakeMKV is open this says so rather than writing
something that will not survive.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

require_macos

if [[ ! -x "$LAUNCHER" ]]; then
  print_error "Cannot find ${LAUNCHER}. Run this from inside the project."
  exit 1
fi

"$LAUNCHER" refresh-key
