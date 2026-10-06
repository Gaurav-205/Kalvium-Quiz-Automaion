"""Browser setup and the small set of page operations everything else uses."""

from __future__ import annotations

import logging
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout

from .config import PACKAGE_DIR, js_config, path, stamp

log = logging.getLogger("kalbot.browser")

JS_HELPERS = (PACKAGE_DIR / "dom.js").read_text(encoding="utf-8")


class UnexpectedState(Exception):
    """The page is not in the state we expected. The current LU is skipped."""


def livebooks_url(cfg: dict) -> str:
    p = cfg["portal"]
    return p["base_url"].rstrip("/") + p["livebooks_path"].format(semester=p["semester"])


def launch_browser(p, cfg: dict):
    """Chrome with a persistent, dedicated profile so the login survives between runs."""
    b = cfg["browser"]
    kw = dict(
        user_data_dir=str(path(cfg, "profile")),
        headless=bool(b.get("headless")),
        slow_mo=b.get("slow_mo_ms") or 0,
        accept_downloads=False,
    )
    if b.get("headless"):
        kw["viewport"] = {"width": 1366, "height": 900}
    else:
        kw["no_viewport"] = True
        kw["args"] = ["--start-maximized"]
    if b.get("executable_path"):
        kw["executable_path"] = b["executable_path"]
    elif b.get("channel"):
        kw["channel"] = b["channel"]
    ctx = p.chromium.launch_persistent_context(**kw)
    ctx.set_default_timeout(cfg["timeouts"]["action_ms"])
    ctx.set_default_navigation_timeout(cfg["timeouts"]["navigation_ms"])
    ctx.add_init_script(JS_HELPERS)
    return ctx


def find_chrome(cfg: dict) -> str | None:
    if cfg["browser"].get("chrome_path"):
        return cfg["browser"]["chrome_path"]
    cands = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            cands.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    cands.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    for c in cands:
        if c.exists():
            return str(c)
    for name in ("google-chrome", "google-chrome-stable", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def manual_login(cfg: dict, ui) -> int:
    """Fallback login: plain Chrome (no automation) on the bot profile.

    For when Google refuses to sign in inside the automated window."""
    chrome = find_chrome(cfg)
    if not chrome:
        ui.error("Chrome not found. Set browser.chrome_path in config.yaml.")
        return 2
    profile = path(cfg, "profile")
    ui.say("Opening Chrome with the bot profile.")
    ui.say("Log in to Kalvium, wait until your livebooks show, then [b]close that Chrome window[/b].", markup=True)
    subprocess.run([chrome, f"--user-data-dir={profile}", "--no-first-run",
                    "--no-default-browser-check", livebooks_url(cfg)])
    ui.ok("Chrome closed; the session is saved. Next: kalbot discover")
    return 0


def first_line(exc: BaseException) -> str:
    s = str(exc)
    return s.splitlines()[0] if s else type(exc).__name__


class PageOps:
    """Evaluate helpers, poll, click, pace and snapshot on one page (and its frames)."""

    def __init__(self, cfg: dict, page):
        self.cfg = cfg
        self.page = page
        self.t = cfg["timeouts"]
        self.jscfg = js_config(cfg)
        d = cfg["delays"]
        self.lo, self.hi = float(d["action_min_s"]), float(d["action_max_s"])
        self.expect_confirm = False   # accept the next native confirm() (after clicking Submit)
        page.on("dialog", self._on_dialog)

    # ---------------------------------------------------------------- helpers in the page

    def _on_dialog(self, dialog) -> None:
        try:
            if dialog.type == "beforeunload" or (self.expect_confirm and dialog.type == "confirm"):
                dialog.accept()
            else:
                log.info("dismissed %s dialog: %s", dialog.type, dialog.message[:120])
                dialog.dismiss()
        except PWError:
            pass

    @staticmethod
    def ensure(scope) -> None:
        """Inject dom.js into a page or frame if it is not there yet."""
        if not scope.evaluate("() => !!(window.__kqb && window.__kqb.version)"):
            scope.evaluate(JS_HELPERS)

    def call(self, scope, fn: str, *args):
        """window.__kqb.<fn>(*args) in a frame (main frame by default)."""
        scope = scope or self.page.main_frame
        self.ensure(scope)
        return scope.evaluate("([f, a]) => window.__kqb[f](...a)", [fn, list(args)])

    def eval(self, scope, expr: str, arg=None):
        scope = scope or self.page.main_frame
        self.ensure(scope)
        return scope.evaluate(expr, arg)

    # ---------------------------------------------------------------- waiting

    def poll(self, fn, timeout_ms: float, interval_ms: int = 300):
        """Call fn until it returns something truthy or the timeout passes."""
        deadline = time.monotonic() + timeout_ms / 1000
        seen: set[str] = set()
        while True:
            try:
                v = fn()
                if v:
                    return v
            except PWError as e:
                msg = first_line(e)
                if any(s in msg for s in ("context was destroyed", "navigat", "detached", "closed")):
                    log.debug("poll: %s", msg)   # page navigating between checks
                elif msg not in seen:
                    seen.add(msg)
                    log.warning("page check failed: %s", msg)
            if time.monotonic() > deadline:
                return None
            self.page.wait_for_timeout(interval_ms)

    def pause(self) -> None:
        """Random short pause between actions so the portal has time to settle."""
        self.page.wait_for_timeout(random.uniform(self.lo, self.hi) * 1000)

    def settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=self.t["settle_ms"])
        except PWTimeout:
            pass   # SPAs with websockets never go fully idle

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")
        self.settle()

    # ---------------------------------------------------------------- acting

    def click(self, scope, selector: str, what: str) -> None:
        loc = (scope or self.page.main_frame).locator(selector).first
        try:
            loc.scroll_into_view_if_needed(timeout=self.t["action_ms"])
            loc.click(timeout=self.t["action_ms"])
        except PWError as e:
            raise UnexpectedState(f"could not click {what}: {first_line(e)}") from None
        log.info("clicked %s", what)

    def click_text(self, text: str) -> bool:
        """Click a tab/link/button whose accessible name is exactly `text`."""
        name = re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)
        for role in ("tab", "link", "button"):
            loc = self.page.get_by_role(role, name=name)
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    loc.nth(i).click()
                    log.info("clicked %s '%s'", role, text)
                    return True
        loc = self.page.get_by_text(text, exact=True)
        for i in range(min(loc.count(), 5)):
            if loc.nth(i).is_visible():
                loc.nth(i).click()
                log.info("clicked text '%s'", text)
                return True
        return False

    def snapshot(self, out_dir: Path, label: str) -> Path:
        """Write <stamp>_<label>.html and .png. Never raises."""
        out_dir.mkdir(parents=True, exist_ok=True)
        base = out_dir / f"{stamp()}_{slug(label)}"
        try:
            Path(f"{base}.html").write_text(self.page.content(), encoding="utf-8")
        except Exception as e:  # page may be mid-navigation
            log.debug("html snapshot failed: %s", e)
        try:
            self.page.screenshot(path=f"{base}.png", full_page=True)
        except Exception as e:
            log.debug("screenshot failed: %s", e)
        return base


def slug(text: str, maxlen: int = 50) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", text or "").strip("_")
    return s[:maxlen] or "page"
