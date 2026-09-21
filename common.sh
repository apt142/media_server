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

# MakeMKV runs the disc's own Java code for some Blu-ray protections. JDK 25 and
# newer fail that; 17 is the version the MakeMKV forum reports as working.
MAKE_MKV_JAVA_FORMULA="openjdk@17"
MAKE_MKV_JAVA_COMMAND="/opt/homebrew/opt/openjdk@17/bin/java"

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

# MakeMKV has used both locations over the years. Prefer whichever already exists.
makemkv_settings_file() {
  local library_path="${HOME}/Library/MakeMKV/settings.conf"
  local dot_path="${HOME}/.MakeMKV/settings.conf"

  if [[ -f "$library_path" ]]; then
    printf '%s\n' "$library_path"
    return
  fi
  if [[ -f "$dot_path" ]]; then
    printf '%s\n' "$dot_path"
    return
  fi
  printf '%s\n' "$library_path"
}

# Prints the Java path MakeMKV is set to use, or nothing when it has none.
configured_makemkv_java() {
  local settings_file
  settings_file="$(makemkv_settings_file)"
  [[ -f "$settings_file" ]] || return 0
  sed -n 's/^app_Java[[:space:]]*=[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$settings_file" | tail -1
}

set_makemkv_java_path() {
  local java_path="$1"
  local settings_file
  settings_file="$(makemkv_settings_file)"

  mkdir -p "$(dirname "$settings_file")"
  touch "$settings_file"
  # Drop any previous app_Java line, then append the one we want.
  sed -i '' '/^app_Java[[:space:]]*=/d' "$settings_file"
  printf 'app_Java = "%s"\n' "$java_path" >> "$settings_file"
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

# --- Optical discs: reading titles and converting them ---
# Shared by rip-dvd.sh (movies) and rip-shows.sh (TV).

# Picked from the disc type at run time. Encoding a Blu-ray with the 480p preset
# would throw away most of the picture.
DVD_HANDBRAKE_PRESET="Super HQ 480p30 Surround"

BLURAY_HANDBRAKE_PRESET="HQ 1080p30 Surround"

HANDBRAKE_PRESET=""

# DVDs carry telecine flags that produce the timestamps Roku stalls on, so they
# get a constant rate. Blu-ray video is already constant; forcing 30 there would
# only duplicate frames on a 24fps film.
HANDBRAKE_RATE_FLAG="--cfr"

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

human_gigabytes() {
  local bytes="$1"
  if [[ ! "$bytes" =~ ^[0-9]+$ || "$bytes" -eq 0 ]]; then
    printf '?'
    return
  fi
  awk -v bytes="$bytes" 'BEGIN { printf "%.1f GB", bytes / 1073741824 }'
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
