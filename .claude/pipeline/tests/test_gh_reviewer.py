"""The reviewer bot's wrapper, .claude/bin/gh-reviewer, runs only the review's own calls.

    python -m unittest discover -s .claude/pipeline/tests

The wrapper holds the bot token, and the bot's approval is the only one that
merges (docs/Pipeline.md § Merging). Each case runs the real wrapper with a stub
`gh` first on PATH: exit 4 is the wrapper's refusal, exit 0 means the call
reached the stub -- that is, it would have run as the bot.
"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline as p  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / ".claude" / "bin" / "gh-reviewer"
# Not PATH's bash: on Windows that can be WSL's, which cannot read this checkout.
BASH = p.bash_path()

# Calls the review skill makes (.claude/skills/github-pr-review/SKILL.md).
ALLOWED = [
    ["api", "user"],
    ["api", "user", "--jq", ".login"],
    ["api", "repos/{owner}/{repo}/pulls/105/reviews",
     "-f", "commit_id=abc", "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "/repos/{owner}/{repo}/pulls/105/reviews", "-f", "commit_id=" + "a" * 40,
     "-f", "event=REQUEST_CHANGES", "-F", "body=@.pipeline-tmp/review-105.md"],
    ["api", "repos/{owner}/{repo}/pulls/105/reviews",
     "-f", "commit_id=abc", "-f", "event=COMMENT", "-F", "body=@.pipeline-tmp/review-105.md"],
    ["pr", "merge", "105", "--auto", "--squash", "--delete-branch", "--match-head-commit", "abc"],
    ["pr", "merge", "105", "--squash", "--delete-branch", "--match-head-commit", "abc"],
    ["pr", "edit", "105", "--add-label", "status:needs-human"],
    ["pr", "edit", "105", "--add-label", "spec-defect"],
    ["pr", "view", "105"],
]

# The bypasses issue #21 found, and their neighbours.
REFUSED = [
    # A literal owner/repo names another repository (#59): only gh's placeholders.
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "repos/my-org/my.repo/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    ["api", "repos/{owner}/other/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    ["api", "repos/other/{repo}/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    # 1. The endpoint was a glob: anything ending in /pulls/<digit>.../reviews.
    ["api", "repos/o/r/contents/.github/workflows/ci.yml?a=/pulls/1/reviews",
     "-X", "PUT", "-f", "message=x", "-f", "content=eA==", "-f", "branch=main"],
    ["api", "repos/o/r/git/refs?x=/pulls/1/reviews", "-X", "POST", "-f", "ref=refs/heads/x"],
    ["api", "repos/o/r/pulls/1?/reviews", "-X", "PATCH", "-f", "base=other"],
    ["api", "repos/o/r/pulls/1/../../git/refs/pulls/1/reviews"],
    ["api", "repos/o/r/pulls/1/reviews?x=1"],
    ["api", "repos/o/r/pulls/1/reviews", "-X", "PUT"],
    ["api", "repos/o/r/pulls/1/reviews", "--input", "x.json"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "event=APPROVE", "--hostname", "evil.example"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "merge_method=squash"],
    ["api", "repos/o/r/pulls/abc/reviews"],
    # The owner and repo segments: no `.`/`..`, no percent-encoding.
    ["api", "repos/../x/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "repos/o/./pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "repos/o%2F..%2Fx/r/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    # The review's fields: `-F` reads the file it names, and a review body is public.
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE",
     "-F", "body=@/Users/x/.config/token.md"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE",
     "-F", "body=@../../.config/token.md"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE",
     "-F", "body=@.pipeline-tmp/../../token.md"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE",
     "-F", "body=@reviewer-token"],
    ["api", "repos/o/r/pulls/1/reviews", "-F", "commit_id=@token.md", "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-F", "event=@token.md",
     "-F", "body=@x.md"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=DISMISS",
     "-F", "body=@x.md"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=main", "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE",
     "-F", "body=@x.md", "-f", "event=COMMENT"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-f", "event=APPROVE",
     "-f", "body=x"],
    # A flag glued to a value in one word: only the next word would be checked.
    ["api", "repos/o/r/pulls/1/reviews", "-F body=@/Users/x/.config/token", "body=@x.md",
     "-f", "commit_id=abc", "-f", "event=APPROVE"],
    ["api", "repos/o/r/pulls/1/reviews", "-f commit_id=zz", "commit_id=abc",
     "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=abc", "-f event=x", "event=APPROVE",
     "-F", "body=@x.md"],
    ["pr", "view", "105", "-R", "other/repo"],
    ["pr", "view", "105", "--web"],
    # 2. `api user` with any method and any fields.
    ["api", "user", "-X", "PATCH", "-f", "bio=x"],
    ["api", "user", "--jq", ".login", "-X", "PATCH"],
    ["api", "user", "--method", "PATCH"],
    # 3. `pr merge` and `pr edit` with any flags.
    ["pr", "merge", "105", "--admin", "--squash", "--delete-branch", "--match-head-commit", "abc"],
    ["pr", "merge", "105", "--squash", "--delete-branch"],
    ["pr", "merge", "105", "--merge", "--delete-branch", "--match-head-commit", "abc"],
    ["pr", "merge", "105", "--squash", "--delete-branch", "--match-head-commit", "main"],
    ["pr", "merge", "--squash", "--delete-branch", "--match-head-commit", "abc"],
    ["pr", "edit", "105", "--base", "other"],
    ["pr", "edit", "105", "--add-label", "status:ready"],
    ["pr", "edit", "105", "--add-label", "spec-defect", "--base", "other"],
    ["pr", "edit", "105", "--title", "x"],
    # Never part of the review: an unpinned approval, and anything else.
    ["pr", "review", "105", "--approve"],
    ["pr", "comment", "105", "--body", "x"],
    ["auth", "token"],
    ["repo", "delete", "o/r", "--yes"],
    ["api", "repos/o/r", "-X", "DELETE"],
    [],
]


@unittest.skipUnless(BASH, "bash not found")
class GhReviewerAllowlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        stub = Path(cls.tmp.name) / "gh"
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        cls.env = {**os.environ, "PATH": cls.tmp.name + os.pathsep + os.environ.get("PATH", ""),
                   "PIPELINE_REVIEWER_TOKEN": "x"}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_wrapper(self, args):
        return subprocess.run([BASH, str(WRAPPER), *args], env=self.env,
                              capture_output=True, text=True).returncode

    def test_the_reviews_own_calls_reach_gh(self):
        for args in ALLOWED:
            with self.subTest(args=" ".join(args)):
                self.assertEqual(self.run_wrapper(args), 0)

    def test_everything_else_is_refused(self):
        for args in REFUSED:
            with self.subTest(args=" ".join(args)):
                self.assertEqual(self.run_wrapper(args), 4)

    def test_the_callers_environment_does_not_reach_gh(self):
        # gh reads these from the environment, where no argument check sees
        # them: GH_REPO alone sends `pr merge` to another repository.
        names = ["GH_REPO", "GH_HOST", "GH_CONFIG_DIR", "GH_PAGER", "PAGER", "GH_BROWSER",
                 "BROWSER", "SSL_CERT_FILE", "SSL_CERT_DIR"]
        with tempfile.TemporaryDirectory() as d:
            stub = Path(d) / "gh"
            stub.write_text("#!/bin/sh\nenv\n", encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
            env = {**os.environ, "PATH": d + os.pathsep + os.environ.get("PATH", ""),
                   "PIPELINE_REVIEWER_TOKEN": "x", **{n: "marker-" + n for n in names}}
            out = subprocess.run([BASH, str(WRAPPER), "pr", "view", "105"], env=env,
                                 capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        seen = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
        for name in names:
            with self.subTest(name=name):
                self.assertNotIn(name, seen)
        self.assertNotIn("marker-", out.stdout)
        self.assertEqual(seen.get("GH_TOKEN"), "x")


if __name__ == "__main__":
    unittest.main()
