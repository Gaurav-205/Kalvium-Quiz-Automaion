import sys
sys.path.insert(0, '.')
from common import load_config
from playwright.sync_api import sync_playwright
from navigator import launch_browser
from quiz import QuizSolver

cfg = load_config('config.yaml')
with sync_playwright() as p:
    ctx = launch_browser(p, cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto('https://app.kalvium.community/livebooks/2506/08f4e86c-6d85-4384-baaa-f27c1fa0ef54/lessons')
    page.wait_for_timeout(4000)
    solver = QuizSolver(cfg, None, None, dry_run=True)
    # Check all frames
    for i, f in enumerate(page.frames):
        st_f = solver.state(f)
        print(f"Frame {i} ({f.url}): kind={st_f['kind']}, start={st_f.get('start')}, retake={st_f.get('retake')}")
    ctx.close()
