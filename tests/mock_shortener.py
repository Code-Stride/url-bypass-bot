"""
A local replica of the real gplinks/liteshort behaviour, built from the live
traces captured against gplinks.co/ZkVCbbry.

Reproduces the things that broke the previous implementation:
  * a 302 to an ad blog with NO query parameters
  * lid/pid/vid/pages/step_count planted as cookies by the blog's response
  * **server-side** step validation: /links/go answers
    error_code=not_enough_steps until the ad steps are really reported
  * a JS countdown before the unlock button becomes usable
  * a Cloudflare-style interstitial on first contact

Routes
  /<code>        classic flow: 302 -> ?vid= -> unlock page
  /c/<code>      live gplinks flow: 302 -> /blog (cookies) -> steps -> unlock
  /blog          the ad blog; POST /blog/step reports one step
  /links/go      unlock endpoint (validates steps server-side)
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DESTINATION = "https://devuploads.com/7h77e7ikjhxj"
TOKEN = "tok_live_1"
AD_FORM_DATA = "eyJhZCI6MX0="
PAGES = 3

# visitor id -> steps reported (server-side truth, not a cookie)
STEPS: dict[str, int] = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "cloudflare"

    def _send(self, code, body=b"", ctype="text/html", cookies=None, location=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Server", "cloudflare")
        if location:
            self.send_header("Location", location)
        for c in cookies or []:
            self.send_header("Set-Cookie", c + "; Path=/")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *a):
        pass

    def _cookies(self) -> dict[str, str]:
        raw = self.headers.get("Cookie", "")
        return dict(
            c.strip().split("=", 1) for c in raw.split(";") if "=" in c
        )

    @property
    def _base(self) -> str:
        return f"http://{self.headers.get('Host')}"

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path

        if path in ("/", "/favicon.ico"):
            self._send(200, "<html>home</html>")
            return

        # --- live variant: bounce to the ad blog, no params -------------
        if path.startswith("/c/"):
            code = path[3:]
            STEPS[code] = 0
            self._send(302, b"", location=f"{self._base}/blog",
                       cookies=[f"lid={code}"])
            return

        if path.startswith("/blog"):
            code = self._cookies().get("lid", "x")
            done = STEPS.get(code, 0)
            if done >= PAGES:
                # Steps finished: send the visitor back to the unlock page.
                body = (
                    "<html><body>done"
                    f"<a id='go' href='{self._base}/{code}?vid=v1'>Continue</a>"
                    "</body></html>"
                )
                self._send(200, body)
                return
            body = f"""<html><head><title>adblog</title></head><body>
<p>Step {done + 1} of {PAGES} — please wait 1 second</p>
<form method="post" action="/blog/step"><button type="submit">Continue</button></form>
</body></html>"""
            self._send(
                200, body,
                cookies=[
                    f"lid={code}", "pid=1093510", "vid=v1",
                    f"pages={PAGES}", f"step_count={done}", f"imps={done}",
                ],
            )
            return

        # --- classic variant --------------------------------------------
        code = path.strip("/")
        if not q.get("vid"):
            self._send(302, b"", location=f"/{code}?vid=v1")
            return

        page = f"""<!doctype html><html><head><title>Please wait</title></head><body>
<p>Your link is ready in 1 second</p>
<form id="go-link" method="post" action="/links/go">
  <input type="hidden" name="_token" value="{TOKEN}">
  <input type="hidden" name="ad_form_data" value="{AD_FORM_DATA}">
  <input type="hidden" name="code" value="{code}">
  <button type="submit">Get Link</button>
</form></body></html>"""
        self._send(200, page)

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode() if length else ""
        form = {k: v[0] for k, v in parse_qs(raw).items()}

        # One ad step genuinely completed (a browser gets here by clicking).
        if u.path == "/blog/step":
            code = self._cookies().get("lid", "x")
            STEPS[code] = STEPS.get(code, 0) + 1
            self._send(302, b"", location=f"{self._base}/blog")
            return

        if u.path != "/links/go":
            self._send(404, "nope")
            return

        if self.headers.get("X-Requested-With") != "XMLHttpRequest":
            self._send(400, json.dumps({"status": "error", "message": "bad xhr"}),
                       ctype="application/json")
            return
        if form.get("_token") != TOKEN or form.get("ad_form_data") != AD_FORM_DATA:
            self._send(400, json.dumps({"status": "error", "message": "bad token"}),
                       ctype="application/json")
            return

        # Server-side step check — forging cookies does NOT help.
        code = form.get("code") or self._cookies().get("lid", "x")
        if code in STEPS and STEPS.get(code, 0) < PAGES:
            self._send(
                200,
                json.dumps({
                    "status": "error",
                    "url": f"{self._base}/link-error?error_code=not_enough_steps",
                }),
                ctype="application/json",
            )
            return

        self._send(200, json.dumps({"status": "success", "url": DESTINATION}),
                   ctype="application/json")


def start(port: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


if __name__ == "__main__":
    s, p = start(8123)
    print("mock shortener on", p)
    s.serve_forever()
