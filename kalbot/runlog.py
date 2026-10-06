"""What happened in a run: a CSV row per event, and one Result per quiz/assignment."""

from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

FIELDS = [
    "time", "event", "livebook", "lu", "lu_title", "kind", "attempt", "q_no",
    "question", "code", "options", "multi", "chosen", "chosen_text",
    "confidence", "result", "note", "url",
]


@dataclass
class Result:
    livebook: str
    lu: str
    title: str
    status: str            # submitted | preview | done | manual | none | skipped | error
    kind: str = ""         # quiz | written | coding | links | mixed | ""
    detail: str = ""
    url: str = ""
    items: list[dict] = field(default_factory=list)   # questions / fields, for the HTML report


class RunLog:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.csv_path = run_dir / "results.csv"
        # utf-8-sig so Excel on Windows shows non-ASCII text correctly
        self._f = open(self.csv_path, "w", newline="", encoding="utf-8-sig")  # noqa: SIM115 (open for the run)
        self._w = csv.DictWriter(self._f, FIELDS)
        self._w.writeheader()
        self.results: list[Result] = []

    def row(self, **kw) -> None:
        kw.setdefault("time", dt.datetime.now().isoformat(timespec="seconds"))
        self._w.writerow({k: kw.get(k, "") for k in FIELDS})
        self._f.flush()

    def add(self, ctx: dict, status: str, kind: str = "", detail: str = "", url: str = "",
            items: list[dict] | None = None) -> Result:
        r = Result(ctx.get("livebook", ""), ctx.get("lu", ""), ctx.get("lu_title", ""), status, kind,
                   detail, url, items or [])
        self.results.append(r)
        return r

    def count(self, *statuses: str) -> int:
        return sum(1 for r in self.results if r.status in statuses)

    def save_json(self) -> Path:
        p = self.dir / "results.json"
        p.write_text(json.dumps([asdict(r) for r in self.results], indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()
