"""Navigator: login, livebooks, Learning Path, LUs, and handing each LU to the solvers."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout

from .browser import PageOps, UnexpectedState, first_line, livebooks_url
from .config import save_overrides, stamp
from .llm import LLMError
from .quiz import QuizSolver
from .tasks import TaskSolver, TaskView

log = logging.getLogger("kalbot.portal")

# Only these are written to config.yaml by `discover`: they were verified to match
# exactly the cards/rows found on the live page. Quiz/task selectors stay
# suggestions, because one quiz's markup may not fit every quiz.
AUTO_WRITE = ("livebook_card", "lu_row")


@dataclass
class LU:
    index: int
    number: str
    title: str
    href: str
    text: str
    completed: bool
    type_hint: str      # quiz | written | coding | ""


@dataclass
class LUView:
    """What an LU page shows right now."""
    frame: object
    quiz: dict
    quiz_done: bool
    tasks: TaskView | None

    @property
    def quiz_kind(self) -> str:
        return self.quiz.get("kind", "none")

    @property
    def open_groups(self) -> list:
        return self.tasks.open if self.tasks else []

    @property
    def editable_groups(self) -> list:
        """Assignments with boxes you can type in (after a Retake the old "done" text is stale)."""
        return [g for g in self.tasks.groups if not all(f.locked for f in g.fields)] if self.tasks else []

    @property
    def ready(self) -> bool:
        return self.quiz_kind in ("question", "start") or self.quiz_done or bool(self.quiz.get("retake")) \
            or bool(self.tasks)

    def describe(self) -> str:
        if self.quiz_done and not self.open_groups:
            return "quiz already submitted"
        if self.quiz_kind == "start" and not self.open_groups:
            return "behind a Start button"
        if self.quiz_kind == "question":
            return "quiz question visible"
        if self.open_groups:
            return "assignment: " + " | ".join(g.describe() for g in self.open_groups)
        if self.tasks and (self.tasks.done or self.tasks.groups):
            return "assignment already submitted"
        return "no quiz or assignment found"


class Navigator:
    def __init__(self, cfg: dict, ops: PageOps, quiz: QuizSolver, tasks: TaskSolver, runlog, ui, *,
                 run_dir: Path, dry_run: bool, limit: int, livebook: str | None, lu: str | None,
                 enabled: dict[str, bool]):
        self.cfg = cfg
        self.ops = ops
        self.page = ops.page
        self.quiz = quiz
        self.tasks = tasks
        self.runlog = runlog
        self.ui = ui
        self.dry_run = dry_run
        self.limit = limit
        self.enabled = enabled
        self.livebook_filter = (livebook or "").strip().lower()
        self.lu_filter = (lu or "").strip()
        self.t = cfg["timeouts"]
        self.url = livebooks_url(cfg)
        self.host = urlparse(cfg["portal"]["base_url"]).netloc
        self.err_dir = run_dir / "errors"
        self.snap_dir = run_dir / "snapshots"
        self.handled = 0
        pats = cfg["patterns"]
        rx = lambda key: [re.compile(p, re.I) for p in pats[key]]  # noqa: E731
        self.re_done, self.re_not_done = rx("lu_completed"), rx("lu_not_completed")
        self.re_quiz, self.re_written, self.re_coding = rx("lu_quiz"), rx("lu_written"), rx("lu_coding")

    # ------------------------------------------------------------------ small helpers

    def _call(self, fn: str, *args):
        return self.ops.call(self.page.main_frame, fn, *args)

    def _limit_reached(self) -> bool:
        return bool(self.limit) and self.handled >= self.limit

    def _error(self, ctx: dict, exc: Exception) -> None:
        msg = f"{type(exc).__name__}: {first_line(exc)}"
        base = self.ops.snapshot(self.err_dir, f"{ctx.get('livebook', '')}_{ctx.get('lu', '')}")
        self.ui.error(f"{msg}  (screenshot: {base}.png) - skipping", indent=1)
        log.error("%s | %s", ctx, msg)
        self.runlog.add(ctx, "error", detail=msg, url=self.page.url)
        self.runlog.row(event="error", note=f"{msg} [{base.name}]", url=self.page.url, **ctx)

    # ------------------------------------------------------------------ login

    def _is_login(self, url: str) -> bool:
        u = url.lower()
        return any(p.lower() in u for p in self.cfg["patterns"]["login_url"])

    def list_livebooks(self) -> list[dict]:
        return self._call("listLivebooks", self.ops.jscfg)

    def wait_for_login(self) -> None:
        self.ops.goto(self.url)
        if not self._is_login(self.page.url) and self.ops.poll(self.list_livebooks, 8000):
            self.ui.ok("Logged in.")
            return
        mins = float(self.cfg["run"]["login_timeout_minutes"])
        if urlparse(self.page.url).netloc == self.host and not self._is_login(self.page.url):
            self.ui.note("Waiting for the livebooks list to appear...")
        else:
            self.ui.warn("NOT LOGGED IN. Log in to Kalvium in the Chrome window that just opened.")
            self.ui.note(f"Waiting up to {mins:g} minutes for {self.url} to show your livebooks...", indent=1)
            self.ui.note("If Google blocks the sign-in there, close this, run `kalbot login`, then run again.",
                         indent=1)
        deadline = time.monotonic() + mins * 60
        last_nav = 0.0
        on_list_since = None
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(2000)
            url = self.page.url
            if urlparse(url).netloc != self.host or self._is_login(url):
                on_list_since = None
                continue
            if self.ops.poll(self.list_livebooks, 2000):
                self.ui.ok("Login detected.")
                return
            if "/livebooks" in urlparse(url).path:
                on_list_since = on_list_since or time.monotonic()
                if time.monotonic() - on_list_since > 25:
                    self._no_cards()
            elif time.monotonic() - last_nav > 10:
                last_nav = time.monotonic()   # logged in but landed elsewhere
                try:
                    self.ops.goto(self.url)
                except PWError:
                    pass
        raise SystemExit("Timed out waiting for login. Run again, or use `kalbot login`.")

    def _no_cards(self) -> None:
        """Logged in and on the livebooks page, but no cards recognised: stop with a clear hint."""
        base = self.ops.snapshot(self.snap_dir, "livebooks_no_cards")
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
            self.ops.goto(lb["url"])
        elif lb.get("href"):
            self.ops.goto(lb["href"])
        else:
            self.ops.goto(self.url)
            self.ops.poll(self.list_livebooks, self.t["list_ms"])
            cards = self.list_livebooks()
            match = next((c for c in cards if c["name"] == lb["name"]), None)
            if not match:
                raise UnexpectedState(f"livebook card '{lb['name']}' not found")
            old = self.page.url
            self.ops.click(None, f'[data-kqb-lb="{match["index"]}"]', f"livebook {lb['name']}")
            try:
                self.page.wait_for_url(lambda u: u != old, timeout=self.t["navigation_ms"])
            except PWTimeout:
                raise UnexpectedState("clicking the livebook card did not open it") from None
            self.ops.settle()
        lb["url"] = self.page.url
        self.ops.pause()

    def list_lus(self) -> list[LU]:
        out = []
        for r in self._call("listLUs", self.ops.jscfg):
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
        rendered = lambda: self.list_lus() or self._call("collapsedModules", self.ops.jscfg)  # noqa: E731
        # the page may still be loading: wait for the tab (or a path that is already shown)
        how = self.ops.poll(lambda: ("tab" if tab and self.ops.click_text(tab) else None) or
                            ("shown" if rendered() else None), self.t["list_ms"])
        if how == "tab":
            self.ops.pause()
            self.ops.settle()
        # wait until the path has rendered: LU rows or (all-collapsed) module toggles
        self.ops.poll(rendered, self.t["list_ms"])
        clicked: set[str] = set()
        for _ in range(40):   # expand collapsed modules one at a time (DOM re-renders)
            if not self._call("collapsedModules", self.ops.jscfg):
                break
            texts = self.ops.eval(None, "() => [...document.querySelectorAll('[data-kqb-mod]')]"
                                        ".map(e => e.innerText.trim().slice(0, 80))")
            todo = [i for i, t in enumerate(texts) if t not in clicked]
            if not todo:
                break
            clicked.add(texts[todo[0]])
            self.ops.click(None, f'[data-kqb-mod="{todo[0]}"]', f"module '{texts[todo[0]]}'")
            self.page.wait_for_timeout(600)
        return self.ops.poll(self.list_lus, self.t["list_ms"]) or []

    # ------------------------------------------------------------------ one LU

    def open_lu(self, lb: dict, lu: LU) -> None:
        self.ops.close_overlay()   # an assignment workspace left open by the previous LU
        if lu.href:
            self.ops.goto(lu.href)
        else:
            self.open_livebook(lb)
            self.ops.close_overlay()
            fresh = {x.number: x for x in self.open_learning_path()}
            if lu.number not in fresh:
                raise UnexpectedState(f"LU {lu.number} not found on the Learning Path")
            old = self.page.url
            self.ops.click(None, f'[data-kqb-lu="{fresh[lu.number].index}"]', f"LU {lu.number}")
            try:
                self.page.wait_for_url(lambda u: u != old, timeout=8000)
            except PWTimeout:
                pass   # LU may open in place (panel/drawer)
            self.ops.settle()
        self.ops.pause()
        self.go_to_lessons()

    def go_to_lessons(self) -> None:
        """An LU opens on an overview card; its lessons, quizzes and assignments are under /lessons."""
        if urlparse(self.page.url).path.rstrip("/").endswith("/lessons"):
            return
        try:
            if re.search(r"/livebooks/\d+/[0-9a-f-]{20,}$", urlparse(self.page.url).path.rstrip("/")):
                self.ops.goto(self.page.url.split("?")[0].rstrip("/") + "/lessons")
                return
            btn = self.page.locator('button[aria-label="Go to Lessons"], a[href$="/lessons"], '
                                    'button:has-text("Go to Lessons"), [role="button"]:has-text("Go to Lessons")').first
            # the SPA renders late: wait for the button, unless the LU's content shows up first
            if self.ops.poll(lambda: btn.is_visible() or self.inspect().ready, 3000) and btn.is_visible():
                btn.click()
                self.ops.settle()
                self.ops.pause()
        except PWError as e:
            log.debug("lessons navigation: %s", first_line(e))

    def inspect(self) -> LUView:
        frame, st, done = self.quiz.locate(self.page)
        return LUView(frame, st, done, self.tasks.inspect(self.page))

    def wait_lu_content(self) -> LUView:
        """Wait for the LU to render; look behind in-page tabs and lazy sections too."""
        last: dict = {}
        start = time.monotonic()

        def check():
            last["v"] = self.inspect()
            return last["v"] if last["v"].ready else None

        def check_or_quiet():
            # a plain reading page: stop waiting once its text has stopped changing
            if check():
                return last["v"]
            n, now = len(self.lu_material()), time.monotonic()
            if n != last.get("n"):
                last["n"], last["since"] = n, now
            elif n > 200 and now - start > 3 and now - last["since"] > 2.5:   # not a spinner
                return "quiet"
            return None

        got = self.ops.poll(check_or_quiet, self.t["lu_settle_ms"])
        if isinstance(got, LUView):
            return got
        tab = self._call("markInPageTab", [str(x) for x in self.cfg["texts"]["tabs"]])
        if tab:
            self.ops.click(None, '[data-kqb-tab="1"]', f"tab '{tab}'")
            self.ops.pause()
            got = self.ops.poll(check, 5000)
            if got:
                return got
        self._call("scrollAll")
        self.ops.pause()
        return self.ops.poll(check, 3000) or last.get("v") or self.inspect()

    def _started(self) -> LUView | None:
        v = self.inspect()
        return v if v.quiz_kind == "question" or v.open_groups else None

    def _started_or_confirm(self) -> LUView | None:
        self.ops.confirm(self.cfg["texts"]["proceed"], "confirm Start")   # "Proceed?" after Start Assignment
        return self._started()

    def press_retake(self) -> LUView:
        """Click Retake (a quiz or an assignment), confirm, and wait for questions or answer boxes."""
        frame = next((f for f in self.ops.frames() if self.quiz.state(f).get("retake")), None)
        if frame is None:
            raise UnexpectedState("Retake button disappeared")
        self.ops.click(frame, '[data-kqb-btn="retake"]', "Retake")
        self.ops.pause()

        def ready():
            self.ops.confirm(self.cfg["texts"]["proceed"], "confirm Retake")
            v = self.inspect()
            return v if v.quiz_kind in ("question", "start") or v.editable_groups else None

        v = self.ops.poll(ready, self.t["question_change_ms"])
        if v is None:
            raise UnexpectedState("clicked Retake but no quiz or assignment appeared")
        return self.press_start() if v.quiz_kind == "start" and not v.editable_groups else v

    def press_start(self) -> LUView:
        """Click Start (quiz or assignment) until a question or an answer box appears."""
        for _ in range(3):   # some have an instructions screen with a second Start
            frame, st, _ = self.quiz.locate(self.page)
            if st["kind"] != "start":
                break
            self.ops.click(frame, '[data-kqb-btn="start"]', "Start")
            self.ops.pause()
            got = self.ops.poll(self._started_or_confirm, self.t["question_change_ms"])
            if got:
                return got
        raise UnexpectedState("clicked Start but no quiz or assignment appeared")

    def lu_material(self) -> str:
        try:
            return self._call("mainText") or ""
        except PWError:
            return ""

    def process_lu(self, lb: dict, lu: LU, ctx: dict) -> None:
        self.ui.lu(lu.number, lu.title)
        self.open_lu(lb, lu)
        v = self.wait_lu_content()
        material = self.lu_material()
        handled = False

        improve = v.quiz_done and self.quiz.can_improve(v.quiz)   # --retake, no full marks, Retake offered
        retakes_quiz = "assignment" not in v.quiz.get("retakeText", "").lower()
        if improve and not (self.enabled["quiz"] if retakes_quiz else
                            any(self.enabled[k] for k in ("written", "coding", "links"))):
            self.runlog.add(ctx, "skipped", "quiz" if retakes_quiz else "", "retake not selected (--only)")
            return
        if improve:
            if self.dry_run and not self.cfg["run"]["dry_run_click_start"]:
                self.ui.note("[dry-run] submitted without full marks; Retake not clicked "
                             "(it would replace the recorded attempt)", indent=1)
                self.runlog.row(event="dry_run", note="retake not clicked", url=self.page.url, **ctx)
                self.runlog.add(ctx, "preview", lu.type_hint, "retake available (not clicked in dry-run)")
                self.handled += 1
                return
            self.ui.step("Submitted before without full marks; retaking (--retake).", indent=1)
            v = self.press_retake()
            material = self.lu_material() or material

        if v.quiz_kind == "start" and not v.quiz_done and not v.open_groups:
            if lu.type_hint == "quiz" and not self.enabled["quiz"]:
                self.runlog.add(ctx, "skipped", "quiz", "quiz not selected (--only)")
                return
            if self.dry_run and not self.cfg["run"]["dry_run_click_start"]:
                self.ui.note("[dry-run] behind a Start button; not clicking it "
                             "(set run.dry_run_click_start: true to preview what's behind it)", indent=1)
                self.runlog.row(event="dry_run", note="start button not clicked", url=self.page.url, **ctx)
                self.runlog.add(ctx, "preview", lu.type_hint, "behind a Start button (not clicked in dry-run)")
                self.handled += 1
                return
            v = self.press_start()
            material = self.lu_material() or material

        if v.quiz_kind == "question" and (not v.quiz_done or improve):
            handled = True
            if not self.enabled["quiz"]:
                self.runlog.add(ctx, "skipped", "quiz", "quiz not selected (--only)")
            else:
                self._record_quiz(ctx, self.quiz.run(self.page, ctx, material, fresh=improve))
                v = self.inspect() if not self._limit_reached() else v
        elif v.quiz_done and v.quiz_kind != "question":
            perfect = " with full marks" if v.quiz.get("maxScore") else ""
            self.ui.note(f"Quiz already submitted{perfect}.", indent=1)

        outcomes = []
        if v.tasks and v.tasks.groups and not self._limit_reached():
            outcomes = self.tasks.run(self.page, ctx, material, v.tasks, fresh=improve)
            for o in outcomes:
                self._record_task(ctx, o)
        if handled or any(o.status != "done" for o in outcomes):
            return

        if outcomes or v.quiz_done or (v.tasks and v.tasks.done):
            if not outcomes:
                self.runlog.add(ctx, "done", "quiz" if v.quiz_done else "", "already submitted", self.page.url)
            self.runlog.row(event="skip_completed", note="already submitted", url=self.page.url, **ctx)
            if not lu.completed:   # still open on the Learning Path, so something else is left
                self.ui.warn("Already submitted, but the LU is still open: something else is left.", indent=1)
                self.runlog.add(ctx, "manual", lu.type_hint or "other",
                                "LU still open after its submission (check what's left)", self.page.url)
            return
        if lu.type_hint in ("coding", "written"):
            self.ui.warn(f"Looks like a {lu.type_hint} task, but no answer box was recognised.", indent=1)
            self.runlog.add(ctx, "manual", lu.type_hint, "no answer box recognised (see the snapshot)", self.page.url)
            self.ops.snapshot(self.snap_dir, f"unrecognised_{ctx['livebook']}_{ctx['lu']}")
        else:
            self.ui.note("No quiz or assignment found.", indent=1)
            self.runlog.add(ctx, "none", "", "no quiz or assignment found", self.page.url)
        self.runlog.row(event="no_task", url=self.page.url, **ctx)

    def _record_quiz(self, ctx: dict, out) -> None:
        if out.status == "submitted":
            self.handled += 1
            self.runlog.add(ctx, "submitted", "quiz", out.result, self.page.url, out.items)
        elif out.status == "dry_run":
            self.handled += 1
            self.runlog.add(ctx, "preview", "quiz", "question 1 read (dry-run)", self.page.url, out.items)
        elif out.status == "needs_retake":
            self.handled += 1
            self.runlog.add(ctx, "preview", "quiz", "retake available (not clicked in dry-run)", self.page.url)
        elif out.status == "checks_only":
            self.runlog.add(ctx, "done", "quiz", out.result, self.page.url, out.items)
        elif out.status == "already_done":
            self.runlog.add(ctx, "done", "quiz", "already submitted", self.page.url)
        else:
            self.runlog.add(ctx, "none", "quiz", "quiz not readable", self.page.url)

    def _record_task(self, ctx: dict, o) -> None:
        if o.status in ("submitted", "preview"):
            self.handled += 1
        self.runlog.add(ctx, o.status, o.kind, o.detail, self.page.url, o.items)

    # ------------------------------------------------------------------ full run

    def _livebooks(self) -> list[dict]:
        lbs = self.list_livebooks()
        self.ui.say(f"\nFound {len(lbs)} livebooks in semester {self.cfg['portal']['semester']}:")
        for lb in lbs:
            self.ui.say(f"- {lb['name']}", indent=1)
        if self.livebook_filter:
            lbs = [lb for lb in lbs if self.livebook_filter in lb["name"].lower()]
            if not lbs:
                self.ui.warn(f"No livebook name contains '{self.livebook_filter}'.")
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
            self.ui.note(f"\nReached --limit {self.limit}.")

    def process_livebook(self, lb: dict) -> None:
        self.ui.section(lb["name"])
        self.open_livebook(lb)
        lus = self.open_learning_path()
        if not lus:
            raise UnexpectedState("no LUs found on the Learning Path")
        self.ui.say(f"  {len(lus)} LUs on the Learning Path:")
        self.ui.lu_list(lus)
        if self.lu_filter:
            lus = [x for x in lus if x.number == self.lu_filter]
            if not lus:
                self.ui.warn(f"LU {self.lu_filter} not in this livebook.", indent=1)
        for lu in lus:
            if self._limit_reached():
                return
            ctx = {"livebook": lb["name"], "lu": lu.number, "lu_title": lu.title}
            retake = self.cfg["run"].get("retake_completed")   # a complete LU may hold a quiz without full marks
            if lu.completed and self.cfg["run"]["trust_list_completion"] and not self.lu_filter and not retake:
                self.runlog.add(ctx, "done", lu.type_hint, "complete on the Learning Path")
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
        return self.ops.call(frame or self.page.main_frame, "report")

    def discover(self, config_path: Path, max_lus: int = 8) -> None:
        """Explore without touching any quiz or assignment control; save snapshots + a JSON report."""
        report: dict = {"when": stamp(), "pages": []}
        suggestions: dict[str, str] = {}

        def snap(label: str, extra: dict | None = None, frame=None):
            base = self.ops.snapshot(self.snap_dir, label)
            entry = {"label": label, "snapshot": str(base), **(extra or {})}
            try:
                entry["page"] = self._report(frame)
            except PWError as e:
                entry["page_error"] = str(e)
            report["pages"].append(entry)

        def suggest(key: str, attr: str, frame=None):
            sel = self.ops.call(frame or self.page.main_frame, "suggest", attr)
            if sel:
                suggestions[key] = sel

        lbs = self._livebooks()
        snap("livebooks", {"livebooks": lbs})
        suggest("livebook_card", "data-kqb-lb")
        if not lbs:
            self.ui.warn("No livebook cards detected. Check the snapshots and set selectors.livebook_card.")
            return self._write_report(report, suggestions, config_path)

        lb = lbs[0]
        self.open_livebook(lb)
        snap(f"livebook_{lb['name']}")
        lus = self.open_learning_path()
        snap("learning_path", {"lus": [lu.__dict__ for lu in lus]})
        suggest("lu_row", "data-kqb-lurow")
        self.ui.say(f"\n{lb['name']}: {len(lus)} LUs detected (done/todo is read from the Learning Path):")
        self.ui.lu_list(lus)
        if not lus:
            self.ui.warn("No LUs detected. Check the learning_path snapshot and set selectors.lu_row.")
            return self._write_report(report, suggestions, config_path)

        todo = [x for x in lus if not x.completed][:max_lus]
        done = [x for x in lus if x.completed][:2]   # to capture a result screen
        self.ui.say(f"\nVisiting {len(todo)} unfinished and {len(done)} finished LUs (read-only):")
        for lu in todo + done:
            try:
                self.open_lu(lb, lu)
                v = self.wait_lu_content()
            except (UnexpectedState, PWError) as e:
                self.ui.warn(f"{lu.number}: could not open ({first_line(e)})", indent=2)
                continue
            kind = v.describe()
            self.ui.say(f"{lu.number:>5}  {kind}", indent=2)
            st = v.quiz
            info = {"lu": lu.__dict__, "classified": kind,
                    "quiz_state": {k: st.get(k) for k in ("kind", "source", "question", "code", "options", "multi",
                                                         "progress", "start", "retake", "completed", "next",
                                                         "submitBtn")},
                    "assignments": [{"submit": g.submit_text, "done": g.done,
                                     "fields": [{k: getattr(f, k) for k in ("kind", "link_kind", "tag", "editor",
                                                                            "label", "language", "locked")}
                                                | {"prompt": f.prompt[:300]} for f in g.fields]}
                                    for g in (v.tasks.groups if v.tasks else [])]}
            if st["kind"] == "question":
                self.ui.say(f"Q: {st['question'][:90]}", "muted", indent=5)
                for i, o in enumerate(st["options"]):
                    self.ui.say(f"[{i}] {o[:80]}", "muted", indent=5)
                suggest("quiz_option", "data-kqb-opt", v.frame)
            for g in v.open_groups:
                for f in g.fields:
                    self.ui.say(f"- {f.kind}{'/' + f.link_kind if f.link_kind else ''}: {f.title}", "muted", indent=5)
            if v.tasks and v.tasks.groups:
                suggest("task_field", "data-kqb-field", v.tasks.frame)
            snap(f"lu_{lu.number}", info, v.frame if v.frame is not self.page.main_frame else None)
        self._write_report(report, suggestions, config_path)

    def _write_report(self, report: dict, suggestions: dict, config_path: Path) -> None:
        report["suggested_selectors"] = suggestions
        path = self.snap_dir / f"{stamp()}_discovery_report.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self.ui.say(f"\nDiscovery report: {path}")
        auto = {k: v for k, v in suggestions.items() if k in AUTO_WRITE}
        written = save_overrides(Path(config_path), "selectors", auto)
        for key, sel in suggestions.items():
            if key in written:
                state = f"written to {Path(config_path).name}"
            elif key in AUTO_WRITE:
                state = "not written (config already has a value)"
            else:
                state = "suggestion only; the built-in detection already handles it"
            self.ui.say(f"selectors.{key}: {sel}   <- {state}", indent=1)
        if not suggestions:
            self.ui.note("No stable data-attribute/role selectors found; built-in heuristics will be used.", indent=1)
        self.ui.say("\nNext: kalbot run --dry-run --limit 1")
