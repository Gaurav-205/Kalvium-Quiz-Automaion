import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import load_config
from playwright.sync_api import sync_playwright
from navigator import launch_browser

cfg = load_config('config.yaml')
with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto('https://app.kalvium.community/livebooks/2505/fc0cace2-b230-45f6-9331-f2169c12210b/lessons')
    page.wait_for_timeout(4000)
    
    # Check if Resume / Retake is present
    btn = page.locator('button:has-text("Resume Assignment"), button:has-text("Retake Assignment")').first
    if btn.count() and btn.is_visible():
        btn.click()
        page.wait_for_timeout(3000)
        conf = page.locator('button:has-text("Proceed"), button:has-text("Start"), button:has-text("Yes")').first
        if conf.count() and conf.is_visible():
            conf.click()
            page.wait_for_timeout(3000)
            
    # Check submit button
    sub = page.locator('button:has-text("Submit"), [role="button"]:has-text("Submit")').first
    print("Submit button:", sub.inner_text() if sub.count() else "not found")
    if sub.count() and sub.is_visible():
        sub.click()
        page.wait_for_timeout(2000)
        # Check all modals / dialogs / buttons
        dialogs = page.evaluate("""() => {
            return {
                modals: Array.from(document.querySelectorAll('[role="dialog"], .modal, [class*="modal"], [class*="dialog"]')).map(m => m.innerText),
                buttons: Array.from(document.querySelectorAll('button, [role="button"]')).map(b => b.innerText.trim()).filter(Boolean)
            };
        }""")
        print("After clicking Submit:", dialogs)
    ctx.close()
