"""A tiny in-memory stand-in for the GitHub REST endpoints kalbot uses."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeGitHub:
    def __init__(self, login: str = "student"):
        self.login = login
        self.repos: dict[str, dict] = {}     # name -> {description, private, files, pages, commits}
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    gh: FakeGitHub = None

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: dict, headers: dict | None = None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n) or b"{}")

    def _auth(self) -> bool:
        if self.headers.get("Authorization") != "Bearer test-token":
            self._send(401, {"message": "Bad credentials"})
            return False
        return True

    def _repo(self, path: str):
        m = re.match(r"^/repos/([^/]+)/([^/]+)(/.*)?$", path)
        if not m or m[1] != self.gh.login:
            return None, None
        return self.gh.repos.get(m[2]), m[3] or ""

    def do_GET(self):
        if not self._auth():
            return
        if self.path == "/user":
            return self._send(200, {"login": self.gh.login}, {"X-OAuth-Scopes": "repo"})
        repo, rest = self._repo(self.path)
        if repo is None:
            return self._send(404, {"message": "Not Found"})
        if rest == "":
            return self._send(200, {"name": repo["name"], "default_branch": "main"})
        if rest == "/git/ref/heads/main":
            return self._send(200, {"object": {"sha": repo["commits"][-1]}})
        if rest.startswith("/git/commits/"):
            return self._send(200, {"tree": {"sha": "tree-" + rest.rsplit("/", 1)[1]}})
        if rest == "/pages" and repo["pages"]:
            return self._send(200, {"html_url": repo["pages"]})
        self._send(404, {"message": "Not Found"})

    def do_POST(self):
        if not self._auth():
            return
        body = self._body()
        with self.gh.lock:
            if self.path == "/user/repos":
                name = body["name"]
                if name in self.gh.repos:
                    return self._send(422, {"message": "name already exists on this account"})
                self.gh.repos[name] = {"name": name, "description": body.get("description", ""),
                                       "private": body.get("private"), "files": {"README.md": f"# {name}\n"},
                                       "pending": {}, "commits": ["c0"], "pages": ""}
                return self._send(201, {"name": name, "html_url": f"https://github.com/{self.gh.login}/{name}",
                                        "owner": {"login": self.gh.login}, "default_branch": "main"})
            repo, rest = self._repo(self.path)
            if repo is None:
                return self._send(404, {"message": "Not Found"})
            if rest == "/git/trees":
                repo["pending"] = {e["path"]: e["content"] for e in body["tree"]}
                return self._send(201, {"sha": "tree-new"})
            if rest == "/git/commits":
                sha = f"c{len(repo['commits'])}"
                repo["next"] = sha
                return self._send(201, {"sha": sha})
            if rest == "/pages":
                if repo["pages"]:
                    return self._send(409, {"message": "already enabled"})
                repo["pages"] = f"https://{self.gh.login}.github.io/{repo['name']}/"
                return self._send(201, {"html_url": repo["pages"]})
        self._send(404, {"message": "Not Found"})

    def do_PATCH(self):
        if not self._auth():
            return
        body = self._body()
        with self.gh.lock:
            repo, rest = self._repo(self.path)
            if repo is None or rest != "/git/refs/heads/main" or body.get("sha") != repo.get("next"):
                return self._send(422, {"message": "bad ref update"})
            repo["files"].update(repo.pop("pending"))
            repo["pending"] = {}
            repo["commits"].append(body["sha"])
            return self._send(200, {"object": {"sha": body["sha"]}})


def serve():
    gh = FakeGitHub()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (Handler,), {"gh": gh}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, gh
