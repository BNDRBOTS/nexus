"""Unit tests: API token creation and reuse (no server started)."""
import os
import pathlib
import stat
import tempfile
import unittest

from server import app as A
from server import config as C


class TokenFileTests(unittest.TestCase):
    def setUp(self):
        self._old = C.DATA_DIR
        C.DATA_DIR = pathlib.Path(tempfile.mkdtemp(prefix="nx-auth-"))

    def tearDown(self):
        C.DATA_DIR = self._old

    def test_created_0600_reused_and_long_enough(self):
        first = A._load_or_create_token()
        self.assertGreaterEqual(len(first), A.TOKEN_FILE_MIN)
        path = C.DATA_DIR / "token"
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(A._load_or_create_token(), first)

    def test_short_or_empty_file_is_replaced(self):
        path = C.DATA_DIR / "token"
        path.write_text("short\n", encoding="utf-8")
        tok = A._load_or_create_token()
        self.assertNotEqual(tok, "short")
        self.assertGreaterEqual(len(tok), A.TOKEN_FILE_MIN)


if __name__ == "__main__":
    unittest.main()
