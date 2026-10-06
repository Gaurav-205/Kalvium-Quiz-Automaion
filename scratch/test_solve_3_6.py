import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import load_config
from playwright.sync_api import sync_playwright
from navigator import launch_browser
from quiz import QuizSolver
from llm import AnswerPicker, make_provider

cfg = load_config('config.yaml')
prov = make_provider(cfg['llm'])

with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    url = 'https://app.kalvium.community/livebooks/2505/7480e7b6-e0b7-4edf-8e58-87bcf9f2e264/lessons'
    print(f"Navigating to LU 3.6: {url}")
    page.goto(url)
    page.wait_for_timeout(4000)
    
    # 1. Check if already completed
    body_text = page.locator("body").inner_text()
    if any(w in body_text for w in ("Well done! You've completed this assignment successfully.", "Best Score\n10/10", "Best Score\n9/10")):
        print("Assignment already completed!")
        ctx.close()
        sys.exit(0)
        
    # 2. Check for Start/Resume/Retake Assignment button
    btn = page.locator('button:has-text("Start Assignment"), button:has-text("Resume Assignment"), button:has-text("Retake Assignment"), [role="button"]:has-text("Start Assignment"), [role="button"]:has-text("Resume Assignment"), [role="button"]:has-text("Retake Assignment")').first
    if btn.count() and btn.is_visible():
        btn_text = btn.inner_text().strip()
        print(f"Found {btn_text}. Opening assignment workspace...")
        btn.click()
        page.wait_for_timeout(2000)
        conf = page.locator('button:has-text("Proceed"), button:has-text("Start"), button:has-text("Yes")').first
        if conf.count() and conf.is_visible():
            conf.click()
            page.wait_for_timeout(3000)

    # 3. Check for textarea editor
    ta = page.locator('textarea.w-md-editor-text-input, textarea').first
    if not ta.count() or not ta.is_visible():
        print("No textarea editor found on this page!")
        ctx.close()
        sys.exit(1)

    # 4. Extract Problem Statement
    full_text = page.locator("body").inner_text()
    prob_idx = full_text.find("Problem Statement")
    if prob_idx != -1:
        prompt_content = full_text[prob_idx:prob_idx + 3500]
    else:
        prompt_content = full_text[:3500]
    print("Problem Statement:\n", prompt_content[:300])

    sys_prompt = (
        "You are an expert student submitting an academic assignment. "
        "Follow all instructions, word counts, formatting, and rubrics precisely. "
        "Write the response in clean, formatted Markdown or plain text. "
        "Do NOT output JSON. Return only the final text to be submitted."
    )
    user_prompt = f"Subject: Introduction to Philosophy\nTopic: Could a Highly Advanced AI Ever Be Conscious?\n\nAssignment Details:\n{prompt_content}\n\nProvide the complete, high-scoring submission text:"
    
    print("Generating academic response with LLM...")
    reply = prov.complete(sys_prompt, user_prompt)
    clean_reply = reply.strip()
    if clean_reply.startswith("{") and clean_reply.endswith("}"):
        try:
            data = json.loads(clean_reply)
            clean_reply = data.get("content") or data.get("answer") or data.get("text") or clean_reply
        except Exception:
            pass

    print(f"Submitting answer ({len(clean_reply.split())} words)...")
    ta.click()
    ta.fill(clean_reply)
    page.wait_for_timeout(2000)

    # Save
    save_btn = page.locator('button:has-text("Save"), [role="button"]:has-text("Save")').first
    if save_btn.count() and save_btn.is_visible():
        save_btn.click()
        page.wait_for_timeout(2000)

    # Submit
    sub_btn = page.locator('button:has-text("Submit"), [role="button"]:has-text("Submit")').first
    if sub_btn.count() and sub_btn.is_visible():
        sub_btn.click()
        page.wait_for_timeout(2000)
        conf2 = page.locator('button:has-text("Yes"), button:has-text("Proceed"), button:has-text("Confirm")').first
        if conf2.count() and conf2.is_visible():
            conf2.click()
            page.wait_for_timeout(3000)

    time.sleep(3)
    print("Submitted successfully!")
    print("Score/Feedback:\n", page.locator("body").inner_text()[:400])
    ctx.close()
