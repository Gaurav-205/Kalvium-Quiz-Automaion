import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from playwright.sync_api import sync_playwright
from common import load_config, js_config
from navigator import launch_browser
from quiz import QuizSolver

cfg = load_config("config.yaml")
with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://app.kalvium.community/livebooks/2505/c03c7d37-c328-4790-afcf-0d0c1a25c856/lessons", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    
    solver = QuizSolver(cfg, None, None, dry_run=True)
    frame, st, completed = solver.locate(page)
    print("st:", st)
    print("completed:", completed)
    
    # check all text on the page related to score or attempts
    texts = page.locator("body").inner_text()
    for line in texts.split("\n"):
        if any(w in line.lower() for w in ["score", "master", "attempt", "5", "result", "grade", "%"]):
            print("  LINE:", line.strip())
            
    ctx.close()
