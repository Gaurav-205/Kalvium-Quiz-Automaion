"""kalbot command line: run, discover, login, doctor."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import re
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, get_env, load_config, path, stamp

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
  kalbot run --retake                     also retake submitted quizzes without full marks
  kalbot login                            plain-Chrome login if Google blocks the automated window
"""

COMMANDS = ("run", "discover", "login", "doctor")
TASK_KINDS = ("quiz", "written", "coding", "links")


def parse_args(argv=None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    for flag, cmd in (("--discover", "discover"), ("--login", "login"), ("--doctor", "doctor")):
        if flag in argv:  # flags from the old `python main.py --discover`
            argv.remove(flag)
            argv.insert(0, cmd)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "-V", "--version")):
        argv.insert(0, "run")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="settings file (default: config.yaml in the kalbot folder)")
    common.add_argument("--semester", type=int, help="override portal.semester")
    common.add_argument("--headless", action="store_true", help="run Chrome without a window")

    ap = argparse.ArgumentParser(
        prog="kalbot",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Complete unfinished Kalvium quizzes, written, coding and link assignments with an LLM.",
    )
    ap.add_argument("-V", "--version", action="version", version=f"kalbot {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="command")
    run = sub.add_parser("run", parents=[common], help="complete unfinished work (default)")
    run.add_argument("--dry-run", action="store_true", help="read and draft everything, submit nothing")
    run.add_argument("--limit", type=int, default=None, help="stop after N quizzes/assignments (0 = no limit)")
    run.add_argument("--livebook", help="only livebooks whose name contains this text")
    run.add_argument("--lu", help='only this LU number, e.g. "1.2"')
    run.add_argument("--only", help=f"comma-separated kinds to do: {','.join(TASK_KINDS)}")
    run.add_argument(
        "--retake",
        action="store_true",
        help="also retake submitted quizzes that don't have full marks (opens completed LUs too)",
    )
    run.add_argument(
        "--fast",
        "--turbo",
        dest="fast",
        action="store_true",
        help="run at maximum speed (minimal pauses, aggressive settling, zero LLM rate-limit gap)",
    )
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--auto", action="store_true", help="don't stop to review written/coding/link drafts")
    mode.add_argument("--review", action="store_true", help="always stop to review drafts (overrides run.review)")
    sub.add_parser("discover", parents=[common], help="explore read-only, save snapshots and selectors")
    sub.add_parser("login", parents=[common], help="open plain Chrome on the bot profile to log in")
    doc = sub.add_parser("doctor", parents=[common], help="check Python, Chrome, keys and settings")
    doc.add_argument("--online", action="store_true", help="also test the LLM key and GitHub token with a live call")
    return ap.parse_args(argv)


class RedactingFilter(logging.Filter):
    """Masks API keys and authentication tokens in log records so secrets are never exposed."""

    PATTERNS = [
        re.compile(r"AIza[0-9A-Za-z-_]{35}"),
        re.compile(r"sk-ant-[0-9A-Za-z-_]{20,}"),
        re.compile(r"ghp_[0-9A-Za-z]{36}"),
        re.compile(r"github_pat_[0-9A-Za-z_]{60,}"),
        re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/-]+=*", re.I),
        re.compile(r"((?:api[_-]?key|token)\s*[:=]\s*['\"]?)[A-Za-z0-9-_]{12,}(['\"]?)", re.I),
        re.compile(r"([?&](?:key|token|api_key|auth)=)[^&\s]+", re.I),
        re.compile(r"(Authorization\s*:\s*(?:Bearer\s+)?)[^\r\n,\s]+", re.I),
    ]

    def _redact_str(self, text: str) -> str:
        for pat in self.PATTERNS:
            text = pat.sub(r"[REDACTED_SECRET]", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact_str(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                clean_dict = dict(record.args)
                for k, v in clean_dict.items():
                    if isinstance(v, str):
                        clean_dict[k] = self._redact_str(v)
                record.args = clean_dict
            elif isinstance(record.args, (tuple, list)):
                new_args = []
                for a in record.args:
                    if isinstance(a, str):
                        new_args.append(self._redact_str(a))
                    elif isinstance(a, Exception):
                        new_args.append(self._redact_str(str(a)))
                    else:
                        new_args.append(a)
                record.args = tuple(new_args) if isinstance(record.args, tuple) else new_args
        if getattr(record, "exc_text", None) and isinstance(record.exc_text, str):
            record.exc_text = self._redact_str(record.exc_text)
        return True


def setup_logging(log_path: Path, console) -> None:
    from rich.logging import RichHandler

    root = logging.getLogger()
    for h in list(root.handlers):  # repeated runs in one process (tests) must not stack handlers
        if getattr(h, "_kalbot", False):
            root.removeHandler(h)
            h.close()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    con = RichHandler(console=console, show_time=False, show_path=False, markup=False, level=logging.WARNING)
    redactor = RedactingFilter()
    for h in (fh, con):
        h._kalbot = True
        h.addFilter(redactor)
        root.addHandler(h)
    for name in ("httpx", "httpx2", "httpcore", "google_genai", "anthropic", "urllib3"):
        logging.getLogger(name).setLevel(logging.ERROR)


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
    for stream in (sys.stdout, sys.stderr):  # never crash on odd characters in a Windows console
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
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
        if getattr(args, "retake", False):
            cfg["run"]["retake_completed"] = True
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
    from .pipeline import ExecutionPipeline

    discover = args.command == "discover"
    dry = discover or getattr(args, "dry_run", False) or bool(cfg["run"].get("dry_run"))
    enabled = task_filter(cfg, getattr(args, "only", None))

    run_dir = new_run_dir(path(cfg, "runs"), "_discover" if discover else "_dry-run" if dry else "")
    setup_logging(run_dir / "run.log", ui.c)

    pipeline = ExecutionPipeline(cfg, args, ui, run_dir, enabled)
    return pipeline.execute()


# --------------------------------------------------------------------------- doctor


def doctor(cfg: dict, ui, online: bool = False) -> int:
    from rich.table import Table
    from rich.text import Text

    for name in ("httpx", "httpx2", "httpcore", "google_genai", "anthropic", "urllib3"):
        logging.getLogger(name).setLevel(logging.ERROR)

    checks: list[tuple[str, str, str]] = []  # (ok|warn|fail, what, detail)

    def add(state: str, what: str, detail: str = "") -> None:
        checks.append((state, what, detail))

    add("ok" if sys.version_info >= (3, 10) else "fail", "Python", sys.version.split()[0])
    prov = str(cfg["llm"]["provider"]).lower()
    sdk = {"gemini": ("google.genai", "google-genai"), "claude": ("anthropic", "anthropic")}.get(prov)
    for mod, pkg in [("playwright", "playwright"), ("yaml", "PyYAML"), ("rich", "rich")] + ([sdk] if sdk else []):
        found = importlib.util.find_spec(mod.split(".")[0]) is not None and (
            "." not in mod or importlib.util.find_spec(mod) is not None
        )
        add("ok" if found else "fail", f"package {pkg}", "installed" if found else "missing: pip install -e .")

    env = cfg["llm"]["api_key_env"].get(prov, "")
    key = get_env(env) if env else ""
    add(
        "ok" if key else "fail",
        f"{prov} API key",
        f"{env} is set ({len(key)} chars)" if key else f"{env} is not set: add it to .env (see .env.example)",
    )

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
    add(
        "ok" if profile.exists() else "warn",
        "login profile",
        str(profile) if profile.exists() else "not created yet: run `kalbot discover` and log in once",
    )

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
        add(
            "ok" if lf.exists() else "warn",
            "links file",
            f"{len(book.entries)} entries in {lf.name}"
            if lf.exists()
            else f"no {lf.name} yet: copy submissions.example.yaml when you have video/repo links",
        )
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
