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

## Just rip whatever is in the drive

Put the disc in, run one command, walk away:

```bash
./rip.sh
```

No flags, no questions. It identifies the disc, looks the title up, rips it, files it where Plex will find it, and ejects the disc when it is done. The ejected disc is the signal that it finished.

Nothing prompts by default. Where it has to choose — which film the label refers to, which episode a title is — it takes its best answer and says what it picked, rather than waiting at a prompt nobody is sitting in front of. If you would rather be consulted, add `--ask`.

There are two things it will not guess, because guessing would be worse than stopping: a film whose title it cannot find, and a TV disc whose show or season it cannot work out. Those stop with an error telling you the flag to add.

It reads the disc, decides whether it holds a film or episodes of a show, and hands off to `rip-dvd.sh` or `rip-shows.sh`. Any other flags you pass go through to whichever it picks, so `./rip.sh --subtitle-langs eng` works the same either way.

It decides from the shape of the disc rather than the label. A film disc has one dominant title surrounded by shorter extras; an episode disc has several titles of near-identical length, because episodes run to the same slot. A season number on the label counts as further evidence; a bare disc number counts for less, since plenty of films ship as `KNIVES_OUT_FEATURE_DISC1`. It tells you what it concluded and why:

```
This looks like a TV disc: two titles run to almost exactly the same length,
and the label "FIREFLY_D1" carries season or disc numbering.
```

That combination is why it handles Firefly's first disc correctly despite the feature-length pilot sitting next to two ordinary episodes. Check without ripping using `./rip.sh --what-is-it`, and overrule it with `--movie` or `--tv`.

---

## Rip a DVD or Blu-ray

On the server Mac:

1. Plug in the USB optical drive. Insert a disc you own.
2. MakeMKV must already be registered (section 2).
3. Run, with a disc in the drive:

   ```bash
   ./rip-dvd.sh
   ```

   That reads the disc label (often something like `THE_MATRIX`) and looks it up in Apple’s movie catalog. Pick a number, or type `Title 1999` yourself. To skip the prompt and take the first match:

   ```bash
   ./rip-dvd.sh --yes
   ```

   You can still name it by hand:

   ```bash
   ./rip-dvd.sh "The Matrix" 1999
   ```

   Disc labels are not a fingerprint. Box-set discs, “DVD_VIDEO”, and TV seasons often miss or match the wrong film — read the list before you accept it.

That decrypts with MakeMKV, then converts the longest title with HandBrake. The script reads the disc type and picks the preset: **Super HQ 480p30 Surround** for a DVD, **Super HQ 1080p30 Surround** for a Blu-ray. See [Quality](#quality) to tune it. Writes:

```
~/Media/Movies/The Matrix (1999)/The Matrix (1999).mp4
```

Plex names movies from that folder. Expect **longer** than the first rips: MakeMKV 20–40 minutes, then HandBrake can take another 30–90 minutes for a feature. Leave the USB drive plugged in until MakeMKV finishes.

Blu-ray is a different scale. The raw rip is 25–35 GB, so keep that much free, and the 1080p encode runs for several hours on an M1. Check free space with `df -h ~/Media` before you start.

**If it rips the wrong thing** (a commentary cut, a bonus feature, a decoy), see [Picking the right title](#picking-the-right-title).

Older rips done with the original script used a VideoToolbox quality setting that crushed DVD video. Re-rip those titles if they look noisy.

Then in Plex: Movies → **Scan Library Files**. On the Roku, open Movies and play it.

**TV discs:** use [`./rip-shows.sh`](#rip-a-tv-series). `rip-dvd.sh` is movies only — its old `--tv` mode guessed the show from the disc label alone, which is what misnamed the Firefly episodes, and it has been removed.

Flags:

- `--list` — print the disc's titles and stop
- `--title N` — rip that exact title instead of guessing
- `--all` — decrypt every title to `~/Media/Rips/raw/` and stop, so you can sort them out yourself
- `--preset NAME` — HandBrake preset, overriding the disc-type default
- `--quality RF` — override the preset's quality, lower being better
- `--speed veryslow` — more x264 effort; smaller files, 2-3x the time, same quality target (default `slow`)
- `--audio-langs eng,spa` / `--subtitle-langs eng` — extra tracks, forces MKV
- `--min-length 3600` — skip titles under an hour (trailers, extras).
- `--keep-raw` — keep the decrypted `.mkv` under `~/Media/Rips/raw`
- `--direct` — skip HandBrake and keep the DVD MPEG-2 stream as `.mkv`. Closest to the disc. Plex will transcode that for the Roku; 480p on an M1 is light.
- `--ask` — confirm the title instead of taking the best match
- `--no-eject` — leave the disc in the drive when it finishes

If MakeMKV says the application is too old or the key is invalid, repeat section 2 with the current forum key.

### Picking the right title

Blu-rays rarely have one obvious movie on them. A disc may carry the feature, a director's-commentary version of the same runtime, a few bonus cuts, and — on protected discs — dozens of decoy playlists that are deliberately near the right length. Guessing wrong is normal.

List what is actually on the disc:

```bash
./rip-dvd.sh --list
```

```
TITLE  LENGTH     SIZE      SOURCE         FLAG   NAME
0      2:10:32    26.1 GB   00800.mpls     main   title_t00.mkv
1      2:10:32    28.9 GB   00801.mpls     -      title_t01.mkv
2      4:20:00    41.0 GB   00999.mpls     -      title_t02.mkv
```

`main` means MakeMKV's Java playlist detection flagged that one as the real feature — trust it. That flag only appears when Java is working, so if nothing is flagged, fix Java first (see [the JRE section](#troubleshooting)) and list again.

With no flag to go on, judge by **length**, not size. The commentary version above is the *bigger* file because it carries an extra audio track, which is exactly the trap. Match the runtime against the box or a search for the film; a title far longer than the movie is usually a Play All or a decoy.

Then rip that one:

```bash
./rip-dvd.sh --title 0 "Knives Out" 2019
```

`--title` is for movies. TV discs are handled by [`rip-shows.sh`](#rip-a-tv-series), which rips every episode-length title.

Unsure between two? Rip the candidate without encoding, which is much faster, and play it to check:

```bash
./rip-dvd.sh --title 0 --direct --keep-raw "Knives Out" 2019
```

The script picks the `main`-flagged title when one exists, otherwise the longest. It used to rip every title and keep the largest file, which is what sent a commentary cut into the library.

### Rip everything and sort it out yourself

When the disc is fighting you, stop guessing and pull all of it:

```bash
./rip-dvd.sh --all "Knives Out"
```

It decrypts every title over the minimum length into one folder and stops there. **Nothing is encoded and nothing is added to Plex** — this is a staging area, not a library.

```
~/Media/Rips/raw/Knives Out/t00 - 2h10m32s - main.mkv
~/Media/Rips/raw/Knives Out/t01 - 2h10m32s.mkv
~/Media/Rips/raw/Knives Out/t05 - 8m14s.mkv
```

Filenames carry the title number, the runtime, and the `main` flag when MakeMKV identified the feature, so you can usually spot the keeper without playing anything. Those `.mkv` files play in VLC or IINA directly.

Before it starts, it prints how much space the titles need against how much you have free — on a Blu-ray this is routinely 90 GB or more, so read that line. Pass `--yes` to skip the confirmation.

Some titles will fail on a protected disc. That is expected: decoy playlists are designed not to decrypt, and the run continues past them. Re-running skips files you already have, so an interrupted rip picks up where it left off.

Once you know which one you want, either encode it properly:

```bash
./rip-dvd.sh --title 0 "Knives Out" 2019
```

or, if the raw file is good enough, move it into place yourself. Plex reads the folder name, so it needs to land as `~/Media/Movies/Knives Out (2019)/Knives Out (2019).mkv`. Delete the staging folder when you are done — it is large and Plex does not index it.

---

## Where the titles come from

Film titles and years come from **Wikidata**, and TV shows and episodes from **TVmaze**. Neither needs an account or an API key, so nothing to set up.

This used to use Apple's iTunes Search API for films. That endpoint still answers, and still returns HTTP 200, but as of 2026 it reports zero results for `media=movie` and `media=tvShow` while continuing to serve music — so the film lookup had quietly been finding nothing at all, every time. If you have folders named after disc labels rather than films, that is why.

Wikidata is queried by the cleaned disc label. `KNIVES_OUT_FEATURE_DISC1` becomes a search for `KNIVES OUT`, and results are ranked by how closely they match, so the film wins over its sequels and over stage adaptations of it. You can always skip the lookup:

```bash
./rip-dvd.sh "Knives Out" 2019
```

It is a public wiki, so occasionally a disc will not match. When the lookup finds nothing the script stops and tells you to name the film yourself rather than filing it under the disc label.

---

## Quality

Both rippers choose an x264 preset from the disc type. The numbers that matter:

| Disc | Preset | RF | x264 speed |
|---|---|---|---|
| DVD | Super HQ 480p30 Surround | 16 | slow |
| Blu-ray | Super HQ 1080p30 Surround | 18 | slow |

RF is the quality target and **lower is better**, each point costing roughly 20% more file size. Both are in the "Super HQ" family so a Blu-ray gets at least as much care as a DVD.

The speed is `slow` rather than the `veryslow` these presets ask for, which does not affect the quality target — see [Speed](#speed-and-why-it-is-not-the-same-as-quality) below.

Earlier versions used plain `HQ 1080p30 Surround` for Blu-ray, which is RF 20 on the faster `slow` preset. That encoded your Blu-rays *less* carefully than your DVDs, which is why they looked softer than expected. If you ripped Blu-rays before this change, they are worth doing again.

```bash
./rip.sh --quality 16          # better, larger, slower
./rip.sh --quality 20          # faster, smaller, softer
```

### Speed, and why it is not the same as quality

x264 effort defaults to **`slow`**, overriding the `veryslow` both Super HQ presets ask for.

This costs nothing you can see, which is the part worth understanding. **RF is the quality target; the speed preset is not.** At the same RF, `slow` and `veryslow` aim for the same perceptual quality — `veryslow` merely searches harder for ways to reach it in fewer bits. Going from `veryslow` to `slow` buys back more than half the encode time for roughly 5-10% larger files, not for a softer picture.

If you would rather have the smaller file and can spare the hours, ask for it:

```bash
./rip.sh --speed veryslow
```

What you should *not* do is reach for `--preset "HQ 1080p30 Surround"` to save time. That preset changes the effort *and* raises RF to 20, giving back the quality the Super HQ default exists to protect. Change one thing at a time: `--speed` for time, `--quality` for quality.

### How long a Blu-ray takes

On an 8-core M1 Pro (6 performance cores plus 2 efficiency ones; x264 only really scales on the performance cores), a two-hour film at the defaults runs:

| Phase | Time | Bound by |
|---|---|---|
| MakeMKV decrypt | 20-40 min | USB drive read speed |
| HandBrake encode | 2-3 hrs | the 6 performance cores |

So about **three hours start to finish**, or 5-7 hours for the encode alone at `--speed veryslow`. HandBrake prints a live fps and ETA, so you can check within a minute of it starting. A base M1 is roughly half the speed; an M1 Max roughly double. DVDs are far quicker — well under an hour, since 480p is a fraction of the pixels.

If you want the disc exactly, do not encode at all:

```bash
./rip.sh --direct
```

That copies the original video stream untouched. It is genuinely identical to the disc and about 30 GB for a Blu-ray. Plex will transcode it on the fly for the Roku, which the M1 handles.

### Audio

The presets produce AAC stereo plus the surround track, which is what a Roku wants. Blu-ray lossless formats (TrueHD, DTS-HD) cannot go in an MP4 at all, so they are converted to AC3 5.1 — you keep surround, not the lossless master. To keep the original tracks, ask for audio languages, which switches the output to MKV and allows passthrough:

```bash
./rip.sh --audio-langs eng
```

---

## Check a rip will actually play on the Roku

```bash
./check-rip.sh                                  # everything in the library
./check-rip.sh ~/Media/Movies/Knives\ Out\ \(2019\)/*.mp4
```

Plex hides this problem rather than reporting it. A file the Roku cannot decode still "works" — Plex silently re-encodes it in real time, and that is where stuttering, buffering and outright playback failures come from. This tells you which it is:

```
Knives Out (2019).mp4
  video       h264 High, level 4.0, 4 ref frames
  audio       aac ac3
  verdict     plays directly on a Roku
```

A Roku decodes H.264 in hardware and is strict about it: High profile, level 4.2 at most, and no more than 4 reference frames at 1080p. Exceed any of those and it hands the file back to Plex. The rippers now pin profile and level explicitly (`high`, level 4.0 for Blu-ray and 3.1 for DVD) so this cannot drift, but the checker is the way to confirm what you already have on disk.

Two things it flags that are working as intended: lossless audio (TrueHD, DTS-HD) and image-based subtitles (PGS, VOBSUB) both force Plex to transcode, and both only appear if you asked for them with `--audio-langs` or `--subtitle-langs`. If a Roku is your main player, plain `./rip.sh` avoids both.

---

## Languages and subtitles

**This needs `./setup.sh` to have run at least once since this feature was added.** MakeMKV's stock rule is `-sel:all,+sel:(favlang|nolang|single),...`, which discards tracks that are not in your favourite language *during the rip*. Those tracks never reach HandBrake, so no flag can bring them back. Setup now sets `app_DefaultSelectionString = "+sel:all"` so everything survives the rip and the ripper can choose. Check with `./status.sh`, under **MakeMKV track selection**.

Then ask for what you want, on either ripper or through `rip.sh`:

```bash
./rip.sh --audio-langs eng,spa
./rip.sh --subtitle-langs eng
./rip.sh --audio-langs eng,fra --subtitle-langs eng,fra
```

Languages are three-letter codes: `eng`, `spa`, `fra`, `deu`, `jpn`.

Two things worth knowing. Asking for either **switches the output to MKV**, because MP4 cannot carry DVD or Blu-ray bitmap subtitles and cannot hold lossless audio. Plex and Roku play MKV, so this costs you nothing. And subtitles are included but **switched off by default** — nothing is burned into the picture, and you pick a track in the Plex player.

Subtitles from discs are images, not text, so the Roku cannot overlay them without help; Plex transcodes to burn them in when you turn them on. That is normal and only happens while subtitles are switched on.

Default behaviour with no flags is unchanged: English audio, no subtitles, MP4.

---

## Rip a TV series

`rip-shows.sh` is the TV ripper. It reads a whole disc in one pass, works out which episodes are on it, and names them the way Plex wants. DVD and Blu-ray both work, and it picks the encoding preset from whichever it finds.

```bash
./rip-shows.sh
```

That is usually the entire command. It prints what it worked out, shows you the episode list, and waits for a yes before touching anything:

```
==> Reading the disc
Disc label reads "FIREFLY_D1", which looks like "Firefly".
Show: Firefly
This show publishes a DVD running order, so that is what the episodes are numbered by.
Firefly only ran one season, so this is season 1.
Disc 1 by the label, so this starts at episode 1.

==> Episodes on this disc
  title 0   2:02:00     ->  s01e01  Serenity
  title 1   0:44:00     ->  s01e02  The Train Job
  title 2   0:43:30     ->  s01e03  Bushwhacked

Confident: the episode lengths agree with this starting point.
Rip these? [y/N]
```

Put in disc 2 and run the same command. It sees season 1 already has episodes through 3 and starts at 4.

### How it identifies a disc

Disc labels are unreliable — `DVD_VIDEO` and `LOGICAL_VOLUME_ID` are common, and even a good label rarely says which disc of the set it is. So the label is only one signal of several, tried in order of how much they can be trusted:

1. **What you passed in.** `--show`, `--season`, and `--episode` always win.
2. **What is already in your library.** If `~/Media/TV/Firefly/Season 01/` holds episodes through e03, this disc starts at e04. This is the signal that makes multi-disc sets work, and it needs nothing from the disc at all.
3. **The disc label**, for the show name, season, and disc number when it happens to carry them.
4. **Episode lengths.** The disc's shape — how many episodes and whether any run long or short — is matched against the season. A double-length premiere like Firefly's *Serenity* pins the disc exactly.
5. **Asking you**, with a searchable show list, when the rest came up short.

Episode data comes from [TVmaze](https://www.tvmaze.com), which needs no API key or signup.

### Confident vs. not confident

Every plan says which it is, and that distinction is worth reading:

**Confident** means something actually pinned the disc down — a long or short episode matched, or the disc holds the whole season.

**Not confident** means the disc could sit in several places and nothing ruled the others out. This is normal and expected for a mid-season disc of a show where every episode runs 44 minutes; there is genuinely no way to tell disc 3 from disc 4 by content alone. Read the episode names before saying yes. If they are wrong, say where to start:

```bash
./rip-shows.sh --episode 9
```

If you give `--episode` and the lengths disagree with it, it tells you so rather than quietly going along:

```
NOT confident: the episode lengths look more like this disc starts at episode 1.
```

### Episode order

Shows that shipped out of broadcast order get numbered in **DVD order** automatically, because that is the order on the disc in your hand. Firefly is the usual example: *Serenity* is the DVD's first episode but aired eleventh.

When that happens, set the show to DVD order in Plex too, or Plex's metadata will not line up with the filenames: the show → three dots → **Episode ordering** → **DVD Order**. Force it either way with `--order aired` or `--order dvd`.

### Checking before you commit

`--list` runs the whole identification and prints the plan without ripping:

```bash
./rip-shows.sh --list
```

Files land as:

```
~/Media/TV/Firefly/Season 01/Firefly - s01e01 - Serenity.mp4
~/Media/TV/Firefly/Season 01/Firefly - s01e02 - The Train Job.mp4
```

Re-running skips episodes you already have, so an interrupted disc resumes. Menus, extras, and "Play All" bundles are dropped automatically; a genuine double-length episode is kept.

Flags:

- `--show "Name"` — skip the label guess and search for this show
- `--season N` / `--episode N` — season, and the first episode on this disc
- `--order aired|dvd` — force an episode order
- `--list` — show the plan, rip nothing
- `--min-length 1200` — ignore titles under 20 minutes (default 900 seconds)
- `--preset NAME` — override the disc-type HandBrake preset
- `--quality RF` — override the preset's quality, lower being better
- `--speed veryslow` — more x264 effort; smaller files, 2-3x the time, same quality target (default `slow`)
- `--audio-langs eng,spa` / `--subtitle-langs eng` — extra tracks, forces MKV
- `--direct` — skip HandBrake, keep the decrypted `.mkv`
- `--keep-raw` — keep the MakeMKV files in `~/Media/Rips/raw`
- `--ask` — confirm each guess and the episode plan instead of just going
- `--no-eject` — leave the disc in the drive when it finishes

Then in Plex: **TV** library → **Scan Library Files**.

### Tests

The identification logic has unit tests. They stub out Wikidata and TVmaze, so they run offline in well under a second:

```bash
python3 -m unittest test_show_lookup test_movie_lookup
```

Worth running if you change how discs are identified. They cover the Play All and duplicate-playlist filtering, disc label parsing, the confidence rules behind an episode mapping, whether a disc reads as a film or a show, and the film ranking that keeps a sequel or a stage adaptation from beating the film you actually put in the drive.

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

## Remote desktop (virtual display)

Use this to sit at another Mac and control the media-server laptop — Plex, MakeMKV, Terminal — as if it had a monitor attached. Both machines need to be Apple Silicon on macOS Sonoma or later (Tahoe is fine). Stay on the LAN. Do not port-forward Screen Sharing to the internet.

### On the media-server Mac (once)

1. Plug it in. Leave the lid **open**, or use a USB-C HDMI dummy plug if you want the lid closed. If it sleeps, Screen Sharing dies with it.
2. System Settings → **General** → **Sharing**.
3. Turn **Screen Sharing** on.
4. Click the info button (ⓘ) next to Screen Sharing.
5. Allow access for **your user** (the account you log into that laptop with). “All users” also works on a machine nobody else uses.
6. **Computer Settings…** → you can set a VNC password. Useful if you connect from a non-Apple client. For Mac-to-Mac, your login is enough.
7. Confirm Local Network permission if macOS asks: System Settings → Privacy & Security → Local Network → allow **Screen Sharing** / **screensharingd**.

The address is:

```
vnc://MediaServer.local
```

If you did not rename the laptop, `./status.sh` prints the Bonjour name.

### On the Mac you sit at

1. Same Wi-Fi (or Ethernet) as the server. Not a guest network.
2. Open **Screen Sharing** (`/System/Library/CoreServices/Applications/Screen Sharing.app`, or Spotlight: “Screen Sharing”).
3. Connect to `MediaServer.local` (or `vnc://MediaServer.local` from Finder → Go → Connect to Server).
4. Sign in as the **server Mac’s** user, not this Mac’s user.
5. When **Select Screen Sharing Type** appears:
   - Choose **High Performance**
   - Display Type: **1 Virtual Display** (or 2 if you want two remote desktops)
   - Continue
6. You should get a window that *is* the laptop’s desktop, even if its lid is closed (as long as it stayed awake).

If that dialog never appears, the connection fell back to Standard. You are then mirroring the built-in LCD. Lid closed with no dummy plug still sleeps the M1, so High Performance + virtual display does not replace keeping it awake.

### After you are connected

Inside the Screen Sharing window (this is the *remote* Mac’s System Settings):

1. Apple menu → System Settings → **Displays**.
2. Turn on **Dynamic resolution** if you want the virtual display to match the window size.
3. Arrange it as the main display if the built-in lid display is still listed.

Quit Screen Sharing when you are done. Plex and the SMB share keep running; you do not need this session open to watch the Roku.

### If it will not connect

- Server is awake and Screen Sharing is on.
- You used the server account password.
- Try the LAN IP: System Settings → Wi-Fi → Details on the server, then `vnc://192.168.x.x`.
- Firewall: System Settings → Network → Firewall. If it is on, allow Screen Sharing / incoming for port 5900.
- High Performance needs UDP **5900–5902** between the two Macs. Client isolation on Wi-Fi blocks that.
- Only one High Performance session at a time.

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

**MakeMKV says the disc needs a Java runtime (JRE 8+)**

This only happens on **Blu-ray**, and it is not about your DVDs. Some Blu-rays (Lionsgate titles like *Knives Out*, for instance) ship a Java program on the disc that MakeMKV has to run to pick the real playlist or finish the BD+ handshake. Without a working JRE, the disc either refuses to open or shows hundreds of decoy titles.

Installing the newest JDK does not fix it. **MakeMKV fails on JDK 25 and newer** — it needs Java 17:

```bash
brew install openjdk@17
```

Then point MakeMKV at it, either in the app (**MakeMKV → Preferences → Protection → Custom Java executable location**) or from the shell:

```bash
/opt/homebrew/opt/openjdk@17/bin/java -version   # expect openjdk 17.x
printf 'app_Java = "/opt/homebrew/opt/openjdk@17/bin/java"\n' >> ~/Library/MakeMKV/settings.conf
```

Give it the path to the `java` **executable**, not the folder. Quit MakeMKV fully and reopen it. `setup.sh` now does all of this for you.

To confirm it took, scan the disc and look for the Java line:

```bash
/Applications/MakeMKV.app/Contents/MacOS/makemkvcon -r info disc:0 | grep -i java
```

You want `Using Java runtime from /opt/homebrew/opt/openjdk@17/bin/java`. If it still says `/usr/bin/java`, the setting did not save — check whether your MakeMKV keeps settings in `~/.MakeMKV/settings.conf` instead.

`./status.sh` also reports which Java MakeMKV is set to use, and whether that path actually works.

Your system `java` can stay on whatever version you like; this setting only affects MakeMKV.

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
