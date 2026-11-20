import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from media_server.makemkv_key import (
    HOURS_BETWEEN_CHECKS,
    MakeMkvRegistration,
    key_in_settings,
    parse_beta_key,
    write_key,
)

CURRENT_KEY = "T-1UCm55i41FKgyrNBV5PvcL_YZH4I0IXPBfgR076QqUu_fn9i3rvUjV8F3QZzPHWiWR"
OLDER_KEY = "T-9aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789_aBcDeFgHiJkLmNoPqRsTuVwXyZ"

# The shape of the forum post, down to the key sitting in a code block and the
# expiry being a sentence rather than a field.
FORUM_PAGE = f"""\
<html><body>
<div class="postbody"><h3>MakeMKV is free while in beta</h3>
<p>As stated on a main page all features of MakeMKV are free while program is
in beta. The current beta key is</p>
<div class="codebox"><pre><code>{CURRENT_KEY}</code></pre></div>
<p>and is valid until end of November 2026. Please check back for updated key
on this page.</p></div>
</body></html>
"""


# The real markup, copied from the live page in November 2026. The point of
# keeping it is that the synthetic version above is something this project
# wrote and will keep agreeing with, while this is what phpBB actually emits:
# the key inside a codebox, the expiry in running prose after the closing tag.
REAL_FORUM_MARKUP = (
    '<div class="content">As stated on a main page all features of MakeMKV are '
    "free while program is in beta.<br>\n<br>\nThe current beta key is "
    '<div class="codebox"><p>Code: <a href="#" onclick="selectCode(this); '
    'return false;">Select all</a></p><pre><code>'
    f"{CURRENT_KEY}"
    "</code></pre></div> and is valid until end of November 2026. Please check "
    "back for updated key on this page.</div>"
)


class ParsingTheForumPostTests(unittest.TestCase):
    def test_the_real_page_markup_is_understood(self):
        beta_key = parse_beta_key(REAL_FORUM_MARKUP)

        self.assertEqual(beta_key.key, CURRENT_KEY)
        self.assertEqual(beta_key.valid_until, "end of November 2026")

    def test_the_key_and_its_expiry_are_both_read(self):
        beta_key = parse_beta_key(FORUM_PAGE)

        self.assertEqual(beta_key.key, CURRENT_KEY)
        self.assertEqual(beta_key.valid_until, "end of November 2026")

    def test_a_page_with_no_key_on_it_is_not_guessed_at(self):
        """A redesign, an error page and a captcha all land here.

        Answering None leaves the installed key alone, which is the one
        outcome that cannot make things worse.
        """
        for page in ("<html>maintenance</html>", "", "404 Not Found"):
            with self.subTest(page=page):
                self.assertIsNone(parse_beta_key(page))

    def test_a_key_without_a_stated_expiry_is_still_usable(self):
        beta_key = parse_beta_key(f"<code>{CURRENT_KEY}</code>")

        self.assertEqual(beta_key.key, CURRENT_KEY)
        self.assertEqual(beta_key.valid_until, "")
        self.assertEqual(beta_key.describe(), "no expiry given")


class SettingsFileTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.settings_file = Path(temporary_directory.name) / "settings.conf"

    def test_a_key_is_read_back_out_of_the_settings_file(self):
        self.settings_file.write_text(f'app_Key = "{CURRENT_KEY}"\n')

        self.assertEqual(key_in_settings(self.settings_file), CURRENT_KEY)

    def test_a_missing_settings_file_reads_as_no_key(self):
        self.assertEqual(key_in_settings(self.settings_file), "")

    def test_writing_a_key_replaces_the_one_already_there(self):
        """A second app_Key line would leave the winner down to line order."""
        self.settings_file.write_text(
            f'app_Java = "/opt/homebrew/bin/java"\napp_Key = "{OLDER_KEY}"\n'
        )

        write_key(self.settings_file, CURRENT_KEY)

        written = self.settings_file.read_text()
        self.assertEqual(written.count("app_Key"), 1)
        self.assertEqual(key_in_settings(self.settings_file), CURRENT_KEY)

    def test_the_other_settings_are_left_alone(self):
        self.settings_file.write_text('app_Java = "/opt/homebrew/bin/java"\n')

        write_key(self.settings_file, CURRENT_KEY)

        self.assertIn('app_Java = "/opt/homebrew/bin/java"', self.settings_file.read_text())


class RegistrationTestCase(unittest.TestCase):
    """Shared setup. Nothing here reaches a forum or reads the real clock."""

    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_root = Path(temporary_directory.name)
        self.settings_file = self.temporary_root / "settings.conf"
        self.stamp_file = self.temporary_root / "stamp.json"
        self.right_now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def _registration(self, fetch=None, is_running=lambda: False) -> MakeMkvRegistration:
        return MakeMkvRegistration(
            stamp_file=self.stamp_file,
            settings_file=self.settings_file,
            fetch=fetch or (lambda: FORUM_PAGE),
            is_running=is_running,
            now=lambda: self.right_now,
        )


class RefreshingTheKeyTests(RegistrationTestCase):
    def test_a_machine_with_no_key_gets_the_current_one(self):
        outcome = self._registration().refresh()

        self.assertTrue(outcome.is_installed)
        self.assertEqual(key_in_settings(self.settings_file), CURRENT_KEY)

    def test_a_key_that_is_already_current_is_not_rewritten(self):
        write_key(self.settings_file, CURRENT_KEY)

        outcome = self._registration().refresh()

        self.assertFalse(outcome.is_installed)
        self.assertIn("already current", outcome.message)

    def test_an_expired_key_is_replaced(self):
        write_key(self.settings_file, OLDER_KEY)

        outcome = self._registration().refresh()

        self.assertTrue(outcome.is_installed)
        self.assertEqual(key_in_settings(self.settings_file), CURRENT_KEY)

    def test_the_stated_expiry_is_repeated_back(self):
        outcome = self._registration().refresh()

        self.assertIn("valid until end of November 2026", outcome.message)

    def test_a_forum_that_will_not_answer_leaves_the_key_alone(self):
        """A rip must not be lost because a web server was down."""
        write_key(self.settings_file, OLDER_KEY)

        def refuse():
            raise OSError("connection refused")

        outcome = self._registration(fetch=refuse).refresh()

        self.assertFalse(outcome.is_installed)
        self.assertEqual(key_in_settings(self.settings_file), OLDER_KEY)

    def test_nothing_is_written_while_makemkv_is_running(self):
        """MakeMKV rewrites this file from memory when it exits.

        Writing now would look like it worked and then quietly undo itself,
        which is worse than refusing and saying why.
        """
        write_key(self.settings_file, OLDER_KEY)

        outcome = self._registration(is_running=lambda: True).refresh()

        self.assertFalse(outcome.is_installed)
        self.assertEqual(key_in_settings(self.settings_file), OLDER_KEY)
        self.assertIn("Quit MakeMKV", outcome.message)


class CheckingOnlyWhenDueTests(RegistrationTestCase):
    def _record_check_at(self, checked_at: datetime) -> None:
        self.stamp_file.write_text(
            json.dumps(
                {"checked_at": checked_at.isoformat(), "valid_until": "end of May 2026"}
            )
        )

    def test_the_first_check_always_happens(self):
        self.assertIsNotNone(self._registration().refresh_if_due())

    def test_a_recent_check_is_not_repeated(self):
        """The encoder's agent restarts every 30 seconds when it crash-loops.

        Without this that becomes a web request every 30 seconds, aimed at a
        forum that is doing us a favour.
        """
        self._record_check_at(self.right_now - timedelta(hours=1))

        self.assertIsNone(self._registration().refresh_if_due())

    def test_a_stale_check_is_made_again(self):
        self._record_check_at(
            self.right_now - timedelta(hours=HOURS_BETWEEN_CHECKS + 1)
        )

        self.assertIsNotNone(self._registration().refresh_if_due())

    def test_an_unreadable_stamp_is_treated_as_never_checked(self):
        self.stamp_file.write_text("this is not json")

        self.assertIsNotNone(self._registration().refresh_if_due())


class DescribingTheKeyForStatusTests(RegistrationTestCase):
    def test_an_unregistered_machine_says_so_and_says_what_to_run(self):
        lines = "\n".join(self._registration().describe())

        self.assertIn("Registered : no", lines)
        self.assertIn("refresh-makemkv-key.sh", lines)

    def test_a_registered_machine_shows_the_expiry_it_last_heard(self):
        self._registration().refresh()

        lines = "\n".join(self._registration().describe())

        self.assertIn("Registered : yes", lines)
        self.assertIn("valid until end of November 2026", lines)

    def test_the_whole_key_is_not_printed(self):
        """It is long, and status is read over someone's shoulder."""
        self._registration().refresh()

        lines = "\n".join(self._registration().describe())

        self.assertNotIn(CURRENT_KEY, lines)
        self.assertIn(CURRENT_KEY[:8], lines)

    def test_status_does_not_reach_the_forum_to_answer(self):
        """Describing is reading back what the last check wrote down."""
        self._registration().refresh()

        def refuse():
            raise AssertionError("status asked the forum")

        lines = "\n".join(self._registration(fetch=refuse).describe())

        self.assertIn("Registered : yes", lines)
