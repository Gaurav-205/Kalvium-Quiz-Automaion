import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from common import load_config
from playwright.sync_api import sync_playwright
from navigator import launch_browser
from quiz import QuizSolver

cfg = load_config('config.yaml')
with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto('https://app.kalvium.community/livebooks/2506/08f4e86c-6d85-4384-baaa-f27c1fa0ef54/lessons')
    page.wait_for_timeout(4000)
    
    # Check locate BEFORE clicking Start Assignment
    solver = QuizSolver(cfg, None, None, dry_run=True)
    frame, st, comp = solver.locate(page)
    print("BEFORE click locate:", st["kind"], "question:", st.get("question", "")[:60], "options:", len(st.get("options", [])))
    
    # Scroll to bottom
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(2000)

    btn = page.locator('button:has-text("Resume Assignment"), button:has-text("Start Assignment")').first
    print("btn count:", btn.count())
    if btn.count():
        print("btn text:", btn.inner_text())
        btn.click()
        page.wait_for_timeout(4000)
        
        # Check locate AFTER clicking
        frame2, st2, comp2 = solver.locate(page)
        print("AFTER click locate:", st2["kind"])
        print("  question:", st2.get("question", "")[:120])
        print("  options count:", len(st2.get("options", [])))
        for i, opt in enumerate(st2.get("options", [])):
            print(f"    [{i}] {opt[:80]}")
        print("  next button:", st2.get("nextBtn"))
        print("  submit button:", st2.get("submitBtn"))
        
    ctx.close()
