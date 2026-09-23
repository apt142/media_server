import plistlib
import unittest
from pathlib import Path

from media_server.launch_agents import (
    ENCODER_LABEL,
    WATCHER_LABEL,
    AgentInstaller,
    agents_for,
)

LAUNCHER_PATH = Path("/Users/someone/git/media_server/media-server")
USER_ID = 501


class RecordingLaunchctl:
    """Remembers what launchd would have been asked to do."""

    def __init__(self):
        self.argument_lists: list[list[str]] = []

    def __call__(self, arguments: list[str]) -> int:
        self.argument_lists.append(arguments)
        return 0

    @property
    def subcommands(self) -> list[str]:
        return [arguments[0] for arguments in self.argument_lists]


class AgentDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.watcher, self.encoder = agents_for(LAUNCHER_PATH)

    def test_there_is_one_agent_for_each_half_of_the_pipeline(self):
        self.assertEqual(self.watcher.label, WATCHER_LABEL)
        self.assertEqual(self.encoder.label, ENCODER_LABEL)

    def test_the_watcher_runs_the_watch_command(self):
        self.assertEqual(
            self.watcher.command_arguments, (str(LAUNCHER_PATH), "watch")
        )

    def test_the_encoder_keeps_the_mac_awake_for_its_multi_hour_jobs(self):
        self.assertEqual(
            self.encoder.command_arguments,
            ("/usr/bin/caffeinate", "-i", str(LAUNCHER_PATH), "encode", "--forever"),
        )

    def test_each_agent_writes_to_its_own_log(self):
        self.assertEqual(self.watcher.log_path.name, "watcher.log")
        self.assertEqual(self.encoder.log_path.name, "encoder.log")


class PlistContentTests(unittest.TestCase):
    def setUp(self):
        self.watcher, self.encoder = agents_for(LAUNCHER_PATH)

    def test_the_services_start_at_login_and_are_restarted_if_they_die(self):
        content = self.watcher.plist_content()

        self.assertTrue(content["RunAtLoad"])
        self.assertTrue(content["KeepAlive"])

    def test_a_crash_loop_is_throttled_rather_than_spinning(self):
        self.assertGreaterEqual(self.watcher.plist_content()["ThrottleInterval"], 10)

    def test_homebrew_is_on_the_path_so_handbrake_can_be_found(self):
        agent_path = self.encoder.plist_content()["EnvironmentVariables"]["PATH"]

        self.assertIn("/opt/homebrew/bin", agent_path)

    def test_output_is_unbuffered_so_the_log_is_worth_watching(self):
        environment = self.watcher.plist_content()["EnvironmentVariables"]

        self.assertEqual(environment["PYTHONUNBUFFERED"], "1")

    def test_both_streams_go_to_the_same_log_file(self):
        content = self.encoder.plist_content()

        self.assertEqual(content["StandardOutPath"], content["StandardErrorPath"])

    def test_the_content_survives_a_round_trip_through_a_real_plist(self):
        written = plistlib.dumps(self.watcher.plist_content())

        self.assertEqual(plistlib.loads(written), self.watcher.plist_content())


class InstallingTests(unittest.TestCase):
    def setUp(self):
        self.launchctl = RecordingLaunchctl()
        self.announced_lines: list[str] = []
        self.installer = AgentInstaller(
            launcher_path=LAUNCHER_PATH,
            run_command=self.launchctl,
            announce=self.announced_lines.append,
            user_id=USER_ID,
        )

    def test_the_agents_are_registered_in_the_logged_in_user_domain(self):
        self.assertEqual(self.installer.domain, f"gui/{USER_ID}")

    def test_an_agent_already_loaded_is_replaced_rather_than_refused(self):
        self.installer._start(agents_for(LAUNCHER_PATH)[0])

        self.assertEqual(self.launchctl.subcommands, ["bootout", "bootstrap"])

    def test_uninstalling_stops_both_services(self):
        self.installer.uninstall()

        self.assertEqual(self.launchctl.subcommands, ["bootout", "bootout"])

    def test_the_labels_booted_out_are_the_ones_that_were_installed(self):
        self.installer.uninstall()

        booted_out = [arguments[1] for arguments in self.launchctl.argument_lists]
        self.assertEqual(
            booted_out,
            [f"gui/{USER_ID}/{WATCHER_LABEL}", f"gui/{USER_ID}/{ENCODER_LABEL}"],
        )


if __name__ == "__main__":
    unittest.main()
