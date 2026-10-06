import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

from common import load_config
from navigator import launch_browser

cfg = load_config("config.yaml")

with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    # Go to COA Learning Path
    page.goto("https://app.kalvium.community/livebooks?semester=5")
    page.wait_for_timeout(3000)
    
    # Click Computer Organization and Architecture
    page.locator('text="Computer Organization and Architecture"').first.click()
    page.wait_for_timeout(3000)
    
    # Open Learning Path
    page.locator('button:has-text("Learning Path"), [role="tab"]:has-text("Learning Path"), a:has-text("Learning Path")').first.click()
    page.wait_for_timeout(3000)
    
    # Find LU 3.7
    row = page.locator('a:has-text("3.7"), tr:has-text("3.7"), div:has-text("3.7")').first
    print("Row found:", row.count())
    href = row.get_attribute("href")
    print("Href:", href)
    
    # Click 3.7
    row.click()
    page.wait_for_timeout(5000)
    print("Navigated to:", page.url)
    
    # Check if /lessons
    if not page.url.endswith("/lessons"):
        btn = page.locator('button:has-text("Go to Lessons"), a[href$="/lessons"]').first
        if btn.is_visible(timeout=3000):
            btn.click()
            page.wait_for_timeout(5000)
            print("Lessons URL:", page.url)
            
    # Scroll down to bottom
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(2000)
    
    # Check for #test-component or quiz buttons
    test_comp = page.locator('#test-component')
    print("test-component count:", test_comp.count())
    if test_comp.count():
        print("test-component innerText:", test_comp.inner_text()[:300])
        
    # Check buttons on page
    buttons = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('button')).map(b => b.innerText.trim()).filter(Boolean);
    }""")
    print("Buttons found on page:", buttons)
    
    # Check if quiz completed / score
    body_text = page.evaluate("() => document.body.innerText")
    for keyword in ["Quiz", "Assessment", "Score", "Submit", "Start", "Retake"]:
        if keyword in body_text:
            print(f"Keyword '{keyword}' present in page text")
            
    ctx.close()
