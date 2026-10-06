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
    btn = page.locator('button:has-text("Retake Assignment"), [role="button"]:has-text("Retake Assignment")').first
    if btn.count():
        print("Clicking Retake Assignment...")
        btn.click()
        page.wait_for_timeout(2000)
        confirm = page.locator('button:has-text("Proceed"), button:has-text("Start"), button:has-text("Yes")').first
        if confirm.count() and confirm.is_visible():
            print("Confirm button:", confirm.inner_text())
            confirm.click()
            page.wait_for_timeout(3000)
            
        solver = QuizSolver(cfg, None, None, dry_run=True)
        frame, st, comp = solver.locate(page)
        print("Locate state:", st['kind'])
        print("Question:", st.get('question', '')[:200])
        print("Options:", st.get('options'))
        tc = page.locator('#test-component')
        if tc.count():
            print("test-component text:\n", tc.inner_text()[:600])
    ctx.close()
