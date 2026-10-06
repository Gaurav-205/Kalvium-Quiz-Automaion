"""Fast tests for the pieces that don't need a browser."""

import os

import pytest
import yaml
from mock_github import serve as serve_github

from kalbot import config, links
from kalbot.github import GitHub, GitHubError, publish
from kalbot.report import write_report
from kalbot.runlog import RunLog
from kalbot.tasks import run_verdict, word_limits

PATTERNS = yaml.safe_load(config.DEFAULTS_FILE.read_text(encoding="utf-8"))["patterns"]


# --------------------------------------------------------------------------- config


def test_user_config_is_merged_over_defaults_and_typos_are_flagged(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("llm:\n  provider: claude\n  modle: x\nportal:\n  semester: 6\nretry: 3\n", encoding="utf-8")
    cfg = config.load_config(p)
    assert cfg["llm"]["provider"] == "claude" and cfg["portal"]["semester"] == 6
    assert cfg["llm"]["models"]["gemini"]            # untouched defaults survive
    assert cfg["_meta"]["home"] == tmp_path
    warnings = " ".join(cfg["_meta"]["warnings"])
    assert "llm.modle" in warnings and "'retry'" in warnings


def test_bad_config_is_reported(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("llm: [oops", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config(p)
    p.write_text("llm:\n  provider: nobody\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="nobody"):
        config.load_config(p)


def test_dotenv_loads_keys_without_overriding_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("KALBOT_T1", raising=False)
    monkeypatch.setenv("KALBOT_T2", "from-env")
    (tmp_path / ".env").write_text('# keys\nKALBOT_T1="abc"\nexport KALBOT_T2=from-file\n', encoding="utf-8")
    assert config.load_dotenv(tmp_path / ".env") == ["KALBOT_T1"]
    assert os.environ["KALBOT_T1"] == "abc" and os.environ["KALBOT_T2"] == "from-env"


def test_save_overrides_keeps_comments_and_never_overwrites(tmp_path):
    p = tmp_path / "config.yaml"
    assert config.save_overrides(p, "selectors", {"lu_row": "[data-x='1']"}) == ["lu_row"]
    assert yaml.safe_load(p.read_text())["selectors"]["lu_row"] == "[data-x='1']"
    p.write_text("# mine\nselectors:  # keep me\n  lu_row: ''\n  quiz_option: '.opt'\n", encoding="utf-8")
    assert config.save_overrides(p, "selectors", {"lu_row": "a", "quiz_option": "b"}) == ["lu_row"]
    text = p.read_text()
    assert "# mine" in text and "# keep me" in text
    assert yaml.safe_load(text)["selectors"] == {"lu_row": "a", "quiz_option": ".opt"}


# --------------------------------------------------------------------------- links


@pytest.mark.parametrize("label, prompt, kind", [
    ("GitHub repository link", "", "github"),
    ("Paste the link", "Push your code to a GitHub repo and share it", "github"),
    ("Deployed website link", "", "live"),
    ("GitHub Pages link", "", "live"),
    ("Video walkthrough (Loom/YouTube)", "", "video"),
    ("Link to your pull request", "", "pr"),
    ("Submission URL", "", "link"),
])
def test_link_kinds(label, prompt, kind):
    assert links.classify(label, prompt, PATTERNS) == kind


def test_check_url():
    assert links.check_url("github", "https://github.com/me/repo") is None
    assert links.check_url("github", "https://gitlab.com/me/repo")
    assert links.check_url("pr", "https://github.com/a/b/pull/7") is None
    assert links.check_url("pr", "https://github.com/a/b")
    assert links.check_url("video", "www.loom.com/x") == "not an http(s) URL"


def test_linkbook_matches_livebook_and_lu(tmp_path):
    p = tmp_path / "submissions.yaml"
    p.write_text("- livebook: web\n  lu: '1.4'\n  video: https://youtu.be/x\n"
                 "- lu: Portfolio\n  github: https://github.com/me/site\n"
                 "- lu: '2.1'\n  github: not-a-url\n- livebook: only\n", encoding="utf-8")
    book = links.LinkBook.load(p)
    assert book.lookup("Web Development", "1.4", "Portfolio Website") == {
        "video": "https://youtu.be/x", "github": "https://github.com/me/site"}
    assert book.lookup("Data Structures", "1.4", "Stacks") == {}
    assert len(book.problems) == 2       # a bad URL and an entry without `lu:`
    assert links.LinkBook.load(tmp_path / "missing.yaml").entries == []


def test_state_remembers_repos(tmp_path):
    s = links.State(tmp_path / "runs" / "state.json")
    s.save_repo("Web", "1.4", {"github": "https://github.com/me/a", "live": ""})
    assert links.State(tmp_path / "runs" / "state.json").repo("Web", "1.4")["github"].endswith("/a")


# --------------------------------------------------------------------------- assignment helpers


@pytest.mark.parametrize("text, expected", [
    ("Write at least 150 words.", (150, 225)),
    ("Answer in 200-300 words", (200, 300)),
    ("minimum 100 words, maximum 180 words", (100, 180)),
    ("Keep it under... no more than 120 words", (72, 120)),
    ("Word limit: 300", (180, 300)),
    ("Answer in about 40 words", (34, 46)),
    ("Explain your approach.", (120, 250)),
])
def test_word_limits(text, expected):
    assert word_limits([text], (120, 250)) == expected


@pytest.mark.parametrize("out, verdict", [
    ("Test 1: passed\n2/3 test cases passed", "fail"),
    ("3/3 test cases passed", "pass"),
    ("Passed: 4 / 4", "pass"),
    ("Traceback (most recent call last):\nNameError", "fail"),
    ("All tests passed!", "pass"),
    ("Running...", "unknown"),
])
def test_run_verdict(out, verdict):
    assert run_verdict(out, PATTERNS) == verdict


# --------------------------------------------------------------------------- github


def test_github_publish_creates_repo_commits_files_and_pages():
    srv, fake = serve_github()
    try:
        gh = GitHub("test-token", f"http://127.0.0.1:{srv.server_port}")
        assert gh.user()["login"] == "student" and gh.user()["_scopes"] == "repo"
        out = publish(gh, "site", "My site", {"index.html": "<h1>hi</h1>", "README.md": "# site"},
                      private=False, pages=True)
        assert out == {"github": "https://github.com/student/site", "live": "https://student.github.io/site/"}
        assert fake.repos["site"]["files"] == {"README.md": "# site", "index.html": "<h1>hi</h1>"}
        assert gh.free_name("site") == "site-2"
        with pytest.raises(GitHubError, match="token"):
            GitHub("wrong", f"http://127.0.0.1:{srv.server_port}").user()
    finally:
        srv.shutdown()


# --------------------------------------------------------------------------- report


def test_html_report_escapes_content(tmp_path):
    log = RunLog(tmp_path)
    ctx = {"livebook": "Web <b>", "lu": "1.1", "lu_title": "XSS"}
    log.add(ctx, "submitted", "quiz", "5/5 passed", items=[
        {"q": 1, "question": "<script>alert(1)</script>", "code": [], "options": ["a", "b"], "chosen": [1],
         "confidence": "high"}])
    log.add(ctx, "manual", "links", "needs your video link")
    log.close()
    html = write_report(tmp_path / "report.html", log, [("Mode", "LIVE")]).read_text(encoding="utf-8")
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html
    assert "Needs you (1)" in html and html.count("<details") == 2


# --------------------------------------------------------------------------- drafting rules


class StubUI:
    interactive = False

    def note(self, *a, **k):
        pass


class StubLLM:
    def __init__(self):
        self.specs = []

    def write(self, spec, course="", material=""):
        self.specs.append(spec)
        return "drafted answer"

    def project(self, spec, name_hint, course="", material=""):
        from kalbot.llm import Project
        return Project("site", "A site", {"index.html": "<h1>hi</h1>"}, static_site=True)


class StubGitHub:
    def user(self):
        return {"login": "Me"}

    def free_name(self, base):
        return base


def solver(tmp_path, book="", gh=None):
    from kalbot.tasks import TaskSolver
    (tmp_path / "s.yaml").write_text(book, encoding="utf-8")
    cfg = yaml.safe_load(config.DEFAULTS_FILE.read_text(encoding="utf-8"))
    return TaskSolver(cfg, None, StubLLM(), None, StubUI(), linkbook=links.LinkBook.load(tmp_path / "s.yaml"),
                      state=links.State(tmp_path / "state.json"), github=gh, run_dir=tmp_path, dry_run=False,
                      review=False, enabled={})


def field(i, tag, label, prompt="", value="", kind="", link_kind=""):
    from kalbot.tasks import Field
    return Field(i, tag, "url" if tag == "input" else "", "", False, label, prompt, value, False, True, None,
                 False, False, "", 0, -1, kind, link_kind)


def group(*fields):
    from kalbot.tasks import Group
    return Group(0, "Submit", list(fields))


CTX = {"livebook": "Web Development", "lu": "1.4", "lu_title": "Portfolio"}


def test_live_link_is_never_left_empty_whatever_the_field_order(tmp_path):
    from kalbot.tasks import Manual
    live = field(0, "input", "Deployed website link", kind="link", link_kind="live")
    repo = field(1, "input", "GitHub repository link", kind="link", link_kind="github")
    s = solver(tmp_path, "- lu: '1.4'\n  github: https://github.com/me/site\n", gh=StubGitHub())
    with pytest.raises(Manual, match="live site"):      # repo given by the user, so no Pages site to offer
        s._draft(group(live, repo), CTX, "")
    drafts = solver(tmp_path, "", gh=StubGitHub())._draft(group(live, repo), CTX, "")
    assert [d.value for d in drafts] == ["https://me.github.io/site/", "https://github.com/Me/site"]


def test_text_already_in_a_box(tmp_path):
    s = solver(tmp_path)
    done = " ".join(["word"] * 150)
    template = "Problem:\nApproach:\nWhat I learned:"
    drafts = s._draft(group(field(0, "textarea", "", "Reflect (100-200 words)", done, kind="text"),
                            field(1, "textarea", "", "Explain (100-200 words)", template, kind="text")), CTX, "")
    assert drafts[0].value == done and drafts[0].source == "already in the box"
    assert drafts[1].value == "drafted answer" and s.llm.specs[0].existing == template
