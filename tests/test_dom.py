"""The in-page helpers (kalbot/dom.js) on small hand-written pages, in a real browser."""

import pytest
import yaml
from playwright.sync_api import sync_playwright

from kalbot import config

pytestmark = pytest.mark.e2e

JS = (config.PACKAGE_DIR / "dom.js").read_text(encoding="utf-8")
CFG = config.js_config(yaml.safe_load(config.DEFAULTS_FILE.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def page(browser):
    with sync_playwright() as p:
        kw = {"executable_path": browser["executable_path"]} if browser["executable_path"] else \
            {"channel": browser["channel"] or None}
        b = p.chromium.launch(**kw)
        pg = b.new_page()

        def load(body: str):
            pg.set_content(f"<!doctype html><html><body>{body}</body></html>")
            pg.add_script_tag(content=JS)
            return pg

        yield load
        b.close()


def call(pg, fn, *args):
    return pg.evaluate("([f, a]) => window.__kqb[f](...a)", [fn, list(args)])


@pytest.mark.parametrize("body, perfect", [
    ("<p>Your score: 5/5</p>", True),
    ("<p>Best Score</p><p>10/10</p>", True),
    ("<p>Your Score</p><p>100%</p>", True),
    ("<p>Mastery</p><p>100%</p>", True),
    ("<p>You got 5/5 correct</p>", True),
    ("<p>Best Score</p><p>9/10</p>", False),
    ("<p>Score</p><p>3/5</p><p>Answered</p><p>5/5</p>", False),     # a counter, not the score
    ("<p>Attempts 1/1</p><p>Score: 3/5</p>", False),
    ("<p>Checkpoints 4/4 done</p><p>Your score: 2/5</p>", False),
])
def test_max_score(page, body, perfect):
    assert call(page(body), "state", CFG)["maxScore"] is perfect


def test_confirm_never_clicks_inside_a_quiz_overlay(page):
    pg = page('<div class="fixed inset-0"><p>Question 1 of 5</p><p>Is a stack LIFO?</p>'
              '<button>Yes</button><button>No</button><button>Next</button></div>')
    assert call(pg, "confirm", CFG, CFG["proceed"]) is False
    pg = page('<div class="fixed inset-0"><p>Your previous score will be replaced.</p>'
              '<button>Cancel</button><button>Proceed</button></div>')
    assert call(pg, "confirm", CFG, CFG["proceed"]) is True
    assert pg.get_attribute('[data-kqb-btn="confirm"]', "data-kqb-btn") and \
        pg.inner_text('[data-kqb-btn="confirm"]') == "Proceed"


def test_checklist_prefers_proceed_over_the_header_close_and_never_uses_the_page(page):
    pg = page('<div class="fixed inset-0"><header><button aria-label="Close"></button></header>'
              '<label><input type="checkbox"> I covered every section</label><button>Proceed</button></div>')
    c = call(pg, "checklist", CFG)
    assert c == {"boxes": 1, "proceed": True, "inDialog": True, "scope": True}
    assert pg.inner_text('[data-kqb-btn="proceed"]') == "Proceed"
    pg = page('<main><label><input type="checkbox"> Email me when graded</label>'
              '<a href="/next">Continue</a></main>')
    assert call(pg, "checklist", CFG)["scope"] is False


def test_workspace_with_submit_in_its_header(page):
    pg = page('<div class="fixed inset-0"><div class="bar"><button>CPP</button><button>Submit</button></div>'
              '<div><p>Two Sum: print the indices.</p><textarea class="code-input"></textarea></div></div>')
    t = call(pg, "tasks", CFG)
    assert len(t["fields"]) == 1 and t["submits"][0]["text"] == "Submit"
    pg = page('<main><button>Submit</button><section><p>Discuss</p><textarea placeholder="Share your thoughts">'
              '</textarea></section></main>')
    assert call(pg, "tasks", CFG)["fields"] == []    # a box after the page's Submit, not in a workspace


def test_markdown_toolbar_is_not_a_quiz(page):
    pg = page('<div class="w-md-editor"><div class="w-md-editor-toolbar"><button>B</button><button>I</button></div>'
              '<textarea class="w-md-editor-text-input"></textarea></div><button>Submit</button>')
    assert call(pg, "state", CFG)["kind"] == "none"
    assert call(pg, "tasks", CFG)["fields"][0]["md"] is True


def test_react_split_lu_numbers(page):
    pg = page('<a class="row" href="/lu/1" style="display:block"><span>2<!-- -->.<!-- -->12</span> Sprint review</a>'
              '<a class="row" href="/lu/2" style="display:block"><span>2<!-- -->.<!-- -->13</span> Retro</a>')
    assert [r["number"] for r in call(pg, "listLUs", CFG)] == ["2.12", "2.13"]
