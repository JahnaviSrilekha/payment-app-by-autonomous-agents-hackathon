"""T16: app.js pure-logic tests, driven headlessly through Node from Python."""

import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "app_js_tests.js")


class TestAppJsLogic(unittest.TestCase):
    def test_app_js_logic_suite_passes_under_node(self):
        """The shared module's decimal parser (R118/R119), amount formatter (R120/R121),
        sequence refresh guard (R131) and idempotency-key derivation (R116/R135) all
        pass, with sha256 vectors pinned from Python hashlib."""
        node = shutil.which("node")
        if node is None:
            self.fail("node is required to run the app.js logic suite")
        result = subprocess.run(
            [node, SCRIPT], capture_output=True, text=True, timeout=60, cwd=HERE)
        output = (result.stdout + result.stderr).strip()
        self.assertEqual(
            result.returncode, 0,
            "app.js logic tests failed:\n%s" % output[-4000:])
        self.assertIn("app.js logic tests passed", output)


if __name__ == "__main__":
    unittest.main()