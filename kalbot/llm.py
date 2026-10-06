"""LLM providers behind one small interface, plus everything that asks them for work.

Pick the provider in config.yaml (llm.provider: gemini | claude). API keys are
read from environment variables (or .env) only and are never printed or logged.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

from . import prompts

log = logging.getLogger("kalbot.llm")


class LLMError(Exception):
    """fatal=True means every later call will fail too (bad key, quota, model)."""

    def __init__(self, msg: str, fatal: bool = False):
        super().__init__(msg)
        self.fatal = fatal


@dataclass
class Request:
    system: str
    prompt: str
    material: str = ""        # shared LU text; providers put it in a cacheable prefix
    json: bool = False
    kind: str = "quiz"        # quiz | written | coding: picks effort and output budget


# --------------------------------------------------------------------------- providers


class LLMProvider:
    """Minimal interface: one request in, text out."""

    needs_key = True

    def complete(self, req: Request) -> str:
        raise NotImplementedError

    def is_transient(self, exc: Exception) -> bool:
        """True for rate limits / server errors worth retrying after a pause."""
        return False


def _budget(cfg: dict, kind: str) -> int:
    key = "max_output_tokens" if kind == "quiz" else "max_output_tokens_long"
    return int(cfg.get(key) or (8192 if kind == "quiz" else 32000))


class GeminiProvider(LLMProvider):
    def __init__(self, model: str, api_key: str, cfg: dict, fallback_models=()):
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._models = [model] + [m for m in fallback_models if m and m != model]
        temp = cfg.get("temperature")
        self._temperature = float(temp) if isinstance(temp, (int, float)) and not isinstance(temp, bool) else None
        self._cfg = cfg

    def complete(self, req: Request) -> str:
        from google.genai import errors

        system = req.system + ("\n\n" + prompts.material_block(req.material) if req.material else "")
        while True:
            model = self._models[0]
            try:
                resp = self._client.models.generate_content(
                    model=model,
                    contents=req.prompt,
                    config=self._types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=self._temperature if req.kind == "quiz" else None,
                        max_output_tokens=_budget(self._cfg, req.kind),
                        response_mime_type="application/json" if req.json else None,
                    ),
                )
            except errors.ClientError as e:
                if e.code == 404 and len(self._models) > 1:
                    log.warning("Gemini model %s is not available; trying %s", model, self._models[1])
                    self._models.pop(0)
                    continue
                raise
            return resp.text or ""

    def is_transient(self, exc: Exception) -> bool:
        from google.genai import errors

        return isinstance(exc, errors.APIError) and exc.code in (408, 429, 500, 502, 503, 504)


# models that accept the server-side refusal fallback
_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")


class ClaudeProvider(LLMProvider):
    def __init__(self, model: str, api_key: str, cfg: dict, fallback_models=()):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self._model = model
        self._cfg = cfg
        self._effort = cfg.get("effort") or {}
        self._fallback = bool(cfg.get("refusal_fallback", True)) and model.startswith(_FALLBACK_MODELS)

    def complete(self, req: Request) -> str:
        system = [{"type": "text", "text": req.system}]
        if req.material:   # identical for every question of an LU, so it is served from the cache
            system.append({"type": "text", "text": prompts.material_block(req.material),
                           "cache_control": {"type": "ephemeral"}})
        kw = dict(model=self._model, max_tokens=_budget(self._cfg, req.kind), system=system,
                  messages=[{"role": "user", "content": req.prompt}])
        effort = str(self._effort.get(req.kind) or "").strip()
        if effort and "haiku" not in self._model:
            kw["output_config"] = {"effort": effort}
        msg = self._stream(kw)
        if msg.stop_reason == "refusal":
            raise LLMError("Claude declined this request", fatal=False)
        text = "".join(b.text for b in msg.content if b.type == "text")
        if msg.stop_reason == "max_tokens":
            log.warning("Claude reply hit max_tokens (%s)", kw["max_tokens"])
        return text

    def _stream(self, kw: dict):
        # streaming keeps long answers (code, projects) clear of HTTP timeouts
        if self._fallback:
            try:
                with self._client.beta.messages.stream(
                        **kw, betas=["server-side-fallback-2026-07-01"], fallbacks="default") as s:
                    return s.get_final_message()
            except self._anthropic.BadRequestError as e:
                if "fallback" not in str(e).lower():
                    raise
                log.warning("refusal fallback not available for this key; continuing without it")
                self._fallback = False
        with self._client.messages.stream(**kw) as s:
            return s.get_final_message()

    def is_transient(self, exc: Exception) -> bool:
        a = self._anthropic
        if isinstance(exc, (a.RateLimitError, a.APIConnectionError)):
            return True
        return isinstance(exc, a.APIStatusError) and exc.status_code >= 500


PROVIDERS: dict[str, type[LLMProvider]] = {
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
}


def make_provider(llm_cfg: dict) -> LLMProvider:
    name = str(llm_cfg["provider"]).lower().strip()
    if name not in PROVIDERS:
        raise LLMError(f"Unknown llm.provider '{name}'. Use one of: {', '.join(PROVIDERS)}", fatal=True)
    cls = PROVIDERS[name]
    model = (llm_cfg.get("models") or {}).get(name, "")
    fallbacks = (llm_cfg.get("fallback_models") or {}).get(name) or []
    key = ""
    if cls.needs_key:
        env = (llm_cfg.get("api_key_env") or {}).get(name, "")
        key = os.environ.get(env, "").strip() if env else ""
        if not key:
            raise LLMError(
                fatal=True, msg=f"{env} is not set. Add it to the .env file in the kalbot folder "
                f"(see .env.example), or on Windows run:  setx {env} \"<your key>\"  and open a new terminal."
            )
    return cls(model, key, llm_cfg, fallbacks)


# --------------------------------------------------------------------------- data


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


@dataclass
class WriteSpec:
    question: str
    label: str = ""
    words: tuple[int, int] = (120, 250)
    short: bool = False
    max_chars: int = 200
    others: list[str] = field(default_factory=list)
    existing: str = ""        # a template or partial draft already in the box


@dataclass
class CodeSpec:
    question: str
    language: str = ""
    starter: str = ""


@dataclass
class ProjectSpec:
    question: str
    other_links: list[str] = field(default_factory=list)


@dataclass
class Project:
    repo_name: str
    description: str
    files: dict[str, str]
    static_site: bool = False
    requires_existing_repo: bool = False


# --------------------------------------------------------------------------- parsing

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)
_CODE_FENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.S)
_WORD = re.compile(r"[\w'’-]+")


def json_object(text: str) -> dict:
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
    return data


def parse_answer(text: str, n_options: int, multi: bool) -> Answer:
    """Parse and validate a quiz reply. Raises ValueError with a reason."""
    data = json_object(text)
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


def count_words(text: str) -> int:
    return len(_WORD.findall(text or ""))


def clean_text(text: str) -> str:
    """Strip Markdown and chatter a model may add around a written answer."""
    s = (text or "").strip()
    m = re.fullmatch(r"```[^\n]*\n(.*?)\n?```", s, re.S)
    if m:
        s = m.group(1).strip()
    s = re.sub(r"^(here is|here's)[^\n]*:\s*\n+", "", s, flags=re.I)
    s = re.sub(r"^\s*(answer|response)\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"\A#{1,6}\s+[^\n]*\n+", "", s)          # a title line
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def fit_chars(text: str, limit: int) -> str:
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit]
    if not text[limit].isspace() and " " in cut[limit // 2:]:   # don't end mid-word
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-")


def extract_code(text: str) -> str:
    blocks = _CODE_FENCE.findall(text or "")
    code = max(blocks, key=len) if blocks else (text or "")
    return code.strip("\n").rstrip() + "\n"


_SAFE_PATH = re.compile(r"^[\w.@+-]+(/[\w.@+-]+)*$")


def parse_project(text: str, fallback_name: str) -> Project:
    data = json_object(text)
    files_in = data.get("files")
    if not isinstance(files_in, list):
        raise ValueError("files must be a list")
    files: dict[str, str] = {}
    total = 0
    for f in files_in:
        if not isinstance(f, dict):
            raise ValueError("each file must be an object with path and content")
        p, c = str(f.get("path") or "").strip().replace("\\", "/"), f.get("content")
        while p.startswith("./"):
            p = p[2:]
        if not p or not isinstance(c, str):
            raise ValueError("each file needs a path and text content")
        if not _SAFE_PATH.match(p) or ".." in p.split("/") or p.split("/")[0] == ".git":
            raise ValueError(f"unsafe file path {p!r}")
        files[p] = c
        total += len(c.encode("utf-8"))
    requires = bool(data.get("requires_existing_repo"))
    if not requires:
        if not files:
            raise ValueError("no files in the project")
        if len(files) > 40 or total > 300_000:
            raise ValueError("project is too big")
    name = re.sub(r"[^a-z0-9._-]+", "-", str(data.get("repo_name") or fallback_name).lower()).strip("-.")[:60]
    if not name:
        name = re.sub(r"[^a-z0-9]+", "-", fallback_name.lower()).strip("-")[:60] or "kalvium-project"
    static = bool(data.get("static_site")) and "index.html" in files
    return Project(name, str(data.get("description") or "")[:300], files, static, requires)


# --------------------------------------------------------------------------- the assistant


class LLM:
    """Pacing, retries and validation around a provider, for each kind of work."""

    def __init__(self, provider: LLMProvider, cfg: dict):
        llm = cfg["llm"]
        self.provider = provider
        self.min_gap = float(llm.get("min_seconds_between_calls", 0) or 0)
        self.backoff = [float(x) for x in (llm.get("transient_retry_delays") or [5, 15, 30, 60])]
        self.use_material = bool(llm.get("include_lu_content", True))
        self.max_chars = int(llm.get("max_context_chars", 24000))
        self.style = str((cfg.get("written") or {}).get("style", "")).strip()
        self._last_call = 0.0

    def material(self, text: str) -> str:
        if not self.use_material or not text:
            return ""
        return text[: self.max_chars]

    def ask(self, req: Request) -> str:
        for attempt in range(len(self.backoff) + 1):
            wait = self.min_gap - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                return self.provider.complete(req)
            except LLMError:
                raise
            except Exception as e:
                if attempt < len(self.backoff) and self.provider.is_transient(e):
                    delay = self.backoff[attempt]
                    log.warning("LLM busy (%s); retrying in %.0fs", type(e).__name__, delay)
                    time.sleep(delay)
                    continue
                raise LLMError(f"LLM call failed: {type(e).__name__}: {e}", fatal=True) from e
        raise LLMError("LLM call failed after retries", fatal=True)

    # ---------------------------------------------------------------- quiz

    def choose(self, q: Question, course: str = "", material: str = "",
               previous: list[int] | None = None) -> Answer:
        if len(q.options) < 2:
            raise LLMError("fewer than two options extracted")
        prompt = prompts.quiz_prompt(q, course, previous)
        req = Request(prompts.QUIZ_SYSTEM, prompt, self.material(material), json=True, kind="quiz")
        try:
            return parse_answer(self.ask(req), len(q.options), q.multi)
        except ValueError as e:
            log.warning("Invalid LLM reply (%s); retrying once", e)
        req.prompt = (f"{prompt}\n\nYour previous reply was invalid. Reply with ONLY the JSON object "
                      f"{prompts.QUIZ_JSON} using indices 0..{len(q.options) - 1}"
                      + ("" if q.multi else " and exactly one index") + ".")
        try:
            return parse_answer(self.ask(req), len(q.options), q.multi)
        except ValueError as e:
            raise LLMError(f"LLM reply invalid twice: {e}") from None

    # ---------------------------------------------------------------- written answers

    def write(self, spec: WriteSpec, course: str = "", material: str = "") -> str:
        system = prompts.WRITE_SYSTEM.format(style=self.style)
        prompt = prompts.write_prompt(spec, course)
        req = Request(system, prompt, self.material(material), kind="written")
        text = clean_text(self.ask(req))
        if not text:
            text = clean_text(self.ask(req))
        if not text:
            raise LLMError("the model returned an empty answer")
        if spec.short:
            return fit_chars(text.replace("\n", " "), spec.max_chars)
        lo, hi = spec.words
        n = count_words(text)
        if not lo <= n <= hi:
            req.prompt = prompts.revise_words_prompt(prompt, text, n, lo, hi)
            revised = clean_text(self.ask(req))
            m = count_words(revised)
            if revised and abs(m - (lo + hi) / 2) < abs(n - (lo + hi) / 2):
                text = revised
        return text

    # ---------------------------------------------------------------- code

    def code(self, spec: CodeSpec, course: str = "", material: str = "",
             previous: str = "", feedback: str = "") -> str:
        req = Request(prompts.CODE_SYSTEM, prompts.code_prompt(spec, course, previous, feedback),
                      self.material(material), kind="coding")
        code = extract_code(self.ask(req))
        if not code.strip():
            code = extract_code(self.ask(req))
        if not code.strip():
            raise LLMError("the model returned no code")
        return code

    # ---------------------------------------------------------------- projects

    def project(self, spec: ProjectSpec, name_hint: str, course: str = "", material: str = "") -> Project:
        prompt = prompts.project_prompt(spec, course)
        req = Request(prompts.PROJECT_SYSTEM, prompt, self.material(material), json=True, kind="coding")
        try:
            return parse_project(self.ask(req), name_hint)
        except ValueError as e:
            log.warning("Invalid project reply (%s); retrying once", e)
            req.prompt = f"{prompt}\n\nYour previous reply was invalid ({e}). Reply with ONLY the JSON object."
        try:
            return parse_project(self.ask(req), name_hint)
        except ValueError as e:
            raise LLMError(f"project reply invalid twice: {e}") from None
