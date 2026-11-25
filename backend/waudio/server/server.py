"""Local web server for the settings page: serves frontend/ and the JSON API (api.py).

Listens on 127.0.0.1 only, on a free port. The page URL carries a random token, and every API
call must send it back (header X-W-Token) with a matching Host header, so web pages open in a
browser on this Mac cannot read or change the settings.
"""
import json, logging, mimetypes, secrets, socketserver, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import NAME

FRONTEND = Path(__file__).resolve().parents[3] / "frontend"
log = logging.getLogger("waudio")


class LocalServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without its hostname lookup: HTTPServer.server_bind asks socket.getfqdn()
    for a name nothing here uses, and on a Mac whose DNS is slow to answer that takes ~30 s."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port


class Server:
    def __init__(self, api):
        self.api = api
        self.token = secrets.token_urlsafe(18)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # keep request lines out of the log
                pass

            def _send(self, code, body, ctype="application/json; charset=utf-8"):
                data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")  # the address holds the token: never sent on
                self.end_headers()
                self.wfile.write(data)

            def _api(self, method):
                host_ok = self.headers.get("Host") == f"127.0.0.1:{server.port}"
                origin = self.headers.get("Origin")  # a web page's request names its page; other clients send none
                origin_ok = origin is None or origin == f"http://127.0.0.1:{server.port}"
                if not host_ok or not origin_ok or not secrets.compare_digest(self.headers.get("X-W-Token", ""), server.token):
                    return self._send(403, {"error": "forbidden"})
                url = urlparse(self.path)
                body = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}")
                    except ValueError:
                        return self._send(400, {"error": "bad JSON"})
                else:
                    body = {k: v[0] for k, v in parse_qs(url.query).items()}
                code, result = server.api.call(method, url.path.removeprefix("/api/"), body)
                if isinstance(result, bytes):  # api.Raw: a file, with its own type
                    return self._send(code, result, result.ctype)
                self._send(code, result)

            def _static(self):
                rel = urlparse(self.path).path.lstrip("/") or "index.html"
                path = (FRONTEND / rel).resolve()
                if not path.is_relative_to(FRONTEND) or not path.is_file():
                    return self._send(404, b"not found", "text/plain")
                ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                body = path.read_bytes()
                if path.suffix == ".html":  # the app's name, written in one place (waudio/__init__.py)
                    body = body.replace(b"__NAME__", NAME.encode())
                self._send(200, body, ctype)

            def do_GET(self):
                if self.path.startswith("/api/"):
                    return self._api("GET")
                self._static()

            def do_POST(self):
                if self.path.startswith("/api/"):
                    return self._api("POST")
                self._send(405, {"error": "method not allowed"})

        self.httpd = LocalServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        log.info(f"settings server on 127.0.0.1:{self.port}")

    def url(self, pane=""):
        return f"http://127.0.0.1:{self.port}/?t={self.token}" + (f"#{pane}" if pane else "")

    def stop(self):
        self.httpd.shutdown()
