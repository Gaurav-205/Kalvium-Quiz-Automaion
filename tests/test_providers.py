"""The provider classes, checked offline.

Claude: the real Anthropic SDK talks to a local server that records the request
and streams back a canned reply, so the exact wire payload is verified.
Gemini: the SDK client is replaced by a stub that records generate_content().
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from kalbot.llm import ClaudeProvider, GeminiProvider, LLMError, Request

pytest.importorskip("anthropic")


def sse(text: str, stop: str = "end_turn") -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                           "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


@pytest.fixture
def claude_server(monkeypatch):
    seen: list = []
    replies: list = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
            text, stop = replies.pop(0) if replies else ("OK", "end_turn")
            data = sse(text, stop)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    yield seen, replies
    srv.shutdown()


CFG = {"effort": {"quiz": "medium", "written": "high"}, "refusal_fallback": True,
       "max_output_tokens": 8192, "max_output_tokens_long": 32000}


def test_claude_request_caches_material_sets_effort_and_fallback(claude_server):
    seen, _ = claude_server
    p = ClaudeProvider("claude-opus-5-5", "sk-test", CFG)
    out = p.complete(Request("SYSTEM", "QUESTION", material="LU TEXT", json=True, kind="quiz"))
    assert out == "OK"
    req = seen[0]
    body = req["body"]
    assert body["model"] == "claude-opus-5-5" and body["stream"] is True and body["max_tokens"] == 8192
    assert body["system"][0] == {"type": "text", "text": "SYSTEM"}
    assert "LU TEXT" in body["system"][1]["text"] and body["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"] == [{"role": "user", "content": "QUESTION"}]
    assert body["output_config"] == {"effort": "medium"}
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in req["headers"].get("anthropic-beta", "")
    assert "thinking" not in body and "temperature" not in body


def test_claude_long_work_uses_its_budget_and_no_effort_on_haiku(claude_server):
    seen, _ = claude_server
    ClaudeProvider("claude-opus-5-5", "k", CFG).complete(Request("S", "P", kind="written"))
    assert seen[-1]["body"]["max_tokens"] == 32000 and seen[-1]["body"]["output_config"] == {"effort": "high"}
    assert len(seen[-1]["body"]["system"]) == 1          # no material, no cache block
    ClaudeProvider("claude-haiku-4-5", "k", CFG).complete(Request("S", "P", kind="quiz"))
    body = seen[-1]["body"]
    assert "output_config" not in body and "fallbacks" not in body
    assert "anthropic-beta" not in seen[-1]["headers"]


def test_claude_refusal_is_a_non_fatal_error(claude_server):
    _, replies = claude_server
    replies.append(("", "refusal"))
    with pytest.raises(LLMError) as e:
        ClaudeProvider("claude-opus-5-5", "k", CFG).complete(Request("S", "P"))
    assert not e.value.fatal


def test_gemini_puts_material_in_the_system_instruction(monkeypatch):
    calls = []

    class Models:
        def generate_content(self, **kw):
            calls.append(kw)
            return SimpleNamespace(text='{"ok": true}')

    p = GeminiProvider.__new__(GeminiProvider)
    from google.genai import types
    p._types, p._client, p._models = types, SimpleNamespace(models=Models()), ["gemini-x"]
    p._temperature, p._cfg = 0.2, {"max_output_tokens": 100, "max_output_tokens_long": 900}
    assert p.complete(Request("SYS", "Q", material="LU TEXT", json=True, kind="quiz")) == '{"ok": true}'
    c = calls[0]["config"]
    assert calls[0]["model"] == "gemini-x" and calls[0]["contents"] == "Q"
    assert c.system_instruction.startswith("SYS") and "LU TEXT" in c.system_instruction
    assert c.response_mime_type == "application/json" and c.max_output_tokens == 100 and c.temperature == 0.2
    p.complete(Request("SYS", "Write it", kind="written"))
    c = calls[1]["config"]
    assert c.response_mime_type is None and c.max_output_tokens == 900 and c.temperature is None
