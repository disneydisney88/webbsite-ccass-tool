import subprocess
import unittest
from unittest.mock import patch

from utils import fetcher


class PlaywrightInstallTest(unittest.TestCase):
    def setUp(self):
        self.original_attempted = fetcher._PLAYWRIGHT_INSTALL_ATTEMPTED
        self.original_ready = fetcher._PLAYWRIGHT_INSTALL_READY
        self.original_error = fetcher._PLAYWRIGHT_INSTALL_ERROR
        fetcher._PLAYWRIGHT_INSTALL_ATTEMPTED = False
        fetcher._PLAYWRIGHT_INSTALL_READY = False
        fetcher._PLAYWRIGHT_INSTALL_ERROR = ""

    def tearDown(self):
        fetcher._PLAYWRIGHT_INSTALL_ATTEMPTED = self.original_attempted
        fetcher._PLAYWRIGHT_INSTALL_READY = self.original_ready
        fetcher._PLAYWRIGHT_INSTALL_ERROR = self.original_error

    @patch("utils.fetcher.subprocess.run")
    def test_installs_chromium_once_on_demand(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "installed", "")

        self.assertEqual(fetcher.ensure_playwright_chromium(), (True, ""))
        self.assertEqual(fetcher.ensure_playwright_chromium(), (True, ""))
        run.assert_called_once()

    @patch("utils.fetcher.subprocess.run")
    def test_reports_install_failure_without_retry_loop(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "download failed")

        ok, error = fetcher.ensure_playwright_chromium()
        self.assertFalse(ok)
        self.assertIn("download failed", error)
        self.assertEqual(fetcher.ensure_playwright_chromium(), (False, error))
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
