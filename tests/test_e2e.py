"""End-to-end runs of kalbot against the local mock portal (and mock GitHub) with a fake LLM.

The tests share one browser profile and run in order, like a real user would:
first login + discovery, then dry-runs, one real quiz, the full run, and a
final run that should find nothing left to do.
"""

import csv
import json
import os
import urllib.request
from pathlib import Path

import pytest
import yaml
from conftest import INVALID_ONCE, FakeProvider
from mock_github import serve as serve_github
from mock_portal import QUIZZES, serve

from kalbot import cli
from kalbot.llm import count_words

pytestmark = pytest.mark.e2e

LINKS = """
- lu: "*"                     # every LU that asks for a pull request link
  pr: https://github.com/student/philosophy/pull/1
- livebook: web dev
  lu: portfolio
  video: https://www.loom.com/share/0123456789abcdef
"""


@pytest.fixture(scope="module")
def env(tmp_path_factory, browser):
    srv, portal = serve(0)
    gsrv, gh = serve_github()
    base = f"http://127.0.0.1:{srv.server_port}"
    d = tmp_path_factory.mktemp("kalbot")
    cfg = {
        "portal": {"base_url": base},
        "llm": {"provider": "fake", "models": {"fake": "fake-model"}, "min_seconds_between_calls": 0},
        "browser": {**browser, "headless": True},
        "delays": {"action_min_s": 0.05, "action_max_s": 0.1},
        "timeouts": {"settle_ms": 1500, "lu_settle_ms": 3000, "list_ms": 8000, "question_change_ms": 8000,
                     "result_ms": 8000, "task_ms": 8000, "run_output_ms": 8000},
        "run": {"login_timeout_minutes": 1},
        "github": {"api_url": f"http://127.0.0.1:{gsrv.server_port}"},
        "links": {"wait_for_live_site_s": 0},
        "paths": {"profile": str(d / "profile"), "runs": str(d / "runs"), "state": str(d / "runs" / "state.json")},
    }
    (d / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    (d / "submissions.yaml").write_text(LINKS, encoding="utf-8")
    old = os.environ.get("GITHUB_TOKEN")
    os.environ["GITHUB_TOKEN"] = "test-token"
    yield {"base": base, "config": str(d / "config.yaml"), "dir": d, "portal": portal, "gh": gh}
    if old is None:
        os.environ.pop("GITHUB_TOKEN", None)
    else:
        os.environ["GITHUB_TOKEN"] = old
    srv.shutdown()
    gsrv.shutdown()


def run(env, *args):
    cmd = [a for a in args if a in cli.COMMANDS][:1] or ["run"]
    rest = [a for a in args if a not in cli.COMMANDS]
    return cli.main([*cmd, "--config", env["config"], *rest])


def state(env):
    with urllib.request.urlopen(env["base"] + "/api/state") as r:
        return json.load(r)


def latest_run(env) -> Path:
    return sorted((env["dir"] / "runs").glob("2*"))[-1]


def latest_csv(env):
    with open(latest_run(env) / "results.csv", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def submitted_scores(env, key):
    return [s["score"] for s in state(env).get(key, []) if s["answers"] is not None]


def test_1_login_and_discover(env, capsys):
    before = state(env)
    assert run(env, "discover") == 0
    out = capsys.readouterr().out
    assert "NOT LOGGED IN" in out and "Login detected" in out
    assert "Introduction to Philosophy" in out and "Data Structures" in out and "Web Development" in out
    for line in ("1.1  quiz already submitted", "1.2  behind a Start button", "1.3  assignment: written (1 box)",
                 "2.1  quiz question visible", "2.2  assignment: links: pr, video",
                 "2.3  quiz question visible", "2.4  no quiz or assignment found", "2.5  behind a Start button"):
        assert line in out, line
    assert state(env) == before, "discovery must not submit or run anything"
    cfg = yaml.safe_load(Path(env["config"]).read_text(encoding="utf-8"))
    assert cfg["selectors"]["livebook_card"] == '[data-testid="livebook-card"]'
    assert cfg["selectors"]["lu_row"] == '[data-testid="lu-item"]'
    assert "quiz_option" not in cfg["selectors"]      # printed only, never auto-written
    snaps = latest_run(env) / "snapshots"
    assert list(snaps.glob("*_discovery_report.json")) and list(snaps.glob("*learning_path.png"))
    report = json.loads(next(snaps.glob("*_discovery_report.json")).read_text(encoding="utf-8"))
    fields = [f for p in report["pages"] for a in p.get("assignments", []) for f in a["fields"]]
    assert not any("comment" in f["label"].lower() for f in fields), "comment boxes are not assignments"


def test_2_dry_run_stops_at_start_button(env, capsys):
    before = state(env)
    assert run(env, "--dry-run", "--limit", "1") == 0
    out = capsys.readouterr().out
    assert "Logged in." in out and "NOT LOGGED IN" not in out      # session persisted
    assert "behind a Start button; not clicking it" in out
    assert "Previewed: 1" in out
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
    cfg.setdefault("run", {})["dry_run_click_start"] = True
    p = env["dir"] / "config_peek.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    before = state(env)
    assert cli.main(["--config", str(p), "--dry-run", "--livebook", "data", "--lu", "1.1"]) == 0
    rows = [r for r in latest_csv(env) if r["event"] == "dry_run_question"]
    assert rows and rows[0]["question"] == QUIZZES[("2506", "11")][0]["text"]   # quiz inside an iframe
    assert state(env) == before


def test_5_dry_run_drafts_assignment_without_side_effects(env, capsys):
    before = state(env)
    assert run(env, "--dry-run", "--livebook", "web", "--lu", "1.4") == 0
    out = capsys.readouterr().out
    assert "Preview (dry-run" in out and "portfolio-website" in out
    assert state(env) == before and not env["gh"].repos, "dry-run must not submit or create repos"
    drafts = latest_run(env) / "drafts"
    assert list(drafts.rglob("project/index.html")), "project files are saved for review"


def test_6_review_waits_without_a_terminal(env, capsys):
    before = state(env)
    assert run(env, "--livebook", "web", "--lu", "1.1") == 0
    out = capsys.readouterr().out
    assert "waiting for your review" in out and "Needs you: 1" in out
    assert state(env)["tasks"] == before["tasks"], "nothing is typed or submitted without a review"
    assert list((latest_run(env) / "drafts").rglob("*.md")), "drafts are saved for the user"


def test_7_limit_1_real_quiz_with_retake(env, capsys):
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
    assert "Submitted: 1" in out
    assert not FakeProvider.unknown


def test_8_full_run(env, capsys):
    assert run(env, "--auto") == 0
    out = capsys.readouterr().out
    s = state(env)
    # quizzes
    assert submitted_scores(env, "2505/21") == [5]      # ARIA radios, rating widget ignored, JSON retry
    assert submitted_scores(env, "2505/23") == [5]      # plain <div> options behind a Quiz tab
    assert submitted_scores(env, "2506/11") == [5]      # quiz in an iframe
    assert submitted_scores(env, "2506/12") == []       # already submitted (4/5): untouched without --retake
    assert submitted_scores(env, "2505/12") == [2, 5]   # done in test 7: untouched
    assert submitted_scores(env, "2506/14") == [5]      # check questions in the lesson, then the graded quiz
    assert "Finished 2 check question(s)" in out
    tasks = s["tasks"]
    # the portal's assignment workspace: Start -> Proceed -> Markdown editor -> Save -> review checklist -> Submit
    essay = tasks["2505/25"]["answer"]
    assert essay.startswith("## My position") and count_words(essay) > 100
    assert s["saved"]["2505/25"] and "2505/25" in s["reviewed"]
    # Monaco reachable only through monaco.editor.getModels(); no starter code -> a full stdin/stdout program
    assert "int main()" in tasks["2507/17"]["code"] and "cin >>" in tasks["2507/17"]["code"]
    # written: textarea with "minimum 200 words" (first draft too short -> rewritten)
    assert 200 <= count_words(tasks["2505/13"]["answer"]) <= 300 and FakeProvider.revised
    # two rich-text boxes behind one Submit with a confirm modal
    assert 50 <= count_words(tasks["2507/11"]["q1"]) <= 80
    assert 30 <= count_words(tasks["2507/11"]["q2"]) <= 50
    # code editor: Run Tests failed, the model fixed it, then it passed and was submitted
    assert "FizzBuzz" in tasks["2507/12"]["code"]
    assert [r["passed"] for r in s["runs"]] == [2, 3]
    # plain textarea code box behind "Start Assignment"
    assert "return a + b" in tasks["2507/13"]["code"]
    # links: new repo + GitHub Pages + the user's video link
    repo = env["gh"].repos["portfolio-website"]
    assert "index.html" in repo["files"] and repo["pages"] and not repo["private"]
    assert tasks["2507/14"] == {"repo": "https://github.com/student/portfolio-website",
                                "live": "https://student.github.io/portfolio-website/",
                                "video": "https://www.loom.com/share/0123456789abcdef"}
    assert tasks["2507/15"]["short"] == "HyperText Markup Language"
    # never half-submitted: a link kalbot doesn't have (2.2 has its PR link from the "*" entry, no video)
    assert "2505/22" not in tasks and "2507/16" not in tasks
    assert "Submitted: 12" in out and "Errors: 0" in out and "Needs you: 3" in out
    assert "needs your video link" in out
    assert not FakeProvider.unknown
    assert FakeProvider.invalid_sent == INVALID_ONCE
    saved = json.loads((env["dir"] / "runs" / "state.json").read_text(encoding="utf-8"))
    assert saved["repos"]["Web Development|1.4"]["github"].endswith("/portfolio-website")
    assert (latest_run(env) / "report.html").read_text(encoding="utf-8").count("<details") >= 12
    fields = [r for r in latest_csv(env) if r["event"] == "task_field"]
    assert fields and not any("comment" in r["question"].lower() for r in fields)


def test_9_retake_improves_a_submitted_quiz(env, capsys):
    assert run(env, "--retake", "--livebook", "data", "--lu", "1.2") == 0   # 4/5 before; Retake asks "Proceed"
    assert submitted_scores(env, "2506/12") == [5]
    assert run(env, "--retake", "--livebook", "data", "--lu", "1.3") == 0   # already 5/5: left alone
    assert submitted_scores(env, "2506/13") == []
    assert "full marks" in capsys.readouterr().out


def test_10_nothing_left(env, capsys):
    before = state(env)
    repos = dict(env["gh"].repos)
    assert run(env, "--auto") == 0
    out = capsys.readouterr().out
    assert "Submitted: 0" in out and "Errors: 0" in out
    assert state(env) == before and env["gh"].repos == repos
