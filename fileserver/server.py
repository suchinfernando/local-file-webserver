"""HTTP server, request handler and routing glue."""

import json
import os
import socket
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

if __package__ in (None, ""):
    # Allow running this file directly, e.g. `python3 fileserver/server.py`.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "fileserver"

from . import fsutil
from .routes import Routes
from .security import Auth

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def detect_lan_ip():
    """Best-effort discovery of the primary LAN IP address."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalFileWebserver/1.0"
    protocol_version = "HTTP/1.1"

    # -- injected attributes -------------------------------------------------
    @property
    def config(self):
        return self.server.config

    @property
    def auth(self):
        return self.server.auth

    @property
    def routes(self):
        return self.server.routes

    # -- logging -------------------------------------------------------------
    def log_message(self, fmt, *args):
        if self.config.quiet:
            return
        sys.stderr.write("%s - - [%s] %s\n" % (
            self.address_string(), self.log_date_time_string(), fmt % args,
        ))

    def log_error(self, fmt, *args):
        self.log_message(fmt, *args)

    # -- request lifecycle ---------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        self._body_consumed = False
        if not self.auth.verify_header(self.headers.get("Authorization")):
            return self._unauthorized()
        try:
            parsed = urlsplit(self.path)
        except ValueError:
            return self.json_error(400, "malformed request URL")
        path = unquote(parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        try:
            if path == "/" or path == "/index.html":
                if method != "GET":
                    return self.json_error(405, "method not allowed")
                return self._serve_static("index.html")
            if path == "/favicon.ico":
                return self._no_content()
            if path.startswith("/static/"):
                if method != "GET":
                    return self.json_error(405, "method not allowed")
                return self._serve_static(path[len("/static/"):])
            if path.startswith("/api/"):
                return self.routes.handle(self, method, path, query)
            return self.json_error(404, "not found")
        except fsutil.PathEscapeError:
            return self.json_error(403, "path is outside the served root")
        except PermissionError:
            return self.json_error(403, "permission denied")
        except FileNotFoundError:
            return self.json_error(404, "not found")
        except NotADirectoryError:
            return self.json_error(400, "not a directory")
        except IsADirectoryError:
            return self.json_error(400, "is a directory")
        except ValueError as exc:
            return self.json_error(400, str(exc))
        except ConnectionError as exc:
            return self.json_error(400, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            if not self.config.quiet:
                import traceback
                traceback.print_exc()
            return self.json_error(500, "internal error: %s" % exc)

    # -- response helpers ----------------------------------------------------
    def _unauthorized(self):
        body = b"Authentication required"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", self.auth.challenge())
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _no_content(self):
        self._maybe_discard_body()
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def content_length(self):
        raw = self.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value >= 0 else None

    def mark_body_consumed(self):
        self._body_consumed = True

    def read_body(self):
        length = self.content_length() or 0
        data = self.rfile.read(length) if length else b""
        self._body_consumed = True
        return data

    def read_json(self):
        raw = self.read_body()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _maybe_discard_body(self):
        """Drain an unread request body so HTTP/1.1 keep-alive stays in sync."""
        if self.command not in ("POST", "PUT", "PATCH"):
            return
        if getattr(self, "_body_consumed", False):
            return
        length = self.content_length()
        if not length:
            return
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(1 << 16, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
        self._body_consumed = True

    def send_json(self, status, payload):
        self._maybe_discard_body()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json_error(self, status, message):
        self.send_json(status, {"error": message, "status": status})

    def send_file(self, abspath, download_name=None, inline=True, cache=False):
        self._maybe_discard_body()
        stat = os.stat(abspath)
        size = stat.st_size
        ctype = fsutil.content_type(abspath)

        status = HTTPStatus.OK
        start, end = 0, max(size - 1, 0)
        content_range = None

        range_header = self.headers.get("Range")
        if range_header and size > 0:
            try:
                rng = fsutil.parse_range(range_header, size)
            except ValueError:
                rng = "ignore"
            if rng == "ignore":
                pass
            elif rng is None:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            else:
                start, end = rng
                status = HTTPStatus.PARTIAL_CONTENT
                content_range = "bytes %d-%d/%d" % (start, end, size)

        length = (end - start + 1) if size > 0 else 0
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        if content_range:
            self.send_header("Content-Range", content_range)
        disposition = "inline" if inline else "attachment"
        if download_name:
            disposition += "; filename*=UTF-8''%s" % quote(download_name, safe="")
        self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "public, max-age=300" if cache else "no-store")
        self.end_headers()

        if self.command == "HEAD" or length == 0:
            return
        with open(abspath, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(1 << 16, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_static(self, rel):
        try:
            target = fsutil.resolve_within(STATIC_DIR, rel)
        except fsutil.PathEscapeError:
            return self.json_error(403, "invalid static path")
        if not os.path.isfile(target):
            return self.json_error(404, "not found")
        return self.send_file(target, inline=True, cache=True)


def create_server(config, auth):
    """Create (but do not start) a configured server instance."""
    server = ThreadingHTTPServer((config.host, config.port), Handler)
    server.daemon_threads = True
    server.config = config
    server.auth = auth
    server.routes = Routes(config)
    config.bound_port = server.server_address[1]
    return server


def _print_banner(config, auth):
    root = os.path.abspath(config.root)
    lan_ip = config.lan_ip
    print("local-file-webserver %s" % config.version)
    print("Serving:  %s" % root)
    extra = len([k for k in config.roots if k != config.primary_key])
    if extra:
        print("Roots:    %d additional location(s)" % extra)
    if config.drives:
        print("Drives:   %d found (%s mode)"
              % (len(config.drives), config.drive_mode))
        if config.drive_mode == "unlocked":
            print("WARNING:  all filesystems are exposed (drives.mode=unlocked)")
    if config.read_only:
        print("Mode:     READ-ONLY (upload/delete disabled)")
    elif config.use_trash:
        print("Delete:   soft-delete into %s/ (restorable)" % config.trash_dirname)
    print("Local:    %s" % config.local_url())
    if lan_ip and not lan_ip.startswith("127."):
        print("Network:  %s" % config.lan_url(lan_ip))
    if auth.enabled:
        print("Auth:     user=%s  password=%s" % (auth.username, auth.password))
    else:
        print("Auth:     disabled (use --password to enable)")
    print("Press Ctrl+C to stop.")


def run(config, auth, open_browser=False):
    server = create_server(config, auth)
    config.lan_ip = detect_lan_ip()
    _print_banner(config, auth)
    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=[config.local_url()]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()