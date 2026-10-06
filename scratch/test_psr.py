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
    
    btns = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('button, [role="button"], a.btn')).map(b => ({
            text: (b.innerText || b.getAttribute('aria-label') || '').trim(),
            visible: b.offsetWidth > 0 && b.offsetHeight > 0
        })).filter(x => x.text);
    }""")
    print("All buttons on page:", btns)
    print("Page body snippet:\n", page.locator('body').inner_text()[:600])
    ctx.close()
