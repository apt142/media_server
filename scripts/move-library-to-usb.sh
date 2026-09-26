#!/usr/bin/env bash
# Copy ~/Media onto a USB drive and replace ~/Media with a symlink so Plex and SMB keep working.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

usage() {
  cat <<EOF
Usage: ./scripts/move-library-to-usb.sh /Volumes/DriveName

Copies ${MEDIA_ROOT} to DriveName/Media, then replaces ${MEDIA_ROOT} with a
symlink. Plex libraries and the SMB share keep the same path.

The USB drive must stay plugged into the server Mac after this. If it is
unplugged, Plex and the network share will look empty.
EOF
}

VOLUME_PATH="${1:-}"

require_volume() {
  if [[ -z "$VOLUME_PATH" ]]; then
    usage
    exit 1
  fi

  if [[ "$VOLUME_PATH" != /Volumes/* ]]; then
    print_error "Pass a path under /Volumes, for example /Volumes/MyPassport"
    exit 1
  fi

  if [[ ! -d "$VOLUME_PATH" ]]; then
    print_error "No volume at ${VOLUME_PATH}. Plug the drive in and check Finder or: ls /Volumes"
    exit 1
  fi
}

plex_is_running() {
  pgrep -x "Plex Media Server" >/dev/null
}

stop_plex_if_running() {
  if ! plex_is_running; then
    return
  fi

  print_step "Stopping Plex so files are not in use"
  osascript -e 'tell application "Plex Media Server" to quit' >/dev/null 2>&1 || true
  sleep 2
  if plex_is_running; then
    pkill -x "Plex Media Server" || true
  fi
}

start_plex() {
  if [[ -d "$PLEX_APP" ]]; then
    open -a "Plex Media Server"
  fi
}

require_macos
require_not_root

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

require_volume

if media_root_is_symlink; then
  print_error "${MEDIA_ROOT} is already a symlink to $(resolved_media_root)"
  print_error "If you meant to copy to a different drive, remove that symlink first."
  exit 1
fi

if [[ ! -d "$MEDIA_ROOT" ]]; then
  print_error "${MEDIA_ROOT} does not exist. Run ./scripts/setup.sh first."
  exit 1
fi

destination_directory="${VOLUME_PATH}/Media"

print_step "This will ask for your Mac password if Plex needs to be stopped and the share refreshed"
sudo -v

stop_plex_if_running

print_step "Copying ${MEDIA_ROOT} → ${destination_directory}"
mkdir -p "$destination_directory"
rsync -aH --progress "$MEDIA_ROOT"/ "$destination_directory"/

backup_directory="${MEDIA_ROOT}.internal-backup"
print_step "Keeping the internal copy as ${backup_directory} until you confirm the USB works"
if [[ -e "$backup_directory" ]]; then
  print_error "${backup_directory} already exists. Move it aside and run this again."
  exit 1
fi
mv "$MEDIA_ROOT" "$backup_directory"
ln -s "$destination_directory" "$MEDIA_ROOT"

print_step "Refreshing the SMB share so it follows the new location"
if sharing -l | grep -F "$SHARE_NAME" >/dev/null; then
  sudo /usr/sbin/sharing -r "$SHARE_NAME" || true
fi
sudo /usr/sbin/sharing -a "$MEDIA_ROOT" -S "$SHARE_NAME" -n "$SHARE_NAME" -g 000

start_plex

cat <<EOF

Library now lives at:
  ${destination_directory}
  ${MEDIA_ROOT} → that folder

Watch a movie on the Roku, then mount the share from another Mac. If both
work, you can delete the internal backup:

  rm -rf "${backup_directory}"

Keep the USB drive plugged into the server Mac.
EOF
