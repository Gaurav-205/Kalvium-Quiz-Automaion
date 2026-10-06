"""A small local stand-in for the Kalvium portal, used by the tests.

It is NOT a copy of the real site (whose DOM we have not seen). It mixes several
plausible UI patterns on purpose so the bot's heuristics get exercised:
native radios/checkboxes, ARIA radios, plain clickable <div> options, a quiz in
an iframe, a quiz behind an in-page tab, a Start screen, a confirm modal, a
feedback star-rating widget, a sidebar with other LUs' statuses, already
submitted quizzes, collapsible modules, and assignments: textareas, rich-text
boxes, a CodeMirror-style editor with Run Tests, a plain-textarea code box
behind "Start Assignment", link fields, a short answer, and a comment box on
every LU page that must never be touched.

Run it on its own to look around:  python tests/mock_portal.py
"""

from __future__ import annotations

import html
import json
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def Q(text, options, answer, code="", multi=False):
    return {"text": text, "options": options, "answer": answer, "code": code, "multi": multi}


QUIZZES = {
    ("2505", "11"): [Q(f"Philosophy warm-up question {i}?", ["Alpha", "Beta", "Gamma", "Delta"], [i % 4]) for i in range(1, 6)],
    ("2505", "12"): [
        Q("Who is known for the method of questioning called elenchus?", ["Plato", "Socrates", "Aristotle", "Kant"], [1]),
        Q("The Socratic method mainly aims to:", ["Win debates", "Expose contradictions and clarify ideas",
                                                  "Memorise facts", "Avoid questions"], [1]),
        Q("What does this snippet print?", ["0", "1", "2", "Error"], [2],
          code="def ask(n):\n    if n == 0:\n        return 0\n    return 1 + ask(n - 1)\n\nprint(ask(2))"),
        Q("Which of these are Socratic dialogues? Select all that apply.",
          ["Apology", "Republic", "Leviathan", "Critique of Pure Reason"], [0, 1], multi=True),
        Q("'I know that I know nothing' expresses:", ["Dogmatism", "Socratic ignorance", "Nihilism", "Empiricism"], [1]),
    ],
    ("2505", "21"): [
        Q("Virtue ethics is most associated with:", ["Aristotle", "Bentham", "Mill", "Hobbes"], [0]),
        Q("Eudaimonia is usually translated as:", ["Duty", "Flourishing", "Pleasure", "Contract"], [1]),
        Q("The 'golden mean' sits between:", ["Two virtues", "Two vices", "Law and custom", "Reason and faith"], [1]),
        Q("Courage is the mean between cowardice and:", ["Rashness", "Humility", "Prudence", "Justice"], [0]),
        Q("Virtues are developed mainly through:", ["Habit", "Luck", "Birth", "Contracts"], [0]),
    ],
    ("2505", "23"): [
        Q("Utilitarianism judges actions by their:", ["Intentions", "Consequences", "Rules", "Authors"], [1]),
        Q("Who wrote 'Utilitarianism' (1863)?", ["J. S. Mill", "Kant", "Rawls", "Locke"], [0]),
        Q("Bentham's 'felicific calculus' measures:", ["Duties", "Pleasure and pain", "Rights", "Virtues"], [1]),
        Q("Act vs rule utilitarianism differ on:", ["Whether consequences matter",
                                                    "Whether to evaluate single acts or general rules",
                                                    "Whether pleasure is good", "Nothing"], [1]),
        Q("A common objection to utilitarianism concerns:", ["Justice for individuals", "Grammar",
                                                            "Astronomy", "Colour theory"], [0]),
    ],
    ("2506", "11"): [
        Q("Array index access is:", ["O(1)", "O(n)", "O(log n)", "O(n^2)"], [0]),
        Q("What is printed?", ["[1, 2]", "[2, 1]", "[1]", "[]"], [0], code="a = [1]\na.append(2)\nprint(a)"),
        Q("Inserting at the front of a dynamic array is:", ["O(1)", "O(n)", "O(log n)", "Impossible"], [1]),
        Q("Which are true about arrays? Select all that apply.",
          ["Contiguous memory", "Constant-time append (amortised) for dynamic arrays", "Always sorted", "Linked nodes"],
          [0, 1], multi=True),
        Q("Python lists are:", ["Linked lists", "Dynamic arrays", "Hash maps", "Trees"], [1]),
    ],
    ("2506", "12"): [Q(f"Linked list question {i}?", ["A", "B", "C", "D"], [0]) for i in range(1, 6)],
    ("2506", "13"): [Q(f"Stack question {i}?", ["A", "B", "C", "D"], [0]) for i in range(1, 6)],
}

FIZZ_STARTER = "def fizzbuzz(n):\n    # return a list of strings for 1..n\n    pass\n"


def F(name, type, question="", label="", placeholder="", value=""):
    return {"name": name, "type": type, "question": question, "label": label, "placeholder": placeholder,
            "value": value}


# Assignments. fields: name, widget type, the question above it, its label/placeholder.
TASKS = {
    ("2505", "13"): {"fields": [F("answer", "textarea", "Reflect on what 'know thyself' means for you as a "
                                  "learner. Write your answer (minimum 200 words).", placeholder="Type your answer")]},
    ("2505", "22"): {"fields": [
        F("repo", "url", "Build a small script that flags unethical data use.", "GitHub repository link",
          "https://github.com/you/repo"),
        F("video", "url", "", "Video walkthrough (Loom or YouTube)", "https://www.loom.com/share/..."),
    ]},
    ("2507", "11"): {"confirm": True, "fields": [
        F("q1", "rich", "1. What did you learn about semantic HTML this week? (50-80 words)", "Answer 1"),
        F("q2", "plain_rich", "2. Name one accessibility habit you will keep. Answer in about 40 words.", "Answer 2"),
    ]},
    ("2507", "12"): {"run": True, "fields": [
        F("code", "cm5", "Write fizzbuzz(n) returning a list of strings for 1..n: 'Fizz' for multiples of 3, "
          "'Buzz' for multiples of 5, 'FizzBuzz' for both, else the number.", value=FIZZ_STARTER)]},
    ("2507", "13"): {"start": True, "fields": [
        F("code", "code_textarea", "Write a function add(a, b) that returns the sum of two numbers.",
          value="def add(a, b):\n    ...\n")]},
    ("2507", "14"): {"fields": [
        F("repo", "url", "Build a personal portfolio website (HTML/CSS/JS) with an About and a Projects section.",
          "GitHub repository link", "https://github.com/..."),
        F("live", "url", "", "Deployed website link", "https://..."),
        F("video", "url", "", "Video walkthrough link (Loom/YouTube)", "https://..."),
    ]},
    ("2507", "15"): {"fields": [F("short", "text", "", "What does HTML stand for?")]},
    ("2507", "16"): {"fields": [F("video", "url", "Record a 2-minute demo of your portfolio.", "Demo video link")]},
}

# kind: quiz variant or task type; start: has a Start screen; tab: quiz behind an in-page tab
LUS = {
    "2505": {
        "name": "Introduction to Philosophy",
        "rows": "div",
        "modules": [
            ("Module 1: Foundations", True, [
                ("11", "1.1", "What is Philosophy?", {"kind": "native", "start": True}),
                ("12", "1.2", "The Socratic Method", {"kind": "native", "start": True}),
                ("13", "1.3", "Reflection: Know Thyself", {"kind": "task"}),
            ]),
            ("Module 2: Ethics", False, [
                ("21", "2.1", "Virtue Ethics", {"kind": "aria", "start": False}),
                ("22", "2.2", "Ethics in Code", {"kind": "task"}),
                ("23", "2.3", "Utilitarianism", {"kind": "div", "start": False, "tab": True}),
                ("24", "2.4", "Reading: Kant", {"kind": "reading"}),
            ]),
        ],
    },
    "2506": {
        "name": "Data Structures",
        "rows": "a",
        "modules": [
            ("", True, [
                ("11", "1.1", "Arrays", {"kind": "native", "start": True, "iframe": True}),
                ("12", "1.2", "Linked Lists", {"kind": "native", "start": True}),
                ("13", "1.3", "Stacks", {"kind": "native", "start": True}),
            ]),
        ],
    },
    "2507": {
        "name": "Web Development",
        "rows": "div",
        "modules": [
            ("Module 1: Building for the web", True, [
                ("11", "1.1", "Learning Journal", {"kind": "task"}),
                ("12", "1.2", "FizzBuzz", {"kind": "task"}),
                ("13", "1.3", "Sum of Two Numbers", {"kind": "task"}),
                ("14", "1.4", "Portfolio Website", {"kind": "task"}),
                ("15", "1.5", "HTML Basics", {"kind": "task"}),
                ("16", "1.6", "Demo Day", {"kind": "task"}),
            ]),
        ],
    },
}

CSS = """
body{font-family:sans-serif;margin:0} header{background:#223;color:#fff;padding:8px}
header a{color:#fff;margin-right:12px} .wrap{display:flex} aside{width:220px;background:#eee;padding:8px;font-size:13px}
main{flex:1;padding:16px} .card{border:1px solid #ccc;padding:12px;margin:8px;display:inline-block;width:260px}
.lu-item{cursor:pointer;padding:6px;border-bottom:1px solid #ddd} .lu-item:hover{background:#f6f6f6}
.btn{padding:6px 12px;margin:4px;cursor:pointer} .btn[aria-disabled=true]{opacity:.5}
.opt{display:block;padding:6px;margin:4px 0;border:1px solid #ccc;cursor:pointer}
.opt[aria-checked=true]{background:#cde}
.opt-card{padding:8px;margin:4px 0;border:1px solid #bbb;cursor:pointer} .opt-card:hover{background:#fafafa}
.opt-card--chosen{background:#9cf;border-color:#06c}
.pill{display:inline-block;width:22px;text-align:center;border:1px solid #888;margin:2px;cursor:pointer}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center}
.modal-box{background:#fff;padding:20px} .hidden{display:none} pre{background:#f4f4f4;padding:8px}
.rich{min-height:60px;border:1px solid #999;padding:6px} .CodeMirror{border:1px solid #999;min-height:90px}
.CodeMirror-code{font-family:monospace;white-space:pre;padding:6px} .code-input{font-family:monospace}
.comments{margin-top:30px;border-top:1px solid #ccc}
"""

QUIZ_ENGINE = r"""
(function(){
const Q = window.QUIZ; const root = document.getElementById('quiz-root');
let cur = 0, answers = Q.questions.map(() => []), started = !Q.start, done = Q.already;
const h = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
function render() {
  if (done) return renderResult();
  if (!started) {
    root.innerHTML = '<div class="intro"><h3>Quiz</h3><p>5 questions. Choose the best answer.</p><button class="btn" id="start">Start Quiz</button></div>';
    document.getElementById('start').onclick = () => { root.innerHTML = '<p>Loading...</p>'; setTimeout(() => { started = true; render(); }, 400); };
    return;
  }
  const q = Q.questions[cur]; const last = cur === Q.questions.length - 1; const chosen = answers[cur];
  let opts;
  if (Q.variant === 'native') {
    opts = '<div class="options">' + q.options.map((o, i) => `<label class="opt"><input type="${q.multi ? 'checkbox' : 'radio'}" name="q${cur}" value="${i}" ${chosen.includes(i) ? 'checked' : ''}> <span>${h(o)}</span></label>`).join('') + '</div>';
  } else if (Q.variant === 'aria') {
    opts = `<div role="${q.multi ? 'group' : 'radiogroup'}" class="options">` + q.options.map((o, i) => `<div role="${q.multi ? 'checkbox' : 'radio'}" tabindex="0" aria-checked="${chosen.includes(i)}" data-i="${i}" class="opt">${h(o)}</div>`).join('') + '</div>';
  } else {
    opts = '<div class="options">' + q.options.map((o, i) => `<div class="opt-card${chosen.includes(i) ? ' opt-card--chosen' : ''}" data-i="${i}"><b>${'ABCD'[i]}.</b> ${h(o)}</div>`).join('') + '</div>';
  }
  const pills = Q.variant === 'div' ? '<div class="pills">' + Q.questions.map((_, i) => `<span class="pill" data-p="${i}">${i + 1}</span>`).join('') + '</div>' : '';
  const progress = Q.variant === 'aria' ? `<div class="progress">${cur + 1}/${Q.questions.length}</div>` : `<div class="progress">Question ${cur + 1} of ${Q.questions.length}</div>`;
  const code = q.code ? `<pre><code>${h(q.code)}</code></pre>` : '';
  const go = last ? (Q.variant === 'div' ? '<div role="button" class="btn" id="go">Submit</div>' : '<button class="btn" id="go">Submit</button>') : '<button class="btn" id="go">Next &rarr;</button>';
  root.innerHTML = `<div class="quiz">${pills}${progress}<div class="question"><p>${h(q.text)}</p>${code}</div>${opts}<div class="actions"><button class="btn" id="prev">Previous</button>${go}</div></div>`;
  const goEl = document.getElementById('go');
  const sync = () => { const dis = answers[cur].length === 0; if (goEl.tagName === 'BUTTON') goEl.disabled = dis; else goEl.setAttribute('aria-disabled', String(dis)); };
  sync();
  const pick = (i, on) => {
    if (q.multi) answers[cur] = on ? [...new Set([...answers[cur], i])] : answers[cur].filter(x => x !== i);
    else answers[cur] = [i];
  };
  if (Q.variant === 'native') {
    root.querySelectorAll('input').forEach(inp => inp.onchange = () => { pick(+inp.value, inp.checked); setTimeout(render, 50); });
  } else if (Q.variant === 'aria') {
    root.querySelectorAll('[data-i]').forEach(el => el.onclick = () => { const i = +el.dataset.i; pick(i, !answers[cur].includes(i)); render(); });
  } else {
    root.querySelectorAll('[data-i]').forEach(el => el.onclick = () => {
      const i = +el.dataset.i; pick(i, !answers[cur].includes(i));
      root.querySelectorAll('[data-i]').forEach(x => x.classList.toggle('opt-card--chosen', answers[cur].includes(+x.dataset.i)));
      sync();
    });
    root.querySelectorAll('[data-p]').forEach(p => p.onclick = () => { cur = +p.dataset.p; render(); });
  }
  document.getElementById('prev').onclick = () => { if (cur > 0) { cur--; render(); } };
  goEl.onclick = () => {
    if (answers[cur].length === 0) return;
    if (!last) { root.querySelector('.quiz').style.opacity = '0.4'; setTimeout(() => { cur++; render(); }, 350); return; }
    if (Q.variant === 'native') return confirmModal();
    submit();
  };
}
function confirmModal() {
  const m = document.createElement('div');
  m.setAttribute('role', 'dialog'); m.setAttribute('aria-modal', 'true'); m.className = 'modal';
  m.innerHTML = '<div class="modal-box"><p>Are you sure you want to submit?</p><button class="btn" id="cancel">Cancel</button><button class="btn" id="yes">Yes, Submit</button></div>';
  document.body.appendChild(m);
  m.querySelector('#cancel').onclick = () => m.remove();
  m.querySelector('#yes').onclick = () => { m.remove(); submit(); };
}
function submit() {
  root.innerHTML = '<p>Submitting...</p>';
  fetch('/api/submit', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({lb: Q.lb, lu: Q.lu, answers})}).then(r => r.json()).then(res => { done = res; setTimeout(render, 500); });
}
function renderResult() {
  const r = done;
  if (r.already) {
    root.innerHTML = `<div class="result"><p>You have already submitted this quiz.</p><p>Your score: ${r.score}/${r.total}</p><button class="btn" id="retake">Retake</button></div>`;
  } else {
    root.innerHTML = `<div class="result"><h3>Quiz submitted</h3><p class="score">You scored ${r.score}/${r.total}</p><p>${r.passed ? 'Congratulations, you passed!' : 'You failed this quiz. Better luck next time.'}</p>${r.passed ? '' : '<button class="btn" id="retake">Retake Quiz</button>'}</div>`;
  }
  const rt = document.getElementById('retake');
  if (rt) rt.onclick = () => { done = null; cur = 0; answers = Q.questions.map(() => []); started = !Q.start; render(); };
}
setTimeout(render, 500);
})();
"""

# Wires up assignment forms: a CodeMirror-5-like editor API, Run Tests, an
# optional confirm modal, "required" validation, and the submitted state.
TASK_ENGINE = r"""
(function(){
const T = window.TASK;
document.querySelectorAll('[data-cm]').forEach(el => {
  const view = el.querySelector('.CodeMirror-code'); let val = el.dataset.value || '';
  const render = () => { view.textContent = val; };
  el.CodeMirror = { getValue: () => val, setValue: (v) => { val = v; render(); },
                    getOption: (k) => k === 'mode' ? 'python' : k === 'readOnly' ? el.getAttribute('aria-readonly') === 'true' : null };
  render();
});
const form = document.querySelector('[data-task-form]'); if (!form) return;
const status = form.querySelector('.status'); const out = form.querySelector('.output');
const valueOf = (el) => el.CodeMirror ? el.CodeMirror.getValue() : (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') ? el.value : el.innerText;
const values = () => { const v = {}; form.querySelectorAll('[data-task-field]').forEach(el => v[el.dataset.taskField] = valueOf(el)); return v; };
const run = form.querySelector('[data-task-run]');
if (run) run.onclick = () => {
  out.textContent = 'Running...';
  fetch('/api/run', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({lb: T.lb, lu: T.lu, code: values().code})}).then(r => r.json()).then(r => { setTimeout(() => { out.textContent = r.output; }, 300); });
};
function send() {
  status.textContent = 'Submitting...';
  fetch('/api/task', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({lb: T.lb, lu: T.lu, fields: values()})}).then(r => r.json()).then(() => {
      form.querySelectorAll('[data-task-field]').forEach(el => { el.disabled = true; el.readOnly = true;
        if (el.isContentEditable) el.setAttribute('contenteditable', 'false'); el.setAttribute('aria-readonly', 'true'); });
      form.querySelector('[data-task-submit]').remove();
      setTimeout(() => { status.textContent = 'Submission received. Your mentor will review it.'; }, 300);
  });
}
form.querySelector('[data-task-submit]').onclick = () => {
  const empty = Object.entries(values()).filter(([k, v]) => !String(v).trim());
  if (empty.length) { status.textContent = 'This field is required: ' + empty[0][0]; return; }
  if (!T.confirm) return send();
  const m = document.createElement('div');
  m.setAttribute('role', 'dialog'); m.setAttribute('aria-modal', 'true'); m.className = 'modal';
  m.innerHTML = '<div class="modal-box"><p>Submit your assignment? You cannot edit it afterwards.</p><button class="btn" id="no">Cancel</button><button class="btn" id="ok">Confirm</button></div>';
  document.body.appendChild(m);
  m.querySelector('#no').onclick = () => m.remove();
  m.querySelector('#ok').onclick = () => { m.remove(); send(); };
};
const start = document.querySelector('[data-task-start]');
if (start) start.onclick = () => { start.parentElement.remove(); form.classList.remove('hidden'); };
})();
"""

COMMENTS = ('<section class="comments"><h4>Discussion</h4><p>Ask doubts or share thoughts with your batch.</p>'
            '<textarea rows="3" cols="60" placeholder="Add a comment..."></textarea><br>'
            '<button class="btn">Post</button></section>')


def judge(code: str) -> tuple[int, str]:
    tests = [("fizzbuzz(3)[-1] == 'Fizz'", "Fizz" in code), ("fizzbuzz(5)[-1] == 'Buzz'", "Buzz" in code),
             ("fizzbuzz(15)[-1] == 'FizzBuzz'", "% 15" in code or "FizzBuzz" in code)]
    lines = [f"Test {i}: {'passed' if ok else 'failed - ' + name}" for i, (name, ok) in enumerate(tests, 1)]
    passed = sum(ok for _, ok in tests)
    return passed, "\n".join(lines + [f"{passed}/{len(tests)} test cases passed"])


class Portal:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            # (lb, lu) -> list of submissions [{answers, score}]
            self.submissions: dict = {}
            self.submissions[("2505", "11")] = [{"answers": None, "score": 5}]
            self.submissions[("2506", "12")] = [{"answers": None, "score": 4}]
            self.submissions[("2506", "13")] = [{"answers": None, "score": 5}]
            self.tasks: dict = {}    # (lb, lu) -> submitted fields
            self.runs: list = []     # code sent to Run Tests

    def lu_done(self, lb, lu) -> bool:
        info = self.lu_info(lb, lu)
        if lb == "2506" and lu == "12":
            return False   # quiz submitted but the LU still has other work
        if info["kind"] == "task":
            return (lb, lu) in self.tasks
        return info["kind"] in ("native", "aria", "div") and bool(self.submissions.get((lb, lu)))

    def lu_info(self, lb, lu):
        for _, _, rows in LUS[lb]["modules"]:
            for luid, num, title, info in rows:
                if luid == lu:
                    return {"num": num, "title": title, **info}
        raise KeyError(lu)

    def submit(self, lb, lu, answers):
        qs = QUIZZES[(lb, lu)]
        score = sum(1 for q, a in zip(qs, answers, strict=False) if sorted(a) == sorted(q["answer"]))
        with self.lock:
            self.submissions.setdefault((lb, lu), []).append({"answers": answers, "score": score})
        return {"score": score, "total": len(qs), "passed": score / len(qs) >= 0.6}

    def state(self) -> dict:
        s = {f"{k[0]}/{k[1]}": v for k, v in self.submissions.items()}
        s["tasks"] = {f"{k[0]}/{k[1]}": v for k, v in self.tasks.items()}
        s["runs"] = list(self.runs)
        return s


def page(title, body, head=""):
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{CSS}</style>{head}</head><body>{body}</body></html>"""


def header():
    return ('<header><nav><a href="/livebooks?semester=5">Livebooks</a><a href="/quizzes">Quiz</a>'
            '<a href="/profile">Profile</a><input type="text" placeholder="Search"></nav></header>')


def task_field(f: dict, locked: dict | None) -> str:
    e = html.escape
    val = (locked or {}).get(f["name"], f["value"])
    dis = " disabled" if locked else ""
    q = f'<p>{e(f["question"])}</p>' if f["question"] else ""
    attrs = f'data-task-field="{f["name"]}"'
    t = f["type"]
    if t == "textarea":
        return f'<div class="q">{q}<textarea {attrs} rows="6" cols="70" placeholder="{e(f["placeholder"])}"{dis}>{e(val)}</textarea></div>'
    if t in ("rich", "plain_rich"):
        cls = "ProseMirror rich" if t == "rich" else "rich"
        ce = "false" if locked else "true"
        return (f'<div class="q">{q}<div class="{cls}" contenteditable="{ce}" role="textbox" aria-multiline="true" '
                f'aria-label="{e(f["label"])}" {attrs}>{e(val)}</div></div>')
    if t == "cm5":
        ro = ' aria-readonly="true"' if locked else ""
        return (f'<div class="q">{q}<label>Language <select><option selected>Python 3</option><option>JavaScript</option>'
                f'</select></label><div class="CodeMirror" data-cm data-value="{e(val)}" {attrs}{ro}>'
                '<div class="CodeMirror-code"></div></div></div>')
    if t == "code_textarea":
        return f'<div class="q">{q}<textarea class="code-input" name="code" rows="6" cols="60" {attrs}{dis}>{e(val)}</textarea></div>'
    typ = "url" if t == "url" else "text"
    fid = f'f_{f["name"]}'
    return (f'<div class="q">{q}<label for="{fid}">{e(f["label"])}</label><br><input id="{fid}" type="{typ}" '
            f'size="60" placeholder="{e(f["placeholder"])}" value="{e(val)}" {attrs}{dis}></div>')


class Handler(BaseHTTPRequestHandler):
    portal: Portal = None   # set by serve()

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return "kqb_session" in c and c["kqb_session"].value == "1"

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        if u.path == "/api/reset":
            self.portal.reset()
            return self._send(200, "{}", "application/json")
        if not self._authed():
            return self._send(401, "{}", "application/json")
        if u.path == "/api/submit":
            res = self.portal.submit(body["lb"], body["lu"], body["answers"])
            return self._send(200, json.dumps(res), "application/json")
        if u.path == "/api/task":
            with self.portal.lock:
                self.portal.tasks[(body["lb"], body["lu"])] = body["fields"]
            return self._send(200, '{"ok": true}', "application/json")
        if u.path == "/api/run":
            passed, output = judge(body.get("code") or "")
            self.portal.runs.append({"lu": f'{body["lb"]}/{body["lu"]}', "passed": passed})
            return self._send(200, json.dumps({"output": output}), "application/json")
        self._send(404, "not found")

    def do_GET(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        if u.path == "/login":
            return self._send(200, page("Login", """
<main><h2>Sign in to Kalvium (mock)</h2><button id="g">Continue with Google</button>
<script>
function go(){document.cookie='kqb_session=1; path=/; max-age=86400'; location.href='/livebooks?semester=5';}
document.getElementById('g').onclick=go; setTimeout(go, 2500);  // the "user" logs in after 2.5 s
</script></main>"""))
        if u.path == "/api/state":
            return self._send(200, json.dumps(self.portal.state()), "application/json")
        if not self._authed():
            return self._send(302, "", extra={"Location": "/login"})
        if parts == ["livebooks"]:
            sem = parse_qs(u.query).get("semester", ["5"])[0]
            return self._send(200, self.livebooks(sem))
        if len(parts) == 2 and parts[0] == "livebooks":
            return self._send(200, self.livebook(parts[1]))
        if len(parts) == 4 and parts[0] == "livebooks" and parts[2] == "lu":
            return self._send(200, self.lu_page(parts[1], parts[3]))
        if len(parts) == 3 and parts[0] == "quizframe":
            return self._send(200, self.quiz_doc(parts[1], parts[2], standalone=True))
        self._send(404, page("Not found", "<main>Not found</main>"))

    # ---------------------------------------------------------------- pages

    def livebooks(self, sem):
        cards = ""
        if sem == "5":
            for lb, d in LUS.items():
                cards += (f'<div class="card" data-testid="livebook-card"><a href="/livebooks/{lb}"><h3>{d["name"]}</h3></a>'
                          f'<p>40% complete</p><a class="btn" href="/livebooks/{lb}">Continue</a></div>')
        body = f"""{header()}<main><h2>Livebooks</h2><select><option>Semester 5</option></select>
<div id="grid"></div><script>setTimeout(()=>{{document.getElementById('grid').innerHTML={json.dumps(cards)};}},400);</script></main>"""
        return page("Livebooks", body)

    def livebook(self, lb):
        d = LUS[lb]
        mods = ""
        for title, expanded, rows in d["modules"]:
            items = ""
            for luid, num, t, _info in rows:
                done = self.portal.lu_done(lb, luid)
                if d["rows"] == "div":
                    icon = ('<svg class="lucide lucide-check-circle" aria-label="Completed" width="12" height="12"></svg>'
                            if done else '<svg class="lucide lucide-circle" aria-label="Not completed" width="12" height="12"></svg>')
                    items += (f'<div class="lu-item" data-testid="lu-item" role="button" tabindex="0" '
                              f'onclick="location.href=\'/livebooks/{lb}/lu/{luid}\'">'
                              f'<span class="lu-num">{num}</span> <span class="lu-title">{t}</span> {icon}</div>')
                else:
                    pct = "100%" if done else ("40%" if luid == "12" else "0%")
                    items += (f'<a class="lu-item" href="/livebooks/{lb}/lu/{luid}"><span>{num} {t}</span>'
                              f'<span class="pct">{pct}</span></a><br>')
            if title:
                hid = "" if expanded else " hidden"
                mods += (f'<div class="module"><button class="module-toggle" aria-expanded="{str(expanded).lower()}" '
                         f'onclick="const b=this;b.setAttribute(\'aria-expanded\',b.getAttribute(\'aria-expanded\')===\'true\'?\'false\':\'true\');'
                         f'b.nextElementSibling.classList.toggle(\'hidden\')">{title}</button>'
                         f'<div class="lus{hid}">{items}</div></div>')
            else:
                mods += f'<div class="module"><div class="lus">{items}</div></div>'
        body = f"""{header()}<main><h2>{d['name']}</h2>
<div role="tablist"><button role="tab" aria-selected="true" id="t1">Overview</button><button role="tab" id="t2">Learning Path</button></div>
<div id="panel"><p>Overview of the course. 1.5 hours per week.</p></div>
<script>
document.getElementById('t2').onclick=()=>{{document.getElementById('panel').innerHTML='<p>Loading...</p>';
 setTimeout(()=>{{document.getElementById('panel').innerHTML={json.dumps(mods)};}},300);}};
</script></main>"""
        return page(d["name"], body)

    def quiz_doc(self, lb, lu, standalone=False):
        info = self.portal.lu_info(lb, lu)
        qs = [{k: q[k] for k in ("text", "options", "code", "multi")} for q in QUIZZES[(lb, lu)]]
        subs = self.portal.submissions.get((lb, lu))
        already = {"already": True, "score": subs[-1]["score"], "total": len(qs)} if subs else None
        data = {"lb": lb, "lu": lu, "variant": info["kind"], "start": info.get("start", False),
                "questions": qs, "already": already}
        block = (f'<div id="quiz-root"></div><script>window.QUIZ={json.dumps(data)};</script>'
                 f'<script>{QUIZ_ENGINE}</script>')
        return page("Quiz", f"<main>{block}</main>") if standalone else block

    def task_doc(self, lb, lu):
        spec = TASKS[(lb, lu)]
        locked = self.portal.tasks.get((lb, lu))
        fields = "".join(task_field(f, locked) for f in spec["fields"])
        if locked:
            actions = '<p class="status">Submitted on 06 Oct 2026. View your submission above.</p>'
        else:
            run = '<button class="btn" data-task-run>Run Tests</button>' if spec.get("run") else ""
            actions = (f'<div class="actions">{run}<button class="btn" data-task-submit>Submit Assignment</button></div>'
                       '<pre class="output"></pre><p class="status"></p>')
        hidden = " hidden" if spec.get("start") and not locked else ""
        start = ('<div class="intro"><p>This assignment is timed once you start.</p>'
                 '<button class="btn" data-task-start>Start Assignment</button></div>') if hidden else ""
        data = {"lb": lb, "lu": lu, "confirm": bool(spec.get("confirm"))}
        return (f'{start}<div class="assignment{hidden}" data-task-form><h3>Assignment</h3>{fields}{actions}</div>'
                f'<script>window.TASK={json.dumps(data)};</script><script>{TASK_ENGINE}</script>')

    def lu_page(self, lb, lu):
        info = self.portal.lu_info(lb, lu)
        side = "".join(
            f'<div>{num} {t} - {"Quiz completed" if self.portal.lu_done(lb, luid) else "Open"}</div>'
            for _, _, rows in LUS[lb]["modules"] for luid, num, t, _ in rows)
        reading = ("<h2>{}</h2><p>Reading material. In one survey 3/5 philosophers agreed. "
                   "Arguments pass by reference to earlier ideas.</p>").format(html.escape(info["title"]))
        kind = info["kind"]
        if kind in ("native", "aria", "div"):
            if info.get("iframe"):
                task = f'<iframe src="/quizframe/{lb}/{lu}" style="width:100%;height:520px;border:0"></iframe>'
            else:
                task = self.quiz_doc(lb, lu)
            if kind == "aria":
                task += ('<div class="feedback"><p>How would you rate this LU?</p><div role="radiogroup" aria-label="Rating">'
                         + "".join(f'<span role="radio" aria-checked="false" aria-label="{i} star" tabindex="0">&#9733;</span>'
                                   for i in range(1, 6)) + '</div><button class="btn">Send</button></div>')
            if info.get("tab"):
                reading = (f'<div role="tablist"><button role="tab" id="tc">Content</button><button role="tab" id="tq">Quiz</button></div>'
                           f'<div id="content">{reading}</div><div id="quizpane" class="hidden">{task}</div>'
                           '<script>document.getElementById("tq").onclick=()=>{document.getElementById("quizpane").classList.remove("hidden");'
                           'document.getElementById("content").classList.add("hidden");};</script>')
                task = ""
        elif kind == "task":
            task = self.task_doc(lb, lu)
        else:
            task = '<button class="btn">Mark as complete</button>'
        body = f'{header()}<div class="wrap"><aside>{side}</aside><main>{reading}<div id="task">{task}</div>{COMMENTS}</main></div>'
        return page(info["title"], body)


def serve(port=0):
    portal = Portal()
    handler = type("H", (Handler,), {"portal": portal})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, portal


if __name__ == "__main__":
    srv, _ = serve(8765)
    print("Mock portal on http://127.0.0.1:8765/livebooks?semester=5  (Ctrl+C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
