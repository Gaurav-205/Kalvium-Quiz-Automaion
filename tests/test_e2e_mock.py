"""End-to-end runs of main.py against the local mock portal with a fake LLM.

The tests share one browser profile and run in order, like a real user would:
first login + discovery, then dry-runs, one real quiz, the full run, and a
final run that should find nothing left to do.
"""

import csv
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import llm  # noqa: E402
import main as bot  # noqa: E402
from mock_portal import QUIZZES, serve  # noqa: E402

CHROMIUM = os.environ.get("KQB_CHROMIUM", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")

ALL_QUESTIONS = {q["text"]: q for qs in QUIZZES.values() for q in qs}
WRONG_FIRST = {q["text"] for q in QUIZZES[("2505", "12")][:3]}     # forces a fail, then a retake
INVALID_ONCE = {QUIZZES[("2505", "21")][1]["text"]}                  # exercises the JSON retry


class FakeProvider(llm.LLMProvider):
    """Answers from the mock's key, so the tests check the plumbing, not an LLM."""

    needs_key = False
    prompts: list = []
    unknown: list = []
    _invalid_sent: set = set()

    def __init__(self, *a, **k):
        pass

    def complete(self, system, prompt):
        FakeProvider.prompts.append(prompt)
        q = next((q for t, q in ALL_QUESTIONS.items() if t in prompt), None)
        if q is None:
            FakeProvider.unknown.append(prompt)
            return '{"answer_indices": [0], "confidence": "low"}'
        if q["text"] in INVALID_ONCE and q["text"] not in FakeProvider._invalid_sent:
            FakeProvider._invalid_sent.add(q["text"])
            return "I think the answer is B."
        ans = list(q["answer"])
        if q["text"] in WRONG_FIRST and "earlier attempt" not in prompt:
            ans = [(ans[0] + 1) % len(q["options"])]
        return f'```json\n{{"answer_indices": {ans}, "confidence": "high"}}\n```'


llm.PROVIDERS["fake"] = FakeProvider


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    srv, portal = serve(0)
    base = f"http://127.0.0.1:{srv.server_port}"
    d = tmp_path_factory.mktemp("kqb")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg["portal"]["base_url"] = base
    cfg["llm"]["provider"] = "fake"
    cfg["llm"]["models"]["fake"] = "fake-model"
    have = Path(CHROMIUM).exists()   # else use installed Chrome (python -m playwright install chrome)
    cfg["browser"].update(channel="" if have else "chrome", headless=True,
                          executable_path=CHROMIUM if have else "")
    cfg["delays"] = {"action_min_s": 0.05, "action_max_s": 0.1}
    cfg["timeouts"].update(settle_ms=1500, lu_settle_ms=3000, list_ms=8000,
                           question_change_ms=8000, result_ms=8000)
    cfg["run"]["login_timeout_minutes"] = 1
    for k in ("profile", "logs", "snapshots", "errors"):
        cfg["paths"][k] = str(d / k)
    path = d / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    yield {"base": base, "config": str(path), "dir": d, "portal": portal}
    srv.shutdown()


def run(env, *args):
    return bot.main(["--config", env["config"], *args])


def state(env):
    with urllib.request.urlopen(env["base"] + "/api/state") as r:
        return json.load(r)


def latest_csv(env):
    files = sorted((env["dir"] / "logs").glob("run_*.csv"))
    with open(files[-1], encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def submitted_scores(env, key):
    return [s["score"] for s in state(env).get(key, []) if s["answers"] is not None]


def test_1_login_and_discover(env, capsys):
    before = state(env)
    assert run(env, "--discover") == 0
    out = capsys.readouterr().out
    assert "NOT LOGGED IN" in out and "Login detected" in out
    assert "Introduction to Philosophy" in out and "Data Structures" in out
    for line in ("1.1  quiz already submitted", "1.2  quiz behind a Start button", "1.3  written task",
                 "2.1  quiz question visible", "2.2  coding task", "2.3  quiz question visible",
                 "2.4  no quiz found"):
        assert line in out, line
    assert state(env) == before, "discovery must not submit anything"
    cfg = yaml.safe_load(Path(env["config"]).read_text(encoding="utf-8"))
    assert cfg["selectors"]["livebook_card"] == '[data-testid="livebook-card"]'
    assert cfg["selectors"]["lu_row"] == '[data-testid="lu-item"]'
    assert cfg["selectors"]["quiz_option"] == ""      # printed only, never auto-written
    assert list((env["dir"] / "snapshots").glob("*_discovery_report.json"))
    assert list((env["dir"] / "snapshots").glob("*learning_path.png"))


def test_2_dry_run_stops_at_start_button(env, capsys):
    before = state(env)
    assert run(env, "--dry-run", "--limit", "1") == 0
    out = capsys.readouterr().out
    assert "Logged in." in out and "NOT LOGGED IN" not in out      # session persisted
    assert "behind a Start button; not clicking it" in out
    assert state(env) == before


def test_3_dry_run_extracts_question(env, capsys):
    before = state(env)
    assert run(env, "--dry-run", "--livebook", "philosophy", "--lu", "2.1") == 0
    rows = [r for r in latest_csv(env) if r["event"] == "dry_run_question"]
    q = QUIZZES[("2505", "21")][0]
    assert len(rows) == 1
    assert rows[0]["question"] == q["text"]
    assert rows[0]["options"] == " || ".join(f"[{i}] {o}" for i, o in enumerate(q["options"]))
    assert rows[0]["chosen"] == "[0]"
    assert state(env) == before


def test_4_dry_run_can_start_quiz_and_read_code(env, capsys):
    cfg = yaml.safe_load(Path(env["config"]).read_text(encoding="utf-8"))
    cfg["run"]["dry_run_click_start"] = True
    p = env["dir"] / "config_peek.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    before = state(env)
    assert bot.main(["--config", str(p), "--dry-run", "--livebook", "data", "--lu", "1.1"]) == 0
    rows = [r for r in latest_csv(env) if r["event"] == "dry_run_question"]
    assert rows and rows[0]["question"] == QUIZZES[("2506", "11")][0]["text"]   # quiz inside an iframe
    assert state(env) == before


def test_5_limit_1_real_quiz_with_retake(env, capsys):
    assert run(env, "--limit", "1") == 0
    out = capsys.readouterr().out
    assert submitted_scores(env, "2505/12") == [2, 5]        # failed once, retook, passed
    rows = latest_csv(env)
    qs = [r for r in rows if r["event"] == "question"]
    assert len(qs) == 10
    code_q = next(r for r in qs if r["question"] == "What does this snippet print?")
    assert "return 1 + ask(n - 1)" in code_q["code"]
    multi_q = next(r for r in qs if r["question"].startswith("Which of these are Socratic"))
    assert multi_q["multi"] == "True" and multi_q["chosen"] in ("[0, 1]", "[1, 2]")
    results = [r["result"] for r in rows if r["event"] == "result"]
    assert results[0].startswith("2/5 FAILED") and results[1].startswith("5/5 passed")
    assert "Quizzes submitted: 1" in out
    assert not FakeProvider.unknown


def test_6_full_run(env, capsys):
    assert run(env) == 0
    out = capsys.readouterr().out
    assert submitted_scores(env, "2505/21") == [5]      # ARIA radios, rating widget ignored, JSON retry
    assert submitted_scores(env, "2505/23") == [5]      # plain <div> options behind a Quiz tab
    assert submitted_scores(env, "2506/11") == [5]      # quiz in an iframe
    assert submitted_scores(env, "2506/12") == []       # already submitted: untouched
    assert submitted_scores(env, "2505/12") == [2, 5]   # done in test 5: untouched
    assert "Quizzes submitted: 3" in out
    assert "Skipped after an error: 0" in out
    manual = out.split("Manual LUs left")[1].split("CSV log")[0]
    assert "1.3 Reflection" in manual and "2.2 Ethics in Code" in manual and "1.2 Linked Lists" in manual
    assert "Stacks" not in manual                       # "100%" on the Learning Path row = complete
    assert "1.1  todo  Arrays\n" in out.replace(" [quiz]", "")   # title not glued to "0%"
    assert "2.4 Reading: Kant" in out.split("no quiz or task detected")[1]
    assert not FakeProvider.unknown
    assert FakeProvider._invalid_sent == INVALID_ONCE


def test_7_nothing_left(env, capsys):
    before = state(env)
    assert run(env) == 0
    out = capsys.readouterr().out
    assert "Quizzes submitted: 0" in out and "Skipped after an error: 0" in out
    assert state(env) == before
