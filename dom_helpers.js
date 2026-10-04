// DOM helpers injected into portal pages (all frames).
// They only read the page. The one thing they write is data-kqb-* marker
// attributes, so Python can click exactly the element that was analysed and
// never has to guess.
(() => {
  const VERSION = 1;
  if (window.__kqb && window.__kqb.version === VERSION) return;

  const K = { version: VERSION, items: [], root: null };

  // ------------------------------------------------------------------ basics
  const norm = (s) => String(s ?? '')
    .replace(/ /g, ' ')
    .replace(/[ \t\r\f\v]+/g, ' ')
    .replace(/ ?\n ?/g, '\n')
    .replace(/\n{2,}/g, '\n')
    .trim();
  // "Next →" / "  NEXT " / "› Next" all become "next"
  const btnKey = (s) => norm(s).toLowerCase()
    .replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '')
    .replace(/\s+/g, ' ');
  const res = (list) => (list || []).map((p) => new RegExp(p, 'i'));
  const any = (regexes, text) => regexes.some((re) => re.test(text || ''));
  const cls = (el) => (el && el.getAttribute && el.getAttribute('class')) || '';

  function visible(el) {
    if (!el || el.nodeType !== 1 || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && parseFloat(st.opacity || '1') > 0.05;
  }

  function textOf(el) {
    if (!el) return '';
    if (el.tagName === 'INPUT') return norm(el.value || el.getAttribute('aria-label') || '');
    let t = norm(el.innerText || '');
    if (!t) t = norm(el.getAttribute('aria-label') || el.getAttribute('title') || '');
    if (!t && el.querySelector) {
      const img = el.querySelector('img[alt]');
      if (img && img.alt) t = `[image: ${norm(img.alt)}]`;
    }
    return t;
  }

  const chain = (el) => { const out = []; for (let n = el; n; n = n.parentElement) out.push(n); return out; };
  function lca(els) {
    els = els.filter(Boolean);
    if (!els.length) return null;
    let common = chain(els[0]);
    for (const e of els.slice(1)) { const s = new Set(chain(e)); common = common.filter((a) => s.has(a)); }
    return common[0] || null;
  }
  const depth = (el) => chain(el).length;
  // levels above `a` at which `a` and `b` meet
  const distUp = (a, b) => { const c = lca([a, b]); return c ? depth(a) - depth(c) : Infinity; };
  const isDisabled = (el) => !!el && (
    el.disabled === true ||
    el.getAttribute('aria-disabled') === 'true' ||
    /(^|[\s_-])disabled($|[\s_-])/i.test(cls(el)) ||
    !!el.closest('fieldset[disabled]'));

  function clearMarks(attr, value) {
    const sel = value === undefined ? `[${attr}]` : `[${attr}="${value}"]`;
    for (const el of document.querySelectorAll(sel)) el.removeAttribute(attr);
  }
  const mark = (el, attr, val) => el.setAttribute(attr, String(val));

  const CLICKABLE = 'button, [role="button"], a, input[type="submit"], input[type="button"], [role="tab"], [role="link"], [role="menuitem"]';
  // Visible clickable elements whose text equals one of `texts` (case-insensitive).
  function buttons(texts, root) {
    const want = new Set((texts || []).map(btnKey));
    const out = [];
    for (const el of (root || document).querySelectorAll(CLICKABLE)) {
      if (visible(el) && want.has(btnKey(textOf(el)))) out.push(el);
    }
    return out.filter((el) => !out.some((o) => o !== el && el.contains(o)));
  }

  // ------------------------------------------------------------------ options
  const OPT_ROLES = '[role="radio"], [role="checkbox"], [role="option"], [role="menuitemradio"], [role="menuitemcheckbox"]';
  const INPUTS = 'input[type="radio"], input[type="checkbox"]';
  let uidSeq = 0;
  const uids = new WeakMap();
  const uid = (el) => { if (!el) return 'none'; if (!uids.has(el)) uids.set(el, ++uidSeq); return uids.get(el); };

  function inputTarget(inp) {
    const lab = (inp.labels && inp.labels[0]) || inp.closest('label');
    if (lab && visible(lab)) return lab;
    let p = inp.parentElement;
    for (let i = 0; p && i < 3; i++, p = p.parentElement) if (visible(p) && textOf(p)) return p;
    return visible(inp) ? inp : null;
  }

  function semanticGroups() {
    const groups = new Map();
    const covered = [];
    for (const inp of document.querySelectorAll(INPUTS)) {
      if (inp.disabled) continue;               // review mode / locked quiz
      const target = inputTarget(inp);
      if (!target) continue;
      const box = inp.closest('form, fieldset, [role="radiogroup"], [role="group"], ul, ol') ||
        (target.parentElement && target.parentElement.parentElement);
      const key = `in:${inp.type}:${inp.name ? 'n=' + inp.name : 'b=' + uid(box)}`;
      if (!groups.has(key)) groups.set(key, { source: 'native', items: [] });
      groups.get(key).items.push({ el: target, input: inp, aria: null });
      covered.push(target, inp);
    }
    for (const el of document.querySelectorAll(OPT_ROLES)) {
      if (!visible(el) || el.getAttribute('aria-disabled') === 'true') continue;
      if (covered.some((c) => c.contains(el) || el.contains(c))) continue;
      const box = el.closest('[role="radiogroup"], [role="group"], [role="listbox"], [role="menu"]') ||
        (el.parentElement && el.parentElement.parentElement);
      const key = `aria:${uid(box)}`;
      if (!groups.has(key)) groups.set(key, { source: 'aria', items: [] });
      groups.get(key).items.push({ el, input: null, aria: el });
    }
    return [...groups.values()].filter((g) => {
      const n = g.items.length;
      return n >= 2 && n <= 10 && new Set(g.items.map((i) => i.el)).size === n;
    });
  }

  const ownClickable = (el) =>
    el.hasAttribute('onclick') || el.hasAttribute('tabindex') ||
    /^(button|option|radio|checkbox)$/.test(el.getAttribute('role') || '') ||
    getComputedStyle(el).cursor === 'pointer';

  // Plain clickable <div> options with no input/ARIA semantics. Only accepted
  // when they sit next to a Next/Submit button, so a random list never counts.
  function heuristicGroups(cfg) {
    const navs = buttons([...cfg.next, ...cfg.submit]);
    const navKeys = new Set([...cfg.next, ...cfg.submit, ...cfg.start, ...cfg.prev].map(btnKey));
    for (const nav of navs) {
      let cont = nav.parentElement;
      for (let lvl = 0; cont && lvl < 6; lvl++, cont = cont.parentElement) {
        const parents = [cont, ...cont.querySelectorAll('*')].slice(0, 4000);
        for (const parent of parents) {
          if (parent.closest('nav, header, footer, [role="navigation"], [role="tablist"], [role="menu"], [role="menubar"]')) continue;
          if (getComputedStyle(parent).cursor === 'pointer') continue;   // inside an option
          const kids = [...parent.children].filter((k) => visible(k) && textOf(k));
          if (kids.length < 2 || kids.length > 8) continue;
          if (!kids.every((k) => k.tagName === kids[0].tagName)) continue;
          if (kids.some((k) => k.contains(nav) || k.querySelector('input, textarea, select'))) continue;
          const texts = kids.map(textOf);
          if (texts.some((t) => navKeys.has(btnKey(t)) || t.length > 600)) continue;
          if (texts.every((t) => /^\d{1,2}$/.test(t))) continue;   // question-number pills
          if (!kids.every(ownClickable)) continue;
          return [{ source: 'heuristic', items: kids.map((el) => ({ el, input: null, aria: null })) }];
        }
      }
    }
    return [];
  }

  // ------------------------------------------------------------------ question text
  const PROGRESS = /(?:question|ques|q)\s*\.?\s*(\d{1,2})\s*(?:of|\/)\s*(\d{1,2})|^\s*(\d{1,2})\s*\/\s*(\d{1,2})\s*$/im;
  const CODE_SEL = 'pre, .cm-editor, .CodeMirror, .monaco-editor, .hljs, code';

  function partsToQuestion(nodes, els) {
    const texts = [];
    const code = [];
    for (const n of nodes) {
      if (n.nodeType === 3) { texts.push(norm(n.textContent)); continue; }
      let t = textOf(n);
      const blocks = [...(n.matches(CODE_SEL) ? [n] : []), ...n.querySelectorAll(CODE_SEL)]
        .filter((b) => !els.some((e) => e.contains(b)));
      const taken = [];
      for (const b of blocks) {
        if (taken.some((x) => x.contains(b))) continue;
        const raw = (b.innerText || '').replace(/ /g, ' ');
        // inline `code` stays part of the sentence
        if (b.tagName === 'CODE' && !b.closest('pre') && !raw.includes('\n')) continue;
        if (!raw.trim()) continue;
        taken.push(b);
        code.push(raw.replace(/\s+$/, ''));
        t = t.replace(norm(raw), ' ');
      }
      if (n.tagName === 'IMG' && n.alt) t = `[image: ${norm(n.alt)}]`;
      texts.push(t);
    }
    let text = norm(texts.join('\n'));
    let progress = null;
    const m = text.match(PROGRESS);
    if (m) {
      progress = [+(m[1] || m[3]), +(m[2] || m[4])];
      text = norm(text.replace(m[0], ' ').replace(/^[\s:.\-–|]+/, ''));
    }
    return { text, code, progress };
  }

  function questionFor(els, cfg) {
    const first = els[0];
    if (cfg.questionSelector) {
      try {
        const cands = [...document.querySelectorAll(cfg.questionSelector)].filter((q) => visible(q) &&
          (q.contains(first) || (q.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING)));
        const q = cands[cands.length - 1];
        if (q) {
          const r = partsToQuestion([q], els);
          for (const e of els) r.text = norm(r.text.replace(textOf(e), ' '));
          return r;
        }
      } catch (e) { /* bad selector: fall back to heuristics */ }
    }
    const navKeys = new Set([...cfg.next, ...cfg.submit, ...cfg.prev, ...cfg.start].map(btnKey));
    const root = lca(els);
    let cur = first;
    while (cur.parentElement && cur.parentElement !== root) cur = cur.parentElement;
    const picked = [];
    let total = 0;
    let stop = false;
    for (let lvl = 0; cur && cur !== document.body && lvl < 8 && !stop; lvl++) {
      let found = false;
      for (let sib = cur.previousSibling; sib && !stop; sib = sib.previousSibling) {
        if (sib.nodeType === 1) {
          if (els.some((e) => sib.contains(e)) || sib.matches('script, style, noscript, template')) continue;
          if (!visible(sib)) continue;
        } else if (sib.nodeType !== 3) continue;
        const t = sib.nodeType === 3 ? norm(sib.textContent) : textOf(sib);
        if (!t || navKeys.has(btnKey(t))) continue;
        picked.unshift(sib);
        found = true;
        total += t.length;
        if (PROGRESS.test(t) || total > 1500) stop = true;
      }
      if (found) break;
      cur = cur.parentElement;
    }
    const out = partsToQuestion(picked, els);
    if (!out.progress) {
      // look a little wider (quiz header) for "Question 2 of 5"
      let box = root;
      for (let i = 0; box && i < 3; i++) box = box.parentElement;
      const m = box ? norm(box.innerText || '').slice(0, 3000).match(PROGRESS) : null;
      if (m) out.progress = [+(m[1] || m[3]), +(m[2] || m[4])];
    }
    return out;
  }

  // ------------------------------------------------------------------ picking the live question
  function candidateGroups(cfg) {
    if (cfg.optionSelector) {
      try {
        const els = [...document.querySelectorAll(cfg.optionSelector)].filter(visible);
        if (els.length >= 2) {
          return [{
            source: 'config',
            items: els.map((el) => ({
              el,
              input: el.matches(INPUTS) ? el : el.querySelector(INPUTS),
              aria: el.matches(OPT_ROLES) ? el : el.querySelector(OPT_ROLES),
            })),
          }];
        }
      } catch (e) { /* bad selector: fall back to heuristics */ }
    }
    const sem = semanticGroups();
    return sem.length ? sem : heuristicGroups(cfg);
  }

  function pickGroup(cfg) {
    const navs = buttons([...cfg.next, ...cfg.submit]);
    const exclude = res(cfg.exclude);
    let best = null;
    for (const g of candidateGroups(cfg)) {
      const els = g.items.map((i) => i.el);
      const texts = els.map(textOf);
      if (texts.some((t) => !t)) continue;
      const root = lca(els);
      const q = questionFor(els, cfg);
      if (any(exclude, q.text)) continue;                      // feedback / rating widgets
      let nav = Infinity;
      for (const n of navs) nav = Math.min(nav, distUp(root, n));
      if (nav > 7 && texts.every((t) => t.length <= 2)) continue;  // star ratings, emoji scales
      const score = (g.source === 'config' ? 100 : 0) + (nav <= 7 ? 20 - nav : 0) + (q.text ? 5 : 0);
      if (!best || score > best.score) best = { g, els, texts, root, q, score };
    }
    return best;
  }

  const SELECTED_CLASS = /(^|[\s_-])(selected|active|checked|chosen|picked)($|[\s_-])/i;
  function signature(el) {
    return [el, ...el.querySelectorAll('*')].slice(0, 25).map((n) => {
      const s = getComputedStyle(n);
      return `${cls(n)}~${s.backgroundColor}~${s.borderTopColor}~${s.color}~${s.outlineStyle}~${s.boxShadow}~${s.fontWeight}`;
    }).join('|');
  }
  function selInfo(it) {
    if (!it || !it.el.isConnected) return { stale: true };
    if (it.input) return { known: true, sel: !!it.input.checked };
    const a = it.aria || (it.el.matches(OPT_ROLES) ? it.el : it.el.querySelector(OPT_ROLES)) || it.el;
    for (const at of ['aria-checked', 'aria-selected', 'aria-pressed']) {
      if (a.hasAttribute(at)) return { known: true, sel: a.getAttribute(at) === 'true' };
    }
    const inp = it.el.querySelector(INPUTS);
    if (inp) return { known: true, sel: inp.checked };
    const ds = it.el.getAttribute('data-state') ?? it.el.getAttribute('data-selected') ?? it.el.getAttribute('data-checked');
    if (ds !== null) return { known: true, sel: /^(checked|on|active|selected|true)$/i.test(ds) };
    return { known: false, sig: signature(it.el), cls: SELECTED_CLASS.test(cls(it.el)) };
  }

  function nearest(texts, root, maxUp) {
    let best = null;
    let bestD = Infinity;
    for (const b of buttons(texts)) {
      const d = root ? distUp(root, b) : 0;
      if (d < bestD) { best = b; bestD = d; }
    }
    return bestD <= maxUp ? best : null;
  }

  function navInfo(cfg) {
    clearMarks('data-kqb-btn', 'next');
    clearMarks('data-kqb-btn', 'submit');
    const next = nearest(cfg.next, K.root, 7);
    const submit = nearest(cfg.submit, K.root, 7);
    if (next) mark(next, 'data-kqb-btn', 'next');
    if (submit) mark(submit, 'data-kqb-btn', 'submit');
    return {
      next: !!next, nextDisabled: !!next && isDisabled(next),
      submitBtn: !!submit, submitDisabled: !!submit && isDisabled(submit),
    };
  }

  // Full analysis of the current quiz state. Marks options with data-kqb-opt
  // and buttons with data-kqb-btn.
  // Page text without navigation/sidebars, so another LU's "Quiz completed"
  // badge in a sidebar is not mistaken for this LU's state.
  function mainText() {
    if (!document.body) return '';
    let t = norm(document.body.innerText || '');
    for (const el of document.querySelectorAll('nav, aside, header, footer, [role="navigation"], [role="complementary"], [role="banner"]')) {
      const s = norm(el.innerText || '');
      if (s) t = t.replace(s, ' ');
    }
    return t;
  }

  K.state = (cfg) => {
    clearMarks('data-kqb-opt');
    clearMarks('data-kqb-btn');
    const out = { kind: 'none', url: location.href, completed: any(res(cfg.completed), mainText()) };
    const start = buttons(cfg.start).filter((b) => !isDisabled(b));
    const retake = buttons(cfg.retake).filter((b) => !isDisabled(b));
    out.start = start.length > 0;
    out.retake = retake.length > 0;
    if (start.length) mark(start[0], 'data-kqb-btn', 'start');
    if (retake.length) mark(retake[0], 'data-kqb-btn', 'retake');
    const best = pickGroup(cfg);
    if (best) {
      K.items = best.g.items;
      K.root = best.root;
      best.els.forEach((el, i) => mark(el, 'data-kqb-opt', i));
      const multi = best.g.items.some((i) =>
        (i.input && i.input.type === 'checkbox') ||
        (i.aria && /checkbox/.test(i.aria.getAttribute('role') || ''))) ||
        !!(best.root.closest('[aria-multiselectable="true"]') || best.root.querySelector('[aria-multiselectable="true"]')) ||
        any(res(cfg.multi), best.q.text);
      Object.assign(out, {
        kind: 'question',
        source: best.g.source,
        question: best.q.text,
        code: best.q.code,
        options: best.texts,
        multi,
        progress: best.q.progress,
        fingerprint: `${best.q.text}||${best.texts.join('|')}`,
        selected: best.g.items.map(selInfo).map((s) => (s.known ? !!s.sel : null)),
      }, navInfo(cfg));
    } else {
      K.items = [];
      K.root = null;
      const submit = buttons(cfg.submit).filter((b) => !isDisabled(b));
      out.submitBtn = submit.length > 0;
      if (submit.length) mark(submit[0], 'data-kqb-btn', 'submit');
      if (out.start) out.kind = 'start';
    }
    return out;
  };

  K.fingerprint = (cfg) => {
    const b = pickGroup(cfg);
    return b ? `${b.q.text}||${b.texts.join('|')}` : '';
  };

  K.nav = (cfg) => {
    if (!K.root || !K.root.isConnected) {
      const b = pickGroup(cfg);
      K.root = b ? b.root : null;
    }
    return navInfo(cfg);
  };

  K.selInfo = (i) => selInfo(K.items[i]);

  // Options whose look differs from the majority (the selected ones), for
  // plain <div> options after a re-render.
  K.oddOneOut = () => {
    const sigs = K.items.map((it) => (it.el.isConnected ? signature(it.el) : null));
    const counts = new Map();
    sigs.forEach((s) => counts.set(s, (counts.get(s) || 0) + 1));
    let mode = null;
    let c = 0;
    for (const [s, n] of counts) if (n > c) { mode = s; c = n; }
    return sigs.map((s, i) => (s !== mode ? i : -1)).filter((i) => i >= 0);
  };

  K.lines = () => norm(document.body ? document.body.innerText : '')
    .split('\n').map((s) => s.trim()).filter(Boolean);

  // Result screen: only looks at text that was NOT on the page before Submit,
  // so reading content never gets mistaken for a score.
  K.result = (cfg, before) => {
    const old = new Set(before || []);
    const fresh = K.lines().filter((l) => !old.has(l));
    const kw = /(score|scored|marks?|result|correct|points)/i;
    const frac = /(\d+(?:\.\d+)?)\s*(?:\/|out of)\s*(\d+(?:\.\d+)?)/i;
    const pct = /(\d{1,3}(?:\.\d+)?)\s*%/;
    const ordered = [...fresh.filter((l) => kw.test(l)), ...fresh.filter((l) => !kw.test(l))];
    let score = null; let total = null; let percent = null; let snippet = '';
    for (const l of ordered) {
      const m = l.match(frac);
      if (m && +m[2] > 0 && +m[1] <= +m[2]) { score = +m[1]; total = +m[2]; snippet = l; break; }
    }
    if (score === null) {
      for (const l of ordered) {
        const m = l.match(pct);
        if (m && kw.test(l)) { percent = +m[1]; snippet = l; break; }
      }
    }
    const failRe = res(cfg.fail);
    const passRe = res(cfg.pass);
    // a "Try Again" button label alone is not a verdict
    const btnLabels = new Set([...cfg.retake, ...cfg.next, ...cfg.submit, ...cfg.start].map(btnKey));
    const verdictLines = fresh.filter((l) => !btnLabels.has(btnKey(l)));
    const text = verdictLines.join('\n');
    const fail = any(failRe, text);
    const pass = any(passRe, text);
    if (!snippet) snippet = verdictLines.find((l) => any(failRe, l) || any(passRe, l)) || '';
    clearMarks('data-kqb-btn', 'retake');
    const retake = buttons(cfg.retake).filter((b) => !isDisabled(b));
    if (retake.length) mark(retake[0], 'data-kqb-btn', 'retake');
    return {
      score, total, percent, fail, pass,
      retake: retake.length > 0,
      snippet: snippet.slice(0, 200),
      fresh: fresh.slice(0, 15),
      found: score !== null || percent !== null || fail || pass,
    };
  };

  // "Are you sure you want to submit?" modal. Ignores any dialog that holds the
  // Submit button we already clicked (the quiz itself may live in a modal).
  K.confirm = (cfg) => {
    clearMarks('data-kqb-btn', 'confirm');
    const dialogs = [...document.querySelectorAll(
      '[role="dialog"], [role="alertdialog"], dialog[open], [aria-modal="true"]')].filter(visible);
    for (const d of dialogs.reverse()) {
      if (d.querySelector('[data-kqb-clicked]') || (K.root && d.contains(K.root))) continue;
      const b = buttons(cfg.confirm, d).filter((x) => !isDisabled(x) && !x.hasAttribute('data-kqb-clicked'));
      if (b.length) { mark(b[0], 'data-kqb-btn', 'confirm'); return true; }
    }
    return false;
  };

  // ------------------------------------------------------------------ livebooks + learning path
  K.listLivebooks = (cfg) => {
    clearMarks('data-kqb-lb');
    const hrefRe = new RegExp(cfg.livebookHrefRegex);
    let cards = [];
    if (cfg.livebookCardSelector) {
      try { cards = [...document.querySelectorAll(cfg.livebookCardSelector)].filter(visible); } catch (e) { /* fall back */ }
    }
    const idOf = (el) => {
      const a = el.matches('a[href]') ? el : (el.querySelector('a[href]') || el.closest('a[href]'));
      const m = a ? (a.getAttribute('href') || '').match(hrefRe) : null;
      return { a, id: m ? m[1] : null };
    };
    if (!cards.length) {
      // one card per livebook id: the biggest anchor, grown to the card that holds it
      const byId = new Map();
      for (const a of document.querySelectorAll('a[href]')) {
        if (!visible(a)) continue;
        const m = (a.getAttribute('href') || '').match(hrefRe);
        if (!m) continue;
        let card = a;
        for (let p = a.parentElement, i = 0; p && i < 4; p = p.parentElement, i++) {
          const ids = new Set([...p.querySelectorAll('a[href]')]
            .map((x) => ((x.getAttribute('href') || '').match(hrefRe) || [])[1]).filter(Boolean));
          if (ids.size > 1) break;
          card = p;
        }
        const prev = byId.get(m[1]);
        if (!prev || textOf(card).length > textOf(prev.card).length) byId.set(m[1], { card, a });
      }
      cards = [...byId.values()].map((v) => v.card);
    }
    const out = [];
    const seen = new Set();
    for (const card of cards) {
      const { a, id } = idOf(card);
      const key = id || textOf(card);
      if (seen.has(key)) continue;
      seen.add(key);
      const h = card.querySelector('h1, h2, h3, h4, h5, h6, [class*="title" i], [class*="name" i]');
      let name = h ? textOf(h).split('\n')[0] : '';
      if (!name) name = textOf(card).split('\n').find((l) => l.length > 2 && !/^[\d.\s%/]+$/.test(l)) || '';
      const i = out.length;
      mark(card, 'data-kqb-lb', i);
      out.push({ index: i, id, name: name.slice(0, 120), href: a ? a.href : '', text: textOf(card).slice(0, 300) });
    }
    return out;
  };

  K.collapsedModules = (cfg) => {
    clearMarks('data-kqb-mod');
    const re = new RegExp(cfg.moduleRegex, 'i');
    const luRe = new RegExp(cfg.luNumberRegex, 'i');
    let els = [];
    if (cfg.moduleToggleSelector) {
      try {
        els = [...document.querySelectorAll(cfg.moduleToggleSelector)]
          .filter((e) => visible(e) && e.getAttribute('aria-expanded') !== 'true');
      } catch (e) { /* fall back */ }
    } else {
      els = [...document.querySelectorAll('[aria-expanded="false"]')].filter((e) => {
        if (!visible(e) || e.closest('header, nav, [role="navigation"], [role="menubar"]')) return false;
        const t = textOf(e);
        return re.test(t) && !luRe.test(t);
      });
    }
    els.forEach((e, i) => mark(e, 'data-kqb-mod', i));
    return els.length;
  };

  const ROW_CLICK = 'a[href], button, [role="button"], [role="link"], [role="treeitem"], [role="menuitem"], [onclick], [tabindex]';
  const isClickable = (el) => el.matches(ROW_CLICK) || getComputedStyle(el).cursor === 'pointer';

  function hintsFor(row) {
    const bits = [];
    for (const n of [row, ...row.querySelectorAll('*')].slice(0, 200)) {
      for (const at of ['aria-label', 'title', 'alt', 'data-status', 'data-state', 'data-completed', 'data-testid', 'data-type', 'data-icon', 'class']) {
        const v = n.getAttribute && n.getAttribute(at);
        if (v && v.length < 200) bits.push(`${at}=${v}`);
      }
      if ((n.tagName || '').toLowerCase() === 'use') {
        const h = n.getAttribute('href') || n.getAttribute('xlink:href');
        if (h) bits.push(`use=${h}`);
      }
    }
    const pb = row.querySelector('[role="progressbar"], progress');
    const progress = pb ? (pb.getAttribute('aria-valuenow') || pb.value || '') : '';
    return { attrs: bits.join(' ').slice(0, 3000), progress: String(progress) };
  }

  // Visible text pieces in order, so "<span>1.3 Stacks</span><span>100%</span>"
  // reads as ["1.3 Stacks", "100%"] rather than "1.3 Stacks100%".
  function segments(el) {
    const out = [];
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const p = n.parentElement;
      if (!p || p.closest('script, style') || !visible(p)) continue;
      const t = norm(n.textContent);
      if (t) out.push(t);
    }
    return out;
  }

  K.listLUs = (cfg) => {
    clearMarks('data-kqb-lu');
    clearMarks('data-kqb-lurow');
    const numRe = new RegExp(cfg.luNumberRegex, 'i');
    const luLines = (el) => textOf(el).split('\n').filter((l) => numRe.test(l)).length;
    const found = [];   // {row, click}
    if (cfg.luRowSelector) {
      try {
        for (const row of document.querySelectorAll(cfg.luRowSelector)) {
          if (!visible(row)) continue;
          const inner = row.matches(ROW_CLICK) ? row : ([...row.querySelectorAll(ROW_CLICK)].find(visible) || row);
          found.push({ row, click: inner });
        }
      } catch (e) { /* fall back */ }
    }
    if (!found.length) {
      const seen = new Set();
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (!numRe.test(n.textContent || '')) continue;
        const host = n.parentElement;
        if (!host || !visible(host) || host.closest('script, style, [data-kqb-opt]')) continue;
        let row = host;
        let click = null;
        for (let el = host, i = 0; el && el !== document.body && i < 8; el = el.parentElement, i++) {
          if (luLines(el) > 1) break;          // grew into the whole list
          row = el;
          if (!click && isClickable(el)) click = el;
        }
        if (seen.has(row)) continue;
        seen.add(row);
        found.push({ row, click: click || row });
      }
    }
    const out = [];
    const nums = new Set();
    for (const { row, click } of found) {
      const segs = segments(row);
      const at = segs.findIndex((l) => numRe.test(l));
      const m = at >= 0 ? segs[at].match(numRe) : null;
      if (!m || nums.has(m[1])) continue;
      nums.add(m[1]);
      const text = segs.join(' | ');
      const titleLine = segs[at].replace(m[0], '').replace(/^[\s:.\-–|]+/, '').trim() || segs[at + 1] || '';
      const a = click.matches('a[href]') ? click : (row.querySelector('a[href]') || row.closest('a[href]'));
      const i = out.length;
      mark(click, 'data-kqb-lu', i);
      mark(row, 'data-kqb-lurow', i);
      out.push({ index: i, number: m[1], title: titleLine.slice(0, 120), text: text.slice(0, 300), href: a ? a.href : '', hints: hintsFor(row) });
    }
    return out;
  };

  K.luFeatures = (cfg) => {
    const text = norm(document.body ? document.body.innerText : '');
    const editors = [...document.querySelectorAll('.monaco-editor, .cm-editor, .CodeMirror, .ace_editor')]
      .filter((e) => visible(e) && !(e.getAttribute('aria-readonly') === 'true')).length;
    const writers = [...document.querySelectorAll('textarea, [contenteditable="true"], .ProseMirror, .ql-editor')]
      .filter(visible).length;
    return {
      coding: editors > 0 || any(res(cfg.coding), text),
      written: writers > 0 || any(res(cfg.written), text),
      editors, writers, chars: text.length,
    };
  };

  // Scroll every scrollable area to the bottom (lazy-loaded quiz sections).
  K.scrollAll = () => {
    const els = [document.scrollingElement, ...document.querySelectorAll('*')].filter((e) => {
      if (!e) return false;
      if (e === document.scrollingElement) return true;
      const st = getComputedStyle(e);
      return /(auto|scroll)/.test(st.overflowY) && e.scrollHeight > e.clientHeight + 50;
    });
    els.forEach((e) => { e.scrollTop = e.scrollHeight; });
    return els.length;
  };

  // Mark a tab/button (not a link, never inside nav/header/sidebars) whose text
  // is one of `texts`. Used to reveal a quiz that sits behind an LU tab.
  K.markInPageTab = (texts) => {
    clearMarks('data-kqb-tab');
    const want = new Set((texts || []).map(btnKey));
    const el = [...document.querySelectorAll('[role="tab"], button, [role="button"]')].find((e) =>
      visible(e) && !isDisabled(e) && want.has(btnKey(textOf(e))) &&
      !e.closest('nav, header, footer, aside, [role="navigation"], [role="complementary"], [role="banner"]'));
    if (!el) return null;
    mark(el, 'data-kqb-tab', '1');
    return textOf(el);
  };

  K.clickedMark = (sel) => { const el = document.querySelector(sel); if (el) el.setAttribute('data-kqb-clicked', '1'); };

  // ------------------------------------------------------------------ discovery
  K.report = () => {
    const vis = (sel) => [...document.querySelectorAll(sel)].filter(visible);
    const brief = (el) => ({
      tag: el.tagName.toLowerCase(),
      text: textOf(el).slice(0, 80),
      attrs: Object.fromEntries([...el.attributes]
        .filter((a) => !['style'].includes(a.name))
        .slice(0, 10).map((a) => [a.name, a.value.slice(0, 100)])),
    });
    const dataAttrs = {};
    for (const el of document.querySelectorAll('*')) {
      for (const a of el.attributes) {
        if (!a.name.startsWith('data-') || a.name.startsWith('data-kqb')) continue;
        const e = dataAttrs[a.name] || (dataAttrs[a.name] = { count: 0, samples: [] });
        e.count++;
        if (e.samples.length < 6 && !e.samples.includes(a.value)) e.samples.push(a.value.slice(0, 60));
      }
    }
    return {
      url: location.href,
      title: document.title,
      headings: vis('h1, h2, h3, h4').slice(0, 60).map((h) => textOf(h).slice(0, 100)),
      tabs: vis('[role="tab"]').map(brief),
      buttons: vis('button, [role="button"]').slice(0, 150).map(brief),
      links: vis('a[href]').slice(0, 200).map((a) => ({ text: textOf(a).slice(0, 60), href: a.getAttribute('href') })),
      inputs: vis('input, textarea, select, [contenteditable="true"]').slice(0, 80).map(brief),
      ariaWidgets: vis(`${OPT_ROLES}, [role="radiogroup"], [role="progressbar"], [aria-expanded]`).slice(0, 100).map(brief),
      editors: vis('.monaco-editor, .cm-editor, .CodeMirror, .ace_editor, .ProseMirror, .ql-editor').length,
      iframes: [...document.querySelectorAll('iframe')].map((f) => f.src).slice(0, 10),
      dataAttributes: dataAttrs,
    };
  };

  // A selector that matches exactly the elements carrying `attr` (a data-kqb-*
  // marker), preferring test ids / data attributes / roles over classes.
  K.suggest = (attr) => {
    const els = [...document.querySelectorAll(`[${attr}]`)];
    if (els.length < 2) return null;
    const exact = (sel) => {
      try {
        const m = [...document.querySelectorAll(sel)].filter(visible);
        return m.length === els.length && els.every((e) => m.includes(e));
      } catch (e) { return false; }
    };
    const esc = (v) => v.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
    const names = new Set();
    els.forEach((e) => [...e.attributes].forEach((a) => names.add(a.name)));
    const prefer = ['data-testid', 'data-test', 'data-test-id', 'data-cy', 'data-qa'];
    const ordered = [...prefer,
      ...[...names].filter((n) => n.startsWith('data-') && !n.startsWith('data-kqb') && !prefer.includes(n)),
      'role', 'name'];
    for (const n of ordered) {
      const vals = els.map((e) => e.getAttribute(n));
      if (vals.some((v) => v === null)) continue;
      const uniq = [...new Set(vals)];
      if (uniq.length === 1 && exact(`[${n}="${esc(uniq[0])}"]`)) return `[${n}="${esc(uniq[0])}"]`;
      const p = vals.reduce((a, b) => { let i = 0; while (i < a.length && a[i] === b[i]) i++; return a.slice(0, i); });
      if (p.length >= 3 && exact(`[${n}^="${esc(p)}"]`)) return `[${n}^="${esc(p)}"]`;
      if (exact(`[${n}]`)) return `[${n}]`;
    }
    const generated = /(^css-|^sc-|^jsx-|^_|^Mui|^chakra|[:[\]]|[_-](?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{5,}$)/i;
    const utility = /^(flex|grid|block|inline|hidden|relative|absolute|p[xytrbl]?-|m[xytrbl]?-|text-|bg-|w-|h-|min-|max-|gap-|rounded|border|items-|justify-|font-|shadow|cursor-|overflow|space-|hover|transition)/;
    const sets = els.map((e) => new Set(cls(e).split(/\s+/).filter((t) => /^[a-zA-Z][\w-]{2,}$/.test(t) && !generated.test(t) && !utility.test(t))));
    for (const t of [...sets[0]].filter((x) => sets.every((s) => s.has(x)))) {
      const sel = `${els[0].tagName.toLowerCase()}.${CSS.escape(t)}`;
      if (exact(sel)) return sel;
    }
    return null;
  };

  window.__kqb = K;
})();
