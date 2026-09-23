import unittest

from media_server.service_log import ServiceLog


class ServiceLogTests(unittest.TestCase):
    def setUp(self):
        self.written_lines: list[str] = []
        self.log = ServiceLog(self.written_lines.append)

    def test_something_that_matters_is_written_every_time(self):
        self.log.write("Ripped a disc.")
        self.log.write("Ripped a disc.")

        self.assertEqual(self.written_lines, ["Ripped a disc.", "Ripped a disc."])

    def test_a_standing_condition_is_written_down_once(self):
        for _pass in range(5):
            self.log.write_if_changed("Library drive is not mounted.")

        self.assertEqual(self.written_lines, ["Library drive is not mounted."])

    def test_a_condition_changing_is_news_again(self):
        self.log.write_if_changed("Library drive is not mounted.")
        self.log.write_if_changed("Staging is nearly full.")

        self.assertEqual(
            self.written_lines,
            ["Library drive is not mounted.", "Staging is nearly full."],
        )

    def test_a_condition_coming_back_after_something_else_is_written_again(self):
        self.log.write_if_changed("Library drive is not mounted.")
        self.log.write("Delivered a job.")
        self.log.write_if_changed("Library drive is not mounted.")

        self.assertEqual(self.written_lines.count("Library drive is not mounted."), 2)


if __name__ == "__main__":
    unittest.main()
