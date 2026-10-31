import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from server import make_server


class ServerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        Path(self.temp.name, "README.md").write_text("# Synthetic\n", encoding="utf-8")
        self.server = make_server(Path(self.temp.name), port=0)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def get(self, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        request_headers = {"Host": f"127.0.0.1:{self.port}"}
        request_headers.update(headers or {})
        connection.request("GET", path, headers=request_headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_ephemeral_server_serves_allowlisted_ui_and_scan(self):
        self.assertGreater(self.port, 0)
        self.assertEqual(self.server.server_name, "127.0.0.1")
        self.assertEqual(self.get("/")[0], 200)
        desktop_status, desktop_body = self.get("/?desktop=1")
        self.assertEqual(desktop_status, 200)
        self.assertIn(b'<body class="desktop">', desktop_body)
        self.assertEqual(self.get("/csv.js")[0], 200)
        status, config = self.get("/api/settings")
        self.assertEqual(status, 200)
        self.assertIn(b"privacy_rules", config)
        status, body = self.get("/api/scan")
        self.assertEqual(status, 200)
        self.assertIn(b"README.md", body)

    def test_rejects_custom_root_query_and_unlisted_assets(self):
        self.assertEqual(self.get("/api/scan?root=%2Fetc")[0], 400)
        self.assertEqual(self.get("/etc/passwd")[0], 404)

    def test_rejects_host_and_cross_origin_request(self):
        self.assertEqual(self.get("/", {"Host": "evil.test"})[0], 403)
        self.assertEqual(self.get("/api/scan", {"Origin": "https://attacker.example"})[0], 403)

    def test_folder_change_replaces_scanner_and_settings(self):
        other = Path(self.temp.name, "other")
        other.mkdir()
        (other / "NOTES.md").write_text("# Other\n", encoding="utf-8")
        from scanner import KnowledgeScanner
        replacement = KnowledgeScanner(other, "NOTES.md")
        with self.server.state_lock:
            self.server.scanner = replacement
        status, config = self.get("/api/settings")
        self.assertEqual(status, 200)
        self.assertIn(b"NOTES.md", config)
        status, body = self.get("/api/scan")
        self.assertEqual(status, 200)
        self.assertIn(b"NOTES.md", body)
        self.assertNotIn(b"README.md", body)


if __name__ == "__main__":
    unittest.main()
