"""Quiz solver: read a question, ask the LLM, click the answer, verify, move on.

The page analysis lives in dom_helpers.js (window.__kqb). It marks the options
and buttons it identified with data-kqb-* attributes, and this module only ever
clicks those marked elements, so nothing is clicked on a guess.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout

from common import Pacer, UnexpectedState, ensure_helpers, folder, js_config, save_snapshot
from llm import AnswerPicker, Question

log = logging.getLogger("kqb.quiz")


@dataclass
class QuizOutcome:
    status: str          # submitted | dry_run | needs_start | already_done | no_quiz
    result: str = ""
    attempts: int = 0
    questions: int = 0


def _short(text: str, n: int = 110) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _num(x) -> str:
    return str(int(x)) if x is not None and float(x).is_integer() else str(x)


def describe(r: dict | None) -> str:
    if not r or not r.get("found"):
        return "submitted (no score shown)"
    parts = []
    if r.get("score") is not None:
        parts.append(f"{_num(r['score'])}/{_num(r['total'])}")
    elif r.get("percent") is not None:
        parts.append(f"{_num(r['percent'])}%")
    if r.get("fail"):
        parts.append("FAILED")
    elif r.get("pass"):
        parts.append("passed")
    if r.get("snippet"):
        parts.append(f'"{r["snippet"]}"')
    return " ".join(parts)


class QuizSolver:
    def __init__(self, cfg: dict, picker: AnswerPicker | None, runlog, dry_run: bool):
        self.cfg = cfg
        self.jscfg = js_config(cfg)
        self.picker = picker
        self.runlog = runlog
        self.dry_run = dry_run
        self.pacer = Pacer(cfg)
        self.t = cfg["timeouts"]
        self.opts = cfg["run"]
        self.snap_dir = folder(cfg, "snapshots")
        self.expect_confirm = False   # read by the native-dialog handler in navigator.py

    # ------------------------------------------------------------------ page analysis

    def _eval(self, scope, expr: str, arg=None):
        ensure_helpers(scope)
        return scope.evaluate(expr, arg)

    def state(self, scope) -> dict:
        return self._eval(scope, "cfg => window.__kqb.state(cfg)", self.jscfg)

    def locate(self, page):
        """Best quiz state across the page and its iframes.

        Returns (frame, state, completed) where completed is True when any frame
        shows an already-submitted quiz (score text or a Retake button).
        """
        best = None
        completed = False
        for frame in page.frames:
            try:
                st = self.state(frame)
            except PWError as e:
                log.debug("frame %s not readable: %s", frame.url, e)
                continue
            completed = completed or bool(st.get("completed") or st.get("retake"))
            rank = {"question": 2, "start": 1}.get(st["kind"], 0)
            if best is None or rank > best[0]:
                best = (rank, frame, st)
        if best is None:
            raise UnexpectedState("page is not readable")
        return best[1], best[2], completed

    def _poll(self, page, fn, timeout_ms: float, interval_ms: int = 300):
        """Call fn until it returns something truthy or the timeout passes."""
        deadline = time.monotonic() + timeout_ms / 1000
        seen: set[str] = set()
        while True:
            try:
                v = fn()
                if v:
                    return v
            except PWError as e:
                msg = str(e).splitlines()[0]
                if any(s in msg for s in ("context was destroyed", "navigat", "detached", "closed")):
                    log.debug("poll: %s", msg)   # page navigating between checks
                elif msg not in seen:
                    seen.add(msg)
                    log.warning("page check failed: %s", msg)
            if time.monotonic() > deadline:
                return None
            page.wait_for_timeout(interval_ms)

    def _question(self, page, not_fp: str | None = None):
        frame, st, _ = self.locate(page)
        if st["kind"] == "question" and st["fingerprint"] != not_fp:
            return frame, st
        return None

    def _question_or_start(self, page):
        frame, st, _ = self.locate(page)
        return (frame, st) if st["kind"] in ("question", "start") else None

    def _click(self, scope, selector: str, what: str) -> None:
        loc = scope.locator(selector).first
        try:
            loc.scroll_into_view_if_needed(timeout=self.t["action_ms"])
            loc.click(timeout=self.t["action_ms"])
        except PWError as e:
            raise UnexpectedState(f"could not click {what}: {str(e).splitlines()[0]}") from None
        log.info("clicked %s", what)

    # ------------------------------------------------------------------ main entry

    def run(self, page, ctx: dict) -> QuizOutcome:
        frame, st, completed = self.locate(page)
        if completed:
            return QuizOutcome("already_done")
        if st["kind"] == "none":
            return QuizOutcome("no_quiz")
        if st["kind"] == "start":
            if self.dry_run and not self.opts.get("dry_run_click_start"):
                print("     [dry-run] quiz is behind a Start button; not clicking it "
                      "(set run.dry_run_click_start: true to preview question 1)")
                self.runlog.row(event="dry_run", note="start button not clicked", url=page.url, **ctx)
                return QuizOutcome("needs_start")
            frame, st = self._start(page)
        return self._attempts(page, frame, st, ctx)

    def _start(self, page):
        for _ in range(3):   # some quizzes have an instructions screen with a second Start
            frame, st, _ = self.locate(page)
            if st["kind"] == "question":
                return frame, st
            if st["kind"] != "start":
                break
            self._click(frame, '[data-kqb-btn="start"]', "Start")
            self.pacer.pause(page)
            got = self._poll(page, lambda: self._question(page), self.t["question_change_ms"])
            if got:
                return got
        raise UnexpectedState("clicked Start but no question appeared")

    def _attempts(self, page, frame, st, ctx) -> QuizOutcome:
        max_attempts = 2 if self.opts.get("retake_on_fail", True) else 1
        previous: dict[str, list[int]] = {}
        outcome = QuizOutcome("submitted")
        for attempt in range(1, max_attempts + 1):
            answers, result = self._one_attempt(page, frame, st, ctx, attempt, previous)
            if self.dry_run:
                return QuizOutcome("dry_run", questions=len(answers))
            outcome = QuizOutcome("submitted", describe(result), attempt, len(answers))
            print(f"     Result (attempt {attempt}): {outcome.result}")
            self.runlog.row(event="result", attempt=attempt, result=outcome.result,
                            note=" / ".join((result or {}).get("fresh", [])[:6]), url=page.url, **ctx)
            if self.opts.get("snapshot_quiz_pages"):
                save_snapshot(page, self.snap_dir, f"result_{ctx['livebook']}_{ctx['lu']}_a{attempt}")
            if not (self._failed(result) and attempt < max_attempts):
                break
            if not result.get("retake"):
                print("     Quiz failed but no Retake button is offered.")
                break
            print("     Quiz failed; retaking once.")
            previous = answers
            frame, st = self._retake(page)
        return outcome

    def _has_retake(self, frame) -> bool:
        try:
            return bool(self.state(frame).get("retake"))   # state() marks the button
        except PWError:
            return False

    def _failed(self, r: dict | None) -> bool:
        if not r or not r.get("found"):
            return False
        if r.get("fail"):
            return True
        if r.get("pass"):
            return False
        frac = float(self.opts.get("pass_fraction", 0.6))
        if r.get("score") is not None and r.get("total"):
            return r["score"] / r["total"] < frac
        if r.get("percent") is not None:
            return r["percent"] < frac * 100
        return False

    def _retake(self, page):
        frame = next((f for f in page.frames if self._has_retake(f)), None)
        if frame is None:
            raise UnexpectedState("Retake button disappeared")
        self._click(frame, '[data-kqb-btn="retake"]', "Retake")
        self.pacer.pause(page)
        got = self._poll(page, lambda: self._question_or_start(page), self.t["question_change_ms"])
        if not got:
            raise UnexpectedState("clicked Retake but no quiz appeared")
        frame, st = got
        if st["kind"] == "start":
            frame, st = self._start(page)
        return frame, st

    # ------------------------------------------------------------------ one pass through the quiz

    def _one_attempt(self, page, frame, st, ctx, attempt, previous):
        answers: dict[str, list[int]] = {}
        prev_fp = None
        context = f"{ctx['livebook']} - LU {ctx['lu']} {ctx['lu_title']}".strip()
        max_q = int(self.opts.get("max_questions_per_quiz", 12))
        for n in range(1, max_q + 1):
            if n > 1:
                frame, st = self._wait_next_question(page, frame, prev_fp)
                if st["kind"] != "question":
                    if st.get("submitBtn"):      # a review page with only Submit on it
                        return answers, self._submit(page, frame)
                    raise UnexpectedState(f"question {n} did not appear")

            prog = st.get("progress") or [n, None]
            q = Question(text=st["question"], options=st["options"], multi=bool(st["multi"]),
                         code=st.get("code") or [], number=prog[0], total=prog[1])
            if n == 1 and self.opts.get("snapshot_quiz_pages"):
                save_snapshot(page, self.snap_dir, f"quiz_{ctx['livebook']}_{ctx['lu']}_q1")

            ans = self.picker.choose(q, context=context, previous=previous.get(q.text))
            chosen_text = " | ".join(q.options[i] for i in ans.indices)
            total = f"/{q.total}" if q.total else ""
            print(f"     Q{q.number}{total}: {_short(q.text)}")
            for c in q.code:
                print(f"        [code] {_short(c, 90)}")
            for i, o in enumerate(q.options):
                print(f"        {'*' if i in ans.indices else ' '} [{i}] {_short(o, 90)}")
            print(f"        -> {ans.indices} ({ans.confidence}){' multi-select' if q.multi else ''}")
            self.runlog.row(
                event="dry_run_question" if self.dry_run else "question", attempt=attempt,
                q_no=q.number, question=q.text, code="\n---\n".join(q.code),
                options=" || ".join(f"[{i}] {o}" for i, o in enumerate(q.options)),
                multi=q.multi, chosen=ans.indices, chosen_text=chosen_text,
                confidence=ans.confidence, note=f"detected via {st.get('source')}", url=page.url, **ctx)
            answers[q.text] = ans.indices
            if self.dry_run:
                return answers, None

            self._select(page, frame, st, ans.indices)
            self.pacer.pause(page)

            nav = self._eval(frame, "cfg => window.__kqb.nav(cfg)", self.jscfg)
            last = bool(q.total) and q.number >= q.total
            if nav["submitBtn"] and not nav["submitDisabled"] and (
                    not nav["next"] or nav["nextDisabled"] or last):
                if n != int(self.opts.get("expected_questions", 5)):
                    log.warning("submitting after %d questions (expected %s)", n, self.opts.get("expected_questions"))
                return answers, self._submit(page, frame)
            if nav["next"] and not nav["nextDisabled"]:
                prev_fp = st["fingerprint"]
                self._click(frame, '[data-kqb-btn="next"]', "Next")
                continue
            if nav["next"] or nav["submitBtn"]:
                raise UnexpectedState("Next/Submit stayed disabled after selecting an answer")
            raise UnexpectedState("no Next or Submit button next to the question")
        raise UnexpectedState(f"more than {max_q} questions; stopping to be safe")

    def _wait_next_question(self, page, frame, prev_fp):
        """Wait for the question text/options to change after clicking Next."""
        try:
            frame.wait_for_function(
                "a => !!window.__kqb && window.__kqb.fingerprint(a.cfg) !== a.prev",
                arg={"cfg": self.jscfg, "prev": prev_fp},
                timeout=self.t["question_change_ms"], polling=250)
        except PWTimeout:
            raise UnexpectedState("question did not change after clicking Next") from None
        except PWError as e:   # frame navigated or detached; re-locate below
            log.debug("wait_for_function: %s", e)
        self.pacer.pause(page)
        # mid-transition the old question may be gone before the new one renders
        got = self._poll(page, lambda: self._question(page, not_fp=prev_fp), 5000)
        if got:
            return got
        frame, st, _ = self.locate(page)
        return frame, st

    # ------------------------------------------------------------------ selecting + verifying

    def _select(self, page, frame, st, indices: list[int]) -> None:
        wanted = set(indices)
        selected = st.get("selected") or []
        to_click = [i for i in indices if not (i < len(selected) and selected[i] is True)]
        if st["multi"]:   # untick leftovers from an earlier visit
            to_click += [i for i, s in enumerate(selected) if s is True and i not in wanted]
        for k, i in enumerate(to_click):
            if not frame.locator(f'[data-kqb-opt="{i}"]').count():
                # options were re-rendered (e.g. by React) after the previous click: re-mark them
                again = self.state(frame)
                if again.get("fingerprint") != st["fingerprint"]:
                    raise UnexpectedState("question changed while selecting options")
            before = self._eval(frame, "i => window.__kqb.selInfo(i)", i)
            page.mouse.move(2, 2)   # keep hover styles out of the before/after comparison
            self._click(frame, f'[data-kqb-opt="{i}"]', f"option {i}")
            page.mouse.move(2, 2)
            want = i in wanted
            if not self._verify(page, frame, i, before, want):
                raise UnexpectedState(f"could not verify that option {i} got {'selected' if want else 'cleared'}")
            if k + 1 < len(to_click):
                self.pacer.pause(page)

    def _verify(self, page, frame, i: int, before: dict, want: bool) -> bool:
        def check():
            info = self._eval(frame, "i => window.__kqb.selInfo(i)", i)
            if info.get("stale"):   # options re-rendered: analyse again
                st = self.state(frame)
                if st["kind"] != "question" or i >= len(st["selected"]):
                    return False
                if st["selected"][i] is not None:
                    return st["selected"][i] is want
                odd = self._eval(frame, "() => window.__kqb.oddOneOut()")
                return (i in odd) is want
            if info.get("known"):
                return bool(info.get("sel")) is want
            changed = info.get("sig") != before.get("sig")
            return changed if before.get("sig") is not None else bool(info.get("cls")) is want
        return bool(self._poll(page, check, 3000, 200))

    # ------------------------------------------------------------------ submitting

    def _submit(self, page, frame) -> dict:
        before = self._eval(frame, "() => window.__kqb.lines()")
        self._eval(frame, "s => window.__kqb.clickedMark(s)", '[data-kqb-btn="submit"]')
        self.expect_confirm = True
        confirmed = False
        try:
            self._click(frame, '[data-kqb-btn="submit"]', "Submit")
            arg = {"cfg": self.jscfg, "before": before}

            def check():
                nonlocal confirmed, frame
                if frame.is_detached():
                    frame = page.main_frame
                r = self._eval(frame, "a => window.__kqb.result(a.cfg, a.before)", arg)
                if r["found"]:
                    return r
                if not confirmed and self._eval(frame, "cfg => window.__kqb.confirm(cfg)", self.jscfg):
                    self.pacer.pause(page)
                    self._click(frame, '[data-kqb-btn="confirm"]', "confirm Submit")
                    confirmed = True
                return None

            r = self._poll(page, check, self.t["result_ms"], 400)
            if r:
                self.pacer.pause(page)   # let the result screen finish rendering, then re-read it
                try:
                    r2 = self._eval(frame, "a => window.__kqb.result(a.cfg, a.before)", arg)
                    if r2.get("found"):
                        r = r2
                except PWError:
                    pass
                return r
            _, st, _ = self.locate(page)
            if st["kind"] == "question":
                raise UnexpectedState("no result screen after Submit and the question is still showing")
            return {"found": False, "fresh": []}
        finally:
            self.expect_confirm = False
