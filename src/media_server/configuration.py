"""Where rips are staged and where finished files are delivered."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_CONFIGURATION_PATH = Path.home() / ".config" / "media-server" / "config"
DEFAULT_STAGING_ROOT = Path.home() / "Media" / "Rips"
DEFAULT_LIBRARY_ROOT = Path.home() / "Media"

STAGING_ROOT_KEY = "STAGING_ROOT"
LIBRARY_ROOT_KEY = "LIBRARY_ROOT"


class Configuration:
    """The two roots the pipeline works between.

    Rips are staged on a disk that can take the churn, and finished files are
    delivered to the library, which is usually an external drive that may not
    be mounted at the moment a transcode finishes.
    """

    def __init__(self, staging_root: Path, library_root: Path):
        self.staging_root = staging_root
        self.library_root = library_root

    @classmethod
    def load(
        cls,
        configuration_path: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> Configuration:
        """Resolve both roots from the environment, then the config file, then defaults."""
        if configuration_path is None:
            configuration_path = DEFAULT_CONFIGURATION_PATH
        if environment is None:
            environment = dict(os.environ)

        settings = read_settings_file(configuration_path)
        return cls(
            staging_root=cls._resolve_root(
                STAGING_ROOT_KEY, environment, settings, DEFAULT_STAGING_ROOT
            ),
            library_root=cls._resolve_root(
                LIBRARY_ROOT_KEY, environment, settings, DEFAULT_LIBRARY_ROOT
            ),
        )

    @staticmethod
    def _resolve_root(
        key: str,
        environment: dict[str, str],
        settings: dict[str, str],
        default_root: Path,
    ) -> Path:
        configured_value = environment.get(key) or settings.get(key)
        if not configured_value:
            return default_root
        return expand_path(configured_value)

    @property
    def movies_directory(self) -> Path:
        return self.library_root / "Movies"

    @property
    def tv_directory(self) -> Path:
        return self.library_root / "TV"

    @property
    def files_directory(self) -> Path:
        return self.library_root / "Files"

    @property
    def catalog_path(self) -> Path:
        """The job catalog lives beside the rips so it survives an unmounted library."""
        return self.staging_root / "catalog.sqlite3"

    def is_library_available(self) -> bool:
        """False while the external drive is unplugged, which holds deliveries back."""
        return self.library_root.is_dir()

    def directory_for_media_kind(self, media_kind: str) -> Path:
        if media_kind == "show":
            return self.tv_directory
        return self.movies_directory


def expand_path(raw_value: str) -> Path:
    """Expand ~ and environment variables so config files can stay portable."""
    return Path(os.path.expandvars(raw_value)).expanduser()


def read_settings_file(configuration_path: Path) -> dict[str, str]:
    """Read KEY=value lines, ignoring blanks and # comments.

    Deliberately parsed rather than sourced as shell: the file only ever holds
    paths, and parsing keeps it testable without spawning a shell.
    """
    if not configuration_path.is_file():
        return {}

    settings: dict[str, str] = {}
    for line in configuration_path.read_text().splitlines():
        setting = parse_setting_line(line)
        if setting is None:
            continue
        key, value = setting
        settings[key] = value
    return settings


def parse_setting_line(line: str) -> tuple[str, str] | None:
    """Turn one KEY=value line into a pair, or None when there is nothing to read."""
    stripped_line = line.strip()
    if not stripped_line or stripped_line.startswith("#"):
        return None
    if "=" not in stripped_line:
        return None

    key, _, value = stripped_line.partition("=")
    return key.strip(), strip_surrounding_quotes(value.strip())


def strip_surrounding_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value
