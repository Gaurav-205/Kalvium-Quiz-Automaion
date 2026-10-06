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
    page.goto('https://app.kalvium.community/livebooks/2505/fc0cace2-b230-45f6-9331-f2169c12210b/lessons')
    page.wait_for_timeout(4000)
    
    solver = QuizSolver(cfg, None, None, dry_run=True)
    frame, st, completed = solver.locate(page)
    print("Initial locate:", st)
    
    # Check if 'Retake Assignment' button exists
    btn = page.locator('button:has-text("Retake Assignment"), [role="button"]:has-text("Retake Assignment")').first
    if btn.count():
        print("Clicking Retake Assignment...")
        btn.click()
        page.wait_for_timeout(2000)
        
        # Check for confirmation modal (e.g. Proceed / Yes / Start)
        confirm = page.locator('button:has-text("Proceed"), button:has-text("Start"), button:has-text("Yes")').first
        if confirm.count() and confirm.is_visible():
            print("Confirm button:", confirm.inner_text())
            confirm.click()
            page.wait_for_timeout(3000)
            
        frame2, st2, comp2 = solver.locate(page)
        print("After click locate:", st2)
        print("Page URL:", page.url)
        tc = page.locator('#test-component')
        print("test-component count:", tc.count())
        if tc.count():
            print("test-component text:\n", tc.inner_text()[:400])
        else:
            print("Body text after click:\n", page.locator('body').inner_text()[:400])
            
    ctx.close()
