"""Project loop across a server restart: record verified progress, stop the server, start it again on the
same data directory, and resume from the stored next step. SQLite only (the data directory is the state).
Run from repo root:  python3 -m unittest -v tests.test_resume
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from tests import mock_servers as M
from tests.test_api import Api, ROOT, TOKEN, _free_port


def _start(data_dir):
    port = _free_port()
    env = dict(os.environ, NX_PORT=str(port), NX_HOST="127.0.0.1", NX_DATA_DIR=data_dir, NX_TOKEN=TOKEN,
               NX_ENABLE_PYTHON="", NX_GATEWAY_URL="", NX_GATEWAY_KEY="", NX_DB_URL="")
    proc = subprocess.Popen([sys.executable, "-m", "server.app"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/", timeout=1)
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    return proc, base


def _stop(proc):
    proc.terminate()
    proc.wait(timeout=10)


class ResumeTests(unittest.TestCase):
    def test_loop_survives_restart(self):
        data = tempfile.mkdtemp(prefix="nx-resume-")
        model_srv, model_url = M.start_model()
        proc, base = _start(data)
        try:
            api = Api(base)
            s, _ = api.call("PUT", "/api/settings", {"provider": {"mode": "custom", "preset": "custom",
                                                                   "base_url": model_url + "/v1", "model": "mock-1"}})
            self.assertEqual(s, 200)
            s, pr = api.call("POST", "/api/doc/project", {"name": "Resume Project", "instructions": "Ship report", "scope": ["notes/plan.md"]})
            self.assertEqual(s, 201)
            pid = pr["id"]
            s, _ = api.call("PUT", "/api/files", {"project": pid, "path": "notes/plan.md", "content": "plan v1"})
            self.assertEqual(s, 200)
            s, chat = api.call("POST", "/api/chats", {"project_id": pid, "title": "Work"})
            cid = chat["id"]

            # Loop step: the model records verified progress through the project_set_status tool.
            s, _ = api.call("POST", f"/api/chats/{cid}/messages", {"content": "STATUS:plan drafted and checked"})
            self.assertEqual(s, 200)
            s, before = api.call("GET", f"/api/projects/{pid}")
            self.assertEqual(before["project"]["status"]["note"], "plan drafted and checked")
            self.assertEqual(before["project"]["status"]["next_step"], "continue")
            s, _ = api.call("POST", f"/api/chats/{cid}/messages", {"content": "ECHO:before restart"})
            self.assertEqual(s, 200)
        finally:
            _stop(proc)

        # Restart on the same data directory with a new port.
        proc, base = _start(data)
        try:
            api = Api(base)
            s, after = api.call("GET", f"/api/projects/{pid}")
            self.assertEqual(s, 200)
            self.assertEqual(after["project"]["status"]["note"], "plan drafted and checked", "status survives restart")
            self.assertEqual(after["project"]["status"]["next_step"], "continue")
            self.assertEqual(after["project"]["instructions"], "Ship report")
            self.assertEqual(after["project"]["scope"], ["notes/plan.md"], "source scope survives restart")
            self.assertTrue(any(a["type"] == "status.updated" for a in after["actions"]), "record survives restart")
            s, ctx = api.call("GET", f"/api/chats/{cid}/context")
            self.assertEqual(s, 200)
            self.assertTrue(any("plan drafted and checked" in m["content"] for m in ctx["messages"]),
                            "project progress injected into the chat context after restart")
            s, detail = api.call("GET", f"/api/chats/{cid}")
            self.assertEqual(s, 200)
            self.assertTrue(any("before restart" in m["content"] for m in detail.get("messages", [])),
                            "chat history survives restart")

            # The Continue button sends this exact message; the model must see the stored progress.
            s, _ = api.call("PUT", "/api/settings", {"provider": {"mode": "custom", "preset": "custom",
                                                                   "base_url": model_url + "/v1", "model": "mock-1"}})
            s, _ = api.call("POST", f"/api/chats/{cid}/messages",
                            {"content": "Continue from the last verified state. Next step: continue"})
            self.assertEqual(s, 200)
            sent = M._Model.seen[-1]["body"]["messages"]
            system_text = "\n".join(m["content"] for m in sent if m["role"] == "system")
            self.assertIn("plan drafted and checked", system_text, "model sees progress after restart")
            self.assertIn("Next step: continue", system_text)
        finally:
            _stop(proc)
            model_srv.shutdown()


if __name__ == "__main__":
    unittest.main()
