import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from playwright.sync_api import sync_playwright

from common import load_config
from navigator import launch_browser, Navigator
from quiz import QuizSolver

cfg = load_config("config.yaml")

with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    solver = QuizSolver(cfg, None, None, dry_run=True)
    nav = Navigator(cfg, page, solver, None, dry_run=True, limit=0, livebook="Computer Organization", lu=None)
    nav.wait_for_login()
    
    lbs = nav.list_livebooks()
    coa = next(lb for lb in lbs if "Computer Organization" in lb["name"])
    nav.open_livebook(coa)
    lus = nav.open_learning_path()
    lu_3_7 = next(lu for lu in lus if lu.number == "3.7")
    print(f"Opening LU {lu_3_7.number}: {lu_3_7.title} (href: {lu_3_7.href})")
    nav.open_lu(coa, lu_3_7)
    
    page.wait_for_timeout(4000)
    print("Page URL:", page.url)
    if not page.url.endswith("/lessons"):
        page.goto(page.url.rstrip("/") + "/lessons")
        page.wait_for_timeout(4000)
        print("After lessons goto URL:", page.url)
    
    # Check what state is initially
    frame, st, completed = solver.locate(page)
    print("Initial locate state:", st["kind"], st.get("question", "")[:60], "options:", len(st.get("options", [])))
    
    # Scroll all
    page.evaluate("() => window.__kqb.scrollAll()")
    page.wait_for_timeout(2000)
    
    test_comp = page.locator('#test-component')
    print("test-component count:", test_comp.count())
    if test_comp.count():
        print("test-component text:", test_comp.inner_text()[:400])
        
    jscfg = nav.jscfg
    feats = page.evaluate("cfg => window.__kqb.luFeatures(cfg)", jscfg)
    print("luFeatures:", feats)
        
    frame, st2, comp2 = solver.locate(page)
    print("After scroll locate state:", st2["kind"], st2.get("question", "")[:60], "retake:", st2.get("retake"), "completed:", comp2)
    
    ctx.close()
