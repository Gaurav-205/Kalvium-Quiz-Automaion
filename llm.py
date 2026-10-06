"""LLM providers behind one small interface, plus the answer picker.

Pick the provider in config.yaml (llm.provider: gemini | claude). API keys are
read from environment variables only and are never printed or logged.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field

log = logging.getLogger("kqb.llm")


class LLMError(Exception):
    """fatal=True means every later call will fail too (bad key, quota, model)."""

    def __init__(self, msg: str, fatal: bool = False):
        super().__init__(msg)
        self.fatal = fatal


@dataclass
class Question:
    text: str
    options: list[str]
    multi: bool = False
    code: list[str] = field(default_factory=list)
    number: int | None = None
    total: int | None = None


@dataclass
class Answer:
    indices: list[int]
    confidence: str
    raw: str = ""


# --------------------------------------------------------------------------- providers


class LLMProvider:
    """Minimal interface: one system prompt + one user prompt in, text out."""

    needs_key = True

    def complete(self, system: str, prompt: str) -> str:
        raise NotImplementedError

    def is_transient(self, exc: Exception) -> bool:
        """True for rate limits / server errors worth retrying after a pause."""
        return False


class GeminiProvider(LLMProvider):
    def __init__(self, model: str, api_key: str, cfg: dict, fallback_models=()):
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._models = [model] + [m for m in fallback_models if m and m != model]
        self._temperature = float(cfg.get("temperature", 0.0))
        self._max_tokens = int(cfg.get("max_output_tokens", 2048))

    def complete(self, system: str, prompt: str) -> str:
        from google.genai import errors

        attempts = 0
        max_attempts = len(self._models)
        while True:
            model = self._models[0]
            try:
                resp = self._client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=self._types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=self._temperature,
                        max_output_tokens=self._max_tokens,
                        response_mime_type="application/json",
                    ),
                )
            except errors.APIError as e:
                attempts += 1
                is_quota = e.code == 429 or "RESOURCE_EXHAUSTED" in str(e) or "quota" in str(e).lower()
                is_overloaded = e.code in (500, 502, 503, 504) or "overloaded" in str(e).lower()
                if e.code == 404 and len(self._models) > 1:
                    log.warning("Gemini model %s not available (404); dropping and switching to %s", model, self._models[1])
                    print(f"     [LLM fallback] {model} not available (404); switching to {self._models[1]}")
                    self._models.pop(0)
                    continue
                if (is_quota or is_overloaded) and len(self._models) > 1 and attempts < max_attempts:
                    reason = "quota exceeded (429)" if is_quota else "server overloaded (503)"
                    log.warning("Gemini model %s %s; rotating to fallback model %s", model, reason, self._models[1])
                    print(f"     [LLM fallback] {model} {reason}; switching to {self._models[1]}")
                    self._models.append(self._models.pop(0))
                    continue
                raise
            return resp.text or ""

    def is_transient(self, exc: Exception) -> bool:
        from google.genai import errors

        return isinstance(exc, errors.APIError) and exc.code in (408, 429, 500, 502, 503, 504)


class ClaudeProvider(LLMProvider):
    def __init__(self, model: str, api_key: str, cfg: dict, fallback_models=()):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self._model = model
        self._max_tokens = int(cfg.get("max_output_tokens", 2048))

    def complete(self, system: str, prompt: str) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    def is_transient(self, exc: Exception) -> bool:
        import anthropic

        if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError)):
            return True
        return isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500


PROVIDERS: dict[str, type[LLMProvider]] = {
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
}


def make_provider(llm_cfg: dict) -> LLMProvider:
    name = str(llm_cfg["provider"]).lower().strip()
    if name not in PROVIDERS:
        raise LLMError(f"Unknown llm.provider '{name}'. Use one of: {', '.join(PROVIDERS)}")
    cls = PROVIDERS[name]
    model = (llm_cfg.get("models") or {}).get(name, "")
    fallbacks = (llm_cfg.get("fallback_models") or {}).get(name) or []
    key = ""
    if cls.needs_key:
        env = (llm_cfg.get("api_key_env") or {}).get(name, "")
        key = os.environ.get(env, "").strip() if env else ""
        if not key and env and sys.platform == "win32" and "PYTEST_CURRENT_TEST" not in os.environ:
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
                    val, _ = winreg.QueryValueEx(k, env)
                    key = str(val).strip()
            except Exception:
                pass
        if not key:
            raise LLMError(
                fatal=True, msg=f"Environment variable {env} is not set. On Windows run:  setx {env} \"<your key>\"  "
                "then open a NEW terminal and run again."
            )
    return cls(model, key, llm_cfg, fallbacks)


# --------------------------------------------------------------------------- answer picking

SYSTEM_PROMPT = (
    "You are an expert tutor answering multiple-choice quiz questions from a college course. "
    "Think carefully, pick the best answer, and reply with a single JSON object only."
)

JSON_SHAPE = '{"answer_indices": [<0-based ints>], "confidence": "high|medium|low"}'


def build_prompt(q: Question, context: str = "", previous: list[int] | None = None) -> str:
    lines = []
    if context:
        lines.append(f"Course context: {context}")
    if q.number and q.total:
        lines.append(f"Question {q.number} of {q.total}.")
    lines += ["", "Question:", q.text.strip() or "(no question text found; infer it from the options)"]
    for block in q.code:
        lines += ["", "Code in the question:", "```", block.rstrip(), "```"]
    lines += ["", "Options (0-based index):"]
    lines += [f"[{i}] {opt}" for i, opt in enumerate(q.options)]
    lines.append("IMPORTANT: Use 0-based indexing for answer_indices (e.g. 0 for the first option, 1 for the second). Do NOT use 1-based index.")
    lines.append("")
    if q.multi:
        lines.append("This question may have MORE THAN ONE correct option. Select every correct option.")
    else:
        lines.append("Exactly ONE option is correct. Select exactly one.")
    if previous:
        lines.append(
            f"Note: an earlier attempt chose {previous} and that attempt failed overall; "
            "that choice may be wrong, so reconsider carefully."
        )
    lines += ["", f"Reply with JSON only, no prose and no code fences, exactly in this shape:", JSON_SHAPE]
    return "\n".join(lines)


_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def parse_answer(text: str, n_options: int, multi: bool) -> Answer:
    """Parse and validate the model reply. Raises ValueError with a reason."""
    s = (text or "").strip()
    m = _FENCE.search(s)
    if m:
        s = m.group(1).strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    try:
        data = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e.msg}") from None
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    idx = data.get("answer_indices")
    if isinstance(idx, int) and not isinstance(idx, bool):
        idx = [idx]
    if not isinstance(idx, list) or not idx:
        raise ValueError("answer_indices must be a non-empty list of integers")
    out: list[int] = []
    for v in idx:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"answer index {v!r} is not an integer")
        if not 0 <= v < n_options:
            raise ValueError(f"answer index {v} is out of range 0..{n_options - 1}")
        if v not in out:
            out.append(v)
    if not multi and len(out) != 1:
        raise ValueError(f"exactly one index is required for this question, got {len(out)}")
    conf = str(data.get("confidence", "")).strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "low"
    return Answer(sorted(out), conf, text)


class AnswerPicker:
    def __init__(self, provider: LLMProvider, llm_cfg: dict):
        self.provider = provider
        self.min_gap = float(llm_cfg.get("min_seconds_between_calls", 0) or 0)
        self.backoff = [float(x) for x in (llm_cfg.get("transient_retry_delays") or [5, 15, 30, 60])]
        self._last_call = 0.0

    def _call(self, prompt: str) -> str:
        for attempt in range(len(self.backoff) + 1):
            wait = self.min_gap - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                return self.provider.complete(SYSTEM_PROMPT, prompt)
            except Exception as e:
                if attempt < len(self.backoff) and self.provider.is_transient(e):
                    delay = self.backoff[attempt]
                    log.warning("LLM busy (%s); retrying in %.0fs", type(e).__name__, delay)
                    time.sleep(delay)
                    continue
                raise LLMError(f"LLM call failed: {type(e).__name__}: {e}", fatal=True) from e
        raise LLMError("LLM call failed after retries", fatal=True)

    def choose(self, q: Question, context: str = "", previous: list[int] | None = None) -> Answer:
        if len(q.options) < 2:
            raise LLMError("fewer than two options extracted")
        prompt = build_prompt(q, context, previous)
        reply = self._call(prompt)
        try:
            return parse_answer(reply, len(q.options), q.multi)
        except ValueError as e:
            log.warning("Invalid LLM reply (%s); retrying once", e)
        retry = (
            f"{prompt}\n\nYour previous reply was invalid. Reply with ONLY the JSON object "
            f"{JSON_SHAPE} using indices 0..{len(q.options) - 1}"
            + ("" if q.multi else " and exactly one index") + "."
        )
        reply = self._call(retry)
        try:
            return parse_answer(reply, len(q.options), q.multi)
        except ValueError as e:
            # Last-ditch rescue: if model used 1-based indexing (e.g. 1..N instead of 0..N-1)
            try:
                start, end = reply.find("{"), reply.rfind("}")
                if start != -1 and end > start:
                    data = json.loads(reply[start:end + 1])
                    idx = data.get("answer_indices")
                    if isinstance(idx, int) and not isinstance(idx, bool):
                        idx = [idx]
                    if isinstance(idx, list) and idx and all(isinstance(v, int) and not isinstance(v, bool) for v in idx):
                        n_opts = len(q.options)
                        if min(idx) >= 1 and all(v <= n_opts for v in idx) and any(v == n_opts for v in idx):
                            adjusted = json.dumps({**data, "answer_indices": [v - 1 for v in idx]})
                            return parse_answer(adjusted, n_opts, q.multi)
            except Exception:
                pass
            raise LLMError(f"LLM reply invalid twice: {e}") from None
