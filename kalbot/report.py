"""A self-contained HTML report for one run (no external assets, works offline)."""

from __future__ import annotations

import html
from pathlib import Path

from .ui import COUNT_LABELS, STATUS

CSS = """
:root{--bg:#f7f7f8;--card:#fff;--fg:#1d1d1f;--muted:#6b6b76;--line:#e4e4e8;--ok:#1a7f37;--warn:#9a6700;
--err:#cf222e;--accent:#0969da;--code:#f2f2f5}
@media (prefers-color-scheme:dark){:root{--bg:#111214;--card:#1a1b1e;--fg:#e8e8ea;--muted:#9a9aa4;--line:#2b2c31;
--ok:#3fb950;--warn:#d29922;--err:#f85149;--accent:#58a6ff;--code:#22242a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}
.meta{color:var(--muted);font-size:13px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.card b{display:block;font-size:22px}.card span{color:var(--muted);font-size:13px}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;
padding:4px 12px;cursor:pointer;font:inherit;font-size:13px}
.filters button.on{border-color:var(--accent);color:var(--accent)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:8px}
summary{cursor:pointer;padding:10px 14px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
summary .lu{font-weight:600}summary .d{color:var(--muted);font-size:13px;flex-basis:100%}
.badge{font-size:12px;border-radius:6px;padding:1px 8px;border:1px solid currentColor;white-space:nowrap}
.submitted{color:var(--ok)}.manual{color:var(--warn)}.error{color:var(--err)}.preview{color:var(--accent)}
.none,.skipped,.done{color:var(--muted)}.body{padding:0 14px 12px;border-top:1px solid var(--line)}
.q{margin-top:12px}.opt{margin-left:14px}.opt.c{color:var(--ok);font-weight:600}
pre{background:var(--code);padding:10px;border-radius:8px;overflow:auto;font-size:13px;white-space:pre-wrap}
.k{color:var(--muted);font-size:13px}a{color:var(--accent)}
"""

JS = """
document.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('.filters button').forEach(x=>x.classList.toggle('on',x===b));
 const s=b.dataset.s;document.querySelectorAll('details').forEach(d=>d.hidden=s!=='all'&&d.dataset.s!==s);});
"""


def e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _items(r) -> str:
    out = []
    for it in r.items:
        if "options" in it:   # quiz question
            code = "".join(f"<pre>{e(c)}</pre>" for c in it.get("code") or [])
            opts = "".join(f'<div class="opt{" c" if i in it["chosen"] else ""}">{"●" if i in it["chosen"] else "○"} '
                           f"{e(o)}</div>" for i, o in enumerate(it["options"]))
            att = f" · attempt {it['attempt']}" if it.get("attempt", 1) > 1 else ""
            out.append(f'<div class="q"><b>Q{e(it.get("q"))}</b> {e(it["question"])}{code}{opts}'
                       f'<div class="k">confidence: {e(it["confidence"])}{att}</div></div>')
        else:                 # assignment field
            meta = " · ".join(x for x in (it.get("kind"), it.get("language"), it.get("source"),
                                          f"{it['words']} words" if it.get("words") else "") if x)
            val = it.get("value") or ""
            if it.get("kind") == "link":
                body = f'<p><a href="{e(val)}" rel="noreferrer">{e(val)}</a></p>'
                if it.get("files"):
                    body += f'<div class="k">files: {e(", ".join(it["files"]))}</div>'
            else:
                body = f"<pre>{e(val)}</pre>"
            out.append(f'<div class="q"><b>{e(it.get("label"))}</b> <span class="k">{e(meta)}</span>{body}</div>')
    if r.url:
        out.append(f'<p class="k"><a href="{e(r.url)}" rel="noreferrer">open on the portal</a></p>')
    return "".join(out)


def write_report(path: Path, runlog, meta: list[tuple[str, str]]) -> Path:
    res = runlog.results
    counts = {k: sum(1 for r in res if r.status == k) for k, _ in COUNT_LABELS}
    cards = "".join(f'<div class="card {k}"><b>{counts[k]}</b><span>{e(label)}</span></div>'
                    for k, label in COUNT_LABELS if counts[k] or k in ("submitted", "manual", "error"))
    filters = '<button class="on" data-s="all">All</button>' + "".join(
        f'<button data-s="{k}">{e(label)} ({counts[k]})</button>' for k, label in COUNT_LABELS if counts[k])
    rows = []
    for r in res:
        label = STATUS.get(r.status, (r.status, ""))[0]
        body = _items(r)
        rows.append(
            f'<details data-s="{e(r.status)}"><summary><span class="badge {e(r.status)}">{e(label)}</span>'
            f'<span class="lu">{e(r.livebook)} · LU {e(r.lu)} {e(r.title)}</span>'
            f'<span class="k">{e(r.kind)}</span><span class="d">{e(r.detail)}</span></summary>'
            + (f'<div class="body">{body}</div>' if body else "") + "</details>")
    info = " · ".join(f"{e(k)}: {e(v)}" for k, v in meta)
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>kalbot run report</title>'
           f"<style>{CSS}</style></head><body><main><h1>kalbot run report</h1><div class=\"meta\">{info}</div>"
           f'<div class="cards">{cards}</div><div class="filters">{filters}</div>'
           f'{"".join(rows) or "<p>Nothing ran.</p>"}'
           f"</main><script>{JS}</script></body></html>")
    path.write_text(doc, encoding="utf-8")
    return path
