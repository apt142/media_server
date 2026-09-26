# The unattended disc pipeline

This is the rebuild that aims at one goal: the only things you do by hand are
putting a disc in and taking a disc out. Everything else — identifying the
disc, ripping it, transcoding it, moving it to the external drive, cleaning up
— happens on its own.

The shell scripts in the parent folder still work and are unchanged. This
folder is the new effort, and it does not replace them yet.

## Why it is split in two

Ripping and transcoding have completely different bottlenecks:

| Stage | Time per Blu-ray | Bound by |
|---|---|---|
| Rip (MakeMKV decrypt) | 25–40 min | the optical drive |
| Transcode (HandBrake) | 2–3 hrs | the CPU |

Transcoding is roughly four to five times slower than ripping. If the two run
as one linear job, the drive sits idle for hours while a film encodes, and you
can only feed it one disc every three hours.

Splitting them means the disc pops out as soon as the decrypt finishes, and the
transcode joins a queue that grinds through the backlog on its own. You can
feed discs back to back all afternoon and let the machine catch up overnight.
That split is the reason this exists, and everything else here follows from it.

## How a disc moves through

```
  disc in  →  rip (fast)  →  eject  →  transcode (slow)  →  deliver  →  clean up
                  │                          │                 │
               staged                     encoded          delivered
```

Every disc is a row in a SQLite catalog, and it is always in exactly one state:

| State | Meaning |
|---|---|
| `staged` | Ripped and sitting on the staging disk, waiting its turn to transcode |
| `encoding` | A worker has claimed it and is transcoding it now |
| `encoded` | Transcoded, waiting for the library drive to be available |
| `delivered` | On the library drive, staging copies removed |
| `failed` | Something went wrong. The disc can be re-inserted to try again |
| `needs_review` | Could not be identified confidently and needs you to decide |

The catalog lives on the **staging** disk, not the library disk, so unplugging
the external drive never takes the queue with it.

## Setup

### 1. Write a config file

From the repo root:

```sh
./media-server init-config
```

That writes `~/.config/media-server/config`. Edit it to point at your two
folders:

```sh
# Where rips are staged while they wait to be transcoded. This disk takes the
# churn, so give it room for the backlog rather than for the whole library.
STAGING_ROOT=/Users/you/Media/Rips

# Where finished files are delivered. Point this at the external drive. When it
# is not mounted, finished jobs wait here until it comes back.
LIBRARY_ROOT=/Volumes/MediaDrive/Media
```

The file is deliberately kept outside the repo, since the paths are specific to
your machine and should not be committed.

Both settings can also be given as environment variables, which win over the
file. That is handy for a one-off run against a different drive:

```sh
LIBRARY_ROOT=/Volumes/OtherDrive ./media-server status
```

### 2. Choose where staging lives

Staging needs room for the **backlog**, not the library. A Blu-ray rip is
roughly 40 GB, so ten queued discs is about 400 GB.

Put staging on the internal SSD if you have the room. It is faster, and it
keeps the external drive from having to serve Plex, accept a rip, and feed a
transcode at the same time — three things competing for the same disk heads
will make playback stutter.

### 3. Check it

```sh
./media-server status
```

```
Folders
  Staging : /Users/you/Media/Rips
            455.4 GB free of 926 GB (51% used)
  Library : /Volumes/MediaDrive/Media  (mounted)
            1802.1 GB free of 3726 GB (51% used)

Backlog
  3 disc(s) holding 94.0 GB in staging

Queue
    2  staged        ripped, waiting to be transcoded
       #7    Iron Man 2 And Thor
       #8    The Matrix
    1  encoded       transcoded, waiting for the library drive
       #6    Knives Out
```

Delivered films are listed too, but only the last five of them. That pile only
ever grows, and status is for seeing what is moving right now.

## Commands

| Command | What it does |
|---|---|
| `watch` | Rips every disc put in the drive, until stopped |
| `rip` | Rips the disc in the drive, records the job, ejects the disc. `--again` redoes a disc already ripped |
| `scan` | Says what the disc is and lists its titles, without ripping |
| `encode` | Transcodes the ripped backlog and delivers it. `--one` does a single job, `--forever` keeps going |
| `status` | Both folders, free space on each, backlog size, queue depth |
| `queue` | Lists the discs still on their way through, with size and state |
| `attention` | Lists jobs that stopped: failed, or needing a decision |
| `deliver` | Moves everything finished onto the library drive |
| `forget <id>` | Drops a job so its disc stops counting as a duplicate |
| `config` | Shows the resolved folders |
| `init-config` | Writes a starter config file |
| `install-agents` | Runs `watch` and `encode --forever` as background services |
| `uninstall-agents` | Stops those services and removes them |

All of them run through `./media-server` at the repo root, which is a thin
wrapper around `python3 -m media_server`. Nothing needs installing and there
are no third-party dependencies — it is Python's standard library throughout.

Commands that only read (`status`, `queue`, `attention`, `config`) create
nothing, so they are safe to run before anything is set up.

## Ripping a disc

Put a disc in and run:

```sh
./media-server rip
```

It reads the disc, works out whether it holds a film or episodes, decrypts what
is worth keeping into staging, records the job, and ejects the disc. Nothing
asks you a question along the way.

```
  MakeMKV v1.17.7 darwin(arm64-release) started
Disc label: "FIREFLY_S01_D2"
This looks like a TV disc: 4 titles run at episode length, and the label
"FIREFLY_S01_D2" carries a season number.
Reading title 0, 0:44:01, 1.5 GB, VTS_01, title_t00.mkv
Reading title 1, 0:43:58, 1.5 GB, VTS_02, title_t01.mkv
Reading title 2, 0:44:10, 1.5 GB, VTS_03, title_t02.mkv
Reading title 3, 0:43:45, 1.5 GB, VTS_04, title_t03.mkv
Ripped 4 title(s) as job #2. The disc is out and the transcode is queued.
```

To look before you leap, `scan` does the same reading and prints the titles
without touching the disc otherwise — the equivalent of the old
`./scripts/rip-dvd.sh --list`.

### What gets ripped

On a **film** disc, every title running longer than 65 minutes — not just the
longest one. Double features are common, and nothing on a disc reliably
separates two films from one film and its commentary cut. Taking both costs a
file you delete in a second; taking one costs a film that was never ripped and
that you will not notice is missing until you want it.

Two flags adjust that when you already know what is on the disc:

```sh
./media-server rip --main-feature-only   # take one film, the old behaviour
./media-server rip --min-minutes 90      # raise the bar for what counts
```

With `--main-feature-only`, MakeMKV's own feature flag wins when its playlist
detection worked, and otherwise the longest title does. Length beats file size
there, because a commentary or bonus-angle cut runs as long as the film but
carries more audio, so "biggest file" picks the wrong one.

When a disc does yield two films, they go into the library as **separate
movies** rather than two parts of one:

```
Movies/Iron Man 2 And Thor - feature1/Iron Man 2 And Thor - feature1.mp4
Movies/Iron Man 2 And Thor - feature2/Iron Man 2 And Thor - feature2.mp4
```

Sharing a folder is what makes Plex stack files into one long film, so they get
one each. They are numbered in disc order, which is what lets you tell them
apart: play a few seconds of each and rename.

On a **TV** disc, every title of episode length. Anything shorter is a menu
loop or a trailer and is left behind.

### When a title will not decrypt

It is reported and skipped, and the rest of the disc carries on. One bad
episode should not cost you the other five. If *every* title fails, no job is
recorded and the staging folder is cleaned up, so a failed disc leaves nothing
behind to confuse the queue.

MakeMKV ends every failure with the same "0 titles saved" line whatever went
wrong, so the reason is read out of the messages above it and said plainly:

| What the messages say | What it means |
|---|---|
| `L-EC UNCORRECTABLE`, `MEDIUM ERROR` | The drive read the disc and the data was bad. A damaged patch — clean the disc from the centre outwards |
| `ipc/send`, `Device not configured` | The drive stopped answering entirely. Either this disc makes the drive give up, or the cable and power need looking at |
| `evaluation period has expired` | The key needs updating |

The difference between the first two matters more than it looks. A medium
error means the drive tried and the disc lost; a device that stopped being
configured means the drive never reported trouble at all, it just went away.
Cleaning a disc fixes one of those and not the other.

Anything not on this list falls back to pointing at the raw messages. Guessing
at wording MakeMKV might use would mean confidently naming the wrong cause,
which sends you off fixing something that was never broken.

### Titles are provisional for now

The job is named from the disc label with the bookkeeping stripped off, so
`FELLOWSHIP_EE_D2` becomes "Fellowship", part 2. That is good enough to keep
the queue readable, but it is not a real lookup yet — wiring in the Wikidata
and TVmaze searches is still on the list below.

A bare number is kept on a film and dropped from a show, because `IRON_MAN_2`
is a sequel and `FIREFLY 2` is a disc. Getting that backwards would hand Plex
"Iron Man" for an Iron Man 2 disc, which matches something real and wrong.

## Transcoding the backlog

```sh
./media-server encode
```

That works through everything ripped, one job at a time, writing each into
staging already shaped like the library and then moving it onto the library
drive **before starting the next one**. `--one` does a single job if you would
rather not commit the machine to the whole queue.

Delivering per job rather than at the end of the queue is what keeps the
staging disk from filling up. A finished Blu-ray is tens of gigabytes, and a
five-disc backlog takes most of a day to work through; holding all of it until
the last disc finishes is how a staging disk with room to spare runs out.

Delivery also runs before the first transcode and when there is nothing to
transcode at all, so work held back by an unplugged drive goes out the next
time you ask for anything.

```
Transcoding The Matrix
  wrote Movies/The Matrix/The Matrix.mp4
Transcoding Firefly
  wrote TV/Firefly/Season 01/Firefly - s01e01.mp4
  wrote TV/Firefly/Season 01/Firefly - s01e02.mp4
```

`./media-server deliver` does the moving on its own if you ever want it
separately, which is mostly useful for flushing a backlog after plugging the
drive back in.

Expect **two to three hours per Blu-ray** on an 8-core M1 Pro. That is the
whole reason ripping and transcoding are separate: the disc came out hours ago.

### Quality settings, and why they are what they are

Both presets are the "Super HQ" family, so a Blu-ray is treated at least as
carefully as a DVD. The plain `HQ 1080p30 Surround` preset is RF 20 on x264
`slow`, against RF 16 `veryslow` for DVDs — which encodes the *higher* quality
source less carefully than the lower quality one.

The speed preset is `slow` rather than the preset's own `veryslow`. RF is the
quality target; the speed preset only decides how long x264 spends hitting it
in fewer bits. On this hardware `veryslow` costs five to seven hours per
Blu-ray against two to three for `slow`, for roughly 5–10% less file size.

Profile and level are pinned explicitly rather than left to the preset. The
Roku decodes H.264 in hardware and refuses anything past High profile at level
4.0 for 1080p or 3.1 for 480p — most easily broken by x264 keeping more
reference frames than the level allows. Overriding one video setting on the
command line can leave HandBrake applying its own defaults for the rest, which
is exactly how a rip ends up buffering and then erroring on the Roku.

### Episode numbering across discs

Disc two of a season has no idea it is disc two. The episode number to start at
is worked out when the disc is ripped, from what the catalog has already
recorded for that show and season, and stored on the job. So disc one produces
`s01e01`–`s01e04` and disc two carries on at `s01e05` without being told.

## Running it hands-off

The commands above all do one thing and stop. Two of them can instead be left
running, which is what turns this from a set of tools into the thing it is
meant to be: put a disc in, wait for it to come out, put the next one in.

```sh
./media-server install-agents
```

That writes two launchd agents, starts them, and starts them again at every
login. From then on nothing needs typing.

| Agent | Runs | Does |
|---|---|---|
| `com.mediaserver.watcher` | `watch` | Rips each disc as it goes in, then ejects it |
| `com.mediaserver.encoder` | `encode --forever` | Transcodes the backlog and delivers it |

Watch them work:

```sh
tail -f ~/Library/Logs/media-server/watcher.log
tail -f ~/Library/Logs/media-server/encoder.log
```

`./media-server uninstall-agents` stops both. Every command still works by
hand whether the agents are running or not.

### Why agents rather than daemons

A `LaunchDaemon` starts at boot with no logged-in user. MakeMKV wants a user
session to run in, so a daemon would start dependably and then fail to rip
anything. A `LaunchAgent` starts at login instead, which on a machine that logs
itself in comes to the same thing without the problem.

Two details the agents carry that are easy to miss when writing a plist by
hand. Homebrew is put on the `PATH` explicitly, because launchd starts agents
with a bare path that has no `/opt/homebrew/bin` in it and `HandBrakeCLI`
would be missing even though it runs fine from a terminal. And the encoder runs
under `caffeinate -i`, because a Blu-ray takes two to three hours and an idle
Mac will otherwise go to sleep in the middle of one.

### The watcher only acts on a disc arriving

A rip starts when the drive goes from empty to holding something, not whenever
a disc happens to be present. That distinction matters: the rip worker ejects
when it finishes, so if an eject ever failed, a watcher keyed on presence would
rip the same disc over and over. Keyed on the change, a stuck disc is ignored
until a person takes it out.

The drive is given fifteen seconds to spin up before anything reads it.
`drutil` reports a disc the moment the tray closes, well before the table of
contents is readable, and scanning that early fails on a disc that is perfectly
fine a few seconds later.

### A disc that fails stays in the drive

A disc popping out means the pipeline is finished with it. Ejecting a disc that
failed would use the same signal for two opposite outcomes, so a disc that
needs a person stays where it can be seen. The reason is in the log and in
`./media-server attention`.

### The watcher stops accepting discs before staging fills

Ripping outruns transcoding by four or five to one, so a stack of discs will
fill the staging disk long before the encoder catches up. When free space drops
below 60 GB, the disc in the drive is held rather than refused: it stays put and
is picked up on a later pass, once the encoder has drained some of the backlog.
A full disk costs you time instead of attention.

`watch --max-discs 5` adds a second limit on the number of discs waiting,
which is the more predictable of the two if you would rather not think in
gigabytes.

## Things worth knowing

### The same disc twice is ignored

Each disc gets a fingerprint built from its label **and** the lengths of the
titles on it. The label alone is not enough — plenty of discs are stamped
`DVD_VIDEO`, and a season box set often uses the same label on all five discs.
Adding the title lengths tells those apart, while the same disc always
fingerprints identically.

Re-inserting a disc you have already ripped is recognised and skipped. A disc
whose last attempt **failed** is not treated as a duplicate, because
re-inserting it is exactly how you retry.

When the first attempt went through but was not good enough to keep, `--again`
overrides the check:

```sh
./media-server rip --again
```

That throws away the first attempt's record and its staged files, then rips the
disc fresh. A job that is **being transcoded right now** is refused instead:
deleting its files out from under the encoder would fail the transcode rather
than redo it, so let it finish or stop the encoder first.

Anything already delivered to the library is left where it is. The new rip
writes to the same paths and replaces it as it lands, which does mean that if
the second attempt produces *fewer* files than the first — `--main-feature-only`
after a double feature, say — the extra file from the first attempt stays in the
library until you delete it.

### An unplugged drive never loses work

When a transcode finishes and the library drive is not mounted, nothing fails.
The job stays in `encoded` and is retried on the next delivery pass. Plug the
drive back in, run `encode` or `deliver`, and the backlog flushes.

"Not mounted" means the whole of `LIBRARY_ROOT` is not there as a directory,
not just the volume. A drive that is plugged in but has no library folder on it
yet is indistinguishable from an absent one, so the path being looked for is
printed alongside the message. If it is a folder you have not created yet,
`mkdir -p` it once and the holds clear.

### A crash cannot leave a half-file in your library

Staging and the library are usually different volumes, so a move between them
is really a copy followed by a delete. If the drive is unplugged part way
through a plain copy, you are left with a truncated file that looks complete.

Every file is copied to a `.partial` name, size-checked, renamed into place,
and only then is the staged original deleted. A rename within one volume is
atomic, so a file in your library is either whole or not there at all. Any
`.partial` files left behind by an interrupted run are cleaned up on the next
`deliver`.

### Disc detection does not use mount events

macOS publishes disk events through Disk Arbitration, which looks like the
obvious way to notice a disc. It is not, because encrypted Blu-rays frequently
never present a mountable volume — so the discs you most want to catch would go
unnoticed. `drutil status` reports what the hardware sees whether or not the
disc can be mounted.

### The catalog is safe to share

The watcher and the encoder are separate processes writing to the same SQLite
file, and you will run `status` against it while both are working. Claiming a
job is wrapped in an immediate transaction, so two encoders can never take the
same disc, and the database is opened in write-ahead logging mode so a read
never waits on a write. An existing catalog is switched over the first time it
is opened.

### Only one transcode at a time

HandBrake already saturates every core. Running two encodes in parallel gains
no throughput and doubles the memory, so the queue is deliberately worked one
job at a time.

## Layout

```
src/
  media_server/
    configuration.py     the two roots, and how they are resolved
    job_catalog.py       the SQLite queue and the job states
    makemkv.py           reading discs and decrypting titles off them
    disc_classifier.py   film or show, and what the label says
    rip_worker.py        one disc: scan, rip, record, eject
    disc_watcher.py      the loop that notices a disc and starts a rip
    encode_settings.py   what to ask HandBrake for, and why
    encode_worker.py     one job: transcode into the library shape
    encode_service.py    the loop that drains the queue and delivers
    library_layout.py    where a finished file belongs, Plex-style
    file_names.py        names that survive a filesystem and an SMB share
    disc_drive.py        what is in the drive, and ejecting it
    disc_fingerprint.py  recognising a disc that has been seen before
    library_delivery.py  the crash-safe move to the library drive
    volume_space.py      free space on the disks being written to
    launch_agents.py     the launchd plists and installing them
    service_log.py       service output, without repeating itself
    cli.py               the commands above
  tests/
```

## Running the tests

```sh
cd src
python3 -m unittest discover -s tests -t .
```

No network, no real discs, and no real drives: the drive is stubbed and the
folders are temporary, so the suite runs in well under a second.

## Not built yet

The pipeline above is done and tested. Still to come:

- **Identification** — wire in the existing `movie_lookup.py` and
  `show_lookup.py`, including working out which disc of a split film you just
  put in by comparing the runtime on the disc against the film's known runtime.
  Until that lands, titles come from the disc label and films carry no year,
  so `FELLOWSHIP_EE_D2` arrives in the library as "Fellowship" rather than
  something Plex can match.

Worth repeating: every test here runs against stand-ins for MakeMKV, HandBrake
and the drive. None of it has met a real disc yet.
