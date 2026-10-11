"""End-to-end API tests. Starts the real server (subprocess) against a mock model and mock MCP server.
Run from repo root:  python3 -m unittest -v tests.test_api
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from tests import mock_servers as M

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "testtoken"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Api:
    def __init__(self, base, token=TOKEN):
        self.base = base
        self.token = token

    def call(self, method, path, body=None, headers=None, token=True):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"}
        if token and self.token:
            h["Authorization"] = "Bearer " + self.token
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, raw.decode("utf-8", "replace")

    def stream(self, path, body, headers=None):
        data = json.dumps(body).encode()
        h = {"Content-Type": "application/json", "Authorization": "Bearer " + self.token}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method="POST")
        events, cur = [], {}
        with urllib.request.urlopen(req, timeout=60) as r:
            for raw in r:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("event:"):
                    cur["event"] = line[6:].strip()
                elif line.startswith("data:"):
                    cur["data"] = json.loads(line[5:].strip())
                elif line == "" and cur:
                    events.append((cur.get("event"), cur.get("data")))
                    cur = {}
        return events


class NexusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nx-test-")
        cls.model_srv, cls.model_url = M.start_model()
        cls.mcp_srv, cls.mcp_url = M.start_mcp()
        cls.port = _free_port()
        env = dict(os.environ, NX_PORT=str(cls.port), NX_HOST="127.0.0.1", NX_DATA_DIR=cls.tmp,
                   NX_TOKEN=TOKEN, NX_ENABLE_PYTHON="1", NX_GATEWAY_URL="", NX_GATEWAY_KEY="")
        cls.proc = subprocess.Popen([sys.executable, "-m", "server.app"], cwd=ROOT, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base + "/", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        cls.api = Api(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.model_srv.shutdown()
        cls.mcp_srv.shutdown()

    def test_01_auth_and_state(self):
        s, _ = self.api.call("GET", "/api/state", token=False)
        self.assertEqual(s, 401)
        s, body = self.api.call("GET", "/api/state")
        self.assertEqual(s, 200)
        self.assertEqual(set(body["modes"]), {"work", "research", "code"})
        self.assertTrue(body["token_required"])
        self.assertGreaterEqual(len(body["roles"]), 3)

    def test_02_static_ui(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as r:
            html = r.read().decode()
        self.assertIn("<title>Nexus", html)
        s, _ = self.api.call("GET", "/../server/config.py", token=False)
        self.assertEqual(s, 404)

    def test_03_settings_and_provider_status(self):
        s, body = self.api.call("PUT", "/api/settings", {"provider": {
            "mode": "custom", "base_url": self.model_url + "/v1", "model": "mock-1"}})
        self.assertEqual(s, 200)
        self.assertEqual(body["provider"]["model"], "mock-1")
        s, body = self.api.call("GET", "/api/status", headers={"X-NX-Provider-Key": "k-status"})
        self.assertTrue(body["provider"]["reachable"], body)
        s, _ = self.api.call("PUT", "/api/settings", {"bogus": 1})
        self.assertEqual(s, 400)

    def test_04_project_scope_and_files(self):
        s, proj = self.api.call("POST", "/api/doc/project", {"name": "Vault Study", "instructions": "Track facts."})
        self.assertEqual(s, 201)
        NexusTests.project_id = proj["id"]
        s, _ = self.api.call("PUT", "/api/files", {"project": NexusTests.project_id, "path": "notes/source.md",
                                                   "content": "Intro.\n\nThe vault access code is 77-ALPHA.\n\nAudit date: 2026-10-01."})
        self.assertEqual(s, 200)
        self.api.call("PUT", "/api/files", {"project": NexusTests.project_id, "path": "secret.md",
                                            "content": "Hidden marker ZETA-999 outside scope."})
        s, _ = self.api.call("PUT", f"/api/doc/project/{NexusTests.project_id}", {
            "name": "Vault Study", "instructions": "Track facts.", "scope": ["notes/source.md"],
            "status": {"state": "active", "note": "", "next_step": ""}})
        self.assertEqual(s, 200)
        s, _ = self.api.call("PUT", "/api/files", {"project": NexusTests.project_id, "path": "../escape.md", "content": "x"})
        self.assertEqual(s, 400)

    def test_05_chat_stream_and_key_routing(self):
        s, chat = self.api.call("POST", "/api/chats", {"project_id": NexusTests.project_id, "mode": "research",
                                                       "title": "Vault chat"})
        self.assertEqual(s, 201)
        NexusTests.chat_id = chat["id"]
        M._Model.seen.clear()
        events = self.api.stream(f"/api/chats/{NexusTests.chat_id}/messages", {"content": "hello world", "stream": True},
                                 headers={"X-NX-Provider-Key": "k-123"})
        kinds = [e for e, _ in events]
        self.assertEqual(kinds[0], "context")
        self.assertIn("delta", kinds)
        self.assertEqual(kinds[-1], "done")
        self.assertEqual(M._Model.seen[-1]["auth"], "Bearer k-123")
        done = events[-1][1]
        self.assertEqual(done["assistant"]["content"], "ECHO: hello world")
        self.assertEqual(done["assistant"]["meta"]["ctx_hash"], events[0][1]["hash"])
        self.assertEqual(M._Model.seen[-1]["body"]["messages"][0]["role"], "system")

    def test_06_save_response_to_workspace(self):
        s, chat = self.api.call("GET", f"/api/chats/{NexusTests.chat_id}")
        assistant = chat["messages"][-1]
        s, meta = self.api.call("POST", f"/api/chats/{NexusTests.chat_id}/messages/{assistant['id']}/save",
                                {"path": "out/reply.md"})
        self.assertEqual(s, 200, meta)
        s, f = self.api.call("GET", f"/api/files?project={NexusTests.project_id}&path=out/reply.md")
        self.assertEqual(f["content"], "ECHO: hello world")

    def test_07_tool_write_and_status(self):
        s, work = self.api.call("POST", "/api/chats", {"project_id": NexusTests.project_id, "mode": "work"})
        NexusTests.work_chat = work["id"]
        s, res = self.api.call("POST", f"/api/chats/{NexusTests.work_chat}/messages",
                               {"content": "SAVE:out/brief.md|# Brief\nAlpha confirmed."})
        self.assertEqual(s, 200, res)
        self.assertEqual(res["assistant"]["meta"]["tools"][0]["name"], "workspace_write")
        self.assertTrue(res["assistant"]["meta"]["tools"][0]["ok"])
        s, f = self.api.call("GET", f"/api/files?project={NexusTests.project_id}&path=out/brief.md")
        self.assertIn("Alpha confirmed", f["content"])
        s, res = self.api.call("POST", f"/api/chats/{NexusTests.work_chat}/messages", {"content": "STATUS:brief saved"})
        self.assertEqual(s, 200, res)
        s, proj = self.api.call("GET", f"/api/projects/{NexusTests.project_id}")
        self.assertEqual(proj["project"]["status"]["note"], "brief saved")
        types = {a["type"] for a in proj["actions"]}
        self.assertTrue({"file.saved", "status.updated", "message.saved", "tool.executed"} <= types, types)

    def test_08_source_scope_enforced(self):
        s, res = self.api.call("POST", f"/api/chats/{NexusTests.chat_id}/messages", {"content": "SEARCH:77-ALPHA vault"})
        self.assertEqual(s, 200)
        tool_msgs = [m for m in M._Model.seen[-1]["body"]["messages"] if m["role"] == "tool"] or \
                    [m for m in M._Model.seen[-2]["body"]["messages"] if m["role"] == "tool"]
        self.assertTrue(any("77-ALPHA" in m["content"] for m in tool_msgs))
        s, res = self.api.call("POST", f"/api/chats/{NexusTests.chat_id}/messages", {"content": "SEARCH:ZETA-999"})
        self.assertEqual(s, 200)
        last_tool = [m for m in M._Model.seen[-1]["body"]["messages"] if m["role"] == "tool"]
        self.assertTrue(all("ZETA-999" not in m["content"] for m in last_tool))
        s, ctx = self.api.call("GET", f"/api/chats/{NexusTests.chat_id}/context")
        self.assertEqual(ctx["snapshot"]["scope"], ["notes/source.md"])

    def test_09_python_tool_saves_outputs(self):
        s, code = self.api.call("POST", "/api/chats", {"project_id": NexusTests.project_id, "mode": "code"})
        s, res = self.api.call("POST", f"/api/chats/{code['id']}/messages", {"content":
                               "PY:open('gen.txt','w').write('generated ok'); print(2+2)"})
        self.assertEqual(s, 200, res)
        self.assertEqual(res["assistant"]["meta"]["tools"][0]["name"], "python_run")
        s, f = self.api.call("GET", f"/api/files?project={NexusTests.project_id}&path=gen.txt")
        self.assertEqual(f["content"], "generated ok")

    def test_10_mcp_tool(self):
        s, srv = self.api.call("POST", "/api/doc/mcp", {"name": "Calc", "url": self.mcp_url, "enabled": True,
                                                        "headers": {}})
        self.assertEqual(s, 201)
        s, probe = self.api.call("POST", f"/api/mcp/{srv['id']}/probe")
        self.assertTrue(probe["ok"], probe)
        s, chat = self.api.call("POST", "/api/chats", {"project_id": NexusTests.project_id, "mode": "work",
                                                       "tool_groups": ["mcp"]})
        s, res = self.api.call("POST", f"/api/chats/{chat['id']}/messages", {"content": "MCP:2,3"})
        self.assertEqual(s, 200, res)
        self.assertEqual(res["assistant"]["meta"]["tools"][0]["name"], "mcp_" + srv["id"] + "_add")
        self.assertIn("5", res["assistant"]["content"])

    def test_11_pin_context_delete_and_regenerate(self):
        s, chat = self.api.call("GET", f"/api/chats/{NexusTests.chat_id}")
        target = chat["messages"][0]
        s, _ = self.api.call("POST", f"/api/chats/{NexusTests.chat_id}/messages/{target['id']}/pin", {"pinned": True})
        self.assertEqual(s, 200)
        s, ctx = self.api.call("GET", f"/api/chats/{NexusTests.chat_id}/context")
        self.assertTrue(any("Pinned anchors" in m["content"] for m in ctx["messages"]))
        before = len(chat["messages"])
        s, res = self.api.call("POST", f"/api/chats/{NexusTests.chat_id}/regenerate", {})
        self.assertEqual(s, 200, res)
        s, chat2 = self.api.call("GET", f"/api/chats/{NexusTests.chat_id}")
        self.assertEqual(len(chat2["messages"]), before)  # replaced, not appended
        self.assertNotEqual(chat2["messages"][-1]["id"], chat["messages"][-1]["id"])
        s, _ = self.api.call("DELETE", f"/api/chats/{NexusTests.chat_id}/messages/{target['id']}")
        self.assertEqual(s, 200)
        s, acts = self.api.call("GET", f"/api/actions?chat={NexusTests.chat_id}&limit=500")
        types = {a["type"] for a in acts["actions"]}
        self.assertIn("message.superseded", types)
        self.assertIn("message.deleted", types)

    def test_12_provider_failure_keeps_user_message_and_records_error(self):
        s, chat = self.api.call("POST", "/api/chats", {"project_id": NexusTests.project_id, "mode": "work"})
        s, res = self.api.call("POST", f"/api/chats/{chat['id']}/messages", {"content": "FAIL now"})
        self.assertEqual(s, 502)
        s, c2 = self.api.call("GET", f"/api/chats/{chat['id']}")
        self.assertEqual(len(c2["messages"]), 1)
        self.assertEqual(c2["messages"][0]["role"], "user")
        s, acts = self.api.call("GET", f"/api/actions?chat={chat['id']}")
        self.assertIn("turn.error", {a["type"] for a in acts["actions"]})

    def test_13_invalid_inputs(self):
        s, _ = self.api.call("POST", "/api/chats", {"mode": "bogus"})
        self.assertEqual(s, 400)
        s, _ = self.api.call("POST", f"/api/chats/{NexusTests.chat_id}/messages", {"content": "   "})
        self.assertEqual(s, 400)
        s, _ = self.api.call("GET", "/api/chats/chat_missing")
        self.assertEqual(s, 404)

    def test_14_workflow_external_trigger(self):
        s, wf = self.api.call("POST", "/api/doc/workflow", {
            "name": "Brief pipeline", "project_id": NexusTests.project_id, "steps": [
                {"id": "draft", "prompt": "SAVE:wf/out.md|Result for {{input}}", "mode": "work",
                 "save_path": "wf/step1.md"},
                {"id": "echo", "prompt": "ECHO {{prev}}", "mode": "research"}]})
        self.assertEqual(s, 201)
        s, run = self.api.call("POST", f"/api/workflows/{wf['id']}/run", {"input": "XYZ"})
        self.assertEqual(s, 202, run)
        deadline = time.time() + 30
        while time.time() < deadline:
            _, run = self.api.call("GET", f"/api/runs/{run['id']}")
            if run["status"] in ("done", "failed"):
                break
            time.sleep(0.2)
        self.assertEqual(run["status"], "done", run)
        self.assertEqual(len(run["steps"]), 2)
        s, tool_file = self.api.call("GET", f"/api/files?project={NexusTests.project_id}&path=wf/out.md")
        self.assertIn("Result for XYZ", tool_file["content"])          # written by tool call in step 1
        s, f = self.api.call("GET", f"/api/files?project={NexusTests.project_id}&path=wf/step1.md")
        self.assertIn("Done:", f["content"])                            # step output saved via save_path
        s, proj = self.api.call("GET", f"/api/projects/{NexusTests.project_id}")
        self.assertIn("2/2 steps", proj["project"]["status"]["note"])

    def test_15_backup_restore_and_append_only(self):
        s, bk = self.api.call("GET", "/api/backup")
        self.assertEqual(s, 200)
        self.assertGreater(len(bk["actions"]), 10)
        s, _ = self.api.call("POST", "/api/restore", bk)
        self.assertEqual(s, 200)
        s, acts = self.api.call("GET", f"/api/actions?project={NexusTests.project_id}&limit=1000")
        self.assertGreater(len(acts["actions"]), 10)
        db = os.path.join(self.tmp, "nexus.db")
        con = sqlite3.connect(db)
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("UPDATE action SET type='tampered' WHERE id=1")
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("DELETE FROM action WHERE id=1")
        con.close()

    def test_16_python_endpoint(self):
        s, res = self.api.call("POST", "/api/python", {"code": "print(1)", "project_id": NexusTests.project_id})
        self.assertEqual(s, 200)
        self.assertTrue(res["ok"])


    def test_17_reasoning_passback_and_separation(self):
        # DeepSeek contract: with tools present, reasoning_content of the tool-loop turn is sent back.
        s, chat = self.api.call("POST", "/api/chats", {"project_id": NexusTests.project_id, "mode": "work"})
        cid = chat["id"]
        n0 = len(M._Model.seen)
        s, res = self.api.call("POST", f"/api/chats/{cid}/messages", {"content": "THINK:plan the note"})
        self.assertEqual(s, 200, res)
        a = res["assistant"]
        self.assertEqual(a["content"], "Done after think.")
        self.assertNotIn("step one", a["content"])                    # reasoning never leaks into content
        self.assertEqual(a["meta"]["reasoning"], "step one. step two.")
        second = M._Model.seen[n0 + 1]["body"]
        self.assertTrue(second.get("tools"), "tool specs must be present in the tool-loop continuation")
        asst = [m for m in second["messages"] if m["role"] == "assistant" and m.get("tool_calls")]
        self.assertEqual(len(asst), 1)
        self.assertEqual(asst[0].get("reasoning_content"), "step one. step two.")
        # Next turn with tools: prior turn's reasoning is passed back in history.
        n1 = len(M._Model.seen)
        s, res = self.api.call("POST", f"/api/chats/{cid}/messages", {"content": "ECHO:next"})
        self.assertEqual(s, 200, res)
        hist = [m for m in M._Model.seen[n1]["body"]["messages"] if m["role"] == "assistant" and m.get("content") == "Done after think."]
        self.assertTrue(hist and hist[0].get("reasoning_content"), "history reasoning must be passed back with tools")
        # Without tools: reasoning_content is stripped from the request body.
        s, _ = self.api.call("PATCH", f"/api/chats/{cid}", {"tool_groups": []})
        n2 = len(M._Model.seen)
        s, res = self.api.call("POST", f"/api/chats/{cid}/messages", {"content": "ECHO:no tools"})
        self.assertEqual(s, 200, res)
        body = M._Model.seen[n2]["body"]
        self.assertFalse(body.get("tools"))
        self.assertFalse(any("reasoning_content" in m for m in body["messages"]))

    def test_18_truncation_fails_loudly(self):
        s, chat = self.api.call("POST", "/api/chats", {"project_id": NexusTests.project_id, "mode": "work"})
        cid = chat["id"]
        s, before = self.api.call("GET", f"/api/chats/{cid}")
        s, res = self.api.call("POST", f"/api/chats/{cid}/messages", {"content": "CUT:long answer"})
        self.assertEqual(s, 502, res)
        self.assertIn("finish_reason=length", res["error"])
        s, after = self.api.call("GET", f"/api/chats/{cid}")
        self.assertEqual(after["messages"][-1]["role"], "user")       # no empty assistant row persisted
        self.assertEqual(len(after["messages"]), len(before["messages"]) + 1)

    def test_19_provider_settings_validation(self):
        s, res = self.api.call("PUT", "/api/settings", {"provider": {"preset": "nope"}})
        self.assertEqual(s, 400)
        s, res = self.api.call("PUT", "/api/settings", {"provider": {"thinking": "maybe"}})
        self.assertEqual(s, 400)
        s, res = self.api.call("PUT", "/api/settings", {"provider": {"reasoning_effort": "ultra"}})
        self.assertEqual(s, 400)
        s, res = self.api.call("PUT", "/api/settings", {"context_chars": 10})
        self.assertEqual(s, 400)
        s, res = self.api.call("PUT", "/api/settings", {"provider": {"preset": "openrouter", "thinking": None}})
        self.assertEqual(s, 200, res)
        self.assertEqual(res["provider"]["preset"], "openrouter")
        s, res = self.api.call("PUT", "/api/settings", {"provider": {"preset": "deepseek", "base_url": "https://api.deepseek.com"}})
        self.assertEqual(s, 200, res)
        self.assertEqual(res["provider"]["preset"], "deepseek")


if __name__ == "__main__":
    unittest.main()
