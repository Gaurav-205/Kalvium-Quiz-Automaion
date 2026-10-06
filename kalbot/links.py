"""Links for GitHub / live-site / video fields.

They come from the user's submissions.yaml, or from repos kalbot created
earlier (remembered in the state file). kalbot never invents a link: a field
it has no real link for is left for the user.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

log = logging.getLogger("kalbot.links")

KINDS = ("pr", "video", "live", "github", "link")   # also the tie-break order
KIND_NAMES = {"github": "GitHub repository", "pr": "pull request", "live": "live site",
              "video": "video", "link": "link"}
VIDEO_HOSTS = ("youtube.com", "youtu.be", "loom.com", "vimeo.com", "drive.google.com", "dropbox.com",
               "onedrive.live.com", "1drv.ms", "sharepoint.com", "streamable.com", "wistia.com", "screencast.com")


def classify(label: str, prompt: str, patterns: dict) -> str:
    """Which link a field wants: github | pr | live | video | link."""
    for text in (label, prompt[-400:]):
        scores = {k: sum(bool(re.search(p, text or "", re.I)) for p in patterns.get(f"link_{k}", []))
                  for k in KINDS if k != "link"}
        best = max(scores.values())
        if best:
            return next(k for k in KINDS if scores.get(k) == best)
    return "link"


def is_link_field(label: str, prompt: str, input_type: str, patterns: dict) -> bool:
    if input_type == "url":
        return True
    text = f"{label}\n{prompt[-300:]}"
    return any(re.search(p, text, re.I) for p in patterns.get("link_field", []))


def check_url(kind: str, url: str) -> str | None:
    """A reason the URL doesn't fit the field, or None if it looks right."""
    u = urlparse(url or "")
    if u.scheme not in ("http", "https") or not u.netloc:
        return "not an http(s) URL"
    host = u.netloc.lower().removeprefix("www.")
    parts = [p for p in u.path.split("/") if p]
    if kind == "github" and (host != "github.com" or len(parts) < 2):
        return "not a GitHub repository URL (https://github.com/<you>/<repo>)"
    if kind == "pr" and not (host == "github.com" and len(parts) >= 4 and parts[2] == "pull"):
        return "not a GitHub pull request URL (https://github.com/<owner>/<repo>/pull/<n>)"
    if kind == "video" and not any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
        log.info("video link on an unusual host: %s", host)
    return None


class LinkBook:
    """The user's submissions.yaml: links per livebook + LU."""

    def __init__(self, entries: list[dict], path: Path | None = None, problems: list[str] | None = None):
        self.entries = entries
        self.path = path
        self.problems = problems or []

    @classmethod
    def load(cls, path: Path) -> LinkBook:
        if not path.exists():
            return cls([], path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as e:
            return cls([], path, [f"{path.name} is not valid YAML: {e}"])
        if isinstance(data, dict):
            data = data.get("links") or []
        entries, problems = [], []
        for i, e in enumerate(data if isinstance(data, list) else []):
            if not isinstance(e, dict) or not e.get("lu"):
                problems.append(f"{path.name} entry {i + 1}: needs at least `lu:`")
                continue
            e = {str(k).lower(): ("" if v is None else str(v).strip()) for k, v in e.items()}
            for kind in KINDS:
                if e.get(kind):
                    why = check_url(kind, e[kind])
                    if why:
                        problems.append(f"{path.name} entry {i + 1} ({e['lu']}): {kind}: {why}")
            entries.append(e)
        return cls(entries, path, problems)

    def lookup(self, livebook: str, lu_number: str, lu_title: str) -> dict[str, str]:
        """Links for one LU. Entries for that LU win over `lu: "*"` entries (links for every LU)."""
        out: dict[str, str] = {}
        ordered = [e for e in self.entries if e.get("lu") != "*"] + [e for e in self.entries if e.get("lu") == "*"]
        for e in ordered:
            lb = e.get("livebook", "").lower()
            if lb and lb not in (livebook or "").lower():
                continue
            want = e.get("lu", "")
            if want == "*":
                pass
            elif re.fullmatch(r"\d{1,2}\.\d{1,2}", want):
                if want != lu_number:
                    continue
            elif want.lower() not in (lu_title or "").lower():
                continue
            for kind in KINDS:
                if e.get(kind) and kind not in out:
                    out[kind] = e[kind]
        return out


class State:
    """Small JSON memory between runs (repos created per LU)."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            log.warning("ignoring unreadable state file %s", path)
            self.data = {}

    @staticmethod
    def key(livebook: str, lu: str) -> str:
        return f"{livebook}|{lu}"

    def repo(self, livebook: str, lu: str) -> dict:
        return dict(self.data.get("repos", {}).get(self.key(livebook, lu), {}))

    def save_repo(self, livebook: str, lu: str, info: dict) -> None:
        self.data.setdefault("repos", {})[self.key(livebook, lu)] = info
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)
