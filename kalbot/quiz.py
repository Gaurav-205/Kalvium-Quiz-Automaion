"""Quiz solver: read a question, ask the LLM, click the answer, verify, move on.

The page analysis lives in dom.js (window.__kqb). It marks the options and
buttons it identified with data-kqb-* attributes, and this module only ever
clicks those marked elements, so nothing is clicked on a guess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from playwright.sync_api import Error as PWError

from .browser import PageOps, UnexpectedState, first_line
from .llm import LLM, Question

log = logging.getLogger("kalbot.quiz")


@dataclass
class QuizOutcome:
    status: str          # submitted | dry_run | already_done | no_quiz
    result: str = ""
    attempts: int = 0
    items: list[dict] = field(default_factory=list)


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
    def __init__(self, cfg: dict, ops: PageOps, llm: LLM | None, runlog, ui, snap_dir, dry_run: bool):
        self.cfg = cfg
        self.ops = ops
        self.llm = llm
        self.runlog = runlog
        self.ui = ui
        self.dry_run = dry_run
        self.t = cfg["timeouts"]
        self.opts = cfg["run"]
        self.snap_dir = snap_dir

    # ------------------------------------------------------------------ page analysis

    def state(self, scope) -> dict:
        return self.ops.call(scope, "state", self.ops.jscfg)

    def locate(self, page):
        """Best quiz state across the page and its iframes.

        Returns (frame, state, completed) where completed is True when any frame
        shows an already-submitted quiz (score text or a Retake button).
        """
        best = None
        completed = False
        for frame in self.ops.frames():
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

    def _question(self, page, not_fp: str | None = None):
        frame, st, _ = self.locate(page)
        if st["kind"] == "question" and st["fingerprint"] != not_fp:
            return frame, st
        return None

    def _question_or_start(self, page):
        frame, st, _ = self.locate(page)
        return (frame, st) if st["kind"] in ("question", "start") else None

    # ------------------------------------------------------------------ main entry

    def can_improve(self, st: dict) -> bool:
        """--retake: a submitted quiz without a perfect score that offers Retake."""
        return bool(self.opts.get("retake_completed") and st.get("retake") and not st.get("maxScore"))

    def run(self, page, ctx: dict, material: str = "") -> QuizOutcome:
        frame, st, completed = self.locate(page)
        if (completed or st["kind"] == "none") and self.can_improve(st):
            self.ui.step("Submitted before without a perfect score; retaking (--retake).", indent=1)
            frame, st = self._retake(page)
        elif completed:
            return QuizOutcome("already_done")
        if st["kind"] == "start":
            frame, st = self.start(page)
        if st["kind"] != "question":
            return QuizOutcome("no_quiz")
        return self._attempts(page, frame, st, ctx, material)

    def start(self, page):
        proceed = self.cfg["texts"]["proceed"]
        for _ in range(3):   # some quizzes have an instructions screen with a second Start
            self.ops.confirm(proceed, "confirm Start")
            frame, st, _ = self.locate(page)
            if st["kind"] == "question":
                return frame, st
            if st["kind"] != "start":
                break
            self.ops.click(frame, '[data-kqb-btn="start"]', "Start")
            self.ops.pause()
            got = self.ops.poll(lambda: self.ops.confirm(proceed, "confirm Start") and None or self._question(page),
                                self.t["question_change_ms"])
            if got:
                return got
        raise UnexpectedState("clicked Start but no question appeared")

    def _attempts(self, page, frame, st, ctx, material) -> QuizOutcome:
        max_attempts = 2 if self.opts.get("retake_on_fail", True) else 1
        previous: dict[str, list[int]] = {}
        outcome = QuizOutcome("submitted")
        for attempt in range(1, max_attempts + 1):
            answers, items, result = self._one_attempt(page, frame, st, ctx, attempt, previous, material)
            if self.dry_run:
                return QuizOutcome("dry_run", items=items)
            outcome = QuizOutcome("submitted", describe(result), attempt, outcome.items + items)
            (self.ui.ok if not self._failed(result) else self.ui.warn)(
                f"Result (attempt {attempt}): {outcome.result}", indent=1)
            self.runlog.row(event="result", kind="quiz", attempt=attempt, result=outcome.result,
                            note=" / ".join((result or {}).get("fresh", [])[:6]), url=page.url, **ctx)
            if self.opts.get("snapshot_quiz_pages"):
                self.ops.snapshot(self.snap_dir, f"result_{ctx['livebook']}_{ctx['lu']}_a{attempt}")
            if not (self._failed(result) and attempt < max_attempts):
                break
            if not result.get("retake"):
                self.ui.warn("Quiz failed but no Retake button is offered.", indent=1)
                break
            self.ui.step("Quiz failed; retaking once.", indent=1)
            previous = answers
            frame, st = self._retake(page)
        return outcome

    def _failed(self, r: dict | None) -> bool:
        if not r or not r.get("found"):
            return False
        frac = 1.0 if self.opts.get("retake_completed") else float(self.opts.get("pass_fraction", 0.6))
        if r.get("score") is not None and r.get("total"):
            return r["score"] / r["total"] < frac
        if r.get("percent") is not None:
            return r["percent"] < frac * 100
        return bool(r.get("fail")) and not r.get("pass")

    def _retake(self, page):
        def has_retake(f) -> bool:
            try:
                return bool(self.state(f).get("retake"))   # state() marks the button
            except PWError:
                return False

        frame = next((f for f in self.ops.frames() if has_retake(f)), None)
        if frame is None:
            raise UnexpectedState("Retake button disappeared")
        self.ops.click(frame, '[data-kqb-btn="retake"]', "Retake")
        self.ops.pause()
        proceed = self.cfg["texts"]["proceed"]
        got = self.ops.poll(lambda: self.ops.confirm(proceed, "confirm Retake") and None or
                            self._question_or_start(page), self.t["question_change_ms"])
        if not got:
            raise UnexpectedState("clicked Retake but no quiz appeared")
        frame, st = got
        if st["kind"] == "start":
            frame, st = self.start(page)
        return frame, st

    # ------------------------------------------------------------------ one pass through the quiz

    def _one_attempt(self, page, frame, st, ctx, attempt, previous, material):
        answers: dict[str, list[int]] = {}
        items: list[dict] = []
        prev_fp = None
        course = f"{ctx['livebook']} - LU {ctx['lu']} {ctx['lu_title']}".strip()
        max_q = int(self.opts.get("max_questions_per_quiz", 12))
        for n in range(1, max_q + 1):
            if n > 1:
                frame, st = self._wait_next_question(page, frame, prev_fp)
                if st["kind"] != "question":
                    if st.get("submitBtn"):      # a review page with only Submit on it
                        return answers, items, self._submit(page, frame)
                    # a lesson's ungraded check questions end here; the graded quiz may start below
                    self.ops.call(None, "scrollAll")
                    self.ops.pause()
                    frame, st, _ = self.locate(page)
                    if st["kind"] == "start":
                        self.ui.step(f"Finished {n - 1} check question(s); starting the graded quiz.", indent=1)
                        frame, st = self.start(page)
                        prev_fp = None
                    elif st.get("submitBtn"):
                        return answers, items, self._submit(page, frame)
                    elif st["kind"] != "question":
                        self.ui.note(f"Answered {n - 1} check question(s); no graded quiz followed.", indent=1)
                        return answers, items, {"found": True, "score": None, "total": None, "percent": None,
                                                "pass": True, "fail": False, "fresh": [],
                                                "snippet": f"{n - 1} check questions answered"}

            prog = st.get("progress") or [n, None]
            q = Question(text=st["question"], options=st["options"], multi=bool(st["multi"]),
                         code=st.get("code") or [], number=prog[0], total=prog[1])
            if n == 1 and self.opts.get("snapshot_quiz_pages"):
                self.ops.snapshot(self.snap_dir, f"quiz_{ctx['livebook']}_{ctx['lu']}_q1")

            ans = self.llm.choose(q, course=course, material=material, previous=previous.get(q.text))
            chosen_text = " | ".join(q.options[i] for i in ans.indices)
            self.ui.question(q, ans)
            self.runlog.row(
                event="dry_run_question" if self.dry_run else "question", kind="quiz", attempt=attempt,
                q_no=q.number, question=q.text, code="\n---\n".join(q.code),
                options=" || ".join(f"[{i}] {o}" for i, o in enumerate(q.options)),
                multi=q.multi, chosen=ans.indices, chosen_text=chosen_text,
                confidence=ans.confidence, note=f"detected via {st.get('source')}", url=page.url, **ctx)
            items.append({"attempt": attempt, "q": q.number, "question": q.text, "code": q.code,
                          "options": q.options, "chosen": ans.indices, "confidence": ans.confidence})
            answers[q.text] = ans.indices
            if self.dry_run:
                return answers, items, None

            self._select(page, frame, st, ans.indices)
            self.ops.pause()

            nav = self.ops.call(frame, "nav", self.ops.jscfg)
            last = bool(q.total) and q.number >= q.total
            if nav["submitBtn"] and not nav["submitDisabled"] and (
                    not nav["next"] or nav["nextDisabled"] or last):
                if n != int(self.opts.get("expected_questions", 5)):
                    log.warning("submitting after %d questions (expected %s)", n, self.opts.get("expected_questions"))
                return answers, items, self._submit(page, frame)
            if nav["next"] and not nav["nextDisabled"]:
                prev_fp = st["fingerprint"]
                self.ops.click(frame, '[data-kqb-btn="next"]', "Next")
                continue
            if nav["next"] or nav["submitBtn"]:
                raise UnexpectedState("Next/Submit stayed disabled after selecting an answer")
            raise UnexpectedState("no Next or Submit button next to the question")
        raise UnexpectedState(f"more than {max_q} questions; stopping to be safe")

    def _wait_next_question(self, page, frame, prev_fp):
        """Wait for the question text/options to change after clicking Next."""
        try:
            self.ops.ensure(frame)
            frame.wait_for_function(
                "a => !!window.__kqb && window.__kqb.fingerprint(a.cfg) !== a.prev",
                arg={"cfg": self.ops.jscfg, "prev": prev_fp},
                timeout=min(self.t["question_change_ms"], 10000), polling=250)
        except PWError as e:   # timed out (checked below), or the frame navigated: re-locate
            log.debug("wait_for_function: %s", first_line(e))
        self.ops.pause()
        # mid-transition the old question may be gone before the new one renders
        got = self.ops.poll(lambda: self._question(page, not_fp=prev_fp), 5000)
        if got:
            return got
        frame, st, _ = self.locate(page)
        if st["kind"] == "question" and st.get("fingerprint") == prev_fp:
            self.ops.call(None, "scrollAll")   # the next question may render further down
            self.ops.pause()
            frame, st, _ = self.locate(page)
            if st["kind"] == "question" and st.get("fingerprint") == prev_fp:
                raise UnexpectedState("question did not change after clicking Next")
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
            before = self.ops.call(frame, "selInfo", i)
            page.mouse.move(2, 2)   # keep hover styles out of the before/after comparison
            self.ops.click(frame, f'[data-kqb-opt="{i}"]', f"option {i}")
            page.mouse.move(2, 2)
            want = i in wanted
            if not self._verify(frame, i, before, want):
                raise UnexpectedState(f"could not verify that option {i} got {'selected' if want else 'cleared'}")
            if k + 1 < len(to_click):
                self.ops.pause()

    def _verify(self, frame, i: int, before: dict, want: bool) -> bool:
        def check():
            info = self.ops.call(frame, "selInfo", i)
            if info.get("stale"):   # options re-rendered: analyse again
                st = self.state(frame)
                if st["kind"] != "question" or i >= len(st["selected"]):
                    return False
                if st["selected"][i] is not None:
                    return st["selected"][i] is want
                return (i in self.ops.call(frame, "oddOneOut")) is want
            if info.get("known"):
                return bool(info.get("sel")) is want
            changed = info.get("sig") != before.get("sig")
            return changed if before.get("sig") is not None else bool(info.get("cls")) is want
        return bool(self.ops.poll(check, 3000, 200))

    # ------------------------------------------------------------------ submitting

    def _submit(self, page, frame) -> dict:
        before = self.ops.call(frame, "lines")
        self.ops.call(frame, "clickedMark", '[data-kqb-btn="submit"]')
        self.ops.expect_confirm = True
        confirmed = False
        try:
            self.ops.click(frame, '[data-kqb-btn="submit"]', "Submit")

            def check():
                nonlocal confirmed, frame
                if frame.is_detached():
                    frame = page.main_frame
                r = self.ops.call(frame, "result", self.ops.jscfg, before)
                if r["found"]:
                    return r
                if not confirmed and self.ops.call(frame, "confirm", self.ops.jscfg):
                    self.ops.pause()
                    self.ops.click(frame, '[data-kqb-btn="confirm"]', "confirm Submit")
                    confirmed = True
                return None

            r = self.ops.poll(check, self.t["result_ms"], 400)
            if r:
                self.ops.pause()   # let the result screen finish rendering, then re-read it
                try:
                    r2 = self.ops.call(frame, "result", self.ops.jscfg, before)
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
            self.ops.expect_confirm = False
