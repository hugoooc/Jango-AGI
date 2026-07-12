import json
import threading
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

    def post_voice(self):
        request = urllib.request.Request(f"{self.base}/voice", data=b"{}", method="POST")
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())

    def test_voice_endpoint_returns_transcript(self):
        with patch.object(server, "_capture_voice", return_value="increase wing span by 12%"):
            status, payload = self.post_voice()
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"transcript": "increase wing span by 12%"})

    def test_voice_endpoint_rejects_capture_while_agent_runs(self):
        server._busy.set()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post_voice()
        self.assertEqual(caught.exception.code, 409)


if __name__ == "__main__":
    unittest.main()
