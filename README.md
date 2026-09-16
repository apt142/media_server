# MacBook media server

Turn a **MacBook Pro M1 running macOS Tahoe** into a home media server:

- Rip DVDs you own (USB optical drive)
- Serve them to a Roku via Plex
- Share the same folder as a network drive for other Macs

Copy this folder onto **that** MacBook (AirDrop, USB stick, or a git clone). Run every command below on the server Mac, not on some other computer.

`setup.sh` installs apps and turns on sharing. It cannot finish Plex, MakeMKV, or macOS permission prompts. Those steps are in this README, in order.

## What you get

```
~/Media/
  Movies/     Plex movie library
  TV/         Plex TV library
  Files/      general network dump (do not add this to Plex)
  Rips/       temporary MakeMKV output while ripping
```

Plex and the SMB share both use `~/Media`. Later, `./move-library-to-usb.sh` copies that tree to a USB disk and replaces `~/Media` with a symlink, so Plex and the share keep the same path.

## Before you start

On the server Mac:

- Plug it into power. Leave the lid **open** until you have read [Keep it awake](#keep-it-awake).
- Connect it to the same Wi-Fi (or Ethernet) as the Roku and the other Macs. Not a guest network, not a “device isolation” / AP-isolation SSID.
- Plug in the USB DVD drive when you are ready to rip. You do not need it for setup.
- Have a [Plex account](https://www.plex.tv/sign-up/). Create one in a browser if you do not already have one. You will use this **same** account on the server Mac and on the Roku.

Only rip discs you own. Do not forward Plex or SMB to the internet.

---

## 1. Run the installer

Copy this project onto the server Mac, then:

```bash
cd ~/Documents/media-server    # or wherever you put the folder
chmod +x setup.sh rip-dvd.sh move-library-to-usb.sh status.sh fix-rip.sh
./setup.sh --computer-name MediaServer
```

`--computer-name MediaServer` is optional but recommended. It makes the share `smb://MediaServer.local/Media` instead of whatever name the laptop shipped with. If you skip it, `./status.sh` prints the name to use.

The script will ask for the Mac password (File Sharing and power settings). It then:

- Installs Homebrew if needed
- Installs Plex Media Server, HandBrake, and MakeMKV
- Creates `~/Media/{Movies,TV,Files}`
- Turns on SMB file sharing for `~/Media` (guest access off)
- Stops idle sleep while plugged in
- Starts Plex and tries to add it as a login item

When it finishes, **do not stop**. Work through sections 2–6 below on that same Mac.

---

## 2. Register MakeMKV

MakeMKV is free while it is in beta. The author posts a key that lasts about 60 days. You do not buy this key and you do not generate it.

1. On the server Mac, open [MakeMKV is free while in beta](https://forum.makemkv.com/forum/viewtopic.php?t=1053).
2. Copy the key in the first post (a long string starting with `T-`).
3. Open **MakeMKV** from Applications.
   - If macOS says it cannot be opened because it is from an unidentified developer: Control-click MakeMKV → Open → Open. Or run:

     ```bash
     xattr -dr com.apple.quarantine /Applications/MakeMKV.app
     ```

     then open it again.
4. Menu bar: **Help → Register**.
5. Paste the key. Click **OK**.

When the key expires, MakeMKV will refuse to rip. Come back to that same forum thread, copy the new key, and paste it again. A [paid license](https://www.makemkv.com/) skips this rotation.

You also need macOS to let MakeMKV see the USB drive. Do that in the next section.

---

## 3. Allow macOS permissions

Tahoe will prompt for some of these. Anything it does not prompt for, set by hand.

**Local Network** (Plex will not show up on the Roku without this):

The path is the same on Tahoe. Plex is not a top-level Settings item. It only appears *inside* Local Network, and only after it has asked for access.

1. Apple menu → **System Settings**.
2. Sidebar: **Privacy & Security**. Scroll down if you do not see it. Or type `Local Network` in the Settings search box.
3. Click **Local Network**.
4. If **Plex Media Server** is in the list, turn it on.
5. If it is **not** in the list, the app has not requested permission yet. That is normal.
   - Open **Plex Media Server** from Applications (or click the menu-bar icon → **Open Plex…**).
   - Watch for a dialog: *“Plex Media Server” Would Like to Find and Connect to Devices on Your Local Network.* Click **Allow**.
   - Then go back to Local Network. It should now be listed and on.
6. If you still get no dialog and no list entry: quit Plex fully (menu-bar icon → Quit), reopen it, and try again. A reboot after the first launch also forces the prompt on some Macs.

**Removable Volumes** (the USB DVD drive):

1. System Settings → **Privacy & Security** → **Removable Volumes**.
2. Turn on **MakeMKV**.
3. If you rip with `./rip-dvd.sh` from Terminal, also turn on **Terminal** (or **iTerm**, if that is what you use).

**Login Items** (Plex should start when you log in):

1. System Settings → **General** → **Login Items & Extensions**.
2. Under Open at Login, **Plex Media Server** should be listed. If it is not: click **+** and add `/Applications/Plex Media Server.app`.

**File Sharing** (confirm the script actually flipped the GUI):

1. System Settings → **General** → **Sharing**.
2. **File Sharing** should be on.
3. Click the info button next to File Sharing.
4. Shared Folder list should include **Media** (`~/Media`).
5. Your user should have **Read & Write**. Guest access should be off.
6. Options: **Share files and folders using SMB** should be on, and your user should be checked.

If File Sharing is off, turn it on and add `~/Media` with the **+** button.

---

## 4. Set up Plex on the server

Plex Media Server should already be running (menu bar icon that looks like the Plex logo). If it is not, open **Plex Media Server** from Applications.

### Sign in and claim the server

1. Click the Plex menu-bar icon → **Open Plex…**  
   A browser tab should open at `http://127.0.0.1:32400/web`. If it does not, paste that URL yourself.
2. Sign in with the Plex account you created earlier.
3. Name the server something obvious, for example `MediaServer`. This is the name you will see on the Roku.
4. Skip Plex’s streaming / “free movies” offers if it presents them. You only need it to serve files in `~/Media`.

If the page asks you to **claim** the server, do that while signed in. Until it is claimed, the Roku cannot see it.

### Add the two libraries

Stay in the Plex web app (`http://127.0.0.1:32400/web`).

1. Left sidebar: **More** (or **+**) → **Add Library**.

2. Movies:
   - Library type: **Movies**
   - Name: `Movies`
   - Click **Next**
   - **Browse for Media Folder** → `Macintosh HD` → Users → *your username* → **Media** → **Movies**  
     Full path: `/Users/YOUR_USERNAME/Media/Movies`
   - Click **Add**, then **Add Library**.
   - You do not need to change language or advanced options.

3. Add Library again for TV:
   - Library type: **TV Shows**
   - Name: `TV`
   - Folder: `/Users/YOUR_USERNAME/Media/TV`

Do **not** add `~/Media/Files` or `~/Media/Rips`. Those are for the network share and for ripping scratch space.

Delete the placeholder `README.txt` files inside `Movies` and `TV` if they are still there, so Plex does not try to match them as titles.

### Server settings that matter on a laptop

In the Plex web app, click the **wrench** (top right). On the **left**, under Settings, click the **server name** (this Mac), not your account email. Account settings will not change how this machine serves files.

Then set:

| Where | What |
|---|---|
| **Remote Access** | Leave **Remote Access** disabled. You are serving the LAN only. |
| **Library** | Turn on **Scan my library automatically** and **Run a partial scan when changes are detected**. |
| **Network** | Keep **Enable local network discovery (GDM)** on. That is how the Roku finds the server. |
| **Language** | Optional. Set the agent language if you care about metadata language. |

Hardware transcoding in Plex is a paid Plex Pass feature. You do not need it if you rip with `./rip-dvd.sh`, which already converts to H.264 the Roku can play directly.

Close the settings. The empty libraries are fine until you rip something.

---

## 5. Set up the Roku

1. Confirm the Roku is on the **same** Wi-Fi (or same router LAN) as the server Mac.
2. On the Roku: **Streaming Channels** (or Search) → search **Plex** → **Add channel**.
3. Open the Plex channel.
4. Sign in with the **same** Plex account you used on the Mac.
5. The server named in step 4 (for example `MediaServer`) should appear. Open it. **Movies** and **TV** should be listed.

If the server does not appear:

- On the server Mac, run `./status.sh`. **Plex running** and **smbd running** should both say yes.
- Confirm Local Network permission for Plex (section 3).
- On the Roku Plex app, look for a **manual connection** / **enter IP** option. On the server Mac, System Settings → **Wi-Fi** → Details, copy the IP (something like `192.168.1.42`) and enter it. Default Plex port is `32400`.
- Reboot the Roku after Plex is signed in and the server is claimed.

A newly ripped file may take a minute to show up. In Plex on the Mac: library → **More** (three dots) → **Scan Library Files**.

---

## 6. Mount the network drive from another Mac

On a **client** Mac (not the server):

1. Finder → **Go** → **Connect to Server** (⌘K).
2. Enter:

   ```
   smb://MediaServer.local/Media
   ```

   If you did not pass `--computer-name`, use the Bonjour name from `./status.sh` on the server (`smb://Whatever.local/Media`).
3. Connect as **Registered User**.
4. Name and password are the **server Mac’s** login, not the client Mac’s.
5. Check **Remember this password in my keychain** if you want it to remount later.

You should see `Movies`, `TV`, `Files`, and `Rips`. Use **Files** for random documents. Put films and shows only in `Movies` and `TV` so Plex keeps a clean library.

To reconnect after a reboot: Finder sidebar, or the same ⌘K address.

---

## 7. Confirm everything

On the server Mac:

```bash
./status.sh
```

You want:

- MakeMKV, HandBrakeCLI, Plex app: **yes**
- Plex running: **yes**
- smbd running: **yes**
- Media share: **yes**
- sleep minutes (adapter): **0**

Then, from another Mac, mount the share. On the Roku, open Plex and confirm the empty libraries are visible. After the first rip, play that file on the Roku.

---

## Rip a DVD

On the server Mac:

1. Plug in the USB optical drive. Insert a disc you own.
2. MakeMKV must already be registered (section 2).
3. Run:

   ```bash
   ./rip-dvd.sh "The Matrix" 1999
   ```

That decrypts with MakeMKV, then converts the longest title with HandBrake’s **Super HQ 480p30 Surround** preset (slow x264, proper DVD deinterlace). Writes:

```
~/Media/Movies/The Matrix (1999)/The Matrix (1999).mp4
```

Plex names movies from that folder. Expect **longer** than the first rips: MakeMKV 20–40 minutes, then HandBrake can take another 30–90 minutes for a feature. Leave the USB drive plugged in until MakeMKV finishes.

Older rips done with the original script used a VideoToolbox quality setting that crushed DVD video. Re-rip those titles if they look noisy.

Then in Plex: Movies → **Scan Library Files**. On the Roku, open Movies and play it.

**TV discs:** rip the same way, then move the `.mp4` into Plex’s TV layout:

```
~/Media/TV/Show Name/Season 01/Show Name - s01e01 - Episode Name.mp4
```

Flags:

- `--min-length 3600` — skip titles under an hour (trailers, extras)
- `--keep-raw` — keep the decrypted `.mkv` under `~/Media/Rips/raw`
- `--direct` — skip HandBrake and keep the DVD MPEG-2 stream as `.mkv`. Closest to the disc. Plex will transcode that for the Roku; 480p on an M1 is light.

If MakeMKV says the application is too old or the key is invalid, repeat section 2 with the current forum key.

---

## Move the library to a USB drive later

When the internal disk fills up:

```bash
./move-library-to-usb.sh /Volumes/YourDrive
```

That copies `~/Media` to `YourDrive/Media`, points `~/Media` at it, and leaves `~/Media.internal-backup` until you have watched something on the Roku and mounted the share from another Mac. Then:

```bash
rm -rf ~/Media.internal-backup
```

Leave the USB drive plugged into the **server** Mac. If you unplug it, Plex and the share go empty until the volume remounts.

---

## Keep it awake

`setup.sh` sets **idle sleep to never while plugged in**. The screen can still sleep. That is enough if the lid stays open.

Closing the lid on an M1 MacBook is separate. Apple Silicon still sleeps unless a display is attached (clamshell). Options:

1. Leave the lid open. Simplest.
2. USB-C HDMI dummy plug + power adapter, then close the lid.
3. Do **not** run `sudo pmset -a disablesleep 1` on a laptop you still take places. That setting persists. The Mac will not sleep in a bag, and it will cook itself.

Keep it plugged in. A laptop used as a 24/7 server on battery will ruin the battery.

---

## Troubleshooting

**Roku cannot see Plex**

- Plex Media Server is running (`./status.sh`).
- Same Wi-Fi, not guest / AP isolation.
- Local Network permission for Plex is on.
- You signed into the Roku app with the **same** Plex account that claimed the server.
- Try the server’s LAN IP and port `32400` in the Roku Plex app.

**Other Mac cannot mount `smb://…/Media`**

- File Sharing is on and **Media** is in the shared-folder list (section 3).
- You used the server Mac’s username and password.
- Try the LAN IP instead of the `.local` name: `smb://192.168.x.x/Media`.
- On the client: System Settings → Privacy & Security → Local Network, allow **Finder** if it asked.

**MakeMKV does not see the disc**

- Disc is inserted, drive has a light / shows up in Finder.
- Removable Volumes permission for MakeMKV (and Terminal, if using the script).
- Register the current beta key (section 2).

**One movie stalls after a few seconds, then speed-plays with no sound**

That is the file, not Wi-Fi. DVD detelecine can leave a *variable* frame rate or messy timestamps. Roku plays a couple of GOPs, loses the clock, then skips forward with no audio.

On the server Mac, stop the Roku, then remux (seconds):

```bash
./fix-rip.sh ~/Media/Movies/"Movie Title (Year)"/"Movie Title (Year)".mp4
```

Plex → Scan Library Files, try that title again. If it still stalls, rebuild it at a constant frame rate (as long as a new rip):

```bash
./fix-rip.sh --reencode ~/Media/Movies/"Movie Title (Year)"/"Movie Title (Year)".mp4
```

The broken file is kept as `*.stalled.mp4` next to the replacement. New rips from `./rip-dvd.sh` already force a constant frame rate.

**Plex finds the file but the Roku transcodes or buffers**

- Default `./rip-dvd.sh` now writes H.264 Super HQ. A `--direct` `.mkv` is MPEG-2 and Plex will transcode it for the Roku; that is expected and cheap at 480p.
- Confirm you are playing the file under `Movies/`, not a leftover in `Rips/`.

**Plex does not start at login**

- Section 3, Login Items. Also leave the Plex menu-bar app running once; it often offers “Start Plex automatically.”

---

## Manual equivalent

If you would rather click than run `setup.sh`, do this on the server Mac, then continue from section 2.

1. Install [Homebrew](https://brew.sh), then:

   ```bash
   brew install handbrake
   brew install --cask plex-media-server handbrake-app
   ```

2. Download [MakeMKV](https://www.makemkv.com/download/). Drag `MakeMKV.app` to `/Applications`. If Gatekeeper blocks it:

   ```bash
   xattr -dr com.apple.quarantine /Applications/MakeMKV.app
   ```

3. Create `~/Media/Movies`, `~/Media/TV`, and `~/Media/Files`.
4. System Settings → General → Sharing → File Sharing on. Share `~/Media` as `Media`. Guest access off. Your user: Read & Write.
5. System Settings → Battery → Options: prevent automatic sleeping on power adapter.
6. Open Plex Media Server, then follow sections 2–6.

---

## Uninstall / undo

This project does not uninstall apps. To undo the server bits:

```bash
sudo /usr/sbin/sharing -r Media
sudo pmset -c sleep 1
```

Quit Plex. System Settings → General → Login Items & Extensions → remove Plex Media Server. The library files stay in `~/Media` until you delete them.
