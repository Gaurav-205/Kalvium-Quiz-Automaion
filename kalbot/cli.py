"""kalbot command line: run, discover, login, doctor."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config, path, stamp

EXAMPLES = """
examples:
  kalbot doctor                           check your setup (Python, Chrome, API keys, links file)
  kalbot discover                         log in, explore read-only, save snapshots + selectors
  kalbot run --dry-run --limit 1          draft one quiz/assignment and show it; submit nothing
  kalbot run --limit 1                    complete one for real
  kalbot run                              complete everything unfinished this semester
  kalbot run --only quiz,written          just these kinds (quiz, written, coding, links)
  kalbot run --livebook "data" --lu 2.3   one LU (also re-checks an LU marked complete)
  kalbot run --auto                       hands-free: don't stop to review drafts
  kalbot login                            plain-Chrome login if Google blocks the automated window
"""

COMMANDS = ("run", "discover", "login", "doctor")
TASK_KINDS = ("quiz", "written", "coding", "links")


def parse_args(argv=None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    for flag, cmd in (("--discover", "discover"), ("--login", "login"), ("--doctor", "doctor")):
        if flag in argv:   # flags from the old `python main.py --discover`
            argv.remove(flag)
            argv.insert(0, cmd)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "-V", "--version")):
        argv.insert(0, "run")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="settings file (default: config.yaml in the kalbot folder)")
    common.add_argument("--semester", type=int, help="override portal.semester")
    common.add_argument("--headless", action="store_true", help="run Chrome without a window")

    ap = argparse.ArgumentParser(
        prog="kalbot", epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Complete unfinished Kalvium quizzes, written, coding and link assignments with an LLM.")
    ap.add_argument("-V", "--version", action="version", version=f"kalbot {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="command")
    run = sub.add_parser("run", parents=[common], help="complete unfinished work (default)")
    run.add_argument("--dry-run", action="store_true", help="read and draft everything, submit nothing")
    run.add_argument("--limit", type=int, default=None, help="stop after N quizzes/assignments (0 = no limit)")
    run.add_argument("--livebook", help="only livebooks whose name contains this text")
    run.add_argument("--lu", help='only this LU number, e.g. "1.2"')
    run.add_argument("--only", help=f"comma-separated kinds to do: {','.join(TASK_KINDS)}")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--auto", action="store_true", help="don't stop to review written/coding/link drafts")
    mode.add_argument("--review", action="store_true", help="always stop to review drafts (overrides run.review)")
    sub.add_parser("discover", parents=[common], help="explore read-only, save snapshots and selectors")
    sub.add_parser("login", parents=[common], help="open plain Chrome on the bot profile to log in")
    doc = sub.add_parser("doctor", parents=[common], help="check Python, Chrome, keys and settings")
    doc.add_argument("--online", action="store_true", help="also test the LLM key and GitHub token with a live call")
    return ap.parse_args(argv)


def setup_logging(log_path: Path, console) -> None:
    from rich.logging import RichHandler

    root = logging.getLogger()
    for h in list(root.handlers):   # repeated runs in one process (tests) must not stack handlers
        if getattr(h, "_kalbot", False):
            root.removeHandler(h)
            h.close()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    con = RichHandler(console=console, show_time=False, show_path=False, markup=False, level=logging.WARNING)
    for h in (fh, con):
        h._kalbot = True
        root.addHandler(h)
    for name in ("httpx", "httpx2", "httpcore", "google_genai", "anthropic", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def new_run_dir(root: Path, suffix: str) -> Path:
    base = root / (stamp() + suffix)
    d, n = base, 2
    while d.exists():
        d, n = base.with_name(f"{base.name}-{n}"), n + 1
    d.mkdir(parents=True)
    return d


def task_filter(cfg: dict, only: str | None) -> dict[str, bool]:
    if not only:
        return {k: bool(cfg["tasks"].get(k, True)) for k in TASK_KINDS}
    want = {x.strip().lower() for x in only.split(",") if x.strip()}
    bad = want - set(TASK_KINDS)
    if bad:
        raise ConfigError(f"--only: unknown kind(s) {', '.join(sorted(bad))}; use {', '.join(TASK_KINDS)}")
    return {k: k in want for k in TASK_KINDS}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # never crash on odd characters in a Windows console
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    from .ui import UI

    args = parse_args(argv)
    ui = UI()
    try:
        cfg = load_config(args.config)
        if args.semester:
            cfg["portal"]["semester"] = args.semester
        if args.headless:
            cfg["browser"]["headless"] = True
        for w in cfg["_meta"]["warnings"]:
            ui.warn(w)
        if args.command == "login":
            from .browser import manual_login
            return manual_login(cfg, ui)
        if args.command == "doctor":
            return doctor(cfg, ui, online=args.online)
        return run(cfg, args, ui)
    except ConfigError as e:
        ui.error(str(e))
        return 2


# --------------------------------------------------------------------------- run / discover


def run(cfg: dict, args, ui) -> int:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    from .browser import PageOps, first_line, launch_browser
    from .github import GitHub
    from .links import LinkBook, State
    from .llm import LLM, LLMError, make_provider
    from .portal import Navigator
    from .quiz import QuizSolver
    from .report import write_report
    from .runlog import RunLog
    from .tasks import TaskSolver
    from .ui import QuitRun

    discover = args.command == "discover"
    dry = discover or getattr(args, "dry_run", False) or bool(cfg["run"].get("dry_run"))
    limit = args.limit if getattr(args, "limit", None) is not None else int(cfg["run"].get("limit") or 0)
    enabled = task_filter(cfg, getattr(args, "only", None))
    review = bool(cfg["run"].get("review", True)) or getattr(args, "review", False)
    review = review and not getattr(args, "auto", False)

    run_dir = new_run_dir(path(cfg, "runs"), "_discover" if discover else "_dry-run" if dry else "")
    setup_logging(run_dir / "run.log", ui.c)

    llm = None
    prov = cfg["llm"]["provider"]
    if not discover:
        try:
            llm = LLM(make_provider(cfg["llm"]), cfg)
        except LLMError as e:
            ui.error(str(e))
            return 2
    home = cfg["_meta"]["home"]
    linkbook = LinkBook.load(home / cfg["links"]["file"])
    for problem in linkbook.problems:
        ui.warn(problem)
    github = GitHub.from_config(cfg) if cfg["github"].get("auto_create") else None
    meta = [("Mode", "DISCOVER (read-only)" if discover else "DRY-RUN (nothing is submitted)" if dry else "LIVE")]
    if not discover:
        meta += [("LLM", f"{prov} · {cfg['llm']['models'][prov]}"),
                 ("Doing", ", ".join(k for k in TASK_KINDS if enabled[k]) or "nothing"),
                 ("Review", "before each written/coding/link submission" if review else "off (--auto)"),
                 ("Limit", str(limit or "none")),
                 ("Links", f"{len(linkbook.entries)} LU{'s' * (len(linkbook.entries) != 1)} in "
                  f"{cfg['links']['file']}" if linkbook.entries
                  else f"none yet ({cfg['links']['file']})"),
                 ("GitHub", "creates repos for repo-link tasks" if github else "off (no GITHUB_TOKEN)")]
    meta.append(("Output", str(run_dir)))
    ui.banner(f"kalbot {__version__}", meta)

    runlog = RunLog(run_dir)
    code = 0
    with sync_playwright() as p:
        try:
            context = launch_browser(p, cfg)
        except PWError as e:
            ui.error(f"Could not start Chrome: {first_line(e)}")
            ui.note("Is another kalbot window still open (profile in use)? Is Chrome installed? "
                    "Try: python -m playwright install chrome")
            runlog.close()
            return 2
        page = context.pages[0] if context.pages else context.new_page()
        ops = PageOps(cfg, page)
        quiz = QuizSolver(cfg, ops, llm, runlog, ui, run_dir / "snapshots", dry)
        tasks = TaskSolver(cfg, ops, llm, runlog, ui, linkbook=linkbook, state=State(path(cfg, "state")),
                           github=github, run_dir=run_dir, dry_run=dry, review=review, enabled=enabled)
        nav = Navigator(cfg, ops, quiz, tasks, runlog, ui, run_dir=run_dir, dry_run=dry, limit=limit,
                        livebook=getattr(args, "livebook", None), lu=getattr(args, "lu", None), enabled=enabled)
        try:
            nav.wait_for_login()
            if discover:
                nav.discover(cfg["_meta"]["config_path"])
            else:
                nav.run()
        except (KeyboardInterrupt, QuitRun):
            ui.warn("Stopped.")
            code = 130
        except LLMError as e:
            ui.error(f"Stopping: {e}")
            code = 2
        except PWError as e:
            ui.error(f"Browser error (was the window closed?): {first_line(e)}")
            code = 1
        finally:
            runlog.close()
            if not discover:
                runlog.save_json()
                report = write_report(run_dir / "report.html", runlog, meta)
                ui.summary(runlog, report)
            try:
                context.close()
            except Exception:
                pass
    return code


# --------------------------------------------------------------------------- doctor


def doctor(cfg: dict, ui, online: bool = False) -> int:
    from rich.table import Table
    from rich.text import Text

    checks: list[tuple[str, str, str]] = []   # (ok|warn|fail, what, detail)

    def add(state: str, what: str, detail: str = "") -> None:
        checks.append((state, what, detail))

    add("ok" if sys.version_info >= (3, 10) else "fail", "Python", sys.version.split()[0])
    prov = str(cfg["llm"]["provider"]).lower()
    sdk = {"gemini": ("google.genai", "google-genai"), "claude": ("anthropic", "anthropic")}.get(prov)
    for mod, pkg in [("playwright", "playwright"), ("yaml", "PyYAML"), ("rich", "rich")] + ([sdk] if sdk else []):
        found = importlib.util.find_spec(mod.split(".")[0]) is not None and (
            "." not in mod or importlib.util.find_spec(mod) is not None)
        add("ok" if found else "fail", f"package {pkg}", "installed" if found else "missing: pip install -e .")

    env = cfg["llm"]["api_key_env"].get(prov, "")
    key = os.environ.get(env, "")
    add("ok" if key else "fail", f"{prov} API key", f"{env} is set ({len(key)} chars)" if key else
        f"{env} is not set: add it to .env (see .env.example)")
    if key and online:
        from .llm import LLM, Request, make_provider
        try:
            reply = LLM(make_provider(cfg["llm"]), cfg).ask(Request("Reply with the single word OK.", "Ready?"))
            add("ok", f"{prov} live call", f"{cfg['llm']['models'][prov]} replied {reply.strip()[:20]!r}")
        except Exception as e:
            add("fail", f"{prov} live call", str(e)[:160])

    from playwright.sync_api import sync_playwright
    b = cfg["browser"]
    try:
        with sync_playwright() as p:
            kw = {"headless": True}
            if b.get("executable_path"):
                kw["executable_path"] = b["executable_path"]
            elif b.get("channel"):
                kw["channel"] = b["channel"]
            browser = p.chromium.launch(**kw)
            add("ok", "browser", f"{b.get('executable_path') or b.get('channel') or 'chromium'} {browser.version}")
            browser.close()
    except Exception as e:
        add("fail", "browser", f"{str(e).splitlines()[0][:120]} -> python -m playwright install chrome")

    profile = path(cfg, "profile", create=False)
    add("ok" if profile.exists() else "warn", "login profile",
        str(profile) if profile.exists() else "not created yet: run `kalbot discover` and log in once")

    from .github import GitHub, GitHubError
    gh = GitHub.from_config(cfg)
    tok = cfg["github"]["token_env"]
    if not gh:
        add("warn", "GitHub token", f"{tok} not set: repo-link tasks need a link in submissions.yaml")
    elif online:
        try:
            u = gh.user()
            add("ok", "GitHub token", f"user {u['login']}" + (f", scopes: {u['_scopes']}" if u.get("_scopes") else ""))
        except GitHubError as e:
            add("fail", "GitHub token", str(e))
    else:
        add("ok", "GitHub token", f"{tok} is set (use --online to test it)")

    from .links import LinkBook
    lf = cfg["_meta"]["home"] / cfg["links"]["file"]
    book = LinkBook.load(lf)
    if book.problems:
        add("warn", "links file", "; ".join(book.problems)[:200])
    else:
        add("ok" if lf.exists() else "warn", "links file",
            f"{len(book.entries)} entries in {lf.name}" if lf.exists() else
            f"no {lf.name} yet: copy submissions.example.yaml when you have video/repo links")
    for w in cfg["_meta"]["warnings"]:
        add("warn", "config", w)

    table = Table(show_header=False, show_edge=False, pad_edge=False, box=None)
    sym = {"ok": ("✓", "ok"), "warn": ("!", "warn"), "fail": ("✗", "err")}
    for state, what, detail in checks:
        s, style = sym[state]
        table.add_row(Text(s, style=style), Text(what, style="title"), Text(detail))
    ui.section(f"kalbot {__version__} doctor")
    ui.c.print(table)
    failed = sum(1 for c in checks if c[0] == "fail")
    if failed:
        ui.error(f"{failed} problem(s) to fix before running.")
        return 1
    ui.ok("Ready. Next: kalbot discover")
    return 0


if __name__ == "__main__":
    sys.exit(main())
