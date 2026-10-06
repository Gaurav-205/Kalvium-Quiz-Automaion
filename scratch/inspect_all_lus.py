import sys
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import load_config, ensure_helpers, js_config
from playwright.sync_api import sync_playwright
from navigator import launch_browser

cfg = load_config('config.yaml')
jscfg = js_config(cfg)

with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    
    for lb_id, name in [(2509, "DAA Lab"), (2510, "Integrated Work - III")]:
        print(f"\n==================== {name} ({lb_id}) ====================")
        page.goto(f"https://app.kalvium.community/livebooks/{lb_id}", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        
        lp = page.locator('text=Learning Path').first
        if lp.count() and lp.is_visible():
            lp.click()
            page.wait_for_timeout(3000)
            
        ensure_helpers(page.main_frame)
        
        # Expand modules
        for _ in range(10):
            n = page.evaluate("cfg => window.__kqb.collapsedModules(cfg)", jscfg)
            if not n:
                break
            page.locator('[data-kqb-mod="collapsed"]').first.click()
            page.wait_for_timeout(500)
            
        lus = page.evaluate("cfg => window.__kqb.listLUs(cfg)", jscfg)
        print(f"Total LUs in {name}: {len(lus)}")
        for item in lus[:15]:
            print(f"  LU {item.get('number')}: {item.get('title')} (done={item.get('completed')}, hint={item.get('type_hint')}, href={item.get('href')})")
            
    ctx.close()
