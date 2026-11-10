"""clipper codereview: a second reviewer for a branch, in one model call ([codereview]).

The branch's code diff against the base (origin/main) and the changed files in full go to
[codereview] reviewer (DeepSeek through OpenRouter by default) with the codereview prompt;
the answer is a Markdown list of correctness bugs. One call, no tool loop: the model reads
what it is sent and nothing else, which keeps a review to cents.

With --pr (the `review` workflow, on every PR) the review goes on the PR as one comment,
edited in place on each push, and on every issue the PR links (GitHub's closing references,
or #N in its title or body): one comment per issue and PR, edited the same way. A failed
model call posts "review failed" instead; the workflow never fails the PR's checks on it.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import llm
from .errors import ClipperError
from .prompts import Prompts

API = "https://api.github.com"
MARKER = "<!-- clipper-codereview -->"  # the PR's own comment
ISSUE_MARKER = "<!-- clipper-codereview pr={pr} -->"  # a linked issue's, one per PR
# #12, owner/name#12 (kept only when it is this repo), or a link to an issue here.
REF = re.compile(r"(?<![\w/])(?:([\w.-]+/[\w.-]+))?#(\d+)\b")
URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)")
NOTHING = "No correctness bugs found."


# ---------- what goes to the model ----------

def git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                              text=True, encoding="utf-8", errors="replace").stdout
    except subprocess.CalledProcessError as e:
        last = (e.stderr or "").strip().splitlines()
        raise ClipperError(f"git {args[0]}: {last[-1] if last else e}") from None


def fence(text: str) -> str:
    """A code fence longer than any run of backticks in text, so the text can't close it."""
    runs = [len(m) for m in re.findall(r"`+", text)]
    return "`" * max(3, max(runs, default=0) + 1)


def message(diff: str, files: dict[str, str], max_chars: int) -> tuple[str, list[str]]:
    """The user message: the diff, then each changed file in full while the whole stays under
    max_chars. Returns (message, the files left out). A diff alone over max_chars is cut."""
    if len(diff) > max_chars:
        diff = diff[:max_chars] + "\n[... the diff is cut here: it is longer than [codereview] max_chars]\n"
    f = fence(diff)
    parts, size, left_out = [f"## The diff\n\n{f}diff\n{diff}{f}\n"], len(diff), []
    for path, text in files.items():
        if size + len(text) > max_chars:
            left_out.append(path)
            continue
        f = fence(text)
        parts.append(f"## {path} (whole file, after the change)\n\n{f}\n{text}{f}\n")
        size += len(text)
    if left_out:
        parts.append("Left out for size (see the diff only): " + ", ".join(left_out) + "\n")
    return "\n".join(parts), left_out


def read_files(root: Path, head: str, names: list[str]) -> dict[str, str]:
    """The text of each changed file at head (not the working tree); binary files are skipped."""
    out = {}
    for name in names:
        r = subprocess.run(["git", "show", f"{head}:{name}"], cwd=root, capture_output=True)
        try:
            text = r.stdout.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if r.returncode == 0 and "\0" not in text:
            out[name] = text
    return out


def gather(cfg: dict, base: str, head: str, root: Path) -> tuple[str, list[str], list[str]]:
    """(user message, changed files, files left out); an empty message when no code changed."""
    rc = cfg["codereview"]
    span = f"{base}...{head}"
    diff = git(root, "diff", "--no-color", span, "--", *rc["paths"])
    if not diff.strip():
        return "", [], []
    names = [n for n in git(root, "diff", "--name-only", "--diff-filter=d", span, "--", *rc["paths"]).splitlines() if n]
    user, left_out = message(diff, read_files(root, head, names), rc["max_chars"])
    return user, names, left_out


def review(cfg: dict, base: str = "origin/main", head: str = "HEAD", root: Path = Path("."),
           dry_run: bool = False) -> tuple[str, dict]:
    """The review as Markdown, and {model, cost, files, left_out}; ("", {}) when no code changed."""
    rc = cfg["codereview"]
    model = rc["reviewer"]
    user, names, left_out = gather(cfg, base, head, root)
    if not user:
        return "", {}
    system = Prompts(cfg).get("codereview")
    tin = llm.tokens(system + user)
    est = llm.estimate(cfg, model, tin, rc["max_tokens"])
    print(f"codereview: {len(names)} files, about {tin:,} tokens in, about ${est:.3f} on {model} "
          f"plus its thinking (up to {rc['thinking_tokens']:,} tokens out)"
          + (f" ({len(left_out)} files as diff only)" if left_out else ""), flush=True)
    info = {"model": model, "files": len(names), "left_out": left_out, "cost": 0.0}
    if dry_run:
        return "", info
    # A review thinks longer than a stage's call: its own room and patience for that.
    client = llm.LLM({**cfg, "llm": {**cfg["llm"], "thinking_max_tokens": rc["thinking_tokens"],
                                     "timeout_s": rc["timeout_s"]}})
    try:
        text, _ = client.text_call(model, system, user, rc["effort"], rc["max_tokens"])
    except RuntimeError as e:  # HTTP errors and running out of room: one line, not a traceback
        raise ClipperError(f"codereview: {e}") from None
    if not text:
        raise ClipperError(f"{model} gave no review (an empty answer or a refusal)")
    return text, {**info, "cost": client.cost}


# ---------- where it goes ----------

def linked(text: str, repo: str) -> list[int]:
    """Issue or PR numbers text names in this repo (#12, owner/name#12, an issue link), in order."""
    nums = [int(n) for r, n in REF.findall(text) if not r or r.lower() == repo.lower()]
    nums += [int(n) for r, n in URL.findall(text) if r.lower() == repo.lower()]
    return list(dict.fromkeys(nums))


def comment(text: str, info: dict, sha: str, pr: int | None = None, failed: str = "") -> str:
    """A comment's body: its marker (the PR's, or a linked issue's for pr), then the review."""
    marker = ISSUE_MARKER.format(pr=pr) if pr else MARKER
    what = f"Code review of #{pr}" if pr else "Code review"
    head = f"{marker}\n**{what}** by `{info.get('model', '?')}` at {sha[:7]}"
    if failed:
        return f"{head}\n\nReview failed: {failed}\n"
    foot = [f"{info.get('files', 0)} files", f"${info.get('cost', 0.0):.3f}"]
    if info.get("left_out"):
        foot.append("diff only for " + ", ".join(f"`{p}`" for p in info["left_out"]))
    return (f"{head}\n\n{text.strip()}\n\n<sub>{' · '.join(foot)} · a second opinion: check each "
            "finding against the code</sub>\n")


class GitHub:
    """The few REST and GraphQL calls a review needs, as GITHUB_TOKEN (or GH_TOKEN)."""

    def __init__(self, repo: str, token: str):
        import requests

        if not repo or "/" not in repo:
            raise ClipperError("codereview --pr: say which repo with --repo owner/name (or $GITHUB_REPOSITORY)")
        if not token:
            raise ClipperError("codereview --pr: set GITHUB_TOKEN (or GH_TOKEN) to post on GitHub")
        self.repo, self.s = repo, requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})

    def call(self, method: str, path: str, ok404: bool = False, **kw):
        r = self.s.request(method, API + path, timeout=30, **kw)
        if ok404 and r.status_code == 404:
            return None
        if not r.ok:
            raise ClipperError(f"GitHub {method} {path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def pr(self, n: int) -> dict:
        return self.call("GET", f"/repos/{self.repo}/pulls/{n}")

    def closing(self, n: int) -> list[int]:
        """The issues GitHub links to PR n as closed by it (Closes #N, or linked by hand)."""
        owner, name = self.repo.split("/", 1)
        q = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)"
             "{closingIssuesReferences(first:25){nodes{number}}}}}")
        data = self.call("POST", "/graphql", json={"query": q, "variables": {"o": owner, "r": name, "n": n}})
        nodes = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get(
            "closingIssuesReferences", {}).get("nodes", [])
        return [x["number"] for x in nodes]

    def is_issue(self, n: int) -> bool:
        got = self.call("GET", f"/repos/{self.repo}/issues/{n}", ok404=True)
        return bool(got) and "pull_request" not in got

    def upsert(self, n: int, marker: str, body: str) -> str:
        """Edit the comment on issue/PR n that starts with marker, or add one. Returns its url."""
        page = 1
        while True:
            got = self.call("GET", f"/repos/{self.repo}/issues/{n}/comments",
                            params={"per_page": 100, "page": page})
            for c in got:
                if (c.get("body") or "").startswith(marker + "\n"):
                    return self.call("PATCH", f"/repos/{self.repo}/issues/comments/{c['id']}",
                                     json={"body": body})["html_url"]
            if len(got) < 100:
                break
            page += 1
        return self.call("POST", f"/repos/{self.repo}/issues/{n}/comments", json={"body": body})["html_url"]


def issues_for(gh: GitHub, pr: int, info: dict) -> list[int]:
    """The issues PR pr links: GitHub's closing references, then #N in its title and body."""
    named = linked(f"{info.get('title') or ''}\n{info.get('body') or ''}", gh.repo)
    out = list(dict.fromkeys(gh.closing(pr) + [n for n in named if n != pr]))
    return [n for n in out if gh.is_issue(n)]


def run(cfg: dict, base: str, head: str, pr: int | None = None, repo: str = "",
        dry_run: bool = False, root: Path = Path(".")) -> None:
    """Review the branch; print it, or with pr post it on the PR and the issues it links."""
    if pr is None or dry_run:
        text, info = review(cfg, base, head, root, dry_run)
        if not info:
            print("codereview: no code changed: nothing to review")
        elif text:
            print(text)
            print(f"\ncodereview: ${info['cost']:.3f}")
        return
    gh = GitHub(repo or os.environ.get("GITHUB_REPOSITORY", ""),
                os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", ""))
    pinfo = gh.pr(pr)
    sha = pinfo["head"]["sha"]
    failed = ""
    try:
        text, info = review(cfg, base, head, root)
    except Exception as e:  # noqa: BLE001  # say so on the PR; a review never fails its checks
        text, info, failed = "", {"model": cfg["codereview"]["reviewer"]}, f"{type(e).__name__}: {e}"[:400]
    if not info and not failed:
        print("codereview: no code changed: nothing to post")
        return
    print(f"PR #{pr}: {gh.upsert(pr, MARKER, comment(text, info, sha, failed=failed))}")
    for n in issues_for(gh, pr, pinfo):
        print(f"issue #{n}: {gh.upsert(n, ISSUE_MARKER.format(pr=pr), comment(text, info, sha, pr, failed))}")
