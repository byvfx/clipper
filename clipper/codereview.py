"""clipper codereview: a second reviewer for a branch, in one model call ([codereview]).

The branch's code diff against the base (origin/main) and the changed files in full go to
[codereview] reviewer (Grok 4.7 through OpenRouter by default) with the codereview prompt;
the answer is a Markdown list of correctness bugs. One call, no tool loop: the model reads
what it is sent and nothing else.

With --pr (the `review` workflow, on every PR) the review goes on the PR as one comment,
edited in place on each push, and on every issue the PR links (GitHub's closing references
in this repo, or #N in its title or body): one comment per issue and PR, edited the same way.
A push that leaves the code diff as it was (a docs-only push) is not reviewed again: the
comment carries the diff's digest. A failed call says so and keeps the last good review
below; the review is printed to the log before it is posted, so a failed post loses nothing.
"""
from __future__ import annotations

import hashlib
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
DIGEST = "<!-- diff={} -->"  # the code diff a review covered (a failed one has none)
LAST = "<details><summary>The last review, still standing</summary>"
BODY_MAX = 65000  # GitHub refuses a comment over 65536 characters
BOT = "github-actions[bot]"  # who GITHUB_TOKEN posts as (it can't read /user)
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


def message(diff: str, files: dict[str, str], max_chars: int,
            unreadable: tuple[str, ...] | list[str] = ()) -> tuple[str, list[str]]:
    """The user message: the diff, then each changed file in full while the whole stays under
    max_chars. Returns (message, the files left out: unreadable ones, then those past the
    size). A diff alone over max_chars is cut."""
    if len(diff) > max_chars:
        diff = diff[:max_chars] + "\n[... the diff is cut here: it is longer than [codereview] max_chars]\n"
    f = fence(diff)
    parts, size, big = [f"## The diff\n\n{f}diff\n{diff}{f}\n"], len(diff), []
    for path, text in files.items():
        if size + len(text) > max_chars:
            big.append(path)
            continue
        f = fence(text)
        parts.append(f"## {path} (whole file, after the change)\n\n{f}\n{text}{f}\n")
        size += len(text)
    if unreadable:
        parts.append("Not text, so the diff only: " + ", ".join(unreadable) + "\n")
    if big:
        parts.append("Left out for size (see the diff only): " + ", ".join(big) + "\n")
    return "\n".join(parts), [*unreadable, *big]


def read_files(root: Path, head: str, names: list[str]) -> tuple[dict[str, str], list[str]]:
    """The text of each changed file at head (not the working tree), and the names that
    aren't text there (binary, or not readable)."""
    out, unreadable = {}, []
    for name in names:
        r = subprocess.run(["git", "show", f"{head}:{name}"], cwd=root, capture_output=True)
        try:
            text = r.stdout.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        if r.returncode == 0 and text and "\0" not in text:
            out[name] = text
        else:
            unreadable.append(name)
    return out, unreadable


def gather(cfg: dict, base: str, head: str, root: Path) -> dict:
    """{user (the message), names, left_out, sha (head's commit), digest (of the code diff)};
    {} when no code changed."""
    rc = cfg["codereview"]
    span = f"{base}...{head}"
    diff = git(root, "diff", "--no-color", span, "--", *rc["paths"])
    if not diff.strip():
        return {}
    # -z: names as they are, not quoted with octal escapes (core.quotepath)
    names = [n for n in git(root, "diff", "--name-only", "-z", "--diff-filter=d", span, "--",
                            *rc["paths"]).split("\0") if n]
    files, unreadable = read_files(root, head, names)
    user, left_out = message(diff, files, rc["max_chars"], unreadable)
    return {"user": user, "names": names, "left_out": left_out,
            "sha": git(root, "rev-parse", "--verify", f"{head}^{{commit}}").strip(),
            "digest": hashlib.sha256(diff.encode("utf-8")).hexdigest()[:16]}


def call(cfg: dict, g: dict, dry_run: bool = False) -> tuple[str, dict]:
    """The model's review of what gather sent, and {model, files, left_out, sha, digest, cost}."""
    rc = cfg["codereview"]
    model = rc["reviewer"]
    system = Prompts(cfg).get("codereview")
    tin = llm.tokens(system + g["user"])
    est = llm.estimate(cfg, model, tin, rc["max_tokens"])
    print(f"codereview: {len(g['names'])} files, about {tin:,} tokens in, about ${est:.3f} on {model} "
          f"plus its thinking (up to {rc['thinking_tokens']:,} tokens out)"
          + (f" ({len(g['left_out'])} files as diff only)" if g["left_out"] else ""), flush=True)
    info = {"model": model, "files": len(g["names"]), "left_out": g["left_out"], "sha": g["sha"],
            "digest": g["digest"], "cost": 0.0}
    if dry_run:
        return "", info
    client = llm.LLM(cfg)
    try:
        text, _ = client.text_call(model, system, g["user"], rc["effort"], rc["max_tokens"],
                                   thinking_tokens=rc["thinking_tokens"], timeout_s=rc["timeout_s"])
    except RuntimeError as e:  # HTTP errors, a timeout, running out of room: one line
        raise ClipperError(f"codereview: {e}") from None
    if not text:
        raise ClipperError(f"{model} gave no review (an empty answer or a refusal)")
    return text, {**info, "cost": client.cost}


def review(cfg: dict, base: str = "origin/main", head: str = "HEAD", root: Path = Path("."),
           dry_run: bool = False) -> tuple[str, dict]:
    """The review as Markdown, and its info; ("", {}) when no code changed."""
    g = gather(cfg, base, head, root)
    return call(cfg, g, dry_run) if g else ("", {})


# ---------- where it goes ----------

def linked(text: str, repo: str) -> list[int]:
    """Issue or PR numbers text names in this repo (#12, owner/name#12, an issue link), in order."""
    nums = [int(n) for r, n in REF.findall(text) if not r or r.lower() == repo.lower()]
    nums += [int(n) for r, n in URL.findall(text) if r.lower() == repo.lower()]
    return list(dict.fromkeys(nums))


def last_good(body: str) -> str:
    """The review a comment shows, for keeping under a failed one: a good comment's own text,
    or the review a failed one kept; "" when there is none."""
    if "\nReview failed: " in body:
        m = re.search(re.escape(LAST) + r"\n\n(.*)\n</details>", body, re.DOTALL)
        return m[1] if m else ""
    return "\n".join(line for line in body.splitlines() if not line.startswith("<!-- ")).strip()


def comment(text: str, info: dict, pr: int | None = None, failed: str = "", previous: str = "") -> str:
    """A comment's body: its marker (the PR's, or a linked issue's for pr), the digest of the
    diff it reviewed, then the review. failed: why the call failed, with `previous` (the last
    good review) kept below it. Never over GitHub's size limit."""
    marker = ISSUE_MARKER.format(pr=pr) if pr else MARKER
    what = f"Code review of #{pr}" if pr else "Code review"
    head = f"**{what}** by `{info.get('model', '?')}` at {info.get('sha', '?')[:7]}"
    if failed:
        body = f"{marker}\n{head}\n\nReview failed: {failed}\n"
        if previous:
            body += f"\n{LAST}\n\n{previous}\n</details>\n"
        return body[:BODY_MAX]
    foot = [f"{info.get('files', 0)} files", f"${info.get('cost', 0.0):.3f}"]
    if info.get("left_out"):
        foot.append("diff only for " + ", ".join(f"`{p}`" for p in info["left_out"]))
    top = f"{marker}\n{DIGEST.format(info.get('digest', ''))}\n{head}\n\n"
    tail = f"\n\n<sub>{' · '.join(foot)} · a second opinion: check each finding against the code</sub>\n"
    review = text.strip()
    room = BODY_MAX - len(top) - len(tail)
    if len(review) > room:
        cut = "\n\n[... cut: longer than a GitHub comment; the whole review is in the workflow's log]"
        review = review[:room - len(cut)] + cut
    return top + review + tail


class GitHub:
    """The few REST and GraphQL calls a review needs, as GITHUB_TOKEN (or GH_TOKEN)."""

    def __init__(self, repo: str, token: str):
        import requests

        if not repo or "/" not in repo:
            raise ClipperError("codereview --pr: say which repo with --repo owner/name (or $GITHUB_REPOSITORY)")
        if not token:
            raise ClipperError("codereview --pr: set GITHUB_TOKEN (or GH_TOKEN) to post on GitHub")
        self.repo, self.s, self._me = repo, requests.Session(), None
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})

    def call(self, method: str, path: str, ok404: bool = False, **kw):
        r = self.s.request(method, API + path, timeout=30, **kw)
        if ok404 and r.status_code == 404:
            return None
        if not r.ok:
            raise ClipperError(f"GitHub {method} {path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    @property
    def me(self) -> str:
        """The login comments are posted as: the token's user, or the Actions bot (whose
        token can't read /user)."""
        if self._me is None:
            try:
                self._me = self.call("GET", "/user")["login"]
            except ClipperError:
                self._me = BOT
        return self._me

    def pr(self, n: int) -> dict:
        return self.call("GET", f"/repos/{self.repo}/pulls/{n}")

    def closing(self, n: int) -> list[int]:
        """The issues in this repo GitHub links to PR n as closed by it (Closes #N, or linked
        by hand); one in another repo is left out, its number means another issue here."""
        owner, name = self.repo.split("/", 1)
        q = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)"
             "{closingIssuesReferences(first:25){nodes{number repository{nameWithOwner}}}}}}")
        data = self.call("POST", "/graphql", json={"query": q, "variables": {"o": owner, "r": name, "n": n}})
        refs = ((((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
                .get("closingIssuesReferences") or {})
        return [x["number"] for x in refs.get("nodes") or []
                if x and ((x.get("repository") or {}).get("nameWithOwner") or "").lower() == self.repo.lower()]

    def is_issue(self, n: int) -> bool:
        got = self.call("GET", f"/repos/{self.repo}/issues/{n}", ok404=True)
        return bool(got) and "pull_request" not in got

    def find(self, n: int, marker: str) -> dict | None:
        """Our comment on issue/PR n that starts with marker (another author's never counts)."""
        page = 1
        while True:
            got = self.call("GET", f"/repos/{self.repo}/issues/{n}/comments",
                            params={"per_page": 100, "page": page})
            for c in got:
                if ((c.get("body") or "").startswith(marker + "\n")
                        and (c.get("user") or {}).get("login") == self.me):
                    return c
            if len(got) < 100:
                return None
            page += 1

    def upsert(self, n: int, body: str, old: dict | None) -> str:
        """Edit old (find's answer) to body, or add a comment. Returns its url."""
        if old:
            return self.call("PATCH", f"/repos/{self.repo}/issues/comments/{old['id']}", json={"body": body})["html_url"]
        return self.call("POST", f"/repos/{self.repo}/issues/{n}/comments", json={"body": body})["html_url"]


def issues_for(gh: GitHub, pr: int, info: dict) -> list[int]:
    """The issues PR pr links: GitHub's closing references, then #N in its title and body."""
    named = linked(f"{info.get('title') or ''}\n{info.get('body') or ''}", gh.repo)
    out = list(dict.fromkeys(gh.closing(pr) + [n for n in named if n != pr]))
    return [n for n in out if gh.is_issue(n)]


def post(gh: GitHub, n: int, marker: str, text: str, info: dict, pr: int | None, failed: str) -> str:
    """Put the review (or the failure, the last good review kept) in our comment on n."""
    old = gh.find(n, marker)
    previous = last_good(old["body"]) if failed and old else ""
    return gh.upsert(n, comment(text, info, pr, failed, previous), old)


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
    text, failed, g = "", "", {}
    info = {"model": cfg["codereview"]["reviewer"], "sha": pinfo["head"]["sha"]}
    try:
        g = gather(cfg, base, head, root)
        if not g:
            print("codereview: no code changed: nothing to post")
            return
        old = gh.find(pr, MARKER)
        if old and DIGEST.format(g["digest"]) in old["body"]:
            print(f"codereview: the code diff is the one already reviewed ({g['digest']}): nothing to do")
            return
        text, info = call(cfg, g)
    except Exception as e:  # noqa: BLE001  # say so on the PR; a review never fails its checks
        failed = f"{type(e).__name__}: {e}"[:400]
        info = {**info, "sha": g.get("sha", info["sha"])}
    print(text or f"codereview: review failed: {failed}", flush=True)  # in the log before any post
    print(f"PR #{pr}: {post(gh, pr, MARKER, text, info, None, failed)}")
    errors = []
    for n in issues_for(gh, pr, pinfo):
        try:
            print(f"issue #{n}: {post(gh, n, ISSUE_MARKER.format(pr=pr), text, info, pr, failed)}")
        except ClipperError as e:
            errors.append(f"#{n}: {e}")
    if errors:
        raise ClipperError("codereview: not posted on " + "; ".join(errors))
