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
    page.goto('https://app.kalvium.community/livebooks/2506/08f4e86c-6d85-4384-baaa-f27c1fa0ef54/lessons')
    page.wait_for_timeout(4000)
    btns = page.evaluate("""() => Array.from(document.querySelectorAll('button, [role="button"], a.btn')).map(b => (b.innerText || b.getAttribute('aria-label') || '').trim()).filter(Boolean)""")
    print('Buttons:', btns)
    print('Body text:\n', page.locator('body').inner_text()[:600])
    ctx.close()
