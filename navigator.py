"""Navigator: login, livebooks, Learning Path, LUs, and handing quizzes to the solver."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml
from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout

from common import JS_HELPERS, Pacer, UnexpectedState, ensure_helpers, folder, js_config, save_snapshot, stamp
from llm import LLMError
from quiz import QuizSolver

log = logging.getLogger("kqb.nav")


@dataclass
class LU:
    index: int
    number: str
    title: str
    href: str
    text: str
    completed: bool
    type_hint: str      # quiz | written | coding | ""


def livebooks_url(cfg: dict) -> str:
    p = cfg["portal"]
    return p["base_url"].rstrip("/") + p["livebooks_path"].format(semester=p["semester"])


# --------------------------------------------------------------------------- browser


def launch_browser(p, cfg: dict):
    """Headed Chrome with a persistent, dedicated profile (./browser_profile)."""
    b = cfg["browser"]
    kw = dict(
        user_data_dir=str(folder(cfg, "profile")),
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


def manual_login(cfg: dict) -> int:
    """Fallback login: plain Chrome (no automation) on the bot profile.

    Use it if Google refuses to sign in inside the automated window."""
    chrome = find_chrome(cfg)
    if not chrome:
        print("Chrome not found. Set browser.chrome_path in config.yaml.")
        return 2
    profile = folder(cfg, "profile")
    print("Opening Chrome with the bot profile (browser_profile/).")
    print("Log in to Kalvium, wait until your livebooks show, then CLOSE that Chrome window.")
    subprocess.run([chrome, f"--user-data-dir={profile}", "--no-first-run",
                    "--no-default-browser-check", livebooks_url(cfg)])
    print("Chrome closed; the session is saved. Next: python main.py --discover")
    return 0


# --------------------------------------------------------------------------- navigator


class Navigator:
    def __init__(self, cfg: dict, page, solver: QuizSolver, runlog, *, dry_run: bool,
                 limit: int, livebook: str | None, lu: str | None):
        self.cfg = cfg
        self.page = page
        self.solver = solver
        self.runlog = runlog
        self.dry_run = dry_run
        self.limit = limit
        self.livebook_filter = (livebook or "").strip().lower()
        self.lu_filter = (lu or "").strip()
        self.jscfg = js_config(cfg)
        self.pacer = Pacer(cfg)
        self.t = cfg["timeouts"]
        self.url = livebooks_url(cfg)
        self.host = urlparse(cfg["portal"]["base_url"]).netloc
        self.err_dir = folder(cfg, "errors")
        self.snap_dir = folder(cfg, "snapshots")
        self.quizzes = 0
        pats = cfg["patterns"]
        rx = lambda key: [re.compile(p, re.I) for p in pats[key]]  # noqa: E731
        self.re_done, self.re_not_done = rx("lu_completed"), rx("lu_not_completed")
        self.re_quiz, self.re_written, self.re_coding = rx("lu_quiz"), rx("lu_written"), rx("lu_coding")
        page.on("dialog", self._on_dialog)

    # ------------------------------------------------------------------ small helpers

    def _on_dialog(self, dialog) -> None:
        try:
            if dialog.type == "beforeunload" or (self.solver.expect_confirm and dialog.type == "confirm"):
                dialog.accept()
            else:
                log.info("dismissed %s dialog: %s", dialog.type, dialog.message[:120])
                dialog.dismiss()
        except PWError:
            pass

    def _eval(self, expr: str, arg=None):
        ensure_helpers(self.page.main_frame)
        return self.page.evaluate(expr, arg)

    def _poll(self, fn, timeout_ms: float, interval_ms: int = 400):
        return self.solver._poll(self.page, fn, timeout_ms, interval_ms)

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=self.t["settle_ms"])
        except PWTimeout:
            pass   # SPAs with websockets never go fully idle

    def _goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")
        self._settle()

    def _click(self, selector: str, what: str) -> None:
        self.solver._click(self.page.main_frame, selector, what)

    def _click_text(self, text: str) -> bool:
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

    def _limit_reached(self) -> bool:
        return bool(self.limit) and self.quizzes >= self.limit

    def _error(self, ctx: dict, exc: Exception) -> None:
        msg = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
        base = save_snapshot(self.page, self.err_dir, f"{ctx.get('livebook', '')}_{ctx.get('lu', '')}")
        print(f"     ERROR: {msg}\n     screenshot: {base}.png - skipping")
        log.error("%s | %s", ctx, msg)
        self.runlog.errors.append({**ctx, "note": msg})
        self.runlog.row(event="error", note=f"{msg} [{base.name}]", url=self.page.url, **ctx)

    # ------------------------------------------------------------------ login

    def _is_login(self, url: str) -> bool:
        u = url.lower()
        return any(p.lower() in u for p in self.cfg["patterns"]["login_url"])

    def list_livebooks(self) -> list[dict]:
        return self._eval("cfg => window.__kqb.listLivebooks(cfg)", self.jscfg)

    def wait_for_login(self) -> None:
        self._goto(self.url)
        if not self._is_login(self.page.url) and self._poll(self.list_livebooks, 8000):
            print("Logged in.")
            return
        mins = float(self.cfg["run"]["login_timeout_minutes"])
        if urlparse(self.page.url).netloc == self.host and not self._is_login(self.page.url):
            print("Waiting for the livebooks list to appear...")
        else:
            print("\n" + "!" * 70)
            print("NOT LOGGED IN. Log in to Kalvium in the Chrome window that just opened.")
            print(f"Waiting up to {mins:g} minutes for {self.url} to show your livebooks...")
            print("(If Google blocks the sign-in there, close this, run `python main.py --login`, then run again.)")
            print("!" * 70 + "\n")
        deadline = time.monotonic() + mins * 60
        last_nav = 0.0
        on_list_since = None
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(2000)
            url = self.page.url
            if urlparse(url).netloc != self.host or self._is_login(url):
                on_list_since = None
                continue
            if self._poll(self.list_livebooks, 2000):
                print("Login detected.")
                return
            if "/livebooks" in urlparse(url).path:
                on_list_since = on_list_since or time.monotonic()
                if time.monotonic() - on_list_since > 25:
                    self._no_cards()
            elif time.monotonic() - last_nav > 10:
                last_nav = time.monotonic()   # logged in but landed elsewhere
                try:
                    self._goto(self.url)
                except PWError:
                    pass
        raise SystemExit("Timed out waiting for login. Run again, or use `python main.py --login`.")

    def _no_cards(self) -> None:
        """Logged in and on the livebooks page, but no cards recognised: stop with a clear hint."""
        base = save_snapshot(self.page, self.snap_dir, "livebooks_no_cards")
        report = self.snap_dir / f"{base.name}_report.json"
        try:
            report.write_text(json.dumps(self._report(), indent=2, ensure_ascii=False), encoding="utf-8")
        except PWError:
            pass
        raise SystemExit(
            "You seem to be logged in, but no livebook cards were recognised on\n"
            f"  {self.page.url}\n"
            f"Saved {base}.png/.html and {report.name}.\n"
            "Set selectors.livebook_card in config.yaml to a selector matching one card per subject\n"
            "(see the report's links/dataAttributes), or adjust selectors.livebook_href_regex.")

    # ------------------------------------------------------------------ livebook + learning path

    def open_livebook(self, lb: dict) -> None:
        if lb.get("url"):
            self._goto(lb["url"])
        elif lb.get("href"):
            self._goto(lb["href"])
        else:
            self._goto(self.url)
            self._poll(self.list_livebooks, self.t["list_ms"])
            cards = self.list_livebooks()
            match = next((c for c in cards if c["name"] == lb["name"]), None)
            if not match:
                raise UnexpectedState(f"livebook card '{lb['name']}' not found")
            old = self.page.url
            self._click(f'[data-kqb-lb="{match["index"]}"]', f"livebook {lb['name']}")
            try:
                self.page.wait_for_url(lambda u: u != old, timeout=self.t["navigation_ms"])
            except PWTimeout:
                raise UnexpectedState("clicking the livebook card did not open it") from None
            self._settle()
        lb["url"] = self.page.url
        self.pacer.pause(self.page)

    def list_lus(self) -> list[LU]:
        raw = self._eval("cfg => window.__kqb.listLUs(cfg)", self.jscfg)
        out = []
        for r in raw:
            hay = f"{r['text']} {r['hints']['attrs']} progress={r['hints']['progress']}"
            done = any(p.search(hay) for p in self.re_done) and not any(p.search(hay) for p in self.re_not_done)
            kind = ""
            for name, pats in (("quiz", self.re_quiz), ("coding", self.re_coding), ("written", self.re_written)):
                if any(p.search(hay) for p in pats):
                    kind = name
                    break
            out.append(LU(r["index"], r["number"], r["title"], r["href"], r["text"], done, kind))
        return out

    def open_learning_path(self) -> list[LU]:
        tab = self.cfg["selectors"]["learning_path_tab"]
        rendered = lambda: self.list_lus() or self._eval(  # noqa: E731
            "cfg => window.__kqb.collapsedModules(cfg)", self.jscfg)
        # the page may still be loading: wait for the tab (or a path that is already shown)
        how = self._poll(lambda: ("tab" if tab and self._click_text(tab) else None) or
                         ("shown" if rendered() else None), self.t["list_ms"])
        if how == "tab":
            self.pacer.pause(self.page)
            self._settle()
        # wait until the path has rendered: LU rows or (all-collapsed) module toggles
        self._poll(rendered, self.t["list_ms"])
        clicked: set[str] = set()
        for _ in range(40):   # expand collapsed modules one at a time (DOM re-renders)
            n = self._eval("cfg => window.__kqb.collapsedModules(cfg)", self.jscfg)
            if not n:
                break
            texts = self._eval("() => [...document.querySelectorAll('[data-kqb-mod]')].map(e => e.innerText.trim().slice(0, 80))")
            todo = [i for i, t in enumerate(texts) if t not in clicked]
            if not todo:
                break
            clicked.add(texts[todo[0]])
            self._click(f'[data-kqb-mod="{todo[0]}"]', f"module '{texts[todo[0]]}'")
            self.page.wait_for_timeout(600)
        lus = self._poll(self.list_lus, self.t["list_ms"])
        return lus or []

    def _lu_line(self, lu: LU) -> str:
        status = "done" if lu.completed else "todo"
        hint = f" [{lu.type_hint}]" if lu.type_hint else ""
        return f"    {lu.number:>5}  {status:4}  {lu.title[:70]}{hint}"

    # ------------------------------------------------------------------ one LU

    def open_lu(self, lb: dict, lu: LU) -> None:
        if lu.href:
            self._goto(lu.href)
        else:
            self.open_livebook(lb)
            fresh = {x.number: x for x in self.open_learning_path()}
            if lu.number not in fresh:
                raise UnexpectedState(f"LU {lu.number} not found on the Learning Path")
            old = self.page.url
            self._click(f'[data-kqb-lu="{fresh[lu.number].index}"]', f"LU {lu.number}")
            try:
                self.page.wait_for_url(lambda u: u != old, timeout=8000)
            except PWTimeout:
                pass   # LU may open in place (panel/drawer)
            self._settle()
        self.pacer.pause(self.page)

    def wait_lu_content(self):
        """Wait for the LU to render, then look for a quiz (also behind tabs / lazy sections)."""
        last = {}

        def check():
            frame, st, completed = self.solver.locate(self.page)
            last["v"] = (frame, st, completed)
            return last["v"] if (st["kind"] in ("question", "start") or completed) else None

        got = self._poll(check, self.t["lu_settle_ms"])
        if got:
            return got
        tab = self._eval("t => window.__kqb.markInPageTab(t)", self.cfg["texts"]["quiz_tabs"])
        if tab:
            self._click('[data-kqb-tab="1"]', f"tab '{tab}'")
            self.pacer.pause(self.page)
            got = self._poll(check, 5000)
            if got:
                return got
        self._eval("() => window.__kqb.scrollAll()")
        self.pacer.pause(self.page)
        return self._poll(check, 3000) or last.get("v") or self.solver.locate(self.page)

    def process_lu(self, lb: dict, lu: LU, ctx: dict) -> None:
        print(f"\n  -> LU {lu.number} {lu.title}")
        self.open_lu(lb, lu)
        frame, st, completed = self.wait_lu_content()
        feats = self._eval("cfg => window.__kqb.luFeatures(cfg)", self.jscfg)
        task = "coding" if feats["coding"] else "written" if feats["written"] else ""

        if completed:
            print("     Quiz already submitted - skipping.")
            self.runlog.already.append({**ctx, "note": "quiz already submitted"})
            self.runlog.row(event="skip_completed", note="quiz already submitted", url=self.page.url, **ctx)
            if not lu.completed:   # LU still open on the Learning Path, so something else is left
                self.runlog.manual.append({**ctx, "note": f"{task or 'other task'} (quiz already done)"})
            return

        if st["kind"] == "start" and lu.type_hint in ("coding", "written"):
            st = {**st, "kind": "none"}   # a Start button on a coding/written LU is not a quiz

        if st["kind"] in ("question", "start"):
            out = self.solver.run(self.page, ctx)
            if out.status == "submitted":
                self.quizzes += 1
                self.runlog.done.append({**ctx, "result": out.result})
            elif out.status in ("dry_run", "needs_start"):
                self.quizzes += 1
                self.runlog.dry.append(ctx)
            elif out.status == "already_done":
                self.runlog.already.append(ctx)
                self.runlog.row(event="skip_completed", url=self.page.url, **ctx)
            else:
                self.runlog.no_quiz.append(ctx)
            return

        kind = task or (lu.type_hint if lu.type_hint in ("coding", "written") else "")
        if kind:
            print(f"     {kind} task - left for you (manual).")
            self.runlog.manual.append({**ctx, "note": kind})
            self.runlog.row(event="manual", note=kind, url=self.page.url, **ctx)
        else:
            print("     No quiz or task detected.")
            self.runlog.no_quiz.append(ctx)
            self.runlog.row(event="no_quiz", url=self.page.url, **ctx)

    # ------------------------------------------------------------------ full run

    def _livebooks(self) -> list[dict]:
        lbs = self.list_livebooks()
        print(f"\nFound {len(lbs)} livebooks in semester {self.cfg['portal']['semester']}:")
        for lb in lbs:
            print(f"  - {lb['name']}")
        if self.livebook_filter:
            lbs = [lb for lb in lbs if self.livebook_filter in lb["name"].lower()]
            if not lbs:
                print(f"No livebook name contains '{self.livebook_filter}'.")
        return lbs

    def run(self) -> None:
        for lb in self._livebooks():
            if self._limit_reached():
                break
            ctx = {"livebook": lb["name"], "lu": "", "lu_title": ""}
            try:
                self.process_livebook(lb)
            except LLMError as e:
                if e.fatal:
                    raise
                self._error(ctx, e)
            except (UnexpectedState, PWError) as e:
                self._error(ctx, e)
        if self._limit_reached():
            print(f"\nReached --limit {self.limit}.")

    def process_livebook(self, lb: dict) -> None:
        print(f"\n=== {lb['name']} ===")
        self.open_livebook(lb)
        lus = self.open_learning_path()
        if not lus:
            raise UnexpectedState("no LUs found on the Learning Path")
        print(f"  {len(lus)} LUs on the Learning Path:")
        for lu in lus:
            print(self._lu_line(lu))
        if self.lu_filter:
            lus = [x for x in lus if x.number == self.lu_filter]
            if not lus:
                print(f"  LU {self.lu_filter} not in this livebook.")
        for lu in lus:
            if self._limit_reached():
                return
            ctx = {"livebook": lb["name"], "lu": lu.number, "lu_title": lu.title}
            if lu.completed and self.cfg["run"]["trust_list_completion"] and not self.lu_filter:
                self.runlog.already.append({**ctx, "note": "complete on Learning Path"})
                self.runlog.row(event="skip_completed", note="complete on Learning Path", **ctx)
                continue
            try:
                self.process_lu(lb, lu, ctx)
            except LLMError as e:
                if e.fatal:
                    raise
                self._error(ctx, e)
            except (UnexpectedState, PWError) as e:
                self._error(ctx, e)

    # ------------------------------------------------------------------ discovery

    def _report(self, frame=None) -> dict:
        scope = frame or self.page.main_frame
        ensure_helpers(scope)
        return scope.evaluate("() => window.__kqb.report()")

    def discover(self, config_path: str, max_lus: int = 6) -> None:
        """Explore without touching any quiz control; save snapshots + a JSON report."""
        report: dict = {"when": stamp(), "pages": []}
        suggestions: dict[str, str] = {}

        def snap(label: str, extra: dict | None = None, frame=None):
            base = save_snapshot(self.page, self.snap_dir, label)
            entry = {"label": label, "snapshot": str(base), **(extra or {})}
            try:
                entry["page"] = self._report(frame)
            except PWError as e:
                entry["page_error"] = str(e)
            report["pages"].append(entry)

        def suggest(key: str, attr: str, frame=None):
            scope = frame or self.page.main_frame
            sel = scope.evaluate("a => window.__kqb.suggest(a)", attr)
            if sel:
                suggestions[key] = sel

        lbs = self._livebooks()
        snap("livebooks", {"livebooks": lbs})
        suggest("livebook_card", "data-kqb-lb")
        if not lbs:
            print("No livebook cards detected. Check snapshots/ and set selectors.livebook_card.")
            return self._write_report(report, suggestions, config_path)

        lb = lbs[0]
        self.open_livebook(lb)
        snap(f"livebook_{lb['name']}")
        lus = self.open_learning_path()
        snap("learning_path", {"lus": [lu.__dict__ for lu in lus]})
        suggest("lu_row", "data-kqb-lurow")
        print(f"\n{lb['name']}: {len(lus)} LUs detected (done/todo is read from the Learning Path):")
        for lu in lus:
            print(self._lu_line(lu))
        if not lus:
            print("No LUs detected. Check the learning_path snapshot and set selectors.lu_row.")
            return self._write_report(report, suggestions, config_path)

        todo = [x for x in lus if not x.completed][:max_lus]
        done = [x for x in lus if x.completed][:2]   # to capture a result screen
        print(f"\nVisiting {len(todo)} unfinished and {len(done)} finished LUs (read-only):")
        for lu in todo + done:
            try:
                self.open_lu(lb, lu)
                frame, st, completed = self.wait_lu_content()
                feats = self._eval("cfg => window.__kqb.luFeatures(cfg)", self.jscfg)
            except (UnexpectedState, PWError) as e:
                print(f"    {lu.number}: could not open ({e})")
                continue
            kind = ("quiz already submitted" if completed else
                    "quiz behind a Start button" if st["kind"] == "start" else
                    "quiz question visible" if st["kind"] == "question" else
                    "coding task" if feats["coding"] else "written task" if feats["written"] else "no quiz found")
            print(f"    {lu.number:>5}  {kind}")
            info = {"lu": lu.__dict__, "classified": kind, "features": feats,
                    "quiz_state": {k: st.get(k) for k in ("kind", "source", "question", "code", "options",
                                                         "multi", "progress", "start", "retake", "completed",
                                                         "next", "submitBtn")}}
            if st["kind"] == "question":
                print(f"           Q: {st['question'][:90]}")
                for i, o in enumerate(st["options"]):
                    print(f"           [{i}] {o[:80]}")
                suggest("quiz_option", "data-kqb-opt", frame)
            snap(f"lu_{lu.number}", info, frame if frame is not self.page.main_frame else None)
        self._write_report(report, suggestions, config_path)

    def _write_report(self, report: dict, suggestions: dict, config_path: str) -> None:
        report["suggested_selectors"] = suggestions
        path = self.snap_dir / f"{stamp()}_discovery_report.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nDiscovery report: {path}")
        auto = {k: v for k, v in suggestions.items() if k in AUTO_WRITE}
        written = apply_selector_suggestions(Path(config_path), auto)
        for key, sel in suggestions.items():
            if key in written:
                state = "written to config.yaml"
            elif key in AUTO_WRITE:
                state = "not written (config already has a value)"
            else:
                state = "suggestion only; the built-in detection already handles it"
            print(f"  selectors.{key}: {sel}   <- {state}")
        if not suggestions:
            print("  No stable data-attribute/role selectors found; built-in heuristics will be used.")
        print("\nNext: python main.py --dry-run --limit 1")


# Only these are written automatically: they were verified to match exactly the
# cards/rows found on the live page. Quiz selectors stay suggestions, because one
# quiz's markup may not fit every quiz.
AUTO_WRITE = ("livebook_card", "lu_row")


def apply_selector_suggestions(config_path: Path, suggestions: dict) -> list[str]:
    """Fill empty selectors.<key>: "" entries in config.yaml; never overwrite a set value."""
    if not suggestions:
        return []
    original = config_path.read_text(encoding="utf-8")
    text = original
    written = []
    for key, sel in suggestions.items():
        quoted = "'" + sel.replace("'", "''") + "'"
        pat = re.compile(rf'^(\s+{re.escape(key)}:[ \t]*)(""|\'\')', re.M)
        text, n = pat.subn(lambda m: m.group(1) + quoted, text, count=1)
        if n:
            written.append(key)
    if not written:
        return []
    try:
        cfg = yaml.safe_load(text)
        assert all(cfg["selectors"][k] == suggestions[k] for k in written)
    except Exception as e:   # never leave a broken config behind
        log.warning("not writing selector suggestions: %s", e)
        return []
    config_path.write_text(text, encoding="utf-8")
    return written
