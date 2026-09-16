#!/usr/bin/env bash
# Print whether the media-server pieces are installed and running.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_macos

print_yes_no() {
  local label="$1"
  local is_ok="$2"
  if [[ "$is_ok" == "1" ]]; then
    printf '  %-22s yes\n' "$label"
    return
  fi
  printf '  %-22s no\n' "$label"
}

host_name="$(bonjour_name)"
sleep_on_adapter="$(pmset -g custom 2>/dev/null | awk '/AC Power/{found=1} found && /[[:space:]]sleep /{print $2; exit}')"
media_location="$(resolved_media_root)"

print_line "Server"
print_line "  user                   $(id -un)"
print_line "  bonjour name           ${host_name}.local"
print_line "  share URL             smb://${host_name}.local/${SHARE_NAME}"
print_line "  media folder          ${MEDIA_ROOT}"
print_line "  media actually at     ${media_location}"

print_line ""
print_line "Software"
if [[ -x "$MAKE_MKV_COMMAND" ]]; then
  print_yes_no "MakeMKV" 1
else
  print_yes_no "MakeMKV" 0
fi
if handbrake_command >/dev/null; then
  print_yes_no "HandBrakeCLI" 1
else
  print_yes_no "HandBrakeCLI" 0
fi
if [[ -d "$PLEX_APP" ]]; then
  print_yes_no "Plex app" 1
else
  print_yes_no "Plex app" 0
fi
if pgrep -x "Plex Media Server" >/dev/null; then
  print_yes_no "Plex running" 1
else
  print_yes_no "Plex running" 0
fi

print_line ""
print_line "Sharing"
if pgrep -x smbd >/dev/null; then
  print_yes_no "smbd running" 1
else
  print_yes_no "smbd running" 0
fi
if sharing -l | grep -F "$MEDIA_ROOT" >/dev/null; then
  print_yes_no "Media share" 1
else
  print_yes_no "Media share" 0
fi

print_line ""
print_line "Power (adapter)"
print_line "  sleep minutes         ${sleep_on_adapter:-unknown} (0 means never)"
print_line "  lid closed            Apple Silicon still sleeps unless a display is attached"

print_line ""
print_line "Disk"
if [[ -d "$MEDIA_ROOT" ]]; then
  df -h "$MEDIA_ROOT" | awk 'NR==1 || NR==2 {print "  " $0}'
else
  print_line "  ${MEDIA_ROOT} does not exist yet. Run ./setup.sh on the server Mac."
fi
