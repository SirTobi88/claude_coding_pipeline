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

# A full commit SHA: the wrapper takes nothing shorter (#63).
SHA = "0123456789abcdef0123456789abcdef01234567"

# Calls the review skill makes (.claude/skills/github-pr-review/SKILL.md).
ALLOWED = [
    ["api", "user"],
    ["api", "user", "--jq", ".login"],
    ["api", "repos/{owner}/{repo}/pulls/105/reviews",
     "-f", "commit_id=" + SHA, "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "/repos/{owner}/{repo}/pulls/105/reviews", "-f", "commit_id=" + "a" * 40,
     "-f", "event=REQUEST_CHANGES", "-F", "body=@.pipeline-tmp/review-105.md"],
    ["api", "repos/{owner}/{repo}/pulls/105/reviews",
     "-f", "commit_id=" + SHA, "-f", "event=COMMENT", "-F", "body=@.pipeline-tmp/review-105.md"],
    ["pr", "merge", "105", "--auto", "--squash", "--delete-branch", "--match-head-commit", SHA],
    ["pr", "merge", "105", "--squash", "--delete-branch", "--match-head-commit", SHA],
    ["pr", "edit", "105", "--add-label", "status:needs-human"],
    ["pr", "edit", "105", "--add-label", "spec-defect"],
]

# The bypasses issue #21 found, and their neighbours.
REFUSED = [
    # A literal owner/repo names another repository (#59): only gh's placeholders.
    ["api", "repos/o/r/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "repos/my-org/my.repo/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    ["api", "repos/{owner}/other/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    ["api", "repos/other/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    # 1. The endpoint was a glob: anything ending in /pulls/<digit>.../reviews.
    ["api", "repos/{owner}/{repo}/contents/.github/workflows/ci.yml?a=/pulls/1/reviews",
     "-X", "PUT", "-f", "message=x", "-f", "content=eA==", "-f", "branch=main"],
    ["api", "repos/{owner}/{repo}/git/refs?x=/pulls/1/reviews", "-X", "POST", "-f", "ref=refs/heads/x"],
    ["api", "repos/{owner}/{repo}/pulls/1?/reviews", "-X", "PATCH", "-f", "base=other"],
    ["api", "repos/{owner}/{repo}/pulls/1/../../git/refs/pulls/1/reviews"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews?x=1"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-X", "PUT"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "--input", "x.json"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "event=APPROVE", "--hostname", "evil.example"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "merge_method=squash"],
    ["api", "repos/{owner}/{repo}/pulls/abc/reviews"],
    # The owner and repo segments: no `.`/`..`, no percent-encoding.
    ["api", "repos/../x/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "repos/o/./pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "repos/o%2F..%2Fx/r/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    # The review's fields: `-F` reads the file it names, and a review body is public.
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE",
     "-F", "body=@/Users/x/.config/token.md"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE",
     "-F", "body=@../../.config/token.md"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE",
     "-F", "body=@.pipeline-tmp/../../token.md"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE",
     "-F", "body=@reviewer-token"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-F", "commit_id=@token.md", "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-F", "event=@token.md",
     "-F", "body=@x.md"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=DISMISS",
     "-F", "body=@x.md"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=main", "-f", "event=APPROVE",
     "-F", "body=@x.md"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE",
     "-F", "body=@x.md", "-f", "event=COMMENT"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f", "event=APPROVE",
     "-f", "body=x"],
    # A flag glued to a value in one word: only the next word would be checked.
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-F body=@/Users/x/.config/token", "body=@x.md",
     "-f", "commit_id=" + SHA, "-f", "event=APPROVE"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f commit_id=zz", "commit_id=" + SHA,
     "-f", "event=APPROVE", "-F", "body=@x.md"],
    ["api", "repos/{owner}/{repo}/pulls/1/reviews", "-f", "commit_id=" + SHA, "-f event=x", "event=APPROVE",
     "-F", "body=@x.md"],
    ["pr", "view", "105"],                      # the skill reads with plain gh (#73)
    ["pr", "view", "105", "-R", "other/repo"],
    ["pr", "view", "105", "--web"],
    # 2. `api user` with any method and any fields.
    ["api", "user", "-X", "PATCH", "-f", "bio=x"],
    ["api", "user", "--jq", ".login", "-X", "PATCH"],
    ["api", "user", "--method", "PATCH"],
    # 3. `pr merge` and `pr edit` with any flags.
    ["pr", "merge", "105", "--admin", "--squash", "--delete-branch", "--match-head-commit", SHA],
    ["pr", "merge", "105", "--squash", "--delete-branch"],
    ["pr", "merge", "105", "--merge", "--delete-branch", "--match-head-commit", SHA],
    ["pr", "merge", "105", "--squash", "--delete-branch", "--match-head-commit", "main"],
    ["pr", "merge", "--squash", "--delete-branch", "--match-head-commit", SHA],
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
        # The wrapper finds gh on a fixed PATH, never the caller's, so the stub
        # cannot simply come first on PATH. A copy of the wrapper in a scratch
        # checkout looks in that checkout's .stub/ first, and nowhere else
        # differs.
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "repo"
        bindir = cls.root / ".claude" / "bin"
        bindir.mkdir(parents=True)
        text = WRAPPER.read_text(encoding="utf-8")
        marker = 'safe_path="/opt/homebrew/bin:'
        assert marker in text, "the wrapper's fixed PATH moved"
        cls.wrapper = bindir / "gh-reviewer"
        cls.wrapper.write_text(text.replace(marker, 'safe_path="$here/../../.stub:/opt/homebrew/bin:', 1),
                               encoding="utf-8")
        stubdir = cls.root / ".stub"
        stubdir.mkdir()
        stub = stubdir / "gh"
        stub.write_text('#!/bin/sh\necho "cwd=$(pwd)"\necho "args=$*"\n'
                        'for a in "$@"; do case "$a" in body=@*) '
                        'echo "body-file=${a#body=@}"; echo "body-content=$(cat "${a#body=@}")" ;; esac; done\n'
                        'env\n', encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        # MSYS=noglob: Python starts bash.exe with `repos/{owner}/{repo}/…`
        # unquoted, and Git Bash's runtime would brace-expand it to
        # `repos/owner/repo/…` before the wrapper sees it. Claude Code's Bash
        # tool hands bash one quoted string, so the agents' calls keep them.
        msys = " ".join(x for x in (os.environ.get("MSYS", ""), "noglob") if x)
        cls.env = {**os.environ, "PIPELINE_REVIEWER_TOKEN": "x", "MSYS": msys}
        # The review bodies the calls name: real regular files in the checkout,
        # since the wrapper resolves each one before gh may read it (#63).
        for body in ("x.md", ".pipeline-tmp/review-105.md",
                     ".claude/worktrees/review-105/.pipeline-tmp/review-105.md"):
            path = cls.root / body
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("report " + body + "\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def call(self, args, env=None, cwd=None):
        # From the scratch checkout's root unless a test says otherwise: a
        # review's body path is relative to where the caller stands.
        return subprocess.run([BASH, str(self.wrapper), *args], env=env or self.env,
                              cwd=str(cwd or self.root), capture_output=True, text=True)

    def run_wrapper(self, args):
        return self.call(args).returncode

    def test_the_reviews_own_calls_reach_gh(self):
        for args in ALLOWED:
            with self.subTest(args=" ".join(args)):
                self.assertEqual(self.run_wrapper(args), 0)

    def test_everything_else_is_refused(self):
        for args in REFUSED:
            with self.subTest(args=" ".join(args)):
                self.assertEqual(self.run_wrapper(args), 4)

    # What gh may see: what the wrapper sets, the network setup, what Windows
    # needs to start a program, and what bash itself exports.
    GH_SEES = {"PATH", "HOME", "GH_CONFIG_DIR", "GH_NO_UPDATE_NOTIFIER", "GH_PROMPT_DISABLED",
               "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
               "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
               "https_proxy", "http_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
               "SYSTEMROOT", "SystemRoot", "WINDIR", "windir", "COMSPEC", "ComSpec", "PATHEXT",
               "TEMP", "TMP", "TMPDIR", "PWD", "OLDPWD", "SHLVL", "_",
               # Windows: bash cannot unset these two, and MSYS sets MSYSTEM again.
               "PROGRAMFILES(X86)", "COMMONPROGRAMFILES(X86)", "MSYSTEM"}

    def seen(self, out):
        self.assertEqual(out.returncode, 0, out.stderr)
        return dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)

    def test_the_callers_environment_does_not_reach_gh(self):
        # gh and git read far more than any argument check sees: GH_REPO and
        # GIT_DIR pick the repository, HOME and XDG_CONFIG_HOME pick a config
        # whose pager would see the token, PATH picks which gh gets it.
        names = ["GH_REPO", "GH_HOST", "GH_CONFIG_DIR", "GH_PAGER", "PAGER", "GH_BROWSER",
                 "BROWSER", "GH_DEBUG", "GH_FORCE_TTY", "GIT_DIR", "GIT_WORK_TREE",
                 "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
                 "XDG_CONFIG_HOME", "HOME"]
        with tempfile.TemporaryDirectory() as decoy:
            fake = Path(decoy) / "gh"
            fake.write_text("#!/bin/sh\necho DECOY\n", encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            env = {**self.env, "PATH": decoy + os.pathsep + os.environ.get("PATH", ""),
                   **{n: "marker-" + n for n in names}}
            out = self.call(["api", "user"], env)
        seen = self.seen(out)
        self.assertNotIn("DECOY", out.stdout)
        self.assertNotIn("marker-", out.stdout)
        self.assertEqual(set(seen) - self.GH_SEES - {"cwd", "args"}, set())
        self.assertEqual(seen["GH_TOKEN"], "x")
        self.assertTrue(seen["cwd"].endswith("/repo"), seen["cwd"])
        self.assertRegex(seen["GH_CONFIG_DIR"], r"/repo/\.pipeline-tmp/gh-reviewer\.[^/]+$")
        self.assertEqual(seen["HOME"], seen["GH_CONFIG_DIR"])

    def test_each_call_gets_a_fresh_config_dir_and_removes_it(self):
        # A config.yml left in a shared directory would be read by gh: its
        # pager or http_unix_socket would see the token.
        scratch = self.root / ".pipeline-tmp"
        scratch.mkdir(exist_ok=True)
        planted = scratch / "gh-reviewer"
        planted.mkdir(exist_ok=True)
        (planted / "config.yml").write_text("pager: cat\n", encoding="utf-8")
        first = self.seen(self.call(["api", "user"]))["GH_CONFIG_DIR"]
        second = self.seen(self.call(["api", "user"]))["GH_CONFIG_DIR"]
        self.assertNotEqual(first, second)
        self.assertFalse(first.endswith("/gh-reviewer"))
        left = sorted(p.name for p in scratch.iterdir() if p.name.startswith("gh-reviewer"))
        self.assertEqual(left, ["gh-reviewer"], "the per-call directories are removed")

    REVIEW = ["api", "repos/{owner}/{repo}/pulls/105/reviews", "-f", "commit_id=" + SHA,
              "-f", "event=APPROVE", "-F", "body=@.pipeline-tmp/review-105.md"]

    def review(self, body=".pipeline-tmp/review-105.md", sha=SHA):
        return ["api", "repos/{owner}/{repo}/pulls/105/reviews", "-f", "commit_id=" + sha,
                "-f", "event=APPROVE", "-F", "body=@" + body]

    def test_only_full_shas(self):
        # #63: nothing shorter or longer than a full 40-character SHA.
        self.assertEqual(self.run_wrapper(self.review()), 0)
        for sha in ("abc", SHA[:39], SHA + "8", SHA.upper()):
            with self.subTest(sha=sha):
                self.assertEqual(self.run_wrapper(self.review(sha=sha)), 4)
                self.assertEqual(self.run_wrapper(["pr", "merge", "105", "--squash", "--delete-branch",
                                                   "--match-head-commit", sha]), 4)

    def test_the_body_must_be_a_regular_file_inside_the_checkout(self):
        # #63: `-F` reads whatever file it names and a review body is public;
        # the text check alone lets a .md symlink point anywhere.
        scratch = self.root / ".pipeline-tmp"
        (scratch / "a-dir.md").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "token.md"
            secret.write_text("secret\n", encoding="utf-8")
            link = scratch / "link-out.md"
            try:
                link.symlink_to(secret)
            except OSError:
                link = None        # Windows without symlink rights: covered elsewhere
            cases = {"missing": "nope.md", "a directory": ".pipeline-tmp/a-dir.md"}
            if link:
                cases["a symlink out of the checkout"] = ".pipeline-tmp/link-out.md"
            for what, body in cases.items():
                with self.subTest(what):
                    out = self.call(self.review(body))
                    self.assertEqual(out.returncode, 4, out.stderr)
                    self.assertNotIn("args=", out.stdout, "nothing reaches gh")
        # A symlink inside the checkout is read through to its target. Not on
        # Windows: Python makes a native symlink there, whose C:\ target MSYS's
        # realpath does not turn into /c/…, so the wrapper refuses it -- the
        # safe direction, and no agent writes its report through a symlink.
        if os.name == "nt":
            return
        inside = scratch / "link-in.md"
        try:
            inside.symlink_to(scratch / "review-105.md")
        except OSError:
            return
        seen = self.seen(self.call(self.review(".pipeline-tmp/link-in.md")))
        self.assertEqual(seen["body-content"], "report .pipeline-tmp/review-105.md",
                         "gh reads the resolved file")

    def test_the_body_path_is_relative_to_the_caller(self):
        # gh runs in the repository root; a reviewer in a worktree under it
        # still names its own report.
        sub = self.root / ".claude" / "worktrees" / "review-105"
        sub.mkdir(parents=True, exist_ok=True)
        from_sub = self.seen(self.call(self.REVIEW, cwd=sub))["body-content"]
        self.assertEqual(from_sub, "report .claude/worktrees/review-105/.pipeline-tmp/review-105.md")
        from_root = self.seen(self.call(self.REVIEW))["body-content"]
        self.assertEqual(from_root, "report .pipeline-tmp/review-105.md")

    def test_gh_reads_a_private_copy_of_the_body(self):
        # #73: gh never opens the caller's path again, so a file swapped after
        # the check is not what gets posted. The copy lives in the per-call
        # directory and goes with it.
        seen = self.seen(self.call(self.REVIEW))
        self.assertRegex(seen["body-file"], r"^\.pipeline-tmp/gh-reviewer\.[^/]+/body\.md$")
        self.assertEqual(seen["body-content"], "report .pipeline-tmp/review-105.md")
        self.assertFalse((self.root / seen["body-file"]).exists(), "the copy is removed after the call")

    def test_a_md_link_to_a_non_md_file_is_refused(self):
        # #73: the name was checked as written; the file that would be posted
        # must be a .md file itself (not .git/config behind a .md link).
        if os.name == "nt":
            self.skipTest("Windows symlinks resolve outside MSYS paths; refused there anyway")
        config = self.root / ".git-config"
        config.write_text("[core]\n", encoding="utf-8")
        link = self.root / ".pipeline-tmp" / "cfg.md"
        link.parent.mkdir(exist_ok=True)
        try:
            link.symlink_to(config)
        except OSError:
            self.skipTest("cannot create a symlink here")
        out = self.call(self.review(".pipeline-tmp/cfg.md"))
        self.assertEqual(out.returncode, 4, out.stderr)
        self.assertIn("not .md", out.stderr, "refused by the .md check, not another one")
        self.assertNotIn("args=", out.stdout, "nothing reaches gh")

    def test_a_review_from_outside_the_checkout_is_refused(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            self.assertEqual(self.call(self.REVIEW, cwd=elsewhere).returncode, 4)

    def test_gh_s_exit_code_comes_through(self):
        failing = self.root / ".stub" / "gh"
        text = failing.read_text(encoding="utf-8")
        try:
            failing.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            self.assertEqual(self.run_wrapper(["api", "user"]), 7)
        finally:
            failing.write_text(text, encoding="utf-8")

    def test_the_network_setup_passes_through(self):
        env = {**self.env, "HTTPS_PROXY": "http://proxy.example:3128", "SSL_CERT_FILE": "/etc/ca.pem"}
        seen = self.seen(self.call(["api", "user"], env))
        self.assertEqual(seen.get("HTTPS_PROXY"), "http://proxy.example:3128")
        self.assertEqual(seen.get("SSL_CERT_FILE"), "/etc/ca.pem")

    def test_the_token_is_not_on_a_command_line(self):
        # The wrapper clears the environment in bash and hands the token to gh
        # in it. `env -i GH_TOKEN=…` would put the token in env's arguments,
        # which any process listing shows.
        self.assertNotRegex(WRAPPER.read_text(encoding="utf-8"), r"(?m)^[^#]*\benv\s+-i\b")
        self.assertEqual(self.seen(self.call(["api", "user"]))["GH_TOKEN"], "x")


if __name__ == "__main__":
    unittest.main()
