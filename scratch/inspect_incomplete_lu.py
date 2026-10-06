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
    # Check Philosophy LU 1.8 or 3.6 or 4.6
    urls = [
        'https://app.kalvium.community/livebooks/2505/fc0cace2-b230-45f6-9331-f2169c12210b/lessons',
        'https://app.kalvium.community/livebooks/2505/7480e7b6-e0b7-4edf-8e58-87bcf9f2e264/lessons',
    ]
    for u in urls:
        print("=" * 60)
        print("Visiting:", u)
        page.goto(u)
        page.wait_for_timeout(4000)
        print("Page URL:", page.url)
        btns = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button, [role="button"], a.btn, input[type="submit"]'))
                .map(b => (b.innerText || b.getAttribute('aria-label') || '').trim())
                .filter(Boolean);
        }""")
        print("Buttons/clickable roles:", btns)
        print("Body text snippet:\n", page.locator('body').inner_text()[:400])
    ctx.close()
