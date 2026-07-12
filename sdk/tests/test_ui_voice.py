import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from ui import server


class VoiceEndpointTests(unittest.TestCase):
    def setUp(self):
        server._busy.clear()
        server._voice_busy.clear()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        server._busy.clear()
        server._voice_busy.clear()

        server._voice_sessions.clear()

    def request_json(self, path, method="GET", token=True):
        data = b"{}" if method == "POST" else None
        headers = {}
        if method == "POST" and token:
            headers["X-LegacyPilot-Token"] = server.CSRF_TOKEN
        request = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=headers, method=method,
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())

    def wait_for_state(self, voice_id, wanted, timeout=2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, payload = self.request_json(f"/voice/status?id={voice_id}")
            if payload["state"] == wanted:
                return payload
            time.sleep(0.02)
        self.fail(f"voice session did not reach {wanted}")

    def test_voice_session_streams_text_and_stops_manually(self):
        def fake_capture(stop_signal, on_text):
            on_text("increase wing span")
            stop_signal.wait(timeout=1)
            on_text("increase wing span by 12%")
            return "increase wing span by 12%"

        with patch.object(server, "_capture_voice", side_effect=fake_capture):
            status, started = self.request_json("/voice/start", method="POST")
            self.assertEqual(status, 200)
            voice_id = started["voice_id"]

            _, live = self.request_json(f"/voice/status?id={voice_id}")
            self.assertEqual(live["transcript"], "increase wing span")

            stop_status, _ = self.request_json(
                f"/voice/stop?id={voice_id}", method="POST",
            )
            self.assertEqual(stop_status, 200)
            final = self.wait_for_state(voice_id, "done")
            self.assertEqual(final["transcript"], "increase wing span by 12%")

    def test_voice_endpoint_rejects_capture_while_agent_runs(self):
        server._busy.set()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request_json("/voice/start", method="POST")
        self.assertEqual(caught.exception.code, 409)

    def test_post_without_request_token_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request_json("/voice/start", method="POST", token=False)
        self.assertEqual(caught.exception.code, 403)
        self.assertFalse(server._voice_busy.is_set())

    def test_dashboard_receives_request_token(self):
        with urllib.request.urlopen(f"{self.base}/", timeout=2) as response:
            page = response.read().decode()
        self.assertIn(server.CSRF_TOKEN, page)
        self.assertNotIn("__CSRF_TOKEN__", page)

    def test_completed_voice_sessions_expire(self):
        server._voice_sessions["old"] = {
            "state": "done", "finished_at": 10.0,
        }
        with server._lock:
            server._prune_voice_sessions(now=10.0 + server.VOICE_SESSION_TTL)
        self.assertNotIn("old", server._voice_sessions)


if __name__ == "__main__":
    unittest.main()
