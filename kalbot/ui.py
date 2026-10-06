"""Everything printed to the terminal, plus the interactive review prompt.

All dynamic text goes through rich.Text (never markup), so quiz content such
as "[0]" or "[bold]" is shown verbatim.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEME = Theme({
    "ok": "green", "warn": "yellow", "err": "bold red", "accent": "cyan",
    "muted": "grey58", "title": "bold", "chosen": "bold green",
})

STATUS = {   # result status -> (label, style)
    "submitted": ("submitted", "ok"),
    "preview": ("preview", "accent"),
    "manual": ("needs you", "warn"),
    "error": ("error", "err"),
    "none": ("no task found", "muted"),
    "skipped": ("skipped", "muted"),
    "done": ("already done", "muted"),
}
COUNT_LABELS = [("submitted", "Submitted"), ("preview", "Previewed"), ("done", "Already done"),
                ("manual", "Needs you"), ("none", "No task found"), ("skipped", "Skipped"), ("error", "Errors")]

LEXERS = {"python": "python", "python3": "python", "py": "python", "java": "java", "javascript": "javascript",
          "js": "javascript", "node": "javascript", "typescript": "typescript", "ts": "typescript",
          "c++": "cpp", "cpp": "cpp", "c": "c", "c#": "csharp", "csharp": "csharp", "go": "go", "golang": "go",
          "rust": "rust", "ruby": "ruby", "php": "php", "kotlin": "kotlin", "swift": "swift", "sql": "sql",
          "html": "html", "css": "css", "bash": "bash", "shell": "bash", "r": "r", "dart": "dart"}


def short(text: str, n: int = 110) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


@dataclass
class ReviewItem:
    """One thing about to be submitted, as shown in the review panel."""
    label: str
    kind: str                    # text | code | link | project
    value: str
    meta: str = ""
    language: str = ""
    files: list[str] = field(default_factory=list)   # project file list
    editable: bool = True


class QuitRun(Exception):
    """The user chose to stop the run from a prompt."""


class UI:
    def __init__(self, console: Console | None = None, interactive: bool | None = None):
        if console is None:
            width = None if sys.stdout.isatty() else 120
            console = Console(theme=THEME, highlight=False, width=width)
        self.c = console
        self.interactive = (sys.stdin.isatty() and sys.stdout.isatty()) if interactive is None else interactive

    # ---------------------------------------------------------------- basic lines

    def say(self, text: str = "", style: str | None = None, indent: int = 0, markup: bool = False) -> None:
        pad = "  " * indent
        if markup:
            self.c.print(pad + text, style=style)
        else:
            self.c.print(Text(pad + text, style=style or ""))

    def _sym(self, sym: str, style: str, text: str, indent: int) -> None:
        t = Text("  " * indent)
        t.append(f"{sym} ", style=style)
        t.append(text)
        self.c.print(t)

    def ok(self, text: str, indent: int = 0) -> None:
        self._sym("✓", "ok", text, indent)

    def warn(self, text: str, indent: int = 0) -> None:
        self._sym("!", "warn", text, indent)

    def error(self, text: str, indent: int = 0) -> None:
        self._sym("✗", "err", text, indent)

    def note(self, text: str, indent: int = 0) -> None:
        self.say(text, "muted", indent)

    def step(self, text: str, indent: int = 0) -> None:
        self._sym("→", "accent", text, indent)

    def banner(self, title: str, rows: list[tuple[str, str]]) -> None:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="muted")
        grid.add_column()
        for k, v in rows:
            grid.add_row(k, Text(v))
        self.c.print(Panel(grid, title=Text(title, style="title"), title_align="left", border_style="accent",
                           expand=False))

    def section(self, title: str) -> None:
        self.c.print()
        self.c.print(Rule(Text(title, style="title"), style="accent", align="left"))

    def lu(self, number: str, title: str) -> None:
        t = Text("\n")
        t.append(f"LU {number}", style="accent")
        t.append(f"  {title}")
        self.c.print(t)

    def lu_list(self, lus) -> None:
        for lu in lus:
            t = Text("    ")
            t.append(f"{lu.number:>5}  ")
            t.append(f"{'done' if lu.completed else 'todo':4}", style="muted" if lu.completed else "accent")
            t.append(f"  {lu.title[:70]}")
            if lu.type_hint:
                t.append(f" [{lu.type_hint}]", style="muted")
            self.c.print(t)

    # ---------------------------------------------------------------- quiz

    def question(self, q, ans) -> None:
        total = f"/{q.total}" if q.total else ""
        self.say(f"Q{q.number}{total}: {short(q.text)}", indent=2)
        for c in q.code:
            self.say(f"[code] {short(c, 90)}", "muted", indent=3)
        for i, o in enumerate(q.options):
            chosen = i in ans.indices
            t = Text("      ")
            t.append("●" if chosen else "○", style="chosen" if chosen else "muted")
            t.append(f" [{i}] {short(o, 90)}", style="chosen" if chosen else "")
            self.c.print(t)
        conf_style = {"high": "ok", "medium": "warn"}.get(ans.confidence, "err")
        t = Text("      → ")
        t.append(str(ans.indices))
        t.append(f" ({ans.confidence})", style=conf_style)
        if q.multi:
            t.append(" multi-select", style="muted")
        self.c.print(t)

    # ---------------------------------------------------------------- review

    def render_item(self, n: int, item: ReviewItem, last: bool = False):
        head = Text(f"{n}. ", style="accent")
        head.append(item.label or item.kind, style="title")
        if item.meta:
            head.append(f"  · {item.meta}", style="muted")
        if item.kind == "code":
            lexer = LEXERS.get(item.language.lower().split()[0], "text") if item.language else "text"
            body = Syntax(item.value.rstrip("\n"), lexer, line_numbers=True, word_wrap=True,
                          background_color="default")
        elif item.kind == "link":
            body = Text("   " + (item.value or "(none)"), style="accent")
        elif item.kind == "project":
            files = Text("   files: ", style="muted")
            files.append(", ".join(item.files[:20]) + (" …" if len(item.files) > 20 else ""))
            body = Group(Text("   " + item.value, style="accent"), files)
        else:
            body = Text(item.value)
        return Group(head, body) if last else Group(head, body, Text())

    def show(self, title: str, items: list[ReviewItem], note: str = "", style: str = "accent") -> None:
        parts = [self.render_item(i + 1, it, last=i + 1 == len(items)) for i, it in enumerate(items)]
        if note:
            parts.append(Text(note, style="muted"))
        self.c.print(Panel(Group(*parts), title=Text(title, style="title"), title_align="left",
                           border_style=style, padding=(1, 2)))

    def review(self, title: str, items: list[ReviewItem], note: str = "") -> str:
        """Show what is about to be submitted. Returns submit|edit:N|regen|skip|all|quit."""
        self.show(title, items, note, style="warn")
        if not self.interactive:
            return "skip"
        keys = Text("  ")
        for k, label in (("Enter", "submit"), ("e", "edit"), ("r", "regenerate"), ("s", "skip"),
                         ("a", "submit all from now on"), ("q", "quit")):
            keys.append(f"[{k}]", style="accent")
            keys.append(f" {label}   ")
        self.c.print(keys)
        while True:
            ans = self.c.input("  Your choice: ").strip().lower()
            if ans in ("", "y", "yes", "submit"):
                return "submit"
            if ans in ("s", "skip", "n", "no"):
                return "skip"
            if ans in ("a", "all"):
                return "all"
            if ans in ("q", "quit"):
                return "quit"
            if ans in ("r", "regen", "regenerate"):
                return "regen"
            if ans.startswith("e"):
                editable = [i for i, it in enumerate(items) if it.editable]
                if not editable:
                    self.warn("Nothing here can be edited.")
                    continue
                num = ans[1:].strip()
                if not num and len(editable) > 1:
                    num = self.c.input(f"  Edit which item ({', '.join(str(i + 1) for i in editable)})? ").strip()
                idx = int(num) - 1 if num.isdigit() else editable[0]
                if idx in editable:
                    return f"edit:{idx}"
            self.warn("Press Enter to submit, or one of e / r / s / a / q.")

    def ask_text(self, prompt: str) -> str:
        return self.c.input(f"  {prompt}").strip() if self.interactive else ""

    def edit(self, text: str, suffix: str = ".txt") -> str:
        """Open text in the user's editor and return the saved result."""
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as f:
            f.write(text)
            name = f.name
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or (
            "notepad" if os.name == "nt" else "nano" if shutil.which("nano") else "vi")
        self.note(f"Opening {editor}. Save and close the editor to continue.", indent=1)
        try:
            subprocess.run([*shlex.split(editor, posix=os.name != "nt"), name], check=False)
            with open(name, encoding="utf-8") as f:
                return f.read()
        finally:
            os.unlink(name)

    # ---------------------------------------------------------------- summary

    def summary(self, runlog, report_path=None) -> None:
        res = runlog.results
        counts = {k: sum(1 for r in res if r.status == k) for k, _ in COUNT_LABELS}
        self.section("Summary")
        line = Text()
        for i, (k, label) in enumerate(COUNT_LABELS):
            if i:
                line.append(" | ", style="muted")
            line.append(f"{label}: {counts[k]}", style=STATUS[k][1] if counts[k] else "muted")
        self.c.print(line)
        shown = [r for r in res if r.status != "done"]
        if shown:
            table = Table(show_edge=False, header_style="muted", pad_edge=False, expand=False)
            for col in ("Status", "Livebook", "LU", "Type", "Details"):
                table.add_column(col, overflow="fold")
            for r in shown:
                label, style = STATUS.get(r.status, (r.status, ""))
                table.add_row(Text(label, style=style), Text(r.livebook), Text(f"{r.lu} {r.title}".strip()),
                              Text(r.kind), Text(r.detail))
            self.c.print(table)
        manual = [r for r in res if r.status == "manual"]
        if manual:
            self.say("Needs you: finish these on the portal, or add their links to submissions.yaml and run again.",
                     "warn")
        self.say(f"CSV log: {runlog.csv_path}", "muted")
        if report_path:
            self.say(f"Report:  {report_path}", "muted")
