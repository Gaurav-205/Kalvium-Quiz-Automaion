"""Assignments: written answers, code editors and link fields.

An assignment is one Submit button and the boxes above it. For each one:

    detect -> draft every box -> review -> fill + verify -> run tests / fix -> submit -> verify

Nothing is submitted unless every box has a real value: a missing video link,
for example, leaves the whole assignment for the user instead of half-submitting it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PWError

from . import links as linkmod
from .browser import PageOps, UnexpectedState, slug
from .github import GitHub, GitHubError, publish
from .llm import LLM, CodeSpec, Project, ProjectSpec, WriteSpec, count_words, fit_chars
from .ui import QuitRun, ReviewItem, short

log = logging.getLogger("kalbot.tasks")

TASK_KIND = {"text": "written", "short": "written", "code": "coding", "link": "links", "other": "written"}
QUESTION_LIKE = re.compile(r"\?|\b(answer|output|result|explain|what|why|how|which|describe|define)\b", re.I)
CODE_WORDS = re.compile(r"\b(code|program|function|implement|algorithm|"
                        r"write a (python|java|c\+\+|javascript|c) )", re.I)
# fields the portal names by what they hold (input#pr, input#video)
NAMED_LINKS = {re.compile(r"(?i)pr|pr[-_]?(link|url)|pull[-_]?request([-_]?(link|url))?"): "pr",
               re.compile(r"(?i)video([-_]?(link|url))?|demo[-_]?video"): "video"}
# "Write a program that...", "Implement the function..." (not "Explain what this program does")
CODE_TASK = re.compile(r"\b(write|implement|complete|code|solve)\b[^.\n]{0,40}\b(function|program|code|class|"
                       r"method|solution|script|query)\b", re.I)
SUFFIX = {"python": ".py", "python3": ".py", "java": ".java", "javascript": ".js", "js": ".js",
          "typescript": ".ts", "c++": ".cpp", "cpp": ".cpp", "c": ".c", "go": ".go", "rust": ".rs",
          "ruby": ".rb", "php": ".php", "sql": ".sql", "html": ".html", "css": ".css", "kotlin": ".kt"}


class Manual(Exception):
    """This assignment needs the user (a link kalbot doesn't have, a fork/PR task, ...)."""


@dataclass
class Field:
    index: int
    tag: str                 # editor | rich | textarea | input
    type: str
    editor: str
    api: bool
    label: str
    prompt: str
    value: str
    locked: bool
    required: bool
    maxlength: int | None
    mono: bool
    codeHint: bool
    language: str
    submit: int
    run: int
    kind: str = ""           # text | short | code | link | other (a box kalbot won't guess, e.g. "Your name")
    link_kind: str = ""      # github | pr | live | video | link
    md: bool = False         # a Markdown editor (monospace, but never a code box)
    elId: str = ""           # the element's id and name ("pr", "video")
    nth: int = 0             # position among boxes with the same tag + label (set by _read)

    @property
    def key(self) -> tuple:
        # never text that changes while typing (an editor may mirror the answer next to the box)
        return self.tag, self.label, self.elId, self.nth

    @property
    def title(self) -> str:
        if self.kind == "link":
            name = linkmod.KIND_NAMES.get(self.link_kind, "link")
            return f"{name[0].upper()}{name[1:]} link"
        label = self.label.split(" | ")[0].strip()
        if label and (" " in label or not self.prompt.strip()):   # not a bare name="code" attribute
            return short(label, 70)
        lines = [ln.strip() for ln in self.prompt.splitlines() if ln.strip()]
        return short(max(lines, key=len) if lines else label or self.kind, 70)


@dataclass
class Group:
    """One Submit button and the boxes that belong to it."""
    submit: int
    submit_text: str
    fields: list[Field]
    done: bool = False

    @property
    def keys(self) -> tuple:
        return tuple(f.key for f in self.fields)

    @property
    def kinds(self) -> list[str]:
        return sorted({TASK_KIND[f.kind] for f in self.fields})

    @property
    def kind(self) -> str:
        return "+".join(self.kinds)

    def describe(self) -> str:
        parts = []
        for k in self.kinds:
            fs = [f for f in self.fields if TASK_KIND[f.kind] == k]
            if k == "links":
                parts.append("links: " + ", ".join(f.link_kind for f in fs))
            elif k == "coding":
                langs = sorted({f.language for f in fs if f.language})
                parts.append("coding" + (f" ({', '.join(langs)})" if langs else ""))
            else:
                parts.append(f"written ({len(fs)} {'box' if len(fs) == 1 else 'boxes'})")
        return "; ".join(parts)


@dataclass
class TaskView:
    frame: object
    groups: list[Group]
    done: bool               # the page says the assignment was handed in

    @property
    def open(self) -> list[Group]:
        return [g for g in self.groups if not g.done]


@dataclass
class Draft:
    field: Field
    value: str = ""
    source: str = ""
    missing: str = ""
    project: Project | None = None
    repo_name: str = ""


@dataclass
class TaskOutcome:
    status: str              # submitted | preview | manual | skipped | done
    kind: str
    detail: str = ""
    items: list[dict] = field(default_factory=list)


def word_limits(texts: list[str], default: tuple[int, int]) -> tuple[int, int]:
    """Word range a task asks for ('at least 150 words', '200-300 words', ...)."""
    num = r"(\d{2,4})"
    for t in texts:
        t = (t or "").lower()
        m = re.search(rf"{num}\s*(?:-|–|to)\s*{num}\s*words", t)
        if m:
            a, b = sorted((int(m[1]), int(m[2])))
            return a, b
        lo = re.search(rf"(?:minimum|min\.?|at least|no fewer than|not less than)\s*(?:of\s*)?{num}\s*words", t) or \
            re.search(rf"{num}\s*words?\s*(?:minimum|min\b|or more)", t)
        hi = re.search(rf"(?:maximum|max\.?|at most|up to|not exceed(?:ing)?|no more than|within|limit(?:ed)? "
                       rf"(?:of|to))\s*(?:of\s*)?{num}\s*words", t) or \
            re.search(rf"{num}\s*words?\s*(?:maximum|max\b|or less|or fewer)", t) or \
            re.search(rf"word limit\s*(?:of|is|:|-)?\s*{num}", t)
        if lo and hi:
            a, b = sorted((int(lo[1]), int(hi[1])))
            return a, b
        if lo:
            n = int(lo[1])
            return n, max(int(n * 1.5), n + 60)
        if hi:
            n = int(hi[1])
            return max(int(n * 0.6), min(40, n)), n
        m = re.search(rf"(?:about|around|approximately|roughly|in)\s*{num}\s*words", t)
        if m:
            n = int(m[1])
            return int(n * 0.85), int(n * 1.15)
    return default


def problem_statement(material: str, size: int = 4000) -> str:
    """The assignment brief, from its 'Problem Statement' heading on (portal pages put it there)."""
    i = (material or "").lower().find("problem statement")
    return material[i:i + size] if i >= 0 else ""


def run_verdict(text: str, patterns: dict) -> str:
    """pass | fail | unknown from the text a Run/Test button produced."""
    frac = r"(\d+)\s*(?:/|out of|of)\s*(\d+)"
    m = re.search(frac + r"\s*(?:test ?cases?|tests?|cases?)?\s*(?:passed|correct)", text, re.I) \
        or re.search(r"passed\s*:?\s*" + frac, text, re.I)
    if m:
        return "pass" if int(m[1]) == int(m[2]) and int(m[2]) > 0 else "fail"
    if any(re.search(p, text, re.I) for p in patterns["run_fail"]):
        return "fail"
    if any(re.search(p, text, re.I) for p in patterns["run_pass"]):
        return "pass"
    return "unknown"


def verdict_line(out: str) -> str:
    """The line of test output that carries the result (e.g. '2/3 test cases passed')."""
    lines = [ln for ln in out.splitlines() if ln.strip()]
    hit = [ln for ln in lines if re.search(r"\d+\s*(?:/|out of|of)\s*\d+|passed|failed|error", ln, re.I)]
    return (hit or lines or [""])[-1]


def _norm(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").split())


def _alnum(s: str) -> str:
    return re.sub(r"[\W_]+", "", (s or "").lower())


class TaskSolver:
    def __init__(self, cfg: dict, ops: PageOps, llm: LLM | None, runlog, ui, *, linkbook: linkmod.LinkBook,
                 state: linkmod.State, github: GitHub | None, run_dir: Path, dry_run: bool, review: bool,
                 enabled: dict[str, bool]):
        self.cfg = cfg
        self.ops = ops
        self.llm = llm
        self.runlog = runlog
        self.ui = ui
        self.linkbook = linkbook
        self.state = state
        self.gh = github if cfg["github"].get("auto_create") else None
        self.dry_run = dry_run
        self.review_on = review
        self.enabled = enabled
        self.fresh = False   # set per assignment: just retaken, so old answers in the boxes are ignored
        self.pat = cfg["patterns"]
        self.t = cfg["timeouts"]
        self.drafts_dir = run_dir / "drafts"
        self.snap_dir = run_dir / "snapshots"

    # ------------------------------------------------------------------ detection

    def _classify(self, f: Field) -> Field:
        named = next((NAMED_LINKS[k] for t in (f.elId or "").split() for k in NAMED_LINKS if k.fullmatch(t)), "")
        if f.tag in ("input", "textarea") and named:   # the portal's own field ids: input#pr, input#video
            f.kind, f.link_kind = "link", named
            return f
        if f.tag == "editor" or (f.tag == "textarea" and not f.md and (
                f.codeHint or (f.mono and (CODE_WORDS.search(f.label) or CODE_TASK.search(f.prompt[-400:]))))):
            f.kind = "code"
        elif f.tag == "input":
            if linkmod.is_link_field(f.label, f.prompt, f.type, self.pat):
                f.kind = "link"
            else:
                f.kind = "short" if QUESTION_LIKE.search(f"{f.label}\n{f.prompt[-300:]}") else "other"
        elif f.tag == "textarea" and (f.maxlength or 0) <= 500 and linkmod.STRONG_LINK.search(f.label):
            f.kind = "link"   # "Paste your GitHub link" as a textarea
        else:
            f.kind = "text"
        if f.kind == "link":
            f.link_kind = linkmod.classify(f.label, f.prompt, self.pat)
        return f

    def _read(self, frame) -> tuple[list[Group], bool] | None:
        try:
            raw = self.ops.call(frame, "tasks", self.ops.jscfg)
        except PWError as e:
            log.debug("frame %s not readable: %s", frame.url, e)
            return None
        fields = [self._classify(Field(**f)) for f in raw["fields"]]
        seen: dict[tuple, int] = {}
        for f in fields:
            f.nth = seen[(f.tag, f.label, f.elId)] = seen.get((f.tag, f.label, f.elId), -1) + 1
        groups = []
        for s in raw["submits"]:
            fs = [f for f in fields if f.submit == s["index"]]
            if not fs:
                continue
            filled = all(f.value.strip() for f in fs if f.kind != "code")
            done = all(f.locked for f in fs) or (raw["done"] and filled)
            groups.append(Group(s["index"], s["text"], fs, done))
        return groups, bool(raw["done"])

    def inspect(self, page) -> TaskView | None:
        """The frame with the most open assignment boxes."""
        best = None
        any_done = False
        for frame in page.frames:
            got = self._read(frame)
            if not got:
                continue
            groups, done = got
            any_done = any_done or done
            score = sum(len(g.fields) for g in groups if not g.done) * 10 + len(groups)
            if groups and (best is None or score > best[0]):
                best = (score, frame, groups)
        if best is None:
            return TaskView(None, [], any_done) if any_done else None
        return TaskView(best[1], best[2], any_done)

    def _refresh(self, frame, keys: tuple) -> Group:
        got = self._read(frame)
        for g in (got[0] if got else []):
            if g.keys == keys:
                return g
        raise UnexpectedState("the assignment changed on the page while it was being filled")

    # ------------------------------------------------------------------ main entry

    def run(self, page, ctx: dict, material: str, view: TaskView, fresh: bool = False) -> list[TaskOutcome]:
        outcomes: list[TaskOutcome] = []
        seen: set[tuple] = set()
        for _ in range(8):   # one assignment at a time; the page may re-render after each Submit
            g = next((g for g in view.groups if g.keys not in seen), None)
            if g is None:
                break
            seen.add(g.keys)
            if g.done and not (fresh and not all(f.locked for f in g.fields)):
                outcomes.append(TaskOutcome("done", g.kind, "already submitted"))
                continue
            off = [k for k in g.kinds if not self.enabled.get(k, True)]
            if off:
                outcomes.append(TaskOutcome("skipped", g.kind, f"{', '.join(off)} not selected (--only)"))
                continue
            try:
                outcomes.append(self._one(page, view.frame, g, ctx, material, fresh))
            except Manual as m:
                self.ui.warn(f"Needs you: {m}", indent=1)
                outcomes.append(TaskOutcome("manual", g.kind, str(m)))
            view = self.inspect(page) or view
        return outcomes

    def _one(self, page, frame, g: Group, ctx: dict, material: str, fresh: bool = False) -> TaskOutcome:
        self.ui.step(f"Assignment: {g.describe()}", indent=1)
        self.fresh = fresh
        drafts = self._draft(g, ctx, material)
        self._save_drafts(ctx, g, drafts)
        if self.dry_run:
            self.ui.show(self._title(ctx, g, "Preview (dry-run, nothing submitted)"), self._items(drafts))
            self.runlog.row(event="dry_run_task", kind=g.kind, note=g.describe(), url=page.url, **ctx)
            return TaskOutcome("preview", g.kind, g.describe(), self._report(drafts))
        drafts = self._review(g, ctx, material, drafts)
        if drafts is None:
            return TaskOutcome("skipped", g.kind, "skipped in review", [])

        self._publish(drafts, ctx)
        for d in drafts:
            g = self._refresh(frame, g.keys)
            self._fill(page, frame, g, d)
            self.ops.pause()
        tests = self._tests(page, frame, g, drafts, ctx, material)
        res = self._submit(page, frame, g)
        detail = self._detail(res, tests)
        (self.ui.ok if not res.get("unconfirmed") else self.ui.warn)(f"Submitted: {detail}", indent=1)
        for d in drafts:
            self.runlog.row(event="task_field", kind=TASK_KIND[d.field.kind], question=d.field.title,
                            chosen_text=d.value, note=d.source, url=page.url, **ctx)
        self.runlog.row(event="task_result", kind=g.kind, result=detail,
                        note=" / ".join(res.get("fresh", [])[:6]), url=page.url, **ctx)
        self.ops.snapshot(self.snap_dir, f"task_{ctx['livebook']}_{ctx['lu']}")
        return TaskOutcome("submitted", g.kind, detail, self._report(drafts))

    # ------------------------------------------------------------------ drafting

    def _draft(self, g: Group, ctx: dict, material: str) -> list[Draft]:
        lb, lu, title = ctx["livebook"], ctx["lu"], ctx["lu_title"]
        course = f"{lb} - LU {lu} {title}".strip()
        fresh = self.fresh
        given = self.linkbook.lookup(lb, lu, title)
        own = self.linkbook.lookup(lb, lu, title, wildcard=False)   # links you gave for this LU itself
        earlier = self.state.repo(lb, lu)
        drafts = [Draft(f) for f in g.fields if not f.locked]
        drafts = [d for d in drafts if not (d.field.kind == "other" and not d.field.required
                                            and not d.field.value.strip())]   # optional box: leave empty

        # 1. links first: no model calls if a link we can't produce is missing
        for d in drafts:
            f = d.field
            if f.kind == "other":
                if f.value.strip() and not fresh:
                    d.value, d.source = f.value, "already in the box"
                elif f.required:
                    d.missing = f"needs your input for '{f.title}'"
                continue
            if f.kind != "link":
                continue
            k = f.link_kind
            if f.value.strip() and not fresh and not linkmod.check_url(k, f.value.strip()):
                d.value, d.source = f.value.strip(), "already in the box"
            elif given.get(k):
                d.value, d.source = given[k], "submissions.yaml"
            elif k == "link" and len(own) == 1:   # one link for this LU: it is the one asked for
                d.value, d.source = next(iter(own.values())), "submissions.yaml"
            elif k in ("github", "live") and earlier.get(k):
                d.value, d.source = earlier[k], "repo from an earlier run"
            elif k == "github" and self.gh:
                d.source = "new repo"            # generated below
            elif k == "live" and self.gh and self.cfg["github"].get("enable_pages") and any(
                    o.field.link_kind == "github" and not o.value for o in drafts):
                d.source = "GitHub Pages"        # comes with the new repo
            else:
                d.missing = self._missing_hint(k)
        if not any(d.source == "new repo" for d in drafts):
            for d in drafts:
                if d.source == "GitHub Pages":
                    d.source, d.missing = "", self._missing_hint("live")
        missing = [d.missing for d in drafts if d.missing]
        if missing:
            raise Manual("; ".join(dict.fromkeys(missing)))

        # 2. a new repo for "submit your GitHub link" (reviewed before it is created)
        repo = next((d for d in drafts if d.source == "new repo"), None)
        if repo:
            self._plan_repo(repo, drafts, g, ctx, course, material)

        # 3. written answers and code
        texts = [d for d in drafts if d.field.kind in ("text", "short")]
        brief = problem_statement(material)
        for d in drafts:
            f = d.field
            question = f.prompt if len(f.prompt) >= 80 or not brief else f"{brief}\n\n{f.prompt}".strip()
            if f.kind in ("text", "short"):
                # a full assignment brief (or a Markdown editor) gets as much as it needs, unless it sets a limit
                stated = word_limits([f"{f.label}\n{f.prompt}", material], None)
                words = stated or (None if (f.md or brief) else tuple(self.cfg["written"]["default_words"]))
                have = "" if fresh else f.value.strip()
                n = count_words(have)
                if (f.kind == "short" and have) or (stated and n >= 15 and stated[0] <= n <= stated[1] * 1.2):
                    d.value, d.source = have, "already in the box"   # a finished answer that meets the limit
                    continue
                self.ui.note(f"Writing: {f.title}", indent=2)
                max_chars = int(f.maxlength or self.cfg["written"]["short_answer_max_chars"])
                spec = WriteSpec(question or f.label, f.label, words, f.kind == "short", max_chars,
                                 [o.field.title for o in texts if o is not d], existing=have, markdown=f.md)
                d.value, d.source = self.llm.write(spec, course, material), "AI draft"
                if f.maxlength:
                    d.value = fit_chars(d.value, f.maxlength)
            elif f.kind == "code":
                self.ui.note(f"Coding: {f.title}" + (f" ({f.language})" if f.language else ""), indent=2)
                spec = CodeSpec(question or f.label, f.language, f.value)
                d.value, d.source = self.llm.code(spec, course, material), "AI draft"
        return drafts

    def _missing_hint(self, kind: str) -> str:
        name = linkmod.KIND_NAMES.get(kind, "link")
        if kind == "video":
            return "needs your video link: record it, then add `video:` for this LU to submissions.yaml"
        if kind == "github" and not self.gh:
            return ("needs a GitHub repository link: add `github:` to submissions.yaml, "
                    "or set GITHUB_TOKEN so kalbot can create the repo")
        if kind == "pr":
            return "needs your pull request link: add `pr:` to submissions.yaml"
        return f"needs a {name} link: add `{kind}:` for this LU to submissions.yaml"

    def _plan_repo(self, d: Draft, drafts: list[Draft], g: Group, ctx: dict, course: str, material: str) -> None:
        others = sorted({o.field.link_kind for o in drafts if o.field.kind == "link" and o is not d})
        question = "\n\n".join(dict.fromkeys(f.prompt for f in g.fields if f.prompt)) or d.field.label
        self.ui.note("Designing a project for the GitHub repository...", indent=2)
        project = self.llm.project(ProjectSpec(question, others), slug(ctx["lu_title"]).lower(), course, material)
        if project.requires_existing_repo:
            raise Manual("the assignment asks you to work on an existing repository (fork / pull request); "
                         "do that, then add `github:` or `pr:` for this LU to submissions.yaml")
        gh = self.gh
        try:
            owner = gh.user()["login"]
            name = gh.free_name(f"{self.cfg['github'].get('repo_prefix') or ''}{project.repo_name}")
        except GitHubError as e:
            raise Manual(f"GitHub: {e}") from None
        d.value, d.project, d.repo_name = f"https://github.com/{owner}/{name}", project, name
        for o in drafts:
            if o.source == "GitHub Pages":
                if project.static_site:
                    o.value = f"https://{owner.lower()}.github.io/{name}/"
                else:
                    raise Manual("the live-site link needs a deployed app (this project isn't a static site): "
                                 "deploy it, then add `live:` for this LU to submissions.yaml")

    # ------------------------------------------------------------------ review

    def _title(self, ctx: dict, g: Group, prefix: str) -> str:
        return f"{prefix} · {ctx['livebook']} · LU {ctx['lu']} {ctx['lu_title']} · {g.kind}"

    def _items(self, drafts: list[Draft]) -> list[ReviewItem]:
        items = []
        for d in drafts:
            f = d.field
            if f.kind == "code":
                items.append(ReviewItem(f.title, "code", d.value, f"{f.language or 'code'} · {d.source}", f.language))
            elif d.project:
                vis = "private" if self.cfg["github"].get("private") else "public"
                items.append(ReviewItem(f.title, "project", d.value,
                                        f"new {vis} repo, created when you submit · {d.project.description}",
                                        files=sorted(d.project.files), editable=False))
            elif f.kind == "link":
                items.append(ReviewItem(f.title, "link", d.value, d.source))
            else:
                items.append(ReviewItem(f.title, "text", d.value, f"{count_words(d.value)} words · {d.source}"))
        return items

    def _review(self, g: Group, ctx: dict, material: str, drafts: list[Draft]) -> list[Draft] | None:
        while self.review_on:
            choice = self.ui.review(self._title(ctx, g, "Review"), self._items(drafts),
                                    "Nothing has been typed into the portal yet.")
            if choice == "submit":
                break
            if choice == "all":
                self.review_on = False
                break
            if choice == "quit":
                raise QuitRun()
            if choice == "skip":
                if not self.ui.interactive:
                    raise Manual("waiting for your review: run kalbot in a terminal, or use --auto")
                return None
            if choice == "regen":
                drafts = self._draft(g, ctx, material)
                self._save_drafts(ctx, g, drafts)
            elif choice.startswith("edit:"):
                d = drafts[int(choice.split(":")[1])]
                if d.field.kind == "link":
                    url = self.ui.ask_text("New URL (Enter keeps the current one): ")
                    why = linkmod.check_url(d.field.link_kind, url) if url else None
                    if url and why:
                        self.ui.warn(f"Not used: {why}")
                    elif url:
                        d.value, d.source = url, "edited by you"
                else:
                    suffix = SUFFIX.get(d.field.language.lower().split()[0], ".txt") if d.field.kind == "code" \
                        and d.field.language else ".txt"
                    d.value, d.source = self.ui.edit(d.value, suffix).rstrip() + (
                        "\n" if d.field.kind == "code" else ""), "edited by you"
        return drafts

    def _save_drafts(self, ctx: dict, g: Group, drafts: list[Draft]) -> None:
        base = self.drafts_dir / slug(ctx["livebook"]) / slug(f"{ctx['lu']}_{ctx['lu_title']}")
        base.mkdir(parents=True, exist_ok=True)
        for i, d in enumerate(drafts, 1):
            f = d.field
            ext = SUFFIX.get(f.language.lower().split()[0], ".txt") if f.kind == "code" and f.language else ".md"
            (base / f"{i:02d}_{slug(f.title, 40)}{ext}").write_text(d.value, encoding="utf-8")
            if d.project:
                for p, c in d.project.files.items():
                    out = base / "project" / p
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(c, encoding="utf-8")

    # ------------------------------------------------------------------ side effects

    def _publish(self, drafts: list[Draft], ctx: dict) -> None:
        d = next((d for d in drafts if d.project), None)
        if not d:
            return
        g = self.cfg["github"]
        self.ui.step(f"Creating GitHub repo {d.repo_name} ...", indent=1)
        try:
            out = publish(self.gh, d.repo_name, d.project.description, d.project.files,
                          private=bool(g.get("private")), pages=bool(g.get("enable_pages") and d.project.static_site),
                          wait_s=float(self.cfg["links"].get("wait_for_live_site_s") or 0))
        except GitHubError as e:
            raise UnexpectedState(str(e)) from None
        self.state.save_repo(ctx["livebook"], ctx["lu"], out)
        d.value = out["github"]
        for o in drafts:
            if o.source == "GitHub Pages":
                o.value = out["live"] or o.value
        self.ui.ok(f"Repo ready: {out['github']}" + (f"  (live: {out['live']})" if out["live"] else ""), indent=1)

    # ------------------------------------------------------------------ filling

    def _fill(self, page, frame, g: Group, d: Draft) -> None:
        f = next((x for x in g.fields if x.key == d.field.key), None)
        if f is None:
            raise UnexpectedState(f"'{d.field.title}' disappeared from the page")
        sel = f'[data-kqb-field="{f.index}"]'
        if f.tag in ("textarea", "input"):
            loc = frame.locator(sel).first
            try:
                loc.scroll_into_view_if_needed()
                loc.fill(d.value)
            except PWError as e:
                raise UnexpectedState(f"could not type into '{f.title}': {e}") from None
        elif not (f.tag == "editor" and self.ops.call(frame, "setEditor", f.index, d.value)):
            self._type(page, frame, f, d.value)
        if not self.ops.poll(lambda: self._value_ok(frame, f, d.value), 3000, 200):
            raise UnexpectedState(f"could not verify the text in '{f.title}'")
        log.info("filled %s (%s, %d chars)", f.title, f.kind, len(d.value))

    def _type(self, page, frame, f: Field, value: str) -> None:
        """Focus the box, clear it, paste; if the editor ignores the paste, type it."""
        self.ops.call(frame, "focusTarget", f.index)
        self.ops.click(frame, '[data-kqb-focus="1"]', f"'{f.title}'")
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.press("Backspace")
        self.ops.call(frame, "paste", value)
        if self._value_ok(frame, f, value):
            return
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.press("Backspace")
        if f.kind == "code":
            page.keyboard.insert_text(value)
            return
        lines = [ln for ln in value.split("\n") if ln.strip()]
        for i, line in enumerate(lines):
            page.keyboard.insert_text(line)
            if i + 1 < len(lines):
                page.keyboard.press("Enter")

    def _value_ok(self, frame, f: Field, expected: str) -> bool:
        actual = self.ops.call(frame, "fieldValue", f.index)
        if actual is None:
            return False
        if f.tag == "rich":
            return _alnum(actual) == _alnum(expected)
        if f.kind != "code" or f.api or f.tag in ("textarea", "input"):
            return _norm(actual) == _norm(expected)
        # editor read from the DOM: only the visible lines are rendered
        a = _norm(actual)
        lines = [_norm(ln) for ln in expected.splitlines() if ln.strip()][:3]
        return bool(lines) and all(ln in a for ln in lines)

    # ------------------------------------------------------------------ run tests

    def _tests(self, page, frame, g: Group, drafts: list[Draft], ctx: dict, material: str) -> str:
        code = [d for d in drafts if d.field.kind == "code" and d.field.run >= 0]
        if not code or not self.cfg["coding"].get("run_tests", True):
            return ""
        d = code[0]
        course = f"{ctx['livebook']} - LU {ctx['lu']} {ctx['lu_title']}".strip()
        rounds = int(self.cfg["coding"].get("max_fix_rounds", 2))
        verdict, out = "unknown", ""
        for n in range(rounds + 1):
            f = next(x for x in self._refresh(frame, g.keys).fields if x.key == d.field.key)
            verdict, out = self._run_once(frame, f.run)
            self.runlog.row(event="run_tests", kind="coding", attempt=n + 1, result=verdict, note=out[:500],
                            url=page.url, **ctx)
            (self.ui.ok if verdict == "pass" else self.ui.warn if verdict == "fail" else self.ui.note)(
                f"Run {n + 1}: {verdict}" + (f" · {short(verdict_line(out), 90)}" if out else ""), indent=2)
            if verdict != "fail" or n == rounds:
                break
            spec = CodeSpec(d.field.prompt or d.field.label, d.field.language, d.field.value)
            d.value = self.llm.code(spec, course, material, previous=d.value, feedback=out)
            d.source = f"AI draft, fixed after {n + 1} failed run{'s' if n else ''}"
            self._fill(page, frame, self._refresh(frame, g.keys), d)
        return {"pass": "tests passed", "fail": "tests still failing", "unknown": "test output not recognised"}[verdict]

    def _run_once(self, frame, run_index: int) -> tuple[str, str]:
        before = set(self.ops.call(frame, "lines"))
        self.ops.click(frame, f'[data-kqb-run="{run_index}"]', "Run")

        def check():
            fresh = "\n".join(ln for ln in self.ops.call(frame, "lines") if ln not in before)
            v = run_verdict(fresh, self.pat)
            return (v, fresh) if v != "unknown" else None

        got = self.ops.poll(check, self.t["run_output_ms"], 500)
        if not got:
            return "unknown", ""
        self.ops.pause()   # output may still be streaming in
        return check() or got

    # ------------------------------------------------------------------ submitting

    def _steps(self, page, frame, g: Group) -> None:
        """Save, then the Pre-submission Review (tick its checklist, close it), when the page has them."""
        for step in ("save", "review"):
            idx = [f.index for f in self._refresh(frame, g.keys).fields]
            found = self.ops.call(frame, "taskSteps", self.ops.jscfg, idx)
            if not found.get(step):
                continue
            self.ops.click(frame, f'[data-kqb-step="{step}"]', f"'{found[step]}'")
            self.ops.settle()
            self.ops.pause()
            if step == "review":
                self._checklist()

    def _checklist(self) -> None:
        def find():
            for fr in self.ops.frames():
                c = self.ops.call(fr, "checklist", self.ops.jscfg)
                if c["scope"] and (c["boxes"] or (c["proceed"] and c["inDialog"])):
                    return fr, c
            return None

        got = self.ops.poll(find, 5000, 300)
        if not got:
            return
        fr, c = got
        for i in range(c["boxes"]):
            box = fr.locator(f'[data-kqb-chk="{i}"]').first
            try:
                box.check(timeout=self.t["action_ms"])
            except PWError:   # a styled checkbox whose <input> is hidden behind its label
                box.dispatch_event("click")
        self.ui.note(f"Pre-submission review: ticked {c['boxes']} item(s)", indent=2)
        again = self.ops.call(fr, "checklist", self.ops.jscfg)
        if again["proceed"]:
            self.ops.click(fr, '[data-kqb-btn="proceed"]', "close the pre-submission review")
            self.ops.settle()
            self.ops.pause()

    def _submit(self, page, frame, g: Group) -> dict:
        self._steps(page, frame, g)

        def enabled():
            fresh = self._refresh(frame, g.keys)
            raw = self.ops.call(frame, "tasks", self.ops.jscfg)
            return fresh if any(s["index"] == fresh.submit and not s["disabled"] for s in raw["submits"]) else None
        if not self.ops.poll(enabled, 5000, 300):
            raise UnexpectedState("Submit stayed disabled after filling every box")
        g = self._refresh(frame, g.keys)
        sel = f'[data-kqb-tsub="{g.submit}"]'
        idx = [f.index for f in g.fields]
        before = self.ops.call(frame, "lines")
        self.ops.call(frame, "clickedMark", sel)
        self.ops.expect_confirm = True
        confirms = 0
        last: dict = {}
        try:
            self.ops.click(frame, sel, f"'{g.submit_text}'")

            def check():
                nonlocal confirms
                # "Are you sure? Once submitted..." first: its text is a question, not the result
                if confirms < 2 and self.ops.call(frame, "confirm", self.ops.jscfg):
                    self.ops.pause()
                    self.ops.click(frame, '[data-kqb-btn="confirm"]', "confirm Submit")
                    confirms += 1
                    return None
                r = self.ops.call(frame, "taskResult", self.ops.jscfg, before, idx)
                last.update(r)
                if r["total"] and (r["locked"] == r["total"] or r["gone"] == r["total"]):
                    return r
                if (r["success"] or r["done"]) and r["submitGone"]:
                    return r
                if r["error"]:
                    raise UnexpectedState(f"the portal said: {r['error']}")
                return None

            r = self.ops.poll(check, self.t["task_ms"], 400)

            def message() -> bool:
                again = self.ops.call(frame, "taskResult", self.ops.jscfg, before, idx)
                if again.get("message"):
                    r.update(again)
                return bool(again.get("message"))
            if r and not r.get("message"):   # the confirmation text may render a moment later
                self.ops.poll(message, 3000, 300)
        finally:
            self.ops.expect_confirm = False
        if r:
            return r
        if last.get("submitGone") or last.get("success") or last.get("done"):
            return {**last, "unconfirmed": True}
        raise UnexpectedState("no confirmation after Submit and the form is unchanged")

    @staticmethod
    def _detail(res: dict, tests: str) -> str:
        bits = []
        if res.get("score"):
            bits.append(f"{res['score'][0]}/{res['score'][1]}")
        if tests:
            bits.append(tests)
        msg = res.get("message") or next((ln for ln in res.get("fresh", []) if len(ln) < 120), "")
        if res.get("unconfirmed"):
            bits.append("no confirmation shown; check the portal")
        elif msg:
            bits.append(f'"{msg}"')
        return " · ".join(bits) or "submitted"

    @staticmethod
    def _report(drafts: list[Draft]) -> list[dict]:
        return [{"label": d.field.title, "kind": d.field.kind, "link_kind": d.field.link_kind,
                 "language": d.field.language, "value": d.value, "source": d.source,
                 "words": count_words(d.value) if d.field.kind in ("text", "short") else None,
                 "files": sorted(d.project.files) if d.project else []} for d in drafts]
