"""Browser tests (jsdom) for the Nexus UI. Skipped unless node and tests/browser/node_modules are installed."""
import unittest

from tests.browser import run_browser as RB


@unittest.skipUnless(RB.ready(), "needs node and: cd tests/browser && npm install")
class BrowserTests(unittest.TestCase):
    def test_ui_flows(self):
        self.assertEqual(RB.run(), 0, "browser test failures (see FAIL lines above)")


if __name__ == "__main__":
    unittest.main()
