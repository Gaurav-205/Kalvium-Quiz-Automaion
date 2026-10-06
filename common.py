"""Shared helpers: config loading, pacing, snapshots and the CSV run log."""

from __future__ import annotations

import csv
import datetime as dt
import logging
import random
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("kqb")

JS_HELPERS = (ROOT / "dom_helpers.js").read_text(encoding="utf-8")


class UnexpectedState(Exception):
    """The page is not in the state we expected. The current LU is skipped."""


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def folder(cfg: dict, key: str) -> Path:
    """Resolve one of cfg['paths'] relative to the tool folder and create it."""
    p = Path(cfg["paths"][key])
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def js_config(cfg: dict) -> dict:
    """The subset of config.yaml the in-page helpers need."""
    s = cfg["selectors"]
    # str() everything: YAML turns bare Yes/No/On/Off into booleans
    t = {k: [str(x) for x in v] for k, v in cfg["texts"].items()}
    p = {k: [str(x) for x in v] for k, v in cfg["patterns"].items()}
    return {
        "start": t["start"], "next": t["next"], "submit": t["submit"],
        "confirm": t["confirm"], "retake": t["retake"], "prev": t["previous"],
        "optionSelector": s.get("quiz_option") or "",
        "questionSelector": s.get("quiz_question") or "",
        "livebookCardSelector": s.get("livebook_card") or "",
        "livebookHrefRegex": s["livebook_href_regex"],
        "luRowSelector": s.get("lu_row") or "",
        "luNumberRegex": s["lu_number_regex"],
        "moduleToggleSelector": s.get("module_toggle") or "",
        "moduleRegex": s["module_regex"],
        "exclude": p["quiz_exclude"], "multi": p["multi_select"],
        "completed": p["quiz_completed"], "fail": p["result_fail"], "pass": p["result_pass"],
        "coding": p["lu_coding_page"], "written": p["lu_written_page"],
    }


def ensure_helpers(scope) -> None:
    """Inject dom_helpers.js into a page or frame if it is not there yet."""
    try:
        if not scope.evaluate("() => !!(window.__kqb && window.__kqb.version)"):
            scope.evaluate(JS_HELPERS)
    except Exception as e:
        log.debug("ensure_helpers failed on scope %s: %s", scope, e)



def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def slug(text: str, maxlen: int = 50) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", text or "").strip("_")
    return s[:maxlen] or "page"


class Pacer:
    """Random 1-2 s pause between actions so the portal has time to settle."""

    def __init__(self, cfg: dict):
        d = cfg["delays"]
        self.lo, self.hi = float(d["action_min_s"]), float(d["action_max_s"])

    def pause(self, page) -> None:
        page.wait_for_timeout(random.uniform(self.lo, self.hi) * 1000)


def save_snapshot(page, out_dir: Path, label: str) -> Path | None:
    """Write <stamp>_<label>.html and .png. Never raises."""
    base = out_dir / f"{stamp()}_{slug(label)}"
    try:
        Path(f"{base}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:  # page may be mid-navigation
        log.debug("html snapshot failed: %s", e)
    try:
        page.screenshot(path=f"{base}.png", full_page=True)
    except Exception as e:
        log.debug("screenshot failed: %s", e)
    return base


FIELDS = [
    "time", "event", "livebook", "lu", "lu_title", "attempt", "q_no",
    "question", "code", "options", "multi", "chosen", "chosen_text",
    "confidence", "result", "note", "url",
]


class RunLog:
    """CSV log of every question and event, plus the data for the final summary."""

    def __init__(self, logs_dir: Path, ts: str | None = None):
        self.path = logs_dir / f"run_{ts or stamp()}.csv"
        # utf-8-sig so Excel on Windows shows non-ASCII text correctly
        self._f = open(self.path, "w", newline="", encoding="utf-8-sig")
        self._w = csv.DictWriter(self._f, FIELDS)
        self._w.writeheader()
        self.done: list[dict] = []        # quizzes submitted this run
        self.dry: list[dict] = []         # quizzes inspected in dry-run
        self.already: list[dict] = []     # quizzes/LUs already completed
        self.manual: list[dict] = []      # written / coding LUs left for the user
        self.no_quiz: list[dict] = []     # LUs with no quiz or task detected
        self.errors: list[dict] = []      # LUs skipped because of an unexpected state

    def row(self, **kw) -> None:
        kw.setdefault("time", dt.datetime.now().isoformat(timespec="seconds"))
        self._w.writerow({k: kw.get(k, "") for k in FIELDS})
        self._f.flush()

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()

    def summary(self) -> str:
        def lu(d):
            return f"{d.get('livebook', '')} | {d.get('lu', '')} {d.get('lu_title', '')}".strip()

        out = ["", "=" * 70, "SUMMARY", "=" * 70]
        out.append(f"Quizzes submitted: {len(self.done)}")
        for d in self.done:
            out.append(f"  - {lu(d)}: {d.get('result') or 'result not shown'}")
        if self.dry:
            out.append(f"Quizzes inspected (dry-run, nothing clicked): {len(self.dry)}")
            for d in self.dry:
                out.append(f"  - {lu(d)}")
        out.append(f"Skipped, already completed: {len(self.already)}")
        out.append(f"Skipped after an error: {len(self.errors)}")
        for d in self.errors:
            out.append(f"  - {lu(d)}: {d.get('note', '')}")
        out.append(f"Manual LUs left (written/coding): {len(self.manual)}")
        for d in self.manual:
            out.append(f"  - {lu(d)} [{d.get('note', '')}]")
        if self.no_quiz:
            out.append(f"Unfinished LUs with no quiz or task detected (check manually): {len(self.no_quiz)}")
            for d in self.no_quiz:
                out.append(f"  - {lu(d)}")
        out.append(f"CSV log: {self.path}")
        return "\n".join(out)
