import json

import pytest

from kalbot import prompts
from kalbot.llm import (
    LLM,
    CodeSpec,
    LLMError,
    LLMProvider,
    Question,
    WriteSpec,
    clean_text,
    count_words,
    extract_code,
    make_provider,
    parse_answer,
    parse_project,
)


@pytest.mark.parametrize("reply, n, multi, expected", [
    ('{"answer_indices": [2], "confidence": "high"}', 4, False, ([2], "high")),
    ('```json\n{"answer_indices": [1], "confidence": "medium"}\n```', 4, False, ([1], "medium")),
    ('Here you go:\n```\n{"answer_indices": [3, 0], "confidence": "LOW"}\n```', 4, True, ([0, 3], "low")),
    ('{"answer_indices": 1, "confidence": "high"}', 4, False, ([1], "high")),
    ('{"answer_indices": [1, 1], "confidence": "whatever"}', 4, False, ([1], "low")),
])
def test_parse_valid(reply, n, multi, expected):
    a = parse_answer(reply, n, multi)
    assert (a.indices, a.confidence) == expected


@pytest.mark.parametrize("reply, n, multi", [
    ("The answer is B", 4, False),
    ('{"answer_indices": [4], "confidence": "high"}', 4, False),
    ('{"answer_indices": [-1], "confidence": "high"}', 4, False),
    ('{"answer_indices": [0, 1], "confidence": "high"}', 4, False),
    ('{"answer_indices": [], "confidence": "high"}', 4, True),
    ('{"answer_indices": ["1"], "confidence": "high"}', 4, False),
    ('{"answer_indices": [true], "confidence": "high"}', 4, False),
    ('{"answer_indices": [1,}', 4, False),
])
def test_parse_invalid(reply, n, multi):
    with pytest.raises(ValueError):
        parse_answer(reply, n, multi)


class Scripted(LLMProvider):
    needs_key = False

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def complete(self, req):
        self.requests.append(req)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    @property
    def prompts(self):
        return [r.prompt for r in self.requests]

    def is_transient(self, exc):
        return isinstance(exc, TimeoutError)


def make_llm(p, **llm_cfg):
    return LLM(p, {"llm": {"transient_retry_delays": [0, 0], **llm_cfg}, "written": {"style": "Be clear."}})


Q4 = Question("2 + 2 = ?", ["3", "4", "5", "22"])


def test_retry_once_on_invalid_reply():
    p = Scripted(["no idea", '{"answer_indices": [1], "confidence": "high"}'])
    a = make_llm(p).choose(Q4)
    assert a.indices == [1] and len(p.prompts) == 2
    assert "previous reply was invalid" in p.prompts[1]


def test_invalid_twice_is_not_fatal():
    p = Scripted(["nope", '{"answer_indices": [9], "confidence": "high"}'])
    with pytest.raises(LLMError) as e:
        make_llm(p).choose(Q4)
    assert not e.value.fatal


def test_transient_errors_back_off_then_succeed():
    p = Scripted([TimeoutError("busy"), '{"answer_indices": [1], "confidence": "high"}'])
    assert make_llm(p).choose(Q4).indices == [1]


def test_hard_errors_are_fatal():
    p = Scripted([PermissionError("bad key")])
    with pytest.raises(LLMError) as e:
        make_llm(p).choose(Q4)
    assert e.value.fatal


def test_quiz_prompt_and_material():
    q = Question("What prints?", ["1", "2"], multi=True, code=["print(1)"], number=3, total=5)
    p = prompts.quiz_prompt(q, "Course X - LU 1.2", previous=[0])
    assert "Question 3 of 5" in p and "print(1)" in p and "[1] 2" in p
    assert "MORE THAN ONE" in p and "earlier attempt chose [0]" in p and '"answer_indices"' in p
    s = Scripted(['{"answer_indices": [0], "confidence": "high"}'])
    make_llm(s, max_context_chars=10).choose(q, material="0123456789-cut-here")
    assert s.requests[0].material == "0123456789"           # truncated, sent as cacheable context
    s = Scripted(['{"answer_indices": [0], "confidence": "high"}'])
    make_llm(s, include_lu_content=False).choose(q, material="secret")
    assert s.requests[0].material == ""


def test_missing_key_is_reported_without_value(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError) as e:
        make_provider({"provider": "gemini", "models": {"gemini": "m"}, "api_key_env": {"gemini": "GEMINI_API_KEY"}})
    assert "GEMINI_API_KEY" in str(e.value) and e.value.fatal


# --------------------------------------------------------------------------- written / code / project


def test_write_rewrites_when_outside_word_limits():
    short_draft, good = "Too short.", " ".join(["word"] * 60)
    p = Scripted([short_draft, good])
    text = make_llm(p).write(WriteSpec("Explain X.", words=(50, 80)))
    assert count_words(text) == 60 and "must be between 50 and 80 words" in p.prompts[1]


def test_write_keeps_better_draft_and_cleans_markdown():
    p = Scripted(["## Answer\n\n**Bold** start " + "w " * 70, "x"])
    text = make_llm(p).write(WriteSpec("Explain X.", words=(50, 80)))
    assert text.startswith("Bold start") and "#" not in text


def test_short_answers_fit_on_one_line():
    p = Scripted(["HyperText\nMarkup Language and a lot more words after it"])
    assert make_llm(p).write(WriteSpec("HTML?", short=True, max_chars=25)) == "HyperText Markup Language"


def test_code_uses_longest_fence_and_feeds_back_test_output():
    p = Scripted(["```py\nx\n```\nand the full one:\n```python\ndef f():\n    return 1\n```",
                  "```python\ndef f():\n    return 2\n```"])
    ai = make_llm(p)
    assert ai.code(CodeSpec("f returns 1", "python", "def f():\n    pass\n")) == "def f():\n    return 1\n"
    ai.code(CodeSpec("f returns 2"), previous="def f():\n    return 1\n", feedback="1/2 test cases passed")
    assert "1/2 test cases passed" in p.prompts[1] and "Fix the code" in p.prompts[1]
    assert "def f():\n    pass" in p.prompts[0]


def test_clean_text_and_extract_code():
    assert clean_text("Here is my answer:\n\nAnswer: I think so.") == "I think so."
    assert extract_code("no fences at all") == "no fences at all\n"


def test_parse_project_validates_paths_and_names():
    good = {"repo_name": "My Cool App!", "static_site": True,
            "files": [{"path": "./index.html", "content": "<h1>x</h1>"}, {"path": ".gitignore", "content": "x"}]}
    pr = parse_project(json.dumps(good), "fallback")
    assert pr.repo_name == "my-cool-app" and pr.static_site and set(pr.files) == {"index.html", ".gitignore"}
    for bad in ("../etc/passwd", "/abs.txt", ".git/config"):
        with pytest.raises(ValueError):
            parse_project(json.dumps({"files": [{"path": bad, "content": "x"}]}), "f")
    fork = parse_project('{"requires_existing_repo": true, "files": []}', "Fork Task")
    assert fork.requires_existing_repo and fork.repo_name == "fork-task"
    assert not parse_project(json.dumps({**good, "files": [{"path": "app.py", "content": ""}]}), "f").static_site
