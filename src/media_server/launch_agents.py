"""The launchd agents that start the two services and keep them running.

These are user agents rather than system daemons on purpose. A LaunchDaemon
starts at boot with no logged-in user, and MakeMKV wants a user session to run
in, so a daemon would start reliably and then fail to rip anything. An agent
starts at login instead, which on a machine that logs in by itself amounts to
the same thing with none of the problems.

The plists are generated rather than checked in, because both of them have to
carry the absolute path of the launcher on this particular machine.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

WATCHER_LABEL = "com.mediaserver.watcher"
ENCODER_LABEL = "com.mediaserver.encoder"

LAUNCH_AGENTS_DIRECTORY = Path.home() / "Library" / "LaunchAgents"
LOG_DIRECTORY = Path.home() / "Library" / "Logs" / "media-server"

LAUNCHCTL_COMMAND = "launchctl"
CAFFEINATE_COMMAND = "/usr/bin/caffeinate"

# launchd starts agents with a bare PATH that has no Homebrew in it, so
# HandBrakeCLI would be missing even though it runs fine from a terminal.
AGENT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Python buffers its output when it is writing to a file rather than a
# terminal, so without this a log would stay empty for hours and then arrive
# all at once. A service you cannot watch is a service you cannot trust.
AGENT_ENVIRONMENT = {"PATH": AGENT_PATH, "PYTHONUNBUFFERED": "1"}

# A service that fails instantly would be restarted in a tight loop without
# this, which turns one broken install into a full log disk.
RESTART_THROTTLE_SECONDS = 30


@dataclass(frozen=True)
class LaunchAgent:
    """One background service, and how launchd should start it."""

    label: str
    command_arguments: tuple[str, ...]
    summary: str

    @property
    def plist_path(self) -> Path:
        return LAUNCH_AGENTS_DIRECTORY / f"{self.label}.plist"

    @property
    def log_path(self) -> Path:
        return LOG_DIRECTORY / f"{self.label.rsplit('.', 1)[-1]}.log"

    def plist_content(self) -> dict:
        return {
            "Label": self.label,
            "ProgramArguments": list(self.command_arguments),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": RESTART_THROTTLE_SECONDS,
            "EnvironmentVariables": dict(AGENT_ENVIRONMENT),
            "StandardOutPath": str(self.log_path),
            "StandardErrorPath": str(self.log_path),
        }


def agents_for(launcher_path: Path) -> list[LaunchAgent]:
    """Both services, pointed at the launcher on this machine.

    The encoder runs under ``caffeinate -i`` because a Blu-ray takes two to
    three hours and an idle Mac will go to sleep in the middle of it.
    """
    launcher = str(launcher_path)
    return [
        LaunchAgent(
            label=WATCHER_LABEL,
            command_arguments=(launcher, "watch"),
            summary="Rips discs as they go into the drive",
        ),
        LaunchAgent(
            label=ENCODER_LABEL,
            command_arguments=(
                CAFFEINATE_COMMAND,
                "-i",
                launcher,
                "encode",
                "--forever",
            ),
            summary="Transcodes the backlog and delivers finished files",
        ),
    ]


def run_launchctl(arguments: list[str]) -> int:
    """Run launchctl quietly and hand back its exit code."""
    try:
        completed_process = subprocess.run(
            [LAUNCHCTL_COMMAND, *arguments],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return 1
    return completed_process.returncode


class AgentInstaller:
    """Writes the plists and hands them to launchd.

    launchctl is injected so the tests can check what would be run without
    actually registering services on the machine running them.
    """

    def __init__(
        self,
        launcher_path: Path,
        run_command=run_launchctl,
        announce=print,
        user_id: int | None = None,
    ):
        self.launcher_path = launcher_path
        self.run_command = run_command
        self.announce = announce
        self.user_id = os.getuid() if user_id is None else user_id

    @property
    def domain(self) -> str:
        """The per-user launchd domain the agents are registered in."""
        return f"gui/{self.user_id}"

    def install(self) -> int:
        """Write both plists and start both services."""
        LAUNCH_AGENTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
        LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)

        for agent in agents_for(self.launcher_path):
            self._write_plist(agent)
            self._start(agent)
            self.announce(f"  {agent.label}  {agent.summary}")
            self.announce(f"    log {agent.log_path}")

        self.announce("")
        self.announce("Both services are running and will start again at login.")
        return 0

    def uninstall(self) -> int:
        """Stop both services and remove their plists."""
        for agent in agents_for(self.launcher_path):
            self._stop(agent)
            agent.plist_path.unlink(missing_ok=True)
            self.announce(f"  removed {agent.label}")

        self.announce("")
        self.announce("The commands still work by hand. Logs were left in place.")
        return 0

    def _write_plist(self, agent: LaunchAgent) -> None:
        agent.plist_path.write_bytes(plistlib.dumps(agent.plist_content()))

    def _start(self, agent: LaunchAgent) -> None:
        """Register the agent, replacing any copy already loaded.

        Bootstrapping an agent that is already there fails, so the old one is
        booted out first and its failure ignored: not being loaded yet is the
        expected case on a first install.
        """
        self._stop(agent)
        self.run_command(["bootstrap", self.domain, str(agent.plist_path)])

    def _stop(self, agent: LaunchAgent) -> None:
        self.run_command(["bootout", f"{self.domain}/{agent.label}"])
