# Kalvium LU Quiz Bot

Completes the unfinished 5-question multiple-choice quizzes inside the Learning
Units (LUs) of your current semester on <https://app.kalvium.community>, using
an LLM (Gemini by default, Claude optional) to pick answers. Written and coding
LUs are never touched; they are listed for you as "manual" at the end.

> **Read this first.** This tool was built and tested against a local *mock* of
> the portal; it has never seen the real Kalvium pages. It finds livebooks, LUs,
> questions, options and buttons by roles, visible text and page structure
> (not brittle CSS classes), so it should cope with the real site, but your first
> run is the real test. Follow the **First run** steps in order. Each one is
> safe and tells you what it found before anything is clicked for real.

## Setup (Windows 11)

1. Install **Python 3.11+** from python.org (tick *Add python.exe to PATH*) and
   **Google Chrome**.
2. Open a terminal in this folder and run:

   ```bat
   setup.bat
   ```

   (Same as `pip install -r requirements.txt` + `python -m playwright install chrome`.)
3. Get a Gemini API key at <https://aistudio.google.com/apikey> and store it
   **once**:

   ```bat
   setx GEMINI_API_KEY "your-key-here"
   ```

   Then **close the terminal and open a new one** (`setx` only affects new
   terminals). The key is read from that environment variable only; it is never
   written to a file, printed or logged.

## First run

1. **Log in and explore (read-only):**

   ```bat
   python main.py --discover
   ```

   Chrome opens with a dedicated profile in `browser_profile\` (not your normal
   Chrome profile). Log in to Kalvium in that window. The tool waits (up to 10
   minutes) until your semester's livebooks appear, then on its own it:
   lists the livebooks, opens the first one's **Learning Path**, lists its LUs
   with done/todo, and opens a few LUs to classify them (quiz / written / coding /
   already submitted). It clicks no quiz controls at all. It saves HTML +
   screenshots to `snapshots\` plus a `*_discovery_report.json`, and writes stable
   selectors it verified (for livebook cards and LU rows) into `config.yaml`.
   You stay logged in for future runs.

   *If Google refuses the sign-in in that window* ("This browser or app may not be
   secure"): close it, run `python main.py --login` (opens normal Chrome on the
   same `browser_profile`), log in, **close that Chrome window**, then run
   `--discover` again.

2. **Dry run on one quiz:**

   ```bat
   python main.py --dry-run --limit 1
   ```

   Shows question 1, the extracted options and the LLM's choice, and clicks
   nothing in the quiz. If the quiz is behind a **Start Quiz** button, the dry run
   says so and stops there. To preview the question, set
   `run.dry_run_click_start: true` in `config.yaml`. That clicks only Start,
   never an answer. Check the console and `logs\run_*.csv`.

3. **One real quiz:**

   ```bat
   python main.py --limit 1
   ```

   Then check the score in the console/CSV and on the portal.

4. **Everything:**

   ```bat
   python main.py
   ```

If anything looks wrong at any step, see **When something isn't recognised**.

## Usage

| Command | What it does |
|---|---|
| `python main.py` | Complete every unfinished quiz in the semester |
| `python main.py --dry-run` | Extract questions, print chosen answers, click nothing in quizzes |
| `python main.py --limit 3` | Stop after 3 quizzes |
| `python main.py --livebook "philosophy"` | Only livebooks whose name contains the text |
| `python main.py --livebook "philosophy" --lu 1.2` | Only that LU (also re-checks an LU marked complete) |
| `python main.py --semester 6` | Override `portal.semester` |
| `python main.py --discover` | Read-only exploration, snapshots, selector suggestions |
| `python main.py --login` | Plain-Chrome login fallback |

`Ctrl+C` stops cleanly and still prints the summary.

## What it does on each quiz

For every unfinished LU it opens the LU and looks for a quiz, including in
iframes, behind an in-page **Quiz** tab, or further down the page. Then:

1. Clicks **Start** if there is one.
2. For each question, extracts the question text, any code blocks, and all
   options in order. It detects single vs multi-select (checkboxes or wording
   like "select all that apply").
3. Asks the LLM for JSON only: `{"answer_indices": [...], "confidence": "high|medium|low"}`.
   Code fences are stripped and indices are validated. An invalid reply is
   retried once. Rate limits and server errors are retried with backoff.
4. Clicks the option(s), **verifies they are selected**, then clicks **Next**
   (or **Submit** on the last question, confirming an "Are you sure?" dialog
   if one appears). Waits for the question text/options to actually change
   instead of sleeping, with a random 1–2 s pause between actions.
5. Reads the result screen (score / pass / fail) and logs it. If the portal
   says you failed (or shows a score below `run.pass_fraction`) and offers a
   **Retake**, it retakes **once**, telling the LLM what it chose before.

Safety rules:

- It only clicks elements it has identified and tagged. If the page isn't in
  the expected state (a button missing, a selection that didn't register, no
  result after Submit), it saves a screenshot and HTML to `errors\`, logs it,
  skips that LU and continues.
- LUs marked complete on the Learning Path and quizzes that are already
  submitted are skipped.
- A bad API key or exhausted quota stops the whole run instead of failing
  quiz after quiz.

## Output

- **Console**: every question, options (`*` = chosen), confidence, results,
  and a final summary: quizzes submitted with scores, skipped, errors,
  manual (written/coding) LUs left, and LUs where no quiz was found.
- **`logs\run_<timestamp>.csv`**: one row per question/event: livebook, LU,
  attempt, question, code, options, multi-select, chosen indices and text,
  confidence, result, notes, URL. Opens cleanly in Excel.
- **`logs\run_<timestamp>.log`**: detailed technical log.
- **`snapshots\`**: question 1 and the result screen of every quiz, plus
  discovery output. **`errors\`**: the page at every skipped-after-error LU.

## When something isn't recognised

Everything is in `config.yaml`. Leave a selector `""` to use the built-in
detection, or set any Playwright/CSS selector. Look at the matching snapshot
in `snapshots\` or `errors\` (open the `.html` in Chrome and use *Inspect*), or
in the discovery report (it lists buttons, tabs, inputs and every `data-*`
attribute on the page).

| Symptom | Setting |
|---|---|
| No livebooks found | `selectors.livebook_card`, `selectors.livebook_href_regex`, `portal.semester` |
| Learning Path not opened | `selectors.learning_path_tab` (exact tab text) |
| LUs missing / modules not expanded | `selectors.lu_row`, `selectors.module_toggle`, `selectors.lu_number_regex` |
| Done/todo wrong on the Learning Path | `patterns.lu_completed` / `lu_not_completed`, or `run.trust_list_completion: false` |
| Quiz not found / not started | `texts.start`, `texts.quiz_tabs` |
| Options or question text wrong | `selectors.quiz_option`, `selectors.quiz_question` |
| Next / Submit / confirm not clicked | `texts.next`, `texts.submit`, `texts.confirm` |
| Pages slow | `timeouts.*` |
| Gemini 429 "rate limit" | `llm.min_seconds_between_calls: 6` |

Quote words like `'Yes'`, `'No'`, `'On'`, `'Off'` in YAML lists, or YAML reads
them as true/false.

Easiest fix of all: open this folder in **Claude Code on your PC** and ask it to
run `python main.py --discover`, read the snapshots, and adjust `config.yaml`.
On your machine it can see the real pages.

## Switching to Claude

```bat
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Open a new terminal, then in `config.yaml`:

```yaml
llm:
  provider: claude
```

The model is `llm.models.claude` (default `claude-opus-5-5`; `claude-sonnet-5-5`
or `claude-haiku-4-5-20251001` are cheaper). No code changes are needed. To add
another provider, implement `complete()` in a small class in `llm.py` and
register it in `PROVIDERS`.

The Gemini model is `llm.models.gemini` (default `gemini-3.5-flash`). If Google
doesn't serve that ID for your key, the tool falls back to
`gemini-flash-latest` and then `gemini-2.5-flash` automatically.

## Files

| File | Purpose |
|---|---|
| `main.py` | CLI, logging, summary |
| `navigator.py` | Browser/profile, login wait, livebooks, Learning Path, LU classification, discovery |
| `quiz.py` | Quiz solver: extract, ask, select + verify, Next/Submit, result, retake |
| `llm.py` | Provider interface (Gemini, Claude), prompt, JSON parsing/validation/retry |
| `dom_helpers.js` | In-page detection of cards, LU rows, questions, options and buttons |
| `common.py` | Config, pacing, snapshots, CSV log |
| `config.yaml` | All settings |
| `tests/` | Mock portal + end-to-end and unit tests |

## Tests

```bat
pip install -r requirements-dev.txt
python -m pytest tests
```

This starts a local mock portal and runs the full flow (login wait, discover,
dry runs, one quiz with a fail + retake, a full run, a no-op re-run) with a
fake LLM. No portal access or API key is needed.

## Notes

- Keep `browser_profile\` private: it holds your logged-in session. It is in
  `.gitignore`, along with `logs\`, `snapshots\` and `errors\`.
- LLM answers can be wrong. Low-confidence answers are marked in the CSV.
- You said your teacher is fine with automating this ungraded activity.
  Automated use may still be against the portal's terms, so use it on your own
  account only.
