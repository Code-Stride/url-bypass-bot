"""
browser.py — the accurate engine: drive a real Chromium through the real flow.

Why a browser
-------------
Shorteners like gplinks validate progress **server-side**.  Replaying HTTP
requests and forging the `step_count` cookie earns
`error_code=not_enough_steps`, because the ad pages report impressions from
JavaScript with a session the server tracks.  A real browser executes that
JavaScript, so the steps are genuinely completed — and the same browser
transparently satisfies Cloudflare's JS challenges.

Strategy per page
-----------------
1. Detect a destination the moment we navigate somewhere off-network.
2. Otherwise treat the page as a gate:  wait out any countdown, then click the
   most promising control ("Continue", "Get Link", "Click here to continue"),
   dismissing popups/new tabs that ads open.
3. Repeat until a destination is reached or the budget expires.

Everything is heuristic but *verified*: only URLs that pass classify.verdict()
are ever returned.
"""

from __future__ import annotations

import asyncio
import re
import time

from app import config
from app.classify import (
    host_of,
    is_error_url,
    is_noise,
    is_pagination,
    is_shortener,
    verdict,
)
from app.models import Result

# Buttons/links that advance a shortener flow, best candidates first.
CLICK_TEXTS = [
    "get link", "getlink", "get your link", "click here to continue",
    "continue to destination", "continue", "proceed", "next step",
    "verify", "i am not a robot", "unlock", "generate link", "go to link",
    "download now", "download", "skip ad", "skip", "open link",
]

# Never click these — WordPress pagination/navigation on ad blogs, which is
# how a previous run wandered into skrresults.com/page/2/ and called it done.
AVOID_TEXT_RE = re.compile(
    r"^\s*(?:next|prev(?:ious)?|older|newer|page\s*\d+|\d+|home|menu|"
    r"privacy|terms|contact|about|dmca|disclaimer|search)\s*(?:posts?|page)?\s*$",
    re.IGNORECASE,
)

# Selectors tried before text matching (AdLinkFly + common clones).
CLICK_SELECTORS = [
    "#go-link button[type=submit]",
    "#go-link input[type=submit]",
    "form#go-link button",
    "a#invisibleCaptchaShortlink",
    "#getlink", "#get-link", "#btn-main", "#btn6", ".get-link",
    "button.get-link", "a.get-link", "#submit-button", "#downloadbtn",
]

_COUNTDOWN_TEXT_RE = re.compile(r"(\d{1,3})\s*(?:second|sec\b|s\b)", re.IGNORECASE)

# Cloudflare interstitial titles/bodies ("v3" managed challenge & Turnstile).
CF_RE = re.compile(
    r"(just a moment|one moment, please|checking your browser|"
    r"verify you are human|please wait while your request is being verified|"
    r"enable javascript and cookies)",
    re.IGNORECASE,
)


async def _pass_cloudflare(page, result, budget: float = 45.0) -> bool:
    """
    Sit on a Cloudflare interstitial until it clears, clicking the Turnstile
    checkbox if one is shown.  Returns True if the page moved on.
    """
    import time as _t

    end = _t.monotonic() + budget
    announced = False
    while _t.monotonic() < end:
        try:
            title = (await page.title()) or ""
            body = (await page.inner_text("body"))[:600]
        except Exception:  # noqa: BLE001
            return True  # navigated away mid-read
        if not CF_RE.search(title + " " + body):
            return True
        if not announced:
            result.log("wait", "Cloudflare challenge — solving", page.url)
            announced = True

        # Turnstile renders in a cross-origin iframe; click its checkbox.
        for fr in page.frames:
            if "challenges.cloudflare.com" not in (fr.url or ""):
                continue
            for sel in ("input[type=checkbox]", "#checkbox", "label"):
                try:
                    el = fr.locator(sel).first
                    if await el.count():
                        await el.click(timeout=2500)
                        result.log("click", "Turnstile checkbox")
                        break
                except Exception:  # noqa: BLE001
                    continue
        await page.wait_for_timeout(2500)
    return False


class BrowserEngine:
    """Owns one Chromium instance shared by all resolutions."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(config.BROWSER_CONCURRENCY)
        self.available = False
        self.error: str | None = None

    async def start(self) -> bool:
        """Launch Chromium once. Returns False if unavailable (no binary)."""
        async with self._lock:
            if self._browser is not None:
                return True
            try:
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=config.BROWSER_HEADLESS,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-popup-blocking",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--mute-audio",
                        "--window-size=1366,768",
                        "--disable-features=IsolateOrigins,site-per-process",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
                self.available = True
                return True
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).split("\n")[0][:200]
                if "Executable doesn't exist" in str(exc):
                    msg = (
                        "Chromium is not installed. Run "
                        "`python -m playwright install chromium` "
                        "(the Docker image does this for you)."
                    )
                self.error = msg
                self.available = False
                return False

    async def stop(self) -> None:
        async with self._lock:
            try:
                if self._browser is not None:
                    await self._browser.close()
                if self._pw is not None:
                    await self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
            self._pw = None
            self.available = False

    # -- helpers -----------------------------------------------------------
    async def _new_context(self):
        return await self._browser.new_context(
            user_agent=config.CHROME_UA,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Asia/Kolkata",
            java_script_enabled=True,
            ignore_https_errors=True,
        )

    @staticmethod
    async def _harden(context) -> None:
        """Hide the most obvious automation traces."""
        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = window.chrome || {runtime: {}};
            // Neutralise popunders so ad tabs cannot steal the flow.
            window.open = function(){ return null; };
            """
        )

    @staticmethod
    def _countdown_seconds(text: str) -> float:
        m = _COUNTDOWN_TEXT_RE.search(text or "")
        if not m:
            return 0.0
        try:
            return min(float(m.group(1)), 90.0)
        except ValueError:
            return 0.0

    async def _click_something(self, page) -> str | None:
        """Click the control that most likely advances the flow."""
        for sel in CLICK_SELECTORS:
            try:
                el = page.locator(sel).first
                if await el.count() and await el.is_visible():
                    await el.click(timeout=4000, no_wait_after=True)
                    return f"selector {sel}"
            except Exception:  # noqa: BLE001
                continue

        for text in CLICK_TEXTS:
            for role in ("button", "link"):
                try:
                    el = page.get_by_role(role, name=re.compile(text, re.I)).first
                    if not (await el.count() and await el.is_visible()):
                        continue
                    label = ((await el.inner_text()) or "").strip()
                    if AVOID_TEXT_RE.match(label):
                        continue
                    href = ""
                    try:
                        href = (await el.get_attribute("href")) or ""
                    except Exception:  # noqa: BLE001
                        pass
                    if href and is_pagination(href):
                        continue
                    await el.click(timeout=4000, no_wait_after=True)
                    return f"{role} '{label or text}'"
                except Exception:  # noqa: BLE001
                    continue
        # Last resort: a submit button of any form.
        try:
            el = page.locator("button[type=submit], input[type=submit]").first
            if await el.count() and await el.is_visible():
                await el.click(timeout=4000, no_wait_after=True)
                return "submit button"
        except Exception:  # noqa: BLE001
            pass
        return None

    # -- main flow ---------------------------------------------------------
    async def resolve(self, url: str, result: Result) -> Result:
        if not await self.start():
            return result.fail(f"browser unavailable ({self.error})")

        origin_host = host_of(url)
        deadline = time.monotonic() + config.BROWSER_TIMEOUT

        async with self._sem:
            context = await self._new_context()
            await self._harden(context)
            page = await context.new_page()

            # Ads open extra tabs; close them so the flow stays on one page.
            context.on("page", lambda p: asyncio.ensure_future(_close_quietly(p, page)))

            seen: list[str] = []
            visited: set[str] = {origin_host}
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                result.log("navigate", "opened link", url)

                rounds = 0
                idle = 0
                while time.monotonic() < deadline and rounds < 40:
                    rounds += 1
                    await _settle(page)
                    if not await _pass_cloudflare(page, result):
                        result.log("error", "Cloudflare challenge not solved", page.url)
                        return result.fail(
                            "blocked by Cloudflare — the host IP is likely "
                            "flagged; try FLARESOLVERR_URL or a residential proxy"
                        )
                    current = page.url
                    if current not in seen:
                        seen.append(current)
                        result.log("navigate", f"page {rounds}", current)

                    # Reached a credible destination?  Only stop on strong
                    # evidence (known file host or a direct file URL): a
                    # merely "unknown host with a path" is usually the NEXT
                    # gate in the chain, e.g. liteshort -> .../zip.php.
                    ok, conf, why = verdict(current, origin_host, visited)
                    if ok and conf >= 0.85 and not is_shortener(current):
                        result.candidates.append(current)
                        result.log("redirect", f"destination reached ({why})", current)
                        return result.succeed(current, "browser", min(0.99, conf + 0.05))
                    if ok and conf >= 0.55:
                        # Plausible but unproven — remember it as a fallback
                        # and keep walking the flow.
                        if current not in result.candidates:
                            result.candidates.append(current)

                    if is_error_url(current):
                        result.log("error", "shortener returned an error page", current)
                        return result.fail("shortener refused: " + current)

                    # Not a destination => it is a gate we are passing through.
                    visited.add(host_of(current))

                    body = ""
                    try:
                        body = (await page.inner_text("body"))[:4000]
                    except Exception:  # noqa: BLE001
                        pass

                    # Anti-bot bounce: some interstitials send suspected bots
                    # to a search engine, which then shows its own captcha.
                    if "/sorry/" in current or "unusual traffic" in body.lower():
                        result.log("error", "bounced to an anti-bot captcha", current)
                        return result.fail(
                            "the link's interstitial detected automation and "
                            "bounced us to a captcha. This server's IP is "
                            "flagged — use a residential proxy."
                        )

                    # Hard block from the origin (datacenter IP banned).
                    if rounds <= 2 and re.match(
                        r"^\s*(?:403 forbidden|access denied|error 1006|"
                        r"you have been blocked)", body, re.IGNORECASE
                    ):
                        result.log("error", "origin blocked this server", current)
                        return result.fail(
                            "the shortener blocked this server's IP (403). "
                            "Datacenter IPs are often banned — run it behind a "
                            "residential proxy or FlareSolverr."
                        )

                    wait_s = self._countdown_seconds(body)
                    if wait_s:
                        result.log("wait", f"countdown {wait_s:.0f}s", current)
                        await page.wait_for_timeout(min(wait_s + 1.5, 90) * 1000)

                    before = page.url
                    what = await self._click_something(page)
                    if what:
                        result.log("click", what, before)
                        try:
                            await page.wait_for_load_state(
                                "domcontentloaded", timeout=20000
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        await page.wait_for_timeout(1200)
                        if page.url == before:
                            # Click did nothing visible: give JS a moment more.
                            await page.wait_for_timeout(2500)
                        continue

                    # Nothing to click: harvest links and follow the best one.
                    cand = await self._harvest(page, origin_host, visited)
                    if cand:
                        result.candidates.extend(cand)
                        best = cand[0]
                        result.log("navigate", "following best embedded link", best)
                        try:
                            await page.goto(
                                best, wait_until="domcontentloaded", timeout=30000
                            )
                            continue
                        except Exception:  # noqa: BLE001
                            pass

                    # Nothing clickable yet. These pages routinely spend 15-60s
                    # on a timer before the button appears, so keep waiting
                    # instead of giving up after one look.
                    idle += 1
                    result.log(
                        "wait",
                        f"no control yet; waiting for JS ({idle})",
                        current,
                    )
                    await page.wait_for_timeout(5000)
                    if page.url != current:
                        idle = 0
                        continue
                    if idle >= 12:  # ~60s of no progress at all
                        result.log("error", "page never produced a control", current)
                        break

                # Budget spent — fall back to the best candidate we saw.
                from app.classify import pick_best

                best, conf = pick_best(result.candidates, origin_host, visited)
                if best:
                    return result.succeed(best, "browser(partial)", min(conf, 0.7))
                return result.fail("could not reach a destination in time")

            except Exception as exc:  # noqa: BLE001
                result.log("error", f"{type(exc).__name__}: {exc}")
                return result.fail(f"browser error: {exc}")
            finally:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    async def _harvest(
        page, origin_host: str, visited: set[str] | None = None
    ) -> list[str]:
        """Collect plausible destinations embedded in the page."""
        try:
            hrefs = await page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href)"
            )
        except Exception:  # noqa: BLE001
            hrefs = []
        out: list[tuple[float, str]] = []
        for h in hrefs:
            ok, conf, _ = verdict(h, origin_host, visited)
            if ok and conf >= 0.6 and not is_noise(h):
                out.append((conf, h))
        out.sort(reverse=True)
        seen, ranked = set(), []
        for _, u in out:
            if u not in seen:
                seen.add(u)
                ranked.append(u)
        return ranked[:10]


async def _settle(page) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    await page.wait_for_timeout(700)


async def _close_quietly(new_page, keep) -> None:
    """Close ad pop-up tabs, never the main page."""
    try:
        if new_page is not keep:
            await new_page.close()
    except Exception:  # noqa: BLE001
        pass


ENGINE = BrowserEngine()
