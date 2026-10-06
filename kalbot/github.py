"""Just enough of the GitHub REST API to publish a generated project.

Creates a repo, commits all files in one commit, and (for static sites)
turns on GitHub Pages. Uses the standard library only.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

log = logging.getLogger("kalbot.github")


class GitHubError(Exception):
    pass


class GitHub:
    def __init__(self, token: str, api_url: str = "https://api.github.com", timeout: float = 30):
        self.token = token
        self.api = api_url.rstrip("/")
        self.timeout = timeout
        self._user: dict | None = None

    @classmethod
    def from_config(cls, cfg: dict) -> GitHub | None:
        g = cfg.get("github") or {}
        token = os.environ.get(str(g.get("token_env") or "GITHUB_TOKEN"), "").strip()
        if not token:
            return None
        return cls(token, g.get("api_url") or "https://api.github.com")

    def request(self, method: str, path: str, body: dict | None = None):
        """Returns (status, json). Raises GitHubError on HTTP errors other than 404/409/422."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "kalbot",
            **({"Content-Type": "application/json"} if data else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {}), dict(r.headers)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"message": raw[:200].decode(errors="replace")}
            if e.code in (404, 409, 422):
                return e.code, payload, dict(e.headers)
            msg = payload.get("message", "") if isinstance(payload, dict) else ""
            hint = " (check the token and its permissions)" if e.code in (401, 403) else ""
            raise GitHubError(f"GitHub {method} {path}: HTTP {e.code} {msg}{hint}") from None
        except urllib.error.URLError as e:
            raise GitHubError(f"GitHub is not reachable: {e.reason}") from None

    def user(self) -> dict:
        if self._user is None:
            status, data, headers = self.request("GET", "/user")
            if status != 200:
                raise GitHubError(f"could not read the GitHub user (HTTP {status})")
            data["_scopes"] = headers.get("X-OAuth-Scopes") or headers.get("x-oauth-scopes") or ""
            self._user = data
        return self._user

    def repo_exists(self, owner: str, name: str) -> bool:
        status, _, _ = self.request("GET", f"/repos/{owner}/{name}")
        return status == 200

    def free_name(self, base: str) -> str:
        owner = self.user()["login"]
        name, n = base, 2
        while self.repo_exists(owner, name):
            name, n = f"{base}-{n}", n + 1
        return name

    def create_repo(self, name: str, description: str, private: bool) -> dict:
        status, data, _ = self.request("POST", "/user/repos", {
            "name": name, "description": description[:350], "private": private, "auto_init": True})
        if status != 201:
            raise GitHubError(f"could not create repo {name}: {data.get('message', status)}")
        return data

    def commit_files(self, owner: str, repo: str, files: dict[str, str], message: str, branch: str) -> str:
        """Add/replace files on `branch` in a single commit; returns the commit sha."""
        ref = None
        for _ in range(8):   # a just-created repo's first commit can take a moment to appear
            status, ref, _ = self.request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
            if status == 200:
                break
            time.sleep(1)
        if not ref or "object" not in ref:
            raise GitHubError(f"branch {branch} of {owner}/{repo} not found")
        base = ref["object"]["sha"]
        _, commit, _ = self.request("GET", f"/repos/{owner}/{repo}/git/commits/{base}")
        status, tree, _ = self.request("POST", f"/repos/{owner}/{repo}/git/trees", {
            "base_tree": commit["tree"]["sha"],
            "tree": [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in files.items()],
        })
        if status != 201:
            raise GitHubError(f"could not upload files: {tree.get('message', status)}")
        status, new, _ = self.request("POST", f"/repos/{owner}/{repo}/git/commits", {
            "message": message, "tree": tree["sha"], "parents": [base]})
        if status != 201:
            raise GitHubError(f"could not create the commit: {new.get('message', status)}")
        status, out, _ = self.request("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}", {"sha": new["sha"]})
        if status != 200:
            raise GitHubError(f"could not update {branch}: {out.get('message', status)}")
        return new["sha"]

    def enable_pages(self, owner: str, repo: str, branch: str) -> str:
        status, data, _ = self.request("POST", f"/repos/{owner}/{repo}/pages",
                                       {"source": {"branch": branch, "path": "/"}})
        if status == 409:   # already enabled
            status, data, _ = self.request("GET", f"/repos/{owner}/{repo}/pages")
        if status not in (200, 201) or not data.get("html_url"):
            raise GitHubError(f"could not enable GitHub Pages: {data.get('message', status)}")
        return data["html_url"]

    @staticmethod
    def wait_live(url: str, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            try:
                with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=10) as r:
                    if r.status == 200:
                        return True
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            if time.monotonic() > deadline:
                return False
            time.sleep(5)


def publish(gh: GitHub, name: str, description: str, files: dict[str, str], *, private: bool,
            pages: bool, wait_s: float = 0) -> dict:
    """Create the repo, push the files and optionally enable Pages. Returns the links."""
    data = gh.create_repo(name, description, private)
    owner, branch = data["owner"]["login"], data.get("default_branch") or "main"
    gh.commit_files(owner, name, files, "Add project files", branch)
    out = {"github": data["html_url"], "live": ""}
    if pages:
        out["live"] = gh.enable_pages(owner, name, branch)
        if wait_s and not gh.wait_live(out["live"], wait_s):
            log.info("GitHub Pages site not up yet: %s", out["live"])
    log.info("published %s", out)
    return out
