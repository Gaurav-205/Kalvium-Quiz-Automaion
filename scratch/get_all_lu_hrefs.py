import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from common import load_config
from playwright.sync_api import sync_playwright
from navigator import launch_browser, Navigator
from quiz import QuizSolver

cfg = load_config("config.yaml")
with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    solver = QuizSolver(cfg, None, None, dry_run=True)
    nav = Navigator(cfg, page, solver, None, dry_run=True, limit=0, livebook="philosophy", lu=None)
    nav.wait_for_login()
    lb = nav._livebooks()[0]
    nav.open_livebook(lb)
    lus = nav.open_learning_path()
    for lu in lus:
        print(f"LU {lu.number:>5} | href: {lu.href} | title: {lu.title}")
    ctx.close()
