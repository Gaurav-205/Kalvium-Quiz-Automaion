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
    
    btn = page.locator('button:has-text("Resume Assignment"), [role="button"]:has-text("Resume Assignment")').first
    if btn.count() and btn.is_visible():
        print("Clicking Resume Assignment...")
        btn.click()
        page.wait_for_timeout(4000)
        
    print("Page URL after clicking Resume:", page.url)
    print("Frames count:", len(page.frames))
    for i, f in enumerate(page.frames):
        print(f"Frame {i}: url={f.url}")
        
    # Check elements in all frames
    for i, f in enumerate(page.frames):
        try:
            res = f.evaluate("""() => {
                return {
                    textareas: Array.from(document.querySelectorAll('textarea')).map(t => ({placeholder: t.placeholder, id: t.id, class: t.className})),
                    contenteditable: Array.from(document.querySelectorAll('[contenteditable="true"]')).length,
                    buttons: Array.from(document.querySelectorAll('button')).map(b => b.innerText.trim()).filter(Boolean),
                    body: document.body ? document.body.innerText.slice(0, 300) : ''
                };
            }""")
            print(f"Frame {i} content: {res}")
        except Exception as e:
            print(f"Frame {i} error: {e}")
            
    ctx.close()
