"""Kalvium LU quiz bot.

  python main.py --discover                 explore the portal, save snapshots, suggest selectors
  python main.py --dry-run --limit 1        read one quiz and print the chosen answers, click nothing
  python main.py --limit 1                  complete one quiz for real
  python main.py                            complete every unfinished quiz in the semester
  python main.py --livebook "philosophy" --lu 1.2
  python main.py --login                    plain-Chrome login fallback
"""

from __future__ import annotations

import argparse
import logging
import sys

from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

from common import ROOT, RunLog, folder, load_config, stamp
from llm import AnswerPicker, LLMError, make_provider
from navigator import Navigator, launch_browser, manual_login
from quiz import QuizSolver


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Complete unfinished Kalvium LU quizzes with an LLM.")
    ap.add_argument("--dry-run", action="store_true",
                    help="extract questions and print the chosen answers; never click quiz controls")
    ap.add_argument("--limit", type=int, default=None, help="stop after N quizzes (0 = no limit)")
    ap.add_argument("--livebook", help="only livebooks whose name contains this text")
    ap.add_argument("--lu", help='only this LU number, e.g. "1.2"')
    ap.add_argument("--semester", type=int, help="override portal.semester from config.yaml")
    ap.add_argument("--discover", action="store_true",
                    help="explore livebooks/LUs read-only, save snapshots and suggested selectors")
    ap.add_argument("--login", action="store_true",
                    help="open plain Chrome on the bot profile to log in (if Google blocks the automated window)")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"), help="path to config.yaml")
    return ap.parse_args(argv)


def setup_logging(log_path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("  %(levelname)s: %(message)s"))
    root.addHandler(console)
    for name in ("httpx", "httpcore", "google_genai", "anthropic", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # never crash on odd characters in a Windows console
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parse_args(argv)
    cfg = load_config(args.config)
    if args.semester:
        cfg["portal"]["semester"] = args.semester
    if args.login:
        return manual_login(cfg)

    dry = args.dry_run or bool(cfg["run"].get("dry_run"))
    limit = args.limit if args.limit is not None else int(cfg["run"].get("limit") or 0)
    ts = stamp()
    logs = folder(cfg, "logs")
    setup_logging(logs / f"run_{ts}.log")

    picker = None
    if not args.discover:
        try:
            picker = AnswerPicker(make_provider(cfg["llm"]), cfg["llm"])
        except LLMError as e:
            print(f"ERROR: {e}")
            return 2
        prov = cfg["llm"]["provider"]
        print(f"LLM: {prov} ({cfg['llm']['models'][prov]})")
        print(f"Mode: {'DRY-RUN (quiz controls are never clicked)' if dry else 'LIVE'}"
              f" | limit: {limit or 'none'}")

    runlog = RunLog(logs, ts)
    code = 0
    with sync_playwright() as p:
        try:
            context = launch_browser(p, cfg)
        except PWError as e:
            print(f"Could not start Chrome: {str(e).splitlines()[0]}")
            print("Is another bot window still open (profile in use)? Is Chrome installed? "
                  "Try: python -m playwright install chrome")
            runlog.close()
            return 2
        page = context.pages[0] if context.pages else context.new_page()
        solver = QuizSolver(cfg, picker, runlog, dry_run=dry)
        nav = Navigator(cfg, page, solver, runlog, dry_run=dry, limit=limit,
                        livebook=args.livebook, lu=args.lu)
        try:
            nav.wait_for_login()
            if args.discover:
                nav.discover(config_path=args.config)
            else:
                nav.run()
        except KeyboardInterrupt:
            print("\nInterrupted.")
            code = 130
        except LLMError as e:
            print(f"\nStopping: {e}")
            code = 2
        except PWError as e:
            print(f"\nBrowser error (was the window closed?): {str(e).splitlines()[0]}")
            code = 1
        finally:
            if not args.discover:
                print(runlog.summary())
            runlog.close()
            try:
                context.close()
            except Exception:
                pass
    return code


if __name__ == "__main__":
    sys.exit(main())
