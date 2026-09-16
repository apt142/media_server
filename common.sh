# Shared paths and helpers for the media-server scripts.
# Intended to be sourced, not executed.

MEDIA_ROOT="${MEDIA_ROOT:-$HOME/Media}"
MOVIES_DIRECTORY="${MEDIA_ROOT}/Movies"
TV_DIRECTORY="${MEDIA_ROOT}/TV"
FILES_DIRECTORY="${MEDIA_ROOT}/Files"
RIPS_DIRECTORY="${MEDIA_ROOT}/Rips"
SHARE_NAME="${SHARE_NAME:-Media}"

MAKE_MKV_APP="/Applications/MakeMKV.app"
MAKE_MKV_COMMAND="${MAKE_MKV_APP}/Contents/MacOS/makemkvcon"
PLEX_APP="/Applications/Plex Media Server.app"
MAKE_MKV_DOWNLOAD_PAGE="https://www.makemkv.com/download/"
MAKE_MKV_BETA_KEY_PAGE="https://forum.makemkv.com/forum/viewtopic.php?t=1053"

print_step() {
  printf '\n==> %s\n' "$*"
}

print_line() {
  printf '%s\n' "$*"
}

print_error() {
  printf 'error: %s\n' "$*" >&2
}

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    print_error "These scripts only support macOS."
    exit 1
  fi
}

require_not_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    print_error "Run this as your normal user, not with sudo. The script will ask for a password when it needs one."
    exit 1
  fi
}

handbrake_command() {
  command -v HandBrakeCLI
}

ffmpeg_command() {
  command -v ffmpeg
}

ffprobe_command() {
  command -v ffprobe
}

bonjour_name() {
  scutil --get LocalHostName
}

media_root_is_symlink() {
  [[ -L "$MEDIA_ROOT" ]]
}

resolved_media_root() {
  if media_root_is_symlink; then
    readlink "$MEDIA_ROOT"
    return
  fi
  printf '%s\n' "$MEDIA_ROOT"
}
