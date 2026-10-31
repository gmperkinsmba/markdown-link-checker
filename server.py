"""Loopback UI server for a fixed, startup-selected Markdown root."""
from __future__ import annotations
import argparse
import json
import mimetypes
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from scanner import KnowledgeScanner

ASSETS = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css", "/app.js": "app.js", "/csv.js": "csv.js"}
MAX_REPLY = 2_000_000


def make_handler(port: int):
    class Handler(BaseHTTPRequestHandler):
        server_version = "KnowledgeHealth/1"
        sys_version = ""

        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def _boundary(self) -> bool:
            host = self.headers.get("Host", "").lower()
            if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.send_error(403, "Host is not allowed")
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
                self.send_error(403, "Origin is not allowed")
                return False
            return True

        def do_GET(self):
            if not self._boundary():
                return
            path = urlsplit(self.path).path
            if path == "/api/settings":
                if urlsplit(self.path).query:
                    self.send_error(400, "Query parameters are not supported")
                    return
                with self.server.state_lock:
                    payload = json.dumps(self.server.scanner.settings()).encode("utf-8")
                self._send(200, payload, "application/json; charset=utf-8")
                return
            if path == "/api/scan":
                if urlsplit(self.path).query:
                    self.send_error(400, "Query parameters are not supported")
                    return
                with self.server.state_lock:
                    payload = json.dumps(self.server.scanner.scan()).encode("utf-8")
                if len(payload) > MAX_REPLY:
                    self.send_error(413, "Scan output exceeded the response limit")
                    return
                self._send(200, payload, "application/json; charset=utf-8")
                return
            if urlsplit(self.path).query not in ("", "desktop=1"):
                self.send_error(400, "Query parameters are not supported")
                return
            asset = ASSETS.get(path)
            if asset is None:
                self.send_error(404, "Asset not found")
                return
            data = (Path(__file__).parent / asset).read_bytes()
            if path in ("/", "/index.html") and urlsplit(self.path).query == "desktop=1":
                data = data.replace(b"<body>", b'<body class="desktop">', 1)
            self._send(200, data, mimetypes.guess_type(asset)[0] or "application/octet-stream")

        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass
    return Handler


def make_server(root: Path, entrypoint: str = "README.md", port: int = 8346) -> ThreadingHTTPServer:
    scanner = KnowledgeScanner(root, entrypoint)

    class LocalServer(ThreadingHTTPServer):
        daemon_threads = True

        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name = "127.0.0.1"
            self.server_port = self.socket.getsockname()[1]

    server = LocalServer(("127.0.0.1", port), make_handler(0))
    server.scanner = scanner
    server.state_lock = threading.RLock()
    server.RequestHandlerClass = make_handler(server.server_port)
    return server


def main():
    parser = argparse.ArgumentParser(description="Scan one selected Markdown root; the path is fixed at startup.")
    parser.add_argument("--root", required=True, type=Path, help="Selected knowledge-base directory")
    parser.add_argument("--entrypoint", default="README.md", help="Configured Markdown entrypoint, relative to --root")
    parser.add_argument("--port", default=8346, type=int)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    try:
        server = make_server(args.root, args.entrypoint, args.port)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Knowledge Health is ready at http://127.0.0.1:{server.server_port} for configured root {server.scanner.root.name}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
