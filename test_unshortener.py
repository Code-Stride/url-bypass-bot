"""
test_unshortener.py — Unit and integration tests for unshortener engine.
"""

import unittest
from unittest.mock import patch, MagicMock
from unshortener import (
    unshorten,
    resolve,
    unpack_embedded_url,
    try_decode_base64_url,
    decode_adfly,
    _score_candidate,
    _is_intermediary,
    _is_noise,
    _is_shortener,
)


class TestUnshortener(unittest.TestCase):

    def test_base64_decoding(self):
        # Mediafire URL from user request
        real_url = "https://www.mediafire.com/file/9xdmaqc06cvrszo/NothingOS_toogle_4_v_gen_1.zip/file"
        b64 = "aHR0cHM6Ly93d3cubWVkaWFmaXJlLmNvbS9maWxlLzl4ZG1hcWMwNmN2cnN6by9Ob3RoaW5nT1NfdG9vZ2xlXzRfdl9nZW5fMS56aXAvZmlsZQ=="
        
        decoded = try_decode_base64_url(b64)
        self.assertEqual(decoded, real_url)

    def test_unpack_embedded_url_safelinku(self):
        real_url = "https://www.mediafire.com/file/9xdmaqc06cvrszo/NothingOS_toogle_4_v_gen_1.zip/file"
        b64 = "aHR0cHM6Ly93d3cubWVkaWFmaXJlLmNvbS9maWxlLzl4ZG1hcWMwNmN2cnN6by9Ob3RoaW5nT1NfdG9vZ2xlXzRfdl9nZW5fMS56aXAvZmlsZQ=="
        
        # Test sfl.gl/ready/go format
        sfl_ready_url = f"https://sfl.gl/ready/go?u={b64}"
        unpacked = unpack_embedded_url(sfl_ready_url)
        self.assertEqual(unpacked, real_url)

        # Direct unshorten on sfl.gl/ready/go
        res = unshorten(sfl_ready_url)
        self.assertTrue(res["ok"])
        self.assertIn(real_url, res["results"])

    def test_unpack_various_params(self):
        target = "https://example.com/target-file.zip"
        # Plain URL in param
        self.assertEqual(unpack_embedded_url(f"https://short.com/go?url={target}"), target)
        self.assertEqual(unpack_embedded_url(f"https://short.com/go?link={target}"), target)
        self.assertEqual(unpack_embedded_url(f"https://short.com/go?dest={target}"), target)

    def test_is_intermediary(self):
        self.assertTrue(_is_intermediary("https://app.khaddavi.net/redirect.php"))
        self.assertTrue(_is_intermediary("https://example.com/safe.php?link=123"))
        self.assertTrue(_is_intermediary("https://sfl.gl/ready/go?u=xyz"))
        self.assertFalse(_is_intermediary("https://www.mediafire.com/file/xyz/file"))

    def test_is_noise(self):
        self.assertTrue(_is_noise("https://www.google-analytics.com/analytics.js"))
        self.assertTrue(_is_noise("https://www.facebook.com/sharer/sharer.php?u=foo"))
        self.assertFalse(_is_noise("https://www.mediafire.com/file/abc"))

    def test_is_shortener(self):
        self.assertTrue(_is_shortener("https://sfl.gl/0lJsgXTI"))
        self.assertTrue(_is_shortener("https://app.khaddavi.net/some-post/"))
        self.assertTrue(_is_shortener("https://tutwuri.id/test"))
        self.assertTrue(_is_shortener("https://bit.ly/xyz"))
        self.assertFalse(_is_shortener("https://www.mediafire.com/file/abc"))

    def test_score_candidate(self):
        score_mf = _score_candidate("https://www.mediafire.com/file/abc")
        score_mega = _score_candidate("https://mega.nz/file/xyz")
        score_inter = _score_candidate("https://app.khaddavi.net/redirect.php")
        self.assertGreater(score_mf, score_inter)
        self.assertGreater(score_mega, score_inter)

    @patch("unshortener._session.get")
    def test_sfl_blog_redirect_flow(self, mock_get):
        """
        Simulate the exact flow for https://sfl.gl/0lJsgXTI:
        1. sfl.gl/0lJsgXTI -> redirects to app.khaddavi.net/game-mobile-casual...
        2. app.khaddavi.net page contains <a href="https://app.khaddavi.net/redirect.php">
        3. app.khaddavi.net/redirect.php -> redirects to sfl.gl/ready/go?u=<base64>
        4. sfl.gl/ready/go?u=<base64> unpacks to real mediafire URL
        """
        real_url = "https://www.mediafire.com/file/9xdmaqc06cvrszo/NothingOS_toogle_4_v_gen_1.zip/file"
        b64 = "aHR0cHM6Ly93d3cubWVkaWFmaXJlLmNvbS9maWxlLzl4ZG1hcWMwNmN2cnN6by9Ob3RoaW5nT1NfdG9vZ2xlXzRfdl9nZW5fMS56aXAvZmlsZQ=="
        
        # Mock responses
        def side_effect(url, **kwargs):
            resp = MagicMock()
            if "sfl.gl/0lJsgXTI" in url:
                # HTTP 302 to khaddavi blog
                resp.url = "https://app.khaddavi.net/game-mobile-casual-seru-untuk-anak-hiburan-menyenangkan-untuk-si-kecil/"
                resp.headers = {"Content-Type": "text/html; charset=utf-8"}
                resp.text = """
                <html>
                <body>
                    <h1>Game Mobile Casual Seru untuk Anak</h1>
                    <p>Some article content...</p>
                    <a id="btn-3" href="https://app.khaddavi.net/redirect.php">Download File</a>
                </body>
                </html>
                """
                resp.ok = True
                return resp
            elif "app.khaddavi.net/redirect.php" in url:
                # redirect.php redirects to sfl.gl/ready/go?u=...
                resp.url = f"https://sfl.gl/ready/go?u={b64}"
                resp.headers = {"Content-Type": "text/html; charset=utf-8"}
                resp.text = """
                <html>
                <body>
                    <span class="font-medium text-base">OPEN LINK</span>
                </body>
                </html>
                """
                resp.ok = True
                return resp
            elif "sfl.gl/ready/go" in url:
                resp.url = f"https://sfl.gl/ready/go?u={b64}"
                resp.headers = {"Content-Type": "text/html; charset=utf-8"}
                resp.text = "<html><body>Ready</body></html>"
                resp.ok = True
                return resp
            return None

        mock_get.side_effect = side_effect

        res = unshorten("https://sfl.gl/0lJsgXTI")
        self.assertTrue(res["ok"])
        self.assertEqual(res["results"], [real_url])


    def test_adfly_decoder(self):
        # Sample encoded ysmm test
        # When ysmm = 'aHR0cHM6Ly9leGFtcGxlLmNvbQ==' without scrambling or with mock
        self.assertIsNone(decode_adfly("short"))

    def test_nested_base64(self):
        import base64
        url = "https://www.mediafire.com/file/123/test.zip"
        # 3x encoded base64
        b1 = base64.b64encode(url.encode()).decode()
        b2 = base64.b64encode(b1.encode()).decode()
        b3 = base64.b64encode(b2.encode()).decode()
        
        decoded = try_decode_base64_url(b3)
        self.assertEqual(decoded, url)

    def test_url_encoded_target(self):
        url = "https://mega.nz/file/xyz123#key"
        encoded_url = "https%3A%2F%2Fmega.nz%2Ffile%2Fxyz123%23key"
        short_url = f"https://example-shortener.com/out?target={encoded_url}"
        
        unpacked = unpack_embedded_url(short_url)
        self.assertEqual(unpacked, url)


if __name__ == "__main__":
    unittest.main()
