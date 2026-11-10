import subprocess

import pytest

from clipper import codereview, llm
from clipper.errors import ClipperError

INFO = {"model": "openrouter:x-ai/grok-4.7", "files": 3, "cost": 0.0123, "left_out": ["big.py"],
        "sha": "abcdef1234", "digest": "0123456789abcdef"}


def test_a_fence_outlasts_the_backticks_inside():
    assert codereview.fence("plain") == "```"
    assert codereview.fence("a ```md``` block and ````four````") == "`````"


def test_message_keeps_files_while_they_fit():
    files = {"a.py": "x" * 40, "b.py": "y" * 100, "c.py": "z" * 10}
    msg, left = codereview.message("d" * 50, files, max_chars=110, unreadable=["logo.png"])
    assert left == ["logo.png", "b.py"]           # 50 + 40 fits, +100 doesn't, +10 does
    assert "## a.py" in msg and "## c.py" in msg and "## b.py" not in msg
    assert "Left out for size (see the diff only): b.py" in msg
    assert "Not text, so the diff only: logo.png" in msg


def test_a_diff_over_the_limit_is_cut_and_says_so():
    msg, left = codereview.message("d" * 500, {"a.py": "x"}, max_chars=100)
    assert "the diff is cut here" in msg and left == ["a.py"]


def test_linked_takes_this_repos_numbers_once_in_order():
    text = ("Closes #70, part of #66 and byvfx/clipper#12; not other/repo#5. See "
            "https://github.com/byvfx/clipper/issues/70 and https://github.com/x/y/issues/9. "
            "Not a ref: abc#3, a/b/#4")
    assert codereview.linked(text, "byvfx/clipper") == [70, 66, 12]


def test_comment_bodies_start_with_their_marker_and_digest():
    pr = codereview.comment("### 1. a bug", INFO)
    assert pr.startswith(codereview.MARKER + "\n<!-- diff=0123456789abcdef -->\n")
    assert "at abcdef1" in pr and "$0.012" in pr and "`big.py`" in pr and "### 1. a bug" in pr
    issue = codereview.comment("### 1. a bug", INFO, pr=191)
    assert issue.startswith("<!-- clipper-codereview pr=191 -->\n") and "Code review of #191" in issue


def test_a_failed_review_keeps_the_last_good_one_and_no_digest():
    good = codereview.comment("### 1. a bug", INFO)
    failed = codereview.comment("", INFO, failed="RuntimeError: HTTP 502", previous=codereview.last_good(good))
    assert "Review failed: RuntimeError: HTTP 502" in failed and "diff=" not in failed
    assert "### 1. a bug" in failed.split(codereview.LAST)[1]
    again = codereview.comment("", INFO, failed="timeout", previous=codereview.last_good(failed))
    assert "### 1. a bug" in again and again.count(codereview.LAST) == 1   # kept, not nested
    assert codereview.last_good(codereview.comment("", INFO, failed="x")) == ""


def test_a_long_review_is_cut_to_fit_a_comment():
    body = codereview.comment("y" * 100_000, INFO)
    assert len(body) <= codereview.BODY_MAX and "the whole review is in the workflow's log" in body


class FakeGitHub(codereview.GitHub):
    def __init__(self, comments, graphql=None):
        self.repo, self.comments, self.graphql, self.sent, self._me = "o/r", comments, graphql, [], None

    def call(self, method, path, ok404=False, **kw):
        self.sent.append((method, path, kw.get("json")))
        if path == "/user":
            raise ClipperError("HTTP 403")                # GITHUB_TOKEN can't read it
        if path == "/graphql":
            return self.graphql
        if method == "GET":
            page = kw["params"]["page"]
            return self.comments[(page - 1) * 100:page * 100]
        return {"html_url": f"https://x/{method}"}


def bot(i, body):
    return {"id": i, "body": body, "user": {"login": codereview.BOT}}


def test_find_takes_only_our_own_comment_with_the_marker():
    mine = codereview.ISSUE_MARKER.format(pr=191)
    other = codereview.ISSUE_MARKER.format(pr=19)     # pr=19 is not a prefix match for pr=191
    chat = [bot(i, "chat") for i in range(150)]
    quoted = {"id": 6, "body": mine + "\nquoted by a person", "user": {"login": "brandon"}}
    gh = FakeGitHub(chat + [quoted, bot(7, other + "\nold"), bot(8, mine + "\nold")])
    assert gh.find(70, mine)["id"] == 8
    assert gh.upsert(70, "new", gh.find(70, mine)) == "https://x/PATCH"
    assert gh.sent[-1] == ("PATCH", "/repos/o/r/issues/comments/8", {"body": "new"})
    assert FakeGitHub([quoted]).find(70, mine) is None


def test_closing_keeps_this_repos_issues_and_survives_nulls():
    nodes = [{"number": 70, "repository": {"nameWithOwner": "O/R"}},
             {"number": 5, "repository": {"nameWithOwner": "o/other"}}, None]
    gh = FakeGitHub([], {"data": {"repository": {"pullRequest": {"closingIssuesReferences": {"nodes": nodes}}}}})
    assert gh.closing(1) == [70]
    for broken in ({"data": {"repository": {"pullRequest": {"closingIssuesReferences": None}}}},
                   {"data": {"repository": {"pullRequest": {"closingIssuesReferences": {"nodes": None}}}}},
                   {"data": None, "errors": [{"message": "x"}]}):
        assert FakeGitHub([], broken).closing(1) == []


@pytest.fixture
def repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path,
                       check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    (tmp_path / "clipper").mkdir()
    (tmp_path / "clipper" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("old\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "work")
    return tmp_path, git


def test_gather_sends_only_code_and_the_changed_files(repo, defaults):
    root, git = repo
    assert codereview.gather(defaults, "main", "HEAD", root) == {}
    (root / "notes.md").write_text("new\n", encoding="utf-8")
    git("commit", "-qam", "notes only")
    assert codereview.gather(defaults, "main", "HEAD", root) == {}      # no code changed
    (root / "clipper" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (root / "clipper" / "café.py").write_text("y = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "code")
    g = codereview.gather(defaults, "main", "HEAD", root)
    assert g["names"] == ["clipper/a.py", "clipper/café.py"] and not g["left_out"]   # not quoted
    assert "+x = 2" in g["user"] and "## clipper/café.py (whole file" in g["user"]
    assert "notes.md" not in g["user"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    assert g["sha"] == head
    (root / "clipper" / "a.py").write_text("x = 3\n", encoding="utf-8")   # not committed
    g2 = codereview.gather(defaults, "main", "HEAD", root)
    assert "x = 3" not in g2["user"] and g2["digest"] == g["digest"]     # the file as head has it
    git("checkout", "--", "clipper/a.py")
    (root / "notes.md").write_text("newer\n", encoding="utf-8")
    git("commit", "-qam", "more notes")
    assert codereview.gather(defaults, "main", "HEAD", root)["digest"] == g["digest"]   # docs: same diff


def test_the_reviewer_is_no_stage_model(defaults):
    """doctor and `clipper models` read llm.configured: a dev tool's model mustn't make every
    install need an OpenRouter key."""
    assert defaults["codereview"]["reviewer"].startswith("openrouter:")
    assert not any(k.startswith("codereview.") for k in llm.configured(defaults))


def test_a_timed_out_long_call_is_not_sent_again(monkeypatch, defaults):
    import requests

    tries = []

    def post(*a, **kw):
        tries.append(kw["timeout"])
        raise requests.ReadTimeout("slow")

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    with pytest.raises(RuntimeError, match="ReadTimeout"):
        llm.LLM(defaults).text_call("openrouter:x/y", "s", "u", "high", 100, thinking_tokens=10, timeout_s=5)
    assert tries == [5]
