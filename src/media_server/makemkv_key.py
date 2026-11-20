"""Keep MakeMKV's beta key current, because it expires and says so badly.

MakeMKV is free while it is in beta, which it has been since 2010, on a key
that is posted publicly and rotates roughly every two months. When it lapses
the program stops ripping, and what you see is a rip that failed rather than a
sentence about registration. That is the whole reason this file exists: the
chore is small, the failure is annoying, and the gap between them is where an
evening goes.

Nothing here is allowed to fail a rip or a transcode. Every path out ends in a
message rather than an exception, the same way ``film_lookup`` treats Wikidata:
the pipeline's job is to move discs, and a forum being slow is not a reason to
stop doing it.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# The key has been posted in the first post of this thread since 2010.
BETA_KEY_PAGE = "https://forum.makemkv.com/forum/viewtopic.php?t=1053"

USER_AGENT = "media-server-ripper/1.0 (personal DVD library tool)"

# Short on purpose. This runs before a transcode that takes hours, and the
# transcode is the thing that matters; a forum that will not answer promptly
# can be asked again next time.
REQUEST_TIMEOUT_SECONDS = 10

# Keys look like "T-" and then about sixty characters of base64url alphabet.
# Anchoring on that rather than on the page's markup means a forum redesign
# does not break this, and a miss leaves the old key in place rather than
# writing something that is not a key.
KEY_PATTERN = re.compile(r"T-[A-Za-z0-9_]{30,}")

# "and is valid until end of November 2026". Taken as prose and shown as prose:
# it is a human sentence on a forum, not a field, and parsing it into a date
# would invent a precision the source does not have.
VALID_UNTIL_PATTERN = re.compile(r"valid until ([^.<\n]+)", re.IGNORECASE)

SETTING_NAME = "app_Key"

# MakeMKV has moved its settings file between releases, so both are looked for
# rather than one being assumed. The first is where current versions put it.
SETTINGS_FILES = (
    Path.home() / "Library" / "MakeMKV" / "settings.conf",
    Path.home() / ".MakeMKV" / "settings.conf",
)

# MakeMKV rewrites its whole settings file when it exits, so an edit made while
# it is running is reverted the moment it closes. Both names are checked: the
# windowed application and the command-line tool the pipeline itself drives.
MAKEMKV_PROCESS_NAMES = ("MakeMKV", "makemkvcon")

# How long an answer from the forum is treated as current. The key rotates
# every couple of months, so this is not about catching it quickly; it is about
# a crash-looping service not asking a forum the same question every 30
# seconds, which is the restart throttle on the encoder's launchd agent.
HOURS_BETWEEN_CHECKS = 12

STAMP_FILE_NAME = ".makemkv-key.json"


@dataclass(frozen=True)
class BetaKey:
    """What the forum is currently handing out."""

    key: str
    valid_until: str = ""

    def describe(self) -> str:
        if self.valid_until:
            return f"valid until {self.valid_until}"
        return "no expiry given"


@dataclass(frozen=True)
class KeyRefresh:
    """What came of trying to bring the key up to date."""

    message: str
    is_installed: bool = False
    beta_key: BetaKey | None = None


def fetch_beta_key_page() -> str:
    """The forum post that carries the key, as text."""
    request = urllib.request.Request(
        BETA_KEY_PAGE, headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_beta_key(page_text: str) -> BetaKey | None:
    """Pull the key and its stated expiry out of the forum post.

    None when the page did not contain something shaped like a key, which
    covers a redesign, an error page, and a captcha equally. The caller leaves
    the installed key alone in all three cases.
    """
    key_match = KEY_PATTERN.search(page_text)
    if key_match is None:
        return None

    valid_until = VALID_UNTIL_PATTERN.search(page_text)
    return BetaKey(
        key=key_match.group(0),
        valid_until=valid_until.group(1).strip() if valid_until else "",
    )


def settings_file_in_use(candidates=SETTINGS_FILES) -> Path:
    """Where MakeMKV keeps its settings, preferring one that already exists."""
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def is_makemkv_running(
    run_command=lambda arguments: subprocess.run(arguments, capture_output=True).returncode,
) -> bool:
    """Whether MakeMKV is open, in which case its settings must be left alone."""
    return any(
        run_command(["pgrep", "-x", name]) == 0 for name in MAKEMKV_PROCESS_NAMES
    )


def key_in_settings(settings_file: Path) -> str:
    """The key MakeMKV is registered with now, or empty if it has none."""
    for line in read_settings_lines(settings_file):
        name, _, value = line.partition("=")
        if name.strip() == SETTING_NAME:
            return value.strip().strip('"')
    return ""


def read_settings_lines(settings_file: Path) -> list[str]:
    """The settings file's lines, or none at all if it cannot be read."""
    try:
        return settings_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def write_key(settings_file: Path, key: str) -> None:
    """Put ``key`` in the settings file, replacing any key already there.

    Replacing rather than appending matters: MakeMKV reads the file top to
    bottom and a second app_Key line would leave which key wins depending on
    the order they happen to be in.
    """
    kept_lines = [
        line
        for line in read_settings_lines(settings_file)
        if line.partition("=")[0].strip() != SETTING_NAME
    ]
    kept_lines.append(f'{SETTING_NAME} = "{key}"')

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")


class MakeMkvRegistration:
    """Reads and renews the key MakeMKV rips with.

    The network call and the clock are handed in so tests can answer both
    without reaching a forum or waiting for a date to pass.
    """

    def __init__(
        self,
        stamp_file: Path,
        settings_file: Path | None = None,
        fetch=fetch_beta_key_page,
        is_running=is_makemkv_running,
        now=lambda: datetime.now(timezone.utc),
    ):
        self.stamp_file = stamp_file
        self.settings_file = settings_file or settings_file_in_use()
        self.fetch = fetch
        self.is_running = is_running
        self.now = now

    @property
    def installed_key(self) -> str:
        return key_in_settings(self.settings_file)

    def refresh(self) -> KeyRefresh:
        """Fetch the current key and install it if it is not already there."""
        beta_key = self._current_beta_key()
        if beta_key is None:
            return KeyRefresh(
                message="Could not read the current MakeMKV key from the forum."
            )

        if beta_key.key == self.installed_key:
            self._record_check(beta_key)
            return KeyRefresh(
                message=f"MakeMKV key is already current, {beta_key.describe()}.",
                beta_key=beta_key,
            )

        if self.is_running():
            # Writing now would be undone: MakeMKV rewrites this file on exit
            # from what it has in memory. Saying so beats a silent no-op.
            return KeyRefresh(
                message=(
                    "A new MakeMKV key is out, but MakeMKV is running and would "
                    "overwrite it on exit. Quit MakeMKV and try again."
                ),
                beta_key=beta_key,
            )

        try:
            write_key(self.settings_file, beta_key.key)
        except OSError as problem:
            return KeyRefresh(
                message=f"Could not write {self.settings_file}: {problem}",
                beta_key=beta_key,
            )

        self._record_check(beta_key)
        return KeyRefresh(
            message=f"Registered a new MakeMKV key, {beta_key.describe()}.",
            is_installed=True,
            beta_key=beta_key,
        )

    def refresh_if_due(self) -> KeyRefresh | None:
        """Refresh only if the forum has not been asked recently.

        None means nothing was done and nothing is wrong, which is the usual
        answer. Callers that run on a loop use this so a service restarting in
        a tight loop does not turn into a tight loop of web requests.
        """
        if not self._is_check_due():
            return None
        return self.refresh()

    def describe(self) -> list[str]:
        """Lines for ``status``, answered from the last check rather than the web."""
        if not self.installed_key:
            return [
                "  Registered : no",
                f"  Settings   : {self.settings_file}",
                "  Renew with : ./scripts/refresh-makemkv-key.sh",
            ]

        last_check = self._last_check()
        lines = [f"  Registered : yes ({_abbreviate(self.installed_key)})"]
        if last_check.get("valid_until"):
            lines.append(f"  Beta key   : valid until {last_check['valid_until']}")
        lines.append(f"  Checked    : {self._describe_last_check(last_check)}")
        return lines

    def _current_beta_key(self) -> BetaKey | None:
        try:
            return parse_beta_key(self.fetch())
        except (urllib.error.URLError, OSError, ValueError):
            # Every way the web can disappoint, treated the same: the key that
            # is installed stays installed and the pipeline carries on.
            return None

    def _is_check_due(self) -> bool:
        checked_at = self._last_check().get("checked_at", "")
        if not checked_at:
            return True
        try:
            last = datetime.fromisoformat(checked_at)
        except ValueError:
            return True
        return (self.now() - last).total_seconds() >= HOURS_BETWEEN_CHECKS * 3600

    def _last_check(self) -> dict:
        try:
            return json.loads(self.stamp_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _record_check(self, beta_key: BetaKey) -> None:
        """Remember what the forum said, so status can answer without asking."""
        try:
            self.stamp_file.parent.mkdir(parents=True, exist_ok=True)
            self.stamp_file.write_text(
                json.dumps(
                    {
                        "checked_at": self.now().isoformat(),
                        "valid_until": beta_key.valid_until,
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            # A stamp that will not write costs an extra web request next time
            # and nothing else, so it is not worth failing anything over.
            pass

    def _describe_last_check(self, last_check: dict) -> str:
        checked_at = last_check.get("checked_at", "")
        if not checked_at:
            return "never"
        try:
            last = datetime.fromisoformat(checked_at)
        except ValueError:
            return "never"
        return _describe_age((self.now() - last).total_seconds())


def _abbreviate(key: str) -> str:
    """Enough of the key to tell two apart, without printing the whole thing."""
    if len(key) <= 16:
        return key
    return f"{key[:8]}...{key[-4:]}"


def _describe_age(seconds: float) -> str:
    if seconds < 3600:
        return "within the hour"
    hours = int(seconds // 3600)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"
