"""Unattended disc pipeline: watch the drive, rip, transcode, deliver, clean up."""

from .configuration import Configuration
from .disc_classifier import DiscClassifier, DiscVerdict
from .disc_drive import DiscDrive, DiscStatus
from .disc_fingerprint import fingerprint_for_disc
from .disc_watcher import DiscWatcher, WatchOutcome, WatchSettings
from .encode_service import EncodeService, ServicePass
from .encode_settings import EncodeSettings
from .encode_worker import EncodeOutcome, EncodeWorker
from .job_catalog import Job, JobCatalog, JobState, RippedDisc
from .launch_agents import AgentInstaller, LaunchAgent
from .library_delivery import DeliveryReport, LibraryDelivery
from .makemkv import DiscScan, DiscTitle, MakeMkv
from .rip_worker import RipOutcome, RipWorker
from .volume_space import VolumeSpace, space_at

__all__ = [
    "AgentInstaller",
    "Configuration",
    "DeliveryReport",
    "DiscClassifier",
    "DiscDrive",
    "DiscScan",
    "DiscStatus",
    "DiscTitle",
    "DiscVerdict",
    "DiscWatcher",
    "EncodeOutcome",
    "EncodeService",
    "EncodeSettings",
    "EncodeWorker",
    "Job",
    "JobCatalog",
    "JobState",
    "LaunchAgent",
    "LibraryDelivery",
    "MakeMkv",
    "RipOutcome",
    "RipWorker",
    "RippedDisc",
    "ServicePass",
    "VolumeSpace",
    "WatchOutcome",
    "WatchSettings",
    "fingerprint_for_disc",
    "space_at",
]
