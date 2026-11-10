import subprocess

from clipper import codereview, llm


def test_a_fence_outlasts_the_backticks_inside():
    assert codereview.fence("plain") == "```"
    assert codereview.fence("a ```md``` block and ````four````") == "`````"


def test_message_keeps_files_while_they_fit():
    files = {"a.py": "x" * 40, "b.py": "y" * 100, "c.py": "z" * 10}
    msg, left = codereview.message("d" * 50, files, max_chars=110)
    assert left == ["b.py"]                       # 50 + 40 fits, +100 doesn't, +10 does
    assert "## a.py" in msg and "## c.py" in msg and "## b.py" not in msg
    assert "Left out for size (see the diff only): b.py" in msg


def test_a_diff_over_the_limit_is_cut_and_says_so():
    msg, left = codereview.message("d" * 500, {"a.py": "x"}, max_chars=100)
    assert "the diff is cut here" in msg and left == ["a.py"]


def test_linked_takes_this_repos_numbers_once_in_order():
    text = ("Closes #70, part of #66 and byvfx/clipper#12; not other/repo#5. See "
            "https://github.com/byvfx/clipper/issues/70 and https://github.com/x/y/issues/9. "
            "Not a ref: abc#3, a/b/#4")
    assert codereview.linked(text, "byvfx/clipper") == [70, 66, 12]


def test_comment_bodies_start_with_their_marker():
    info = {"model": "openrouter:deepseek/deepseek-v4-pro", "files": 3, "cost": 0.0123,
            "left_out": ["big.py"]}
    pr = codereview.comment("### 1. a bug", info, "abcdef1234")
    assert pr.startswith(codereview.MARKER + "\n") and "at abcdef1" in pr
    assert "$0.012" in pr and "`big.py`" in pr and "### 1. a bug" in pr
    issue = codereview.comment("### 1. a bug", info, "abcdef1234", pr=191)
    assert issue.startswith("<!-- clipper-codereview pr=191 -->\n") and "Code review of #191" in issue
    failed = codereview.comment("", info, "abcdef1234", failed="RuntimeError: HTTP 502")
    assert "Review failed: RuntimeError: HTTP 502" in failed


class FakeGitHub(codereview.GitHub):
    def __init__(self, comments):
        self.repo, self.comments, self.sent = "o/r", comments, []

    def call(self, method, path, ok404=False, **kw):
        self.sent.append((method, path, kw.get("json")))
        if method == "GET":
            page = kw["params"]["page"]
            return self.comments[(page - 1) * 100:page * 100]
        return {"html_url": f"https://x/{method}"}


def test_upsert_edits_its_own_comment_and_only_that():
    mine = codereview.ISSUE_MARKER.format(pr=191)
    other = codereview.ISSUE_MARKER.format(pr=19)    # pr=19 is not a prefix match for pr=191
    gh = FakeGitHub([{"id": i, "body": "chat"} for i in range(150)] + [{"id": 7, "body": other + "\nold"},
                                                                       {"id": 8, "body": mine + "\nold"}])
    assert gh.upsert(70, mine, mine + "\nnew") == "https://x/PATCH"
    assert gh.sent[-1] == ("PATCH", "/repos/o/r/issues/comments/8", {"body": mine + "\nnew"})
    gh = FakeGitHub([{"id": 1, "body": other + "\nold"}])
    assert gh.upsert(70, mine, mine + "\nnew") == "https://x/POST"


def test_gather_sends_only_code_and_the_changed_files(tmp_path, defaults):
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
    assert codereview.gather(defaults, "main", "HEAD", tmp_path) == ("", [], [])
    (tmp_path / "notes.md").write_text("new\n", encoding="utf-8")
    git("commit", "-qam", "notes only")
    assert codereview.gather(defaults, "main", "HEAD", tmp_path)[0] == ""   # no code changed
    (tmp_path / "clipper" / "a.py").write_text("x = 2\n", encoding="utf-8")
    git("commit", "-qam", "code")
    msg, names, left = codereview.gather(defaults, "main", "HEAD", tmp_path)
    assert names == ["clipper/a.py"] and not left
    assert "+x = 2" in msg and "## clipper/a.py (whole file" in msg and "notes.md" not in msg
    (tmp_path / "clipper" / "a.py").write_text("x = 3\n", encoding="utf-8")   # not committed
    msg = codereview.gather(defaults, "main", "HEAD", tmp_path)[0]
    assert "x = 2\n" in msg.split("whole file")[1] and "x = 3" not in msg   # the file as head has it


def test_the_reviewer_is_no_stage_model(defaults):
    """doctor and `clipper models` read llm.configured: a dev tool's model mustn't make every
    install need an OpenRouter key."""
    assert defaults["codereview"]["reviewer"].startswith("openrouter:")
    assert not any(k.startswith("codereview.") for k in llm.configured(defaults))
