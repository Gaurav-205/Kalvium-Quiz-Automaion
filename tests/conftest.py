"""Shared test helpers: a fake LLM that answers from the mock portal's key, and browser lookup."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from mock_portal import QUIZZES  # noqa: E402

from kalbot import llm, prompts  # noqa: E402

ALL_QUESTIONS = {q["text"]: q for qs in QUIZZES.values() for q in qs}
WRONG_FIRST = {q["text"] for q in QUIZZES[("2505", "12")][:3]}     # forces a fail, then a retake
INVALID_ONCE = {QUIZZES[("2505", "21")][1]["text"]}                  # exercises the JSON retry

SENTENCE = ("Semantic structure helps every reader, and I now plan my pages around clear headings, "
            "landmarks and meaningful labels before I write any styling. ")
BUGGY_FIZZ = ("def fizzbuzz(n):\n    out = []\n    for i in range(1, n + 1):\n"
              "        if i % 3 == 0:\n            out.append('Fizz')\n        elif i % 5 == 0:\n"
              "            out.append('Buzz')\n        else:\n            out.append(str(i))\n    return out\n")
GOOD_FIZZ = ("def fizzbuzz(n):\n    out = []\n    for i in range(1, n + 1):\n        if i % 15 == 0:\n"
             "            out.append('FizzBuzz')\n        elif i % 3 == 0:\n            out.append('Fizz')\n"
             "        elif i % 5 == 0:\n            out.append('Buzz')\n        else:\n"
             "            out.append(str(i))\n    return out\n")
PROJECT = {
    "repo_name": "portfolio-website", "description": "Personal portfolio with About and Projects sections",
    "static_site": True, "requires_existing_repo": False,
    "files": [
        {"path": "README.md", "content": "# Portfolio\n\nOpen index.html in a browser.\n"},
        {"path": "index.html", "content": "<!doctype html><title>Me</title><h1>About</h1><h2>Projects</h2>"},
        {"path": "style.css", "content": "body{font-family:sans-serif}"},
    ],
}


def words(n: int) -> str:
    out = (SENTENCE * (n // 10 + 2)).split()[:n]
    return " ".join(out).rstrip(",") + "."


class FakeProvider(llm.LLMProvider):
    """Answers from the mock's key, so the tests check the plumbing, not an LLM."""

    needs_key = False
    requests: list = []
    unknown: list = []
    invalid_sent: set = set()
    revised: list = []

    def __init__(self, *a, **k):
        pass

    def complete(self, req: llm.Request) -> str:
        FakeProvider.requests.append(req)
        p = req.prompt
        if req.system == prompts.PROJECT_SYSTEM:
            return json.dumps(PROJECT)
        if req.kind == "coding":
            if "fizzbuzz" in p.lower():
                return f"```python\n{GOOD_FIZZ if 'produced this output' in p else BUGGY_FIZZ}```"
            if "add(a, b)" in p:
                return "Here you go:\n```python\ndef add(a, b):\n    return a + b\n```"
            FakeProvider.unknown.append(p)
            return "```\npass\n```"
        if req.kind == "written":
            if "one short line" in p:
                return "HyperText Markup Language"
            m = re.search(r"between (\d+) and (\d+) words", p)
            lo, hi = (int(m[1]), int(m[2])) if m else (100, 150)
            if "know thyself" in p.lower() and "Your draft below" not in p:
                return words(lo // 4)           # too short on purpose: kalbot must ask for a rewrite
            if "Your draft below" in p:
                FakeProvider.revised.append(p)
            return words((lo + hi) // 2)
        q = next((q for t, q in ALL_QUESTIONS.items() if t in p), None)
        if q is None:
            FakeProvider.unknown.append(p)
            return '{"answer_indices": [0], "confidence": "low"}'
        if q["text"] in INVALID_ONCE and q["text"] not in FakeProvider.invalid_sent:
            FakeProvider.invalid_sent.add(q["text"])
            return "I think the answer is B."
        ans = list(q["answer"])
        if q["text"] in WRONG_FIRST and "earlier attempt" not in p:
            ans = [(ans[0] + 1) % len(q["options"])]
        return f'```json\n{{"answer_indices": {ans}, "confidence": "high"}}\n```'


llm.PROVIDERS["fake"] = FakeProvider


def browser_settings() -> dict:
    """KALBOT_TEST_BROWSER, else Playwright's Chromium, else any Chromium in the browsers folder, else Chrome."""
    exe = os.environ.get("KALBOT_TEST_BROWSER")
    if exe:
        return {"channel": "", "executable_path": exe}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if Path(p.chromium.executable_path).exists():
            return {"channel": "chromium", "executable_path": ""}
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache" / "ms-playwright")
    for c in sorted(root.glob("chromium-*/chrome-linux*/chrome"), reverse=True):
        return {"channel": "", "executable_path": str(c)}
    return {"channel": "chrome", "executable_path": ""}


@pytest.fixture(scope="session")
def browser():
    return browser_settings()
