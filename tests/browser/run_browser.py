"""Browser-level tests. Starts a server (owner token, mock model) and runs tests/browser/run.js (jsdom).
Needs node and `npm install` in tests/browser. Run: python3 -m unittest tests.test_browser
"""
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from tests import mock_servers as M

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = ROOT / "tests" / "browser"
TOKEN = "browsertoken"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def ready():
    import shutil
    return bool(shutil.which("node")) and (HERE / "node_modules" / "jsdom").is_dir()


def run():
    """Returns the node test exit code (0 = all passed)."""
    model_srv, model_url = M.start_model()
    port = _free_port()
    data = tempfile.mkdtemp(prefix="nx-browser-")
    env = dict(os.environ, NX_PORT=str(port), NX_HOST="127.0.0.1", NX_DATA_DIR=data, NX_TOKEN=TOKEN,
               NX_GATEWAY_URL="", NX_GATEWAY_KEY="", NX_ENABLE_PYTHON="1")
    log = open(os.path.join(data, "server.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "server.app"], cwd=str(ROOT), env=env,
                            stdout=log, stderr=log)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/", timeout=1)
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        req = urllib.request.Request(
            base + "/api/settings", method="PUT", data=(
                '{"provider": {"mode": "custom", "preset": "custom", "base_url": "%s/v1", "model": "mock-1"}}'
                % model_url).encode(),
            headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        result = subprocess.run(["node", str(HERE / "run.js")], cwd=str(HERE), env=dict(
            os.environ, NX_URL=base, NX_TOKEN=TOKEN), timeout=600)
        return result.returncode
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        model_srv.shutdown()
        log.close()
