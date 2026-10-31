"""Native navigation policy and loopback backend lifecycle checks."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
from build_desktop import validated_assets
from desktop_policy import navigation_policy
from desktop import _make_local_server, _scanner_for_selected_root


class DesktopTests(unittest.TestCase):
    def test_startup_folder_picker_string_creates_path_backed_server(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "selected"
            root.mkdir()
            (root / "README.md").write_text("# Synthetic root\n", encoding="utf-8")
            owned = _make_local_server(str(root), port=0)
            try:
                self.assertEqual(owned.scanner.root, root.resolve())
            finally:
                owned.server_close()

    def test_folder_switch_picker_string_creates_path_backed_scanner(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "selected"
            root.mkdir()
            (root / "README.md").write_text("# Synthetic root\n", encoding="utf-8")
            scanner = _scanner_for_selected_root(str(root), "README.md")
            self.assertEqual(scanner.root, root.resolve())

    def test_asset_manifest_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "real.css").write_text("body{}", encoding="utf-8")
            (root / "link.css").symlink_to(root / "real.css")
            manifest = root / "assets.txt"
            manifest.write_text("link.css\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                validated_assets(root, manifest)

    def test_navigation_policy(self):
        origin = "http://127.0.0.1:49125/"
        self.assertEqual(navigation_policy(origin, origin, False, False), "allow")
        self.assertEqual(navigation_policy("blob:" + origin + "export", origin, False, True), "download")
        for target in ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,hello", "http://127.0.0.1:49126/", "https://evil.example"]:
            self.assertEqual(navigation_policy(target, origin, False, False), "cancel")
        self.assertEqual(navigation_policy("https://example.com", origin, True, False), "external")

    def test_ephemeral_server_boundary_and_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "README.md").write_text("# Native test\n", encoding="utf-8")
            with patch("socket.getfqdn", side_effect=AssertionError("Reverse DNS must not block startup")):
                owned = server.make_server(Path(folder), port=0)
            thread = threading.Thread(target=owned.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{owned.server_port}/"
            try:
                with urlopen(url, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                with self.assertRaises(HTTPError) as failure:
                    urlopen(Request(url, headers={"Host": "evil.test"}), timeout=3)
                self.assertEqual(failure.exception.code, 403)
                failure.exception.close()
            finally:
                owned.shutdown()
                owned.server_close()
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(owned.fileno(), -1)


if __name__ == "__main__":
    unittest.main()
