"""
A local stand-in for an AdLinkFly shortener (gplinks / liteshort) so the
bypass flow can be tested without hitting the real, Cloudflare-fronted sites.

It reproduces:
  * /<code>            -> 302 to /<code>?vid=<visitor id>
  * /<code>?vid=...    -> the unlock page with the #go-link form + countdown
  * POST /links/go     -> {"status":"success","url": "<destination>"}
      (rejects the POST unless _token/ad_form_data and the AJAX header match)
  * /i/<code>          -> 302 to an "ad blog" interstitial carrying
                          lid/pid/vid/pages (the newer gplinks flow)
  * an optional Cloudflare-style challenge on the first request
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DESTINATION = "https://example-destination.test/final/file.mkv"
TOKEN = "tok_abc123"
AD_FORM_DATA = "eyJhZCI6MX0="

CHALLENGE_BODY = (
    "<html><head><title>Just a moment...</title></head><body>"
    "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1'>"
    "</script>Enable JavaScript and cookies to continue</body></html>"
)


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "cloudflare"
    challenge_once = True
    seen_hosts: set[str] = set()

    # -- helpers -----------------------------------------------------------
    def _send(self, code, body=b"", ctype="text/html", extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Server", "cloudflare")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass

    def _challenge_if_needed(self) -> bool:
        """Serve one Cloudflare interstitial per client, like the real thing."""
        if not type(self).challenge_once:
            return False
        cookies = self.headers.get("Cookie", "")
        if "cf_clearance=" in cookies:
            return False
        ua = self.headers.get("User-Agent", "")
        # A real Chrome UA + our client's cookie jar gets cleared immediately;
        # this mirrors curl_cffi clearing the managed challenge.
        if "Chrome/" in ua:
            self._send(
                200, "<html>ok</html>",
                extra={"Set-Cookie": "cf_clearance=passed; Path=/"},
            )
            return True
        self._send(403, CHALLENGE_BODY, extra={"cf-mitigated": "challenge"})
        return True

    # -- routes ------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path

        if self._challenge_if_needed():
            return

        # Variant B (the live gplinks flow): redirect carries NO parameters;
        # the ad blog plants lid/pid/vid/pages as *raw* cookies instead.
        if path.startswith("/c/"):
            code = path[3:]
            self._send(
                302, b"",
                extra={"Location": f"http://localhost:{self.server.server_address[1]}/blog/"},
            )
            return

        if path.startswith("/blog"):
            cookies = [
                "lid=ZkVCbbry", "pid=1093510", "vid=MTA0NjUxODg5NQ",
                "pages=5", "step_count=0", "imps=0",
            ]
            body = b"<html><head><title>skrresults</title></head><body>blog</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            for c in cookies:
                self.send_header("Set-Cookie", c + "; Path=/")
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/i/"):
            code = path[3:]
            lid, pid = _b64url(code), _b64url("194570")
            self._send(
                302, b"",
                extra={
                    "Location": (
                        f"http://{self.headers['Host']}/adblog/article"
                        f"?lid={lid}&pid={pid}&pages=2&vid=99386"
                    ),
                    "Set-Cookie": "lid=%s; Path=/" % lid,
                },
            )
            return

        if path.startswith("/adblog/"):
            self._send(200, "<html><body>Step 1 of 2 … CONTINUE</body></html>")
            return

        if path in ("/", "/favicon.ico"):
            self._send(200, "<html>home</html>")
            return

        code = path.strip("/")
        if not q.get("vid"):
            self._send(
                302, b"",
                extra={"Location": f"/{code}?vid=99386"},
            )
            return

        page = f"""<!doctype html><html><head><title>Please wait</title></head>
<body>
<script>var count = 5;</script>
<p>Your link is ready in <span id="timer">5</span> seconds</p>
<form id="go-link" method="post" action="/links/go">
  <input type="hidden" name="_token" value="{TOKEN}">
  <input type="hidden" name="ad_form_data" value="{AD_FORM_DATA}">
  <input type="hidden" name="code" value="{code}">
  <button type="submit">Get Link</button>
</form>
</body></html>"""
        self._send(200, page)

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path != "/links/go":
            self._send(404, "nope")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode()
        form = {k: v[0] for k, v in parse_qs(body).items()}
        if self.headers.get("X-Requested-With") != "XMLHttpRequest":
            self._send(400, json.dumps({"status": "error", "message": "bad xhr"}),
                       ctype="application/json")
            return
        if form.get("_token") != TOKEN or form.get("ad_form_data") != AD_FORM_DATA:
            self._send(400, json.dumps({"status": "error", "message": "bad token"}),
                       ctype="application/json")
            return
        self._send(
            200,
            json.dumps({"status": "success", "url": DESTINATION}),
            ctype="application/json",
        )


def start(port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


if __name__ == "__main__":
    s, p = start(8123)
    print("mock adlinkfly on", p)
    s.serve_forever()
