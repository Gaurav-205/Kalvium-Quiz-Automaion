"""Prompt text for every kind of work. Kept in one place so it is easy to tune."""

from __future__ import annotations

QUIZ_SYSTEM = (
    "You are an expert tutor answering multiple-choice quiz questions from a college course on the "
    "Kalvium learning platform. Use the course material when it is relevant. Pick the best answer and "
    "reply with a single JSON object only."
)
QUIZ_JSON = '{"answer_indices": [<0-based ints>], "confidence": "high|medium|low"}'

WRITE_SYSTEM = (
    "You are a student completing a written assignment for a college course on the Kalvium learning "
    "platform. {style}\n"
    "Reply with the answer text only: no title, no preamble, no word count, and no Markdown headings or "
    "bold text. Separate paragraphs with a blank line. Use simple '- ' bullets only if the question asks "
    "for a list."
)

CODE_SYSTEM = (
    "You are an expert programmer solving a coding exercise from a college course on the Kalvium learning "
    "platform. Reply with the complete final contents of the code editor in one fenced code block and "
    "nothing else. Keep any given function names, signatures, class names and input/output format exactly. "
    "Do not print prompts or extra output unless the problem asks for it."
)

PROJECT_SYSTEM = (
    "You are a student completing a project assignment for a college course on the Kalvium learning "
    "platform. Build a small, complete, working project that meets every stated requirement, with a clear "
    "README.md that explains what it is, how it meets the requirements and how to run it. "
    "Reply with a single JSON object only."
)
PROJECT_JSON = (
    '{"repo_name": "short-lowercase-hyphenated-name", "description": "<one line>", '
    '"static_site": true|false, "requires_existing_repo": true|false, '
    '"files": [{"path": "README.md", "content": "..."}, {"path": "...", "content": "..."}]}'
)


def material_block(text: str) -> str:
    return ("Course material from the LU page, for reference (it may include instructions, "
            "examples and unrelated page text):\n<<<\n" + text.strip() + "\n>>>")


def quiz_prompt(q, course: str = "", previous: list[int] | None = None) -> str:
    lines = []
    if course:
        lines.append(f"Course context: {course}")
    if q.number and q.total:
        lines.append(f"Question {q.number} of {q.total}.")
    lines += ["", "Question:", q.text.strip() or "(no question text found; infer it from the options)"]
    for block in q.code:
        lines += ["", "Code in the question:", "```", block.rstrip(), "```"]
    lines += ["", "Options (0-based index):"]
    lines += [f"[{i}] {opt}" for i, opt in enumerate(q.options)]
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
    lines += ["", "Reply with JSON only, no prose and no code fences, exactly in this shape:", QUIZ_JSON]
    return "\n".join(lines)


def write_prompt(spec, course: str = "") -> str:
    lines = [f"Course and assignment: {course}" if course else "", "",
             "Question / prompt for this answer box:", spec.question.strip() or "(see the course material)"]
    if spec.label and spec.label not in spec.question:
        lines += ["", f"Answer box label: {spec.label}"]
    if spec.existing:
        lines += ["", "The box already contains the text below: a template to fill in, or a partial draft. "
                  "Build on it (fill in the template, or complete and polish the draft):", "<<<", spec.existing, ">>>"]
    if spec.others:
        lines += ["", "Other answer boxes in this assignment (answer only this one, don't repeat them):"]
        lines += [f"- {o}" for o in spec.others]
    lines.append("")
    if spec.short:
        lines.append(f"Length: one short line, at most {spec.max_chars} characters.")
    else:
        lo, hi = spec.words
        lines.append(f"Length: between {lo} and {hi} words (aim for about {(lo + hi) // 2}).")
    lines += ["", "Write the answer now."]
    return "\n".join(lines).strip()


def revise_words_prompt(base: str, draft: str, n: int, lo: int, hi: int) -> str:
    return (f"{base}\n\nYour draft below has {n} words, but it must be between {lo} and {hi} words. "
            f"Rewrite it to fit, keeping the substance.\n\nDraft:\n{draft}")


def code_prompt(spec, course: str = "", previous: str = "", feedback: str = "") -> str:
    lines = [f"Course and exercise: {course}" if course else "", "", "Problem:",
             spec.question.strip() or "(see the course material)"]
    lines += ["", f"Language: {spec.language}" if spec.language else
              "Language: use the language of the starter code (or Python if there is none)."]
    if spec.starter.strip():
        lines += ["", "Starter code currently in the editor (complete it; keep its signatures):",
                  "```", spec.starter.rstrip(), "```"]
    if previous:
        lines += ["", "Your previous code:", "```", previous.rstrip(), "```",
                  "", "Running it on the portal produced this output:", "```", feedback.strip()[:4000], "```",
                  "", "Fix the code so every test passes."]
    lines += ["", "Reply with the complete code in one fenced code block."]
    return "\n".join(lines).strip()


def project_prompt(spec, course: str = "") -> str:
    lines = [f"Course and assignment: {course}" if course else "", "",
             "The assignment asks for a GitHub repository link. What the page says about it:",
             spec.question.strip() or "(see the course material)"]
    if spec.other_links:
        lines += ["", "Other links this assignment also asks for: " + ", ".join(spec.other_links)]
    lines += [
        "",
        "Rules:",
        "- Keep it small: at most 15 text files and 60 KB in total, no binary files, no node_modules.",
        "- Include README.md. Use relative paths like src/app.js.",
        "- static_site: true only if it is plain HTML/CSS/JS that runs when served from the repo root "
        "(index.html at the root), so it can be published on GitHub Pages.",
        "- requires_existing_repo: true (and files: []) if the assignment requires forking, cloning or "
        "opening a pull request on a specific existing repository instead of creating a new one.",
        "",
        "Reply with JSON only, exactly in this shape:",
        PROJECT_JSON,
    ]
    return "\n".join(lines).strip()
