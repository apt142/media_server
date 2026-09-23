"""How much room is left on the disks the pipeline writes to.

Rips are large and encodes are slow, so the staging disk fills up roughly four
times faster than the encoder can drain it. Knowing the free space is what lets
the pipeline stop accepting discs before it runs out rather than after.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

# Below this, the staging disk is close enough to full that accepting another
# disc risks a rip failing part way through.
LOW_SPACE_GIGABYTES = 60.0

BYTES_PER_GIGABYTE = 1024**3


@dataclass(frozen=True)
class VolumeSpace:
    """The free and total size of the volume a path lives on."""

    total_bytes: int
    free_bytes: int
    is_present: bool = True

    @property
    def used_bytes(self) -> int:
        return self.total_bytes - self.free_bytes

    @property
    def free_gigabytes(self) -> float:
        return self.free_bytes / BYTES_PER_GIGABYTE

    @property
    def used_percent(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return self.used_bytes / self.total_bytes * 100

    @property
    def is_low(self) -> bool:
        return self.free_gigabytes < LOW_SPACE_GIGABYTES

    def has_room_for(self, needed_bytes: int) -> bool:
        return needed_bytes <= self.free_bytes

    def describe(self) -> str:
        if not self.is_present:
            return "not available"
        return (
            f"{self.free_gigabytes:.1f} GB free "
            f"of {self.total_bytes / BYTES_PER_GIGABYTE:.0f} GB "
            f"({self.used_percent:.0f}% used)"
        )


VOLUME_MISSING = VolumeSpace(total_bytes=0, free_bytes=0, is_present=False)


def space_at(path: Path) -> VolumeSpace:
    """Measure the volume holding a path.

    The path may not exist yet, because a staging folder is created on first
    use and a library folder lives on a drive that may be unplugged. Walking up
    to the nearest folder that does exist measures the right volume anyway.
    """
    existing_directory = nearest_existing_directory(path)
    if existing_directory is None:
        return VOLUME_MISSING

    try:
        usage = shutil.disk_usage(existing_directory)
    except OSError:
        return VOLUME_MISSING
    return VolumeSpace(total_bytes=usage.total, free_bytes=usage.free)


def nearest_existing_directory(path: Path) -> Path | None:
    """The first directory at or above a path that actually exists."""
    for candidate in [path, *path.parents]:
        if candidate.is_dir():
            return candidate
    return None
