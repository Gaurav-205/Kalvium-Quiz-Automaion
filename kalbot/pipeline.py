"""Execution Pipeline: structured orchestration from initialization to teardown.

Phases:
1. INIT: logging setup, workspace path resolution, directory creation
2. VALIDATION: configuration validation and overrides
3. SERVICES: LLM provider, link book, GitHub client, persistent State journal
4. BROWSER_SESSION: Playwright launch and login verification
5. EXECUTION: task discovery / processing with idempotency checks
6. REPORTING & CLEANUP: results.csv, results.json, report.html, summary and browser closure
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

from . import __version__
from .browser import PageOps, first_line, launch_browser
from .config import path
from .github import GitHub
from .links import LinkBook, State
from .llm import LLM, LLMError, make_provider
from .portal import Navigator
from .quiz import QuizSolver
from .report import write_report
from .runlog import RunLog
from .tasks import TaskSolver
from .ui import QuitRun

log = logging.getLogger("kalbot.pipeline")


class PipelinePhase(Enum):
    INIT = "initialization"
    VALIDATION = "configuration_validation"
    SERVICES = "services_setup"
    BROWSER_SESSION = "browser_session"
    EXECUTION = "task_execution"
    CLEANUP = "cleanup"
    SUMMARY = "summary"


@dataclass
class PipelineContext:
    cfg: dict
    args: Any
    ui: Any
    run_dir: Path
    meta: list[tuple[str, str]] = field(default_factory=list)
    discover: bool = False
    dry_run: bool = False
    limit: int = 0
    enabled: dict[str, bool] = field(default_factory=dict)
    review: bool = True
    llm: LLM | None = None
    linkbook: LinkBook | None = None
    state: State | None = None
    github: GitHub | None = None
    runlog: RunLog | None = None
    phase: PipelinePhase = PipelinePhase.INIT


class ExecutionPipeline:
    """Manages the end-to-end execution lifecycle of kalbot runs and discovery."""

    def __init__(self, cfg: dict, args: Any, ui: Any, run_dir: Path, enabled: dict[str, bool]):
        self.ctx = PipelineContext(
            cfg=cfg,
            args=args,
            ui=ui,
            run_dir=run_dir,
            discover=args.command == "discover",
            dry_run=(args.command == "discover" or getattr(args, "dry_run", False) or bool(cfg["run"].get("dry_run"))),
            limit=(args.limit if getattr(args, "limit", None) is not None else int(cfg["run"].get("limit") or 0)),
            enabled=enabled,
            review=(
                (bool(cfg["run"].get("review", True)) or getattr(args, "review", False))
                and not getattr(args, "auto", False)
            ),
        )

    def prepare_services(self) -> int | None:
        """Instantiate LLM provider, link book, GitHub client, and log metadata banner."""
        self.ctx.phase = PipelinePhase.SERVICES
        prov = self.ctx.cfg["llm"]["provider"]
        if not self.ctx.discover:
            try:
                self.ctx.llm = LLM(make_provider(self.ctx.cfg["llm"]), self.ctx.cfg)
            except LLMError as e:
                self.ctx.ui.error(str(e))
                return 2

        home = self.ctx.cfg["_meta"]["home"]
        self.ctx.linkbook = LinkBook.load(home / self.ctx.cfg["links"]["file"])
        for problem in self.ctx.linkbook.problems:
            self.ctx.ui.warn(problem)

        self.ctx.state = State(path(self.ctx.cfg, "state"))
        self.ctx.github = GitHub.from_config(self.ctx.cfg) if self.ctx.cfg["github"].get("auto_create") else None

        meta = [
            (
                "Mode",
                (
                    "DISCOVER (read-only)"
                    if self.ctx.discover
                    else "DRY-RUN (nothing is submitted)"
                    if self.ctx.dry_run
                    else "LIVE"
                ),
            )
        ]
        if not self.ctx.discover:
            meta += [
                ("LLM", f"{prov} · {self.ctx.cfg['llm']['models'][prov]}"),
                ("Doing", ", ".join(k for k, v in self.ctx.enabled.items() if v) or "nothing"),
                ("Review", "before each written/coding/link submission" if self.ctx.review else "off (--auto)"),
                ("Limit", str(self.ctx.limit or "none")),
                (
                    "Links",
                    (
                        f"{len(self.ctx.linkbook.entries)} LU"
                        f"{'s' * (len(self.ctx.linkbook.entries) != 1)} in {self.ctx.cfg['links']['file']}"
                    )
                    if self.ctx.linkbook.entries
                    else f"none yet ({self.ctx.cfg['links']['file']})",
                ),
                ("GitHub", "creates repos for repo-link tasks" if self.ctx.github else "off (no GITHUB_TOKEN)"),
            ]
        meta.append(("Output", str(self.ctx.run_dir)))
        self.ctx.meta = meta
        self.ctx.ui.banner(f"kalbot {__version__}", meta)
        return None

    def execute(self) -> int:
        """Run through browser launch, session verification, task processing, and cleanup."""
        prep_error = self.prepare_services()
        if prep_error is not None:
            return prep_error

        self.ctx.phase = PipelinePhase.BROWSER_SESSION
        self.ctx.runlog = RunLog(self.ctx.run_dir)
        code = 0

        with sync_playwright() as p:
            try:
                context = launch_browser(p, self.ctx.cfg)
            except PWError as e:
                self.ctx.ui.error(f"Could not start Chrome: {first_line(e)}")
                self.ctx.ui.note(
                    "Is another kalbot window still open (profile in use)? Is Chrome installed? "
                    "Try: python -m playwright install chrome"
                )
                self.ctx.runlog.close()
                return 2

            page = context.pages[0] if context.pages else context.new_page()
            ops = PageOps(self.ctx.cfg, page)
            quiz = QuizSolver(
                self.ctx.cfg,
                ops,
                self.ctx.llm,
                self.ctx.runlog,
                self.ctx.ui,
                self.ctx.run_dir / "snapshots",
                self.ctx.dry_run,
            )
            tasks = TaskSolver(
                self.ctx.cfg,
                ops,
                self.ctx.llm,
                self.ctx.runlog,
                self.ctx.ui,
                linkbook=self.ctx.linkbook,
                state=self.ctx.state,
                github=self.ctx.github,
                run_dir=self.ctx.run_dir,
                dry_run=self.ctx.dry_run,
                review=self.ctx.review,
                enabled=self.ctx.enabled,
            )
            nav = Navigator(
                self.ctx.cfg,
                ops,
                quiz,
                tasks,
                self.ctx.runlog,
                self.ctx.ui,
                run_dir=self.ctx.run_dir,
                dry_run=self.ctx.dry_run,
                limit=self.ctx.limit,
                livebook=getattr(self.ctx.args, "livebook", None),
                lu=getattr(self.ctx.args, "lu", None),
                enabled=self.ctx.enabled,
            )

            try:
                log.info("Pipeline stage: AUTH_SESSION")
                nav.wait_for_login()
                self.ctx.phase = PipelinePhase.EXECUTION
                if self.ctx.discover:
                    log.info("Pipeline stage: DISCOVERY")
                    nav.discover(self.ctx.cfg["_meta"]["config_path"])
                else:
                    log.info("Pipeline stage: TASK_PROCESSING")
                    nav.run()
            except (KeyboardInterrupt, QuitRun):
                self.ctx.ui.warn("Stopped.")
                code = 130
            except LLMError as e:
                self.ctx.ui.error(f"Stopping: {e}")
                code = 2
            except PWError as e:
                self.ctx.ui.error(f"Browser error (was the window closed?): {first_line(e)}")
                code = 1
            finally:
                self.ctx.phase = PipelinePhase.CLEANUP
                log.info("Pipeline stage: CLEANUP & SUMMARY")
                self.ctx.runlog.close()
                if not self.ctx.discover:
                    self.ctx.runlog.save_json()
                    report = write_report(self.ctx.run_dir / "report.html", self.ctx.runlog, self.ctx.meta)
                    self.ctx.ui.summary(self.ctx.runlog, report)
                try:
                    context.close()
                except Exception:
                    pass
        return code
