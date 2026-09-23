"""Unattended disc pipeline: watch the drive, rip, transcode, deliver, clean up."""

from .configuration import Configuration
from .disc_classifier import DiscClassifier, DiscVerdict
from .disc_drive import DiscDrive, DiscStatus
from .disc_fingerprint import fingerprint_for_disc
from .encode_settings import EncodeSettings
from .encode_worker import EncodeOutcome, EncodeWorker
from .job_catalog import Job, JobCatalog, JobState, RippedDisc
from .library_delivery import DeliveryReport, LibraryDelivery
from .makemkv import DiscScan, DiscTitle, MakeMkv
from .rip_worker import RipOutcome, RipWorker
from .volume_space import VolumeSpace, space_at

__all__ = [
    "Configuration",
    "DeliveryReport",
    "DiscClassifier",
    "DiscDrive",
    "DiscScan",
    "DiscStatus",
    "DiscTitle",
    "DiscVerdict",
    "EncodeOutcome",
    "EncodeSettings",
    "EncodeWorker",
    "Job",
    "JobCatalog",
    "JobState",
    "LibraryDelivery",
    "MakeMkv",
    "RipOutcome",
    "RipWorker",
    "RippedDisc",
    "VolumeSpace",
    "fingerprint_for_disc",
    "space_at",
]
