import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import load_config, ensure_helpers
from playwright.sync_api import sync_playwright
from navigator import launch_browser
from quiz import QuizSolver

cfg = load_config('config.yaml')
with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto('https://app.kalvium.community/livebooks/2505/fc0cace2-b230-45f6-9331-f2169c12210b/lessons')
    page.wait_for_timeout(4000)
    
    # Click Resume Assignment or Retake Assignment if visible
    btn = page.locator('button:has-text("Resume Assignment"), button:has-text("Retake Assignment"), button:has-text("Start Assignment")').first
    if btn.count() and btn.is_visible():
        btn.click()
        page.wait_for_timeout(4000)
        
    print("Frames count:", len(page.frames))
    for i, f in enumerate(page.frames):
        print(f"\n--- FRAME {i} ({f.url}) ---")
        try:
            ensure_helpers(f)
            st = f.evaluate("cfg => window.__kqb ? window.__kqb.state(cfg) : 'no_kqb'", cfg["texts"])
            print("kqb state:", st)
        except Exception as e:
            print("eval error:", e)
            
    # Check textarea
    ta = page.locator('textarea.w-md-editor-text-input')
    print("Textarea count:", ta.count())
    if ta.count():
        print("Textarea value:", ta.first.input_value()[:100])
        
    # Check problem statement text
    ps = page.locator('text="Problem Statement"').first
    print("Problem Statement visible:", ps.is_visible() if ps.count() else False)
    
    body = page.locator('body').inner_text()
    print("\nBody text overview (first 1000 chars):\n", body[:1000])
    
    ctx.close()
