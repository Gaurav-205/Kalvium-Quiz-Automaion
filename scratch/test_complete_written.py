import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import load_config
from playwright.sync_api import sync_playwright
from navigator import launch_browser
from llm import make_provider

cfg = load_config('config.yaml')
prov = make_provider(cfg['llm'])

with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto('https://app.kalvium.community/livebooks/2505/fc0cace2-b230-45f6-9331-f2169c12210b/lessons')
    page.wait_for_timeout(4000)
    
    # Click Resume / Start / Retake Assignment if needed
    btn = page.locator('button:has-text("Resume Assignment"), button:has-text("Start Assignment"), button:has-text("Retake Assignment")').first
    if btn.count() and btn.is_visible():
        btn.click()
        page.wait_for_timeout(3000)
        confirm = page.locator('button:has-text("Proceed"), button:has-text("Start"), button:has-text("Yes")').first
        if confirm.count() and confirm.is_visible():
            confirm.click()
            page.wait_for_timeout(3000)
            
    # Find textarea
    ta = page.locator('textarea.w-md-editor-text-input').first
    if not ta.count():
        print("No textarea found!")
        ctx.close()
        sys.exit(1)
        
    # Extract Problem Statement
    body_text = page.locator('body').inner_text()
    prob_idx = body_text.find("Problem Statement")
    if prob_idx != -1:
        prob_text = body_text[prob_idx:prob_idx + 2500]
    else:
        prob_text = body_text[:2500]
    print("Problem Statement:\n", prob_text[:400])
    
    # Prompt LLM to solve the assignment
    sys_prompt = "You are an expert student submitting an academic assignment. Fulfill all instructions, word count limits, formatting, and structural guidelines with precision."
    user_prompt = f"Solve this assignment completely and concisely according to all instructions:\n\n{prob_text}"
    print("\nAsking LLM to generate response...")
    ans = prov.complete(sys_prompt, user_prompt)
    print("\nGenerated Answer Preview:\n", ans[:300])
    print(f"\nTotal characters: {len(ans)}, words: {len(ans.split())}")
    
    # Fill into textarea
    ta.click()
    ta.fill(ans)
    page.wait_for_timeout(2000)
    print("Filled into textarea successfully!")
    
    # Check buttons
    save_btn = page.locator('button:has-text("Save"), [role="button"]:has-text("Save")').first
    if save_btn.count() and save_btn.is_visible():
        print("Clicking Save...")
        save_btn.click()
        page.wait_for_timeout(2000)
        
    submit_btn = page.locator('button:has-text("Submit"), [role="button"]:has-text("Submit")').first
    print("Submit button visible:", submit_btn.is_visible() if submit_btn.count() else False)
    if submit_btn.count() and submit_btn.is_visible():
        print("Clicking Submit...")
        submit_btn.click()
        page.wait_for_timeout(3000)
        
        # Confirm dialog if any
        conf = page.locator('button:has-text("Yes"), button:has-text("Proceed"), button:has-text("Confirm")').first
        if conf.count() and conf.is_visible():
            print("Confirming submit:", conf.inner_text())
            conf.click()
            page.wait_for_timeout(4000)
            
    print("Final page URL:", page.url)
    print("Result snippet:", page.locator('body').inner_text()[:400])
    ctx.close()
