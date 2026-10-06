import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from common import load_config
from playwright.sync_api import sync_playwright
from navigator import launch_browser
from quiz import QuizSolver
from llm import AnswerPicker, make_provider

cfg = load_config("config.yaml")
picker = AnswerPicker(make_provider(cfg["llm"]), cfg["llm"])

with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://app.kalvium.community/livebooks/2505/c7801414-afd4-4cd8-8372-735a757b28e0/lessons", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    
    # Click Retake Quiz
    retake_btn = page.get_by_role("button", name="Retake Quiz")
    if retake_btn.is_visible():
        print("Clicking Retake Quiz...")
        retake_btn.click()
        page.wait_for_timeout(1500)
        proceed_btn = page.get_by_role("button", name="Proceed")
        if proceed_btn.is_visible():
            print("Clicking Proceed...")
            proceed_btn.click()
            page.wait_for_timeout(3000)
            
    solver = QuizSolver(cfg, picker, None, dry_run=True)
    frame, st, completed = solver.locate(page)
    print("State after Proceed:")
    print("  kind:", st.get("kind"))
    print("  question:", st.get("question"))
    print("  options:", st.get("options"))
    
    if st.get("kind") == "question":
        from llm import Question
        prog = st.get("progress") or [1, None]
        q = Question(text=st["question"], options=st["options"], multi=bool(st["multi"]),
                     code=st.get("code") or [], number=prog[0], total=prog[1])
        ans = picker.choose(q, context="Introduction to Philosophy - Five Branches, Five Problems: A Map of What Philosophy Actually Is")
        print("\nGemini Answer Selection:")
        print("  Chosen index:", ans.indices)
        print("  Chosen text:", [q.options[i] for i in ans.indices])
        print("  Confidence:", ans.confidence)
        
    ctx.close()
