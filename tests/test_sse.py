"""Unit tests: shared SSE decoder and completion accumulator (no network)."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from server.provider import ProviderError, SSEDecoder, _Acc, _frame, chat_stream


class SSEDecoderTests(unittest.TestCase):
    def collect(self, chunks):
        d, out = SSEDecoder(), []
        for c in chunks:
            out += d.feed(c)
        return out + d.flush()

    def test_one_byte_chunks_crlf(self):
        raw = b'data: {"a":1}\r\n\r\ndata: {"a":2}\r\n\r\n'
        ev = self.collect([raw[i:i + 1] for i in range(len(raw))])
        self.assertEqual([e[1] for e in ev], ['{"a":1}', '{"a":2}'])

    def test_cr_split_across_chunks(self):
        ev = self.collect([b"data: x\r", b"\n\r", b"\n"])
        self.assertEqual([e[1] for e in ev], ["x"])

    def test_comments_and_multiline_data(self):
        ev = self.collect([b': keep-alive\n\ndata: {"a":\ndata: 1}\n\n'])
        self.assertEqual(len(ev), 1)
        self.assertEqual(json.loads(ev[0][1]), {"a": 1})

    def test_utf8_split_bytewise(self):
        payload = 'data: {"t":"é€😀"}\n\n'.encode("utf-8")
        ev = self.collect([payload[i:i + 1] for i in range(len(payload))])
        self.assertEqual(json.loads(ev[0][1])["t"], "é€😀")

    def test_flush_without_trailing_blank_line(self):
        self.assertEqual([e[1] for e in self.collect([b"data: last"])], ["last"])

    def test_event_name_and_done(self):
        self.assertEqual(self.collect([b"event: message\ndata: [DONE]\n\n"]), [("message", "[DONE]")])

    def test_multiple_events_in_one_chunk(self):
        ev = self.collect([b"data: 1\n\ndata: 2\n\ndata: 3\n\n"])
        self.assertEqual([e[1] for e in ev], ["1", "2", "3"])


class AccTests(unittest.TestCase):
    def test_reasoning_and_content_are_separate(self):
        acc = _Acc()
        got = []
        got += acc.apply({"choices": [{"delta": {"reasoning_content": "r1"}}]})
        got += acc.apply({"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]})
        f = acc.final()
        self.assertEqual((f["reasoning"], f["content"], f["finish_reason"]), ("r1", "hi", "stop"))
        self.assertEqual(got, [("reasoning", "r1"), ("delta", "hi")])

    def test_openrouter_reasoning_field(self):
        acc = _Acc()
        acc.apply({"choices": [{"delta": {"reasoning": "think"}}]})
        self.assertEqual(acc.final()["reasoning"], "think")

    def test_nonstream_parallel_tool_calls_stay_distinct(self):
        acc = _Acc()
        acc.apply({"choices": [{"delta": {"tool_calls": [
            {"function": {"name": "a", "arguments": "{}"}}, {"function": {"name": "b", "arguments": "{}"}}]}}]})
        self.assertEqual([c["name"] for c in acc.final()["tool_calls"]], ["a", "b"])

    def test_streamed_tool_call_arguments_join(self):
        acc = _Acc()
        acc.apply({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "w", "arguments": '{"a":'}}]}}]})
        acc.apply({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}}]})
        self.assertEqual(acc.final()["tool_calls"], [{"id": "c1", "name": "w", "arguments": '{"a":1}'}])

    def test_error_frame_raises(self):
        with self.assertRaises(ProviderError):
            _Acc().apply({"error": {"message": "quota"}})

    def test_malformed_frame_raises_not_silent(self):
        with self.assertRaises(ProviderError):
            _frame("{bad json")


if __name__ == "__main__":
    unittest.main()


class _Trickle(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for _ in range(60):          # one byte every 0.1 s, never sends [DONE]
                self.wfile.write(b": ka\n")
                self.wfile.flush()
                time.sleep(0.1)
        except OSError:
            pass


class StreamDeadlineTests(unittest.TestCase):
    def test_trickling_stream_hits_total_deadline(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Trickle)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cfg = {"base": f"http://127.0.0.1:{srv.server_address[1]}", "model": "m", "key": "",
                   "headers": {}, "preset": "custom", "route": "custom"}
            t0 = time.monotonic()
            with self.assertRaises(ProviderError) as cm:
                for _ in chat_stream(cfg, [{"role": "user", "content": "x"}], timeout=1):
                    pass
            self.assertIn("total", str(cm.exception))
            self.assertLess(time.monotonic() - t0, 4.0)
        finally:
            srv.shutdown()
            srv.server_close()
