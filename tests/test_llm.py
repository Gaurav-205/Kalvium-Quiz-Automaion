import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm import AnswerPicker, LLMError, LLMProvider, Question, build_prompt, parse_answer  # noqa: E402


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
        self.prompts = []

    def complete(self, system, prompt):
        self.prompts.append(prompt)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def is_transient(self, exc):
        return isinstance(exc, TimeoutError)


Q4 = Question("2 + 2 = ?", ["3", "4", "5", "22"])


def test_retry_once_on_invalid_reply():
    p = Scripted(["no idea", '{"answer_indices": [1], "confidence": "high"}'])
    a = AnswerPicker(p, {}).choose(Q4)
    assert a.indices == [1] and len(p.prompts) == 2
    assert "previous reply was invalid" in p.prompts[1]


def test_invalid_twice_is_not_fatal():
    p = Scripted(["nope", '{"answer_indices": [9], "confidence": "high"}'])
    with pytest.raises(LLMError) as e:
        AnswerPicker(p, {}).choose(Q4)
    assert not e.value.fatal


def test_transient_errors_back_off_then_succeed():
    p = Scripted([TimeoutError("busy"), '{"answer_indices": [1], "confidence": "high"}'])
    a = AnswerPicker(p, {"transient_retry_delays": [0, 0]}).choose(Q4)
    assert a.indices == [1]


def test_hard_errors_are_fatal():
    p = Scripted([PermissionError("bad key")])
    with pytest.raises(LLMError) as e:
        AnswerPicker(p, {"transient_retry_delays": [0]}).choose(Q4)
    assert e.value.fatal


def test_prompt_contents():
    q = Question("What prints?", ["1", "2"], multi=True, code=["print(1)"], number=3, total=5)
    p = build_prompt(q, "Course X - LU 1.2", previous=[0])
    assert "Question 3 of 5" in p and "print(1)" in p and "[1] 2" in p
    assert "MORE THAN ONE" in p and "earlier attempt chose [0]" in p and '"answer_indices"' in p


def test_missing_key_is_reported_without_value(monkeypatch):
    from llm import make_provider
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError) as e:
        make_provider({"provider": "gemini", "models": {"gemini": "m"}, "api_key_env": {"gemini": "GEMINI_API_KEY"}})
    assert "GEMINI_API_KEY" in str(e.value) and e.value.fatal
