import sys
import re
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import load_config
from playwright.sync_api import sync_playwright
from navigator import launch_browser
from llm import make_provider

cfg = load_config('config.yaml')
prov = make_provider(cfg['llm'])

def test_on_url(page, url, name):
    print(f"\n==================== Testing: {name} ====================")
    print("Navigating to:", url)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    
    # Check if lessons button exists
    if not page.url.rstrip("/").endswith("/lessons"):
        btn = page.locator('button[aria-label="Go to Lessons"], a[href$="/lessons"], button:has-text("Go to Lessons")').first
        if btn.count() and btn.is_visible():
            btn.click()
            page.wait_for_timeout(3000)
            
    print("Current URL:", page.url)
    
    # Check for Start / Resume / Retake Assignment
    start_btn = page.locator('button:has-text("Start Assignment"), button:has-text("Resume Assignment"), button:has-text("Retake Assignment"), [role="button"]:has-text("Start Assignment"), [role="button"]:has-text("Resume Assignment")').first
    if start_btn.count() and start_btn.is_visible():
        print("Clicking assignment button:", start_btn.inner_text().strip())
        start_btn.click()
        page.wait_for_timeout(3000)
        conf = page.locator('button:has-text("Proceed"), button:has-text("Start"), button:has-text("Yes")').first
        if conf.count() and conf.is_visible():
            print("Confirming modal:", conf.inner_text().strip())
            conf.click()
            page.wait_for_timeout(3000)
            
    # Check elements
    pr_inp = page.locator('input#pr, input[placeholder*="github" i], input[placeholder*="pull" i]').first
    vid_inp = page.locator('input#video, input[placeholder*="drive" i], input[placeholder*="loom" i]').first
    ta = page.locator('textarea.w-md-editor-text-input, textarea:not([readonly])').first
    monaco = page.locator('.monaco-editor').first
    
    print("Has PR Input:", pr_inp.count() and pr_inp.is_visible())
    print("Has Video Input:", vid_inp.count() and vid_inp.is_visible())
    print("Has Textarea:", ta.count() and ta.is_visible())
    print("Has Monaco Editor:", monaco.count() and monaco.is_visible())
    
    btns = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('button, [role="button"]')).map(b => b.innerText.trim()).filter(Boolean);
    }""")
    print("Visible buttons:", btns)

with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    
    # Test 1: DevOps LU 2.3 or 1.8 (Written)
    # Test 2: DAA Lab LU 2.2 (Closest Pair)
    # Test 3: Integrated Work - III LU 2.12 (PR URL)
    
    # Let's test DAA Lab LU 2.2
    # To find the exact URL for DAA Lab LU 2.2, let's navigate to livebook 2509 learning path:
    print("Navigating to DAA Lab (2509)...")
    page.goto('https://app.kalvium.community/livebooks/2509', wait_until='domcontentloaded')
    page.wait_for_timeout(4000)
    
    # Click Learning Path
    lp = page.locator('text=Learning Path').first
    if lp.count() and lp.is_visible():
        lp.click()
        page.wait_for_timeout(3000)
        
    # Find LU 2.2
    lu22 = page.locator('text=Closest Pair of Points').first
    if lu22.count():
        print("Found LU 2.2 on Learning Path, clicking...")
        lu22.click()
        page.wait_for_timeout(4000)
        test_on_url(page, page.url, "DAA Lab 2.2 Closest Pair")
    else:
        print("Could not find LU 2.2 on Learning Path")
        
    ctx.close()
