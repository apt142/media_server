#!/usr/bin/env bash
# Install Plex, ripping tools, a ~/Media library, SMB sharing, and AC-power stay-awake.
# Run this on the MacBook that will be the server, not on some other machine.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

usage() {
  cat <<EOF
Usage: ./setup.sh [--computer-name NAME]

Installs the media-server stack on this Mac:
  Plex Media Server, HandBrake, MakeMKV, ~/Media folders, SMB share, stay-awake on power.

  --computer-name NAME   Optional. Sets ComputerName and LocalHostName so other
                         Macs can mount smb://NAME.local/${SHARE_NAME}
EOF
}

COMPUTER_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --computer-name)
      COMPUTER_NAME="${2:-}"
      if [[ -z "$COMPUTER_NAME" ]]; then
        print_error "--computer-name needs a value"
        exit 1
      fi
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      print_error "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

install_homebrew_if_needed() {
  if command -v brew >/dev/null; then
    return
  fi

  print_step "Installing Homebrew"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  fi
}

install_brew_packages() {
  print_step "Installing Plex and HandBrake"
  brew install handbrake
  brew install --cask plex-media-server handbrake-app
}

latest_makemkv_dmg_name() {
  curl -fsSL "$MAKE_MKV_DOWNLOAD_PAGE" \
    | grep -oE 'makemkv_v[0-9.]+_osx.dmg' \
    | head -1
}

install_makemkv() {
  if [[ -x "$MAKE_MKV_COMMAND" ]]; then
    print_step "MakeMKV already installed"
    return
  fi

  print_step "Installing MakeMKV from makemkv.com"
  print_line "Homebrew no longer ships MakeMKV (Gatekeeper), so this downloads the official disk image."

  local dmg_name
  dmg_name="$(latest_makemkv_dmg_name)"
  if [[ -z "$dmg_name" ]]; then
    print_error "Could not find a macOS disk image on ${MAKE_MKV_DOWNLOAD_PAGE}"
    exit 1
  fi

  local download_path="/tmp/${dmg_name}"
  curl -fL --progress-bar -o "$download_path" "${MAKE_MKV_DOWNLOAD_PAGE%/}/${dmg_name}"

  local mount_point
  mount_point="$(hdiutil attach "$download_path" -nobrowse | awk '/\/Volumes\//{print $NF}')"
  if [[ -z "$mount_point" ]]; then
    print_error "Could not mount ${download_path}"
    exit 1
  fi

  local app_on_disk="${mount_point}/MakeMKV.app"
  if [[ ! -d "$app_on_disk" ]]; then
    hdiutil detach "$mount_point" >/dev/null
    print_error "MakeMKV.app was not on the disk image."
    exit 1
  fi

  rm -rf "$MAKE_MKV_APP"
  cp -R "$app_on_disk" /Applications/
  hdiutil detach "$mount_point" >/dev/null
  xattr -dr com.apple.quarantine "$MAKE_MKV_APP" || true
}

configure_makemkv_java() {
  print_step "Installing Java 17 for MakeMKV disc protection"
  print_line "Some Blu-ray protections make MakeMKV run the disc's own Java code. DVDs never need this."
  brew install "$MAKE_MKV_JAVA_FORMULA"

  if [[ ! -x "$MAKE_MKV_JAVA_COMMAND" ]]; then
    print_error "Expected Java at ${MAKE_MKV_JAVA_COMMAND}. Set it by hand in MakeMKV → Preferences → Protection."
    return
  fi

  set_makemkv_java_path "$MAKE_MKV_JAVA_COMMAND"
  print_line "MakeMKV will use ${MAKE_MKV_JAVA_COMMAND}"
}

create_media_folders() {
  print_step "Creating ${MEDIA_ROOT}"
  mkdir -p \
    "$MOVIES_DIRECTORY" \
    "$TV_DIRECTORY" \
    "$FILES_DIRECTORY" \
    "${RIPS_DIRECTORY}/raw" \
    "${RIPS_DIRECTORY}/converted"

  printf 'Drop movie files here. Name folders like: Movie Title (Year)\n' > "${MOVIES_DIRECTORY}/README.txt"
  printf 'Drop TV files here. Name folders like: Show Name/Season 01/\n' > "${TV_DIRECTORY}/README.txt"
  printf 'General files. Plex ignores this folder.\n' > "${FILES_DIRECTORY}/README.txt"
}

set_computer_name_if_requested() {
  if [[ -z "$COMPUTER_NAME" ]]; then
    return
  fi

  print_step "Setting computer name to ${COMPUTER_NAME}"
  sudo scutil --set ComputerName "$COMPUTER_NAME"
  sudo scutil --set LocalHostName "$COMPUTER_NAME"
}

enable_smb_daemon() {
  print_step "Enabling File Sharing (SMB)"
  sudo defaults write /Library/Preferences/SystemConfiguration/com.apple.smb.server.plist EnabledServices -array disk

  if ! sudo launchctl load -w /System/Library/LaunchDaemons/com.apple.smbd.plist 2>/dev/null; then
    sudo launchctl bootstrap system /System/Library/LaunchDaemons/com.apple.smbd.plist 2>/dev/null || true
    sudo launchctl enable system/com.apple.smbd 2>/dev/null || true
    sudo launchctl kickstart -k system/com.apple.smbd
  fi
}

share_media_folder() {
  if sharing -l | grep -F "$MEDIA_ROOT" >/dev/null; then
    print_line "SMB share for ${MEDIA_ROOT} already exists."
    return
  fi

  # Guest access stays off. Other Macs sign in with this Mac's user account.
  sudo /usr/sbin/sharing -a "$MEDIA_ROOT" -S "$SHARE_NAME" -n "$SHARE_NAME" -g 000
}

configure_power_on_adapter() {
  print_step "Preventing sleep while plugged in"
  # Lid-closed sleep on Apple Silicon is a separate hardware trigger. This only
  # stops idle sleep on AC power. See README for the closed-lid options.
  sudo pmset -c sleep 0
  sudo pmset -c disksleep 0
  sudo pmset -c displaysleep 10
  sudo pmset -c tcpkeepalive 1
  sudo pmset -c womp 1
}

allow_plex_through_firewall() {
  if [[ ! -d "$PLEX_APP" ]]; then
    return
  fi

  print_step "Allowing Plex through the application firewall"
  sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add "$PLEX_APP" || true
  sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp "$PLEX_APP" || true
}

add_plex_login_item() {
  print_step "Opening Plex at login"
  osascript <<'APPLESCRIPT' >/dev/null || true
tell application "System Events"
  set plexPath to "/Applications/Plex Media Server.app"
  set loginNames to name of every login item
  if loginNames does not contain "Plex Media Server" then
    make new login item at end with properties {path:plexPath, hidden:false}
  end if
end tell
APPLESCRIPT
}

start_plex() {
  print_step "Starting Plex Media Server"
  open -a "Plex Media Server"
}

print_next_steps() {
  local host_name
  host_name="$(bonjour_name)"

  cat <<EOF

Setup finished. The remaining steps are in README.md, in order:

  2. Register MakeMKV     ${MAKE_MKV_BETA_KEY_PAGE}
  3. Allow macOS permissions (Local Network, Removable Volumes, Login Items)
  4. Set up Plex on the server
       Movies  →  ${MOVIES_DIRECTORY}
       TV      →  ${TV_DIRECTORY}
  5. Set up the Roku
  6. Mount the network drive from another Mac
       smb://${host_name}.local/${SHARE_NAME}
       Sign in as $(id -un) on this Mac.

Then:
  ./status.sh
  ./rip-dvd.sh "Movie Title" 1999
EOF
}

require_macos
require_not_root

print_step "This will ask for your Mac password for File Sharing and power settings"
sudo -v

install_homebrew_if_needed
install_brew_packages
install_makemkv
configure_makemkv_java
create_media_folders
set_computer_name_if_requested
enable_smb_daemon
share_media_folder
configure_power_on_adapter
allow_plex_through_firewall
add_plex_login_item
start_plex
print_next_steps
