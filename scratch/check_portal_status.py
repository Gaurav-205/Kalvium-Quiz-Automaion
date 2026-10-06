import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from playwright.sync_api import sync_playwright
from common import load_config
from navigator import launch_browser, Navigator
from quiz import QuizSolver

cfg = load_config("config.yaml")

with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    solver = QuizSolver(cfg, None, None, dry_run=True)
    nav = Navigator(cfg, page, solver, None, dry_run=True, limit=0, livebook=None, lu=None)
    nav.wait_for_login()
    
    livebooks = nav._livebooks()
    print("Found livebooks:", [lb['name'] for lb in livebooks])
    
    target_names = [
        "Introduction to Philosophy",
        "Computer Organization and Architecture",
        "DevOps Foundations"
    ]
    
    for lb in livebooks:
        if not any(t.lower() in lb['name'].lower() for t in target_names):
            continue
        print(f"\n==========================================")
        print(f"CHECKING: {lb['name']}")
        print(f"==========================================")
        nav.open_livebook(lb)
        lus = nav.open_learning_path()
        completed_lus = [lu for lu in lus if lu.completed]
        incomplete_lus = [lu for lu in lus if not lu.completed]
        print(f"Total LUs: {len(lus)} | Completed: {len(completed_lus)} | Incomplete: {len(incomplete_lus)}")
        if incomplete_lus:
            print("Incomplete LUs:")
            for lu in incomplete_lus:
                print(f"  - LU {lu.number:>5} | {lu.title} ({lu.type_hint})")
                
    ctx.close()
