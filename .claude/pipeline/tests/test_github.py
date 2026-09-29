"""The GitHub-facing half of .claude/pipeline/pipeline.py, against a fake gh.

    python -m unittest discover -s .claude/pipeline/tests

decide() is pure and tested in test_pipeline.py; this is everything around
it that writes: label changes, claims, the ops a tick applies, and how gh's
JSON becomes a snapshot. FakeGh stands in for the gh binary and keeps each
issue's and pull request's labels, so a test can change them "between the
survey and the write" the way another tick or a person would.
"""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline as p  # noqa: E402


class FakeGh(p.Gh):
    def __init__(self, issues=None, prs=None, responses=None):
        self.gh = "gh"
        self.dry_run = False
        self.log = []
        self.labels_of = {("issue", n): set(ls) for n, ls in (issues or {}).items()}
        self.labels_of.update({("pr", n): set(ls) for n, ls in (prs or {}).items()})
        self.writes: list[list[str]] = []
        self.responses = responses or []   # [(predicate(args) -> bool, stdout or Exception)]
        self.next_issue = 900

    def _run(self, args, input=None, as_reviewer=False, check=True, mutating=False):
        for match, out in self.responses:
            if match(args):
                if isinstance(out, Exception):
                    raise out
                return out
        kind = "pr" if args[0] == "pr" else "issue"
        if args[:1] in (["issue"], ["pr"]) and args[1] == "view" and "labels" in args:
            return json.dumps({"labels": [{"name": l} for l in sorted(self.labels_of.get((kind, int(args[2])), set()))]})
        if args[:1] in (["issue"], ["pr"]) and args[1] == "edit":
            key = (kind, int(args[2]))
            labels = self.labels_of.setdefault(key, set())
            for flag, value in zip(args[3::2], args[4::2]):
                (labels.add if flag == "--add-label" else labels.discard)(value)
            self.writes.append(args)
            return ""
        if args[:2] == ["issue", "list"] and "--label" in args:
            want = args[args.index("--label") + 1]
            hits = [{"number": n} for (k, n), ls in self.labels_of.items() if k == "issue" and want in ls]
            return json.dumps(hits)
        if args[:2] == ["issue", "create"]:
            self.next_issue += 1
            labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
            self.labels_of[("issue", self.next_issue)] = set(labels)
            self.writes.append(args)
            return f"https://github.com/o/r/issues/{self.next_issue}\n"
        if mutating:
            self.writes.append(args)
        return ""


class SetStatusTests(unittest.TestCase):
    def test_every_status_label_goes_not_only_the_surveyed_ones(self):
        gh = FakeGh(issues={5: {p.AGENT_TASK, p.READY, p.ESCALATED}})
        gh.set_status(5, p.IN_REVIEW)
        self.assertEqual(gh.labels_of[("issue", 5)], {p.AGENT_TASK, p.IN_REVIEW})

    def test_a_moved_issue_is_not_written(self):
        gh = FakeGh(issues={5: {p.AGENT_TASK, p.ESCALATED}})
        with self.assertRaises(p.StaleState):
            gh.set_status(5, p.IN_REVIEW, expect=p.IN_PROGRESS)
        self.assertEqual(gh.writes, [])


class ApplyOpsTests(unittest.TestCase):
    def test_a_skipped_status_change_skips_its_comment(self):
        # The implementer escalated between the survey and the write.
        gh = FakeGh(issues={5: {p.AGENT_TASK, p.ESCALATED}})
        plan = p.Plan(ops=[
            {"op": "set-status", "number": 5, "status": p.NEEDS_HUMAN, "expect": p.IN_PROGRESS},
            {"op": "comment", "kind": "issue", "number": 5, "body": "handing to a human"},
        ])
        done = p.apply_ops(gh, plan, lint_fn=None)
        self.assertTrue(done[0].startswith("SKIPPED set-status"))
        self.assertTrue(done[1].startswith("SKIPPED comment"))
        self.assertEqual(gh.writes, [])

    def test_a_failed_label_skips_its_comment_but_not_other_items(self):
        gh = FakeGh(prs={105: set(), 106: set()}, responses=[
            (lambda a: a[:3] == ["pr", "edit", "105"], p.GhError("label not found"))])
        plan = p.Plan(ops=[
            {"op": "add-label", "kind": "pr", "number": 105, "label": p.NEEDS_HUMAN},
            {"op": "comment", "kind": "pr", "number": 105, "body": "handed over"},
            {"op": "comment", "kind": "pr", "number": 106, "body": "other"},
        ])
        done = p.apply_ops(gh, plan, lint_fn=None)
        self.assertEqual([d.split()[0] for d in done], ["FAILED", "SKIPPED", "comment"])

    def test_lint_then_claim_leaves_exactly_one_status(self):
        # The tick lints #5 to ready and claims it in the same run. The claim
        # used to work from the survey's labels and left ready AND in-progress,
        # so the next tick dispatched #5 again.
        gh = FakeGh(issues={5: {p.AGENT_TASK}})
        plan = p.Plan(ops=[{"op": "set-status", "number": 5, "status": p.READY, "expect": None}],
                      dispatch=[{"kind": "implement", "issue": 5}])
        p.apply_ops(gh, plan, lint_fn=None)
        p.claim(gh, plan.dispatch[0])
        self.assertEqual(gh.labels_of[("issue", 5)], {p.AGENT_TASK, p.IN_PROGRESS})


class HandOffTests(unittest.TestCase):
    def test_apply_ops_posts_hand_offs_as_the_bot_and_the_answer_status(self):
        seen = []

        class Recorder(FakeGh):
            def comment(self, kind, n, body, as_bot=False):
                seen.append(("comment", n, as_bot))

            def post_status(self, sha, context, description):
                seen.append(("status", sha, context, description))

        plan = p.Plan(ops=[
            {"op": "comment", "kind": "pr", "number": 105, "body": "@o handed over", "as_bot": True},
            {"op": "post-status", "number": 105, "sha": "abc", "context": p.ANSWERED_STATUS,
             "description": "comments=2 attempts=0"},
        ])
        p.apply_ops(Recorder(), plan, lint_fn=None)
        self.assertEqual(seen, [("comment", 105, True),
                                ("status", "abc", p.ANSWERED_STATUS, "comments=2 attempts=0")])


class ClaimTests(unittest.TestCase):
    def test_an_issue_claimed_since_the_survey_is_refused(self):
        gh = FakeGh(issues={5: {p.AGENT_TASK, p.IN_PROGRESS}})
        with self.assertRaises(p.StaleState):
            p.claim(gh, {"kind": "implement", "issue": 5})

    def test_an_issue_triage_still_holds_is_refused(self):
        gh = FakeGh(issues={5: {p.AGENT_TASK, p.READY, p.WORKING}})
        with self.assertRaises(p.StaleState):
            p.claim(gh, {"kind": "implement", "issue": 5})

    def test_a_resume_leaves_the_status_alone(self):
        gh = FakeGh(issues={5: {p.AGENT_TASK, p.IN_PROGRESS, p.ATTEMPT}})
        p.claim(gh, {"kind": "implement", "issue": 5, "resume": True, "branch": "agent/5-x"})
        self.assertEqual(gh.writes, [])

    def test_a_review_claim_counts_the_attempt_at_the_head(self):
        gh = FakeGh(prs={105: set()})
        p.claim(gh, {"kind": "review", "pr": 105, "head": "abc", "attempt": 2})
        self.assertEqual(gh.labels_of[("pr", 105)], {p.WORKING, p.REVIEWING})
        status = [w for w in gh.writes if w[0] == "api"][0]
        self.assertIn("repos/{owner}/{repo}/statuses/abc", status)
        self.assertIn(f"context={p.REVIEW_STATUS}", status)
        self.assertIn("description=attempt 2", status)

    def test_a_pr_held_by_another_tick_is_refused(self):
        gh = FakeGh(prs={105: {p.WORKING}})
        with self.assertRaises(p.StaleState):
            p.claim(gh, {"kind": "fix", "pr": 105, "round_label": "fix-round-1"})

    def test_a_fix_claim_spends_its_round(self):
        gh = FakeGh(prs={105: set()})
        p.claim(gh, {"kind": "fix", "pr": 105, "round_label": "conflict-round-1"})
        self.assertEqual(gh.labels_of[("pr", 105)], {p.WORKING, "conflict-round-1"})

    def test_roadmap_planning_opens_its_own_claim(self):
        gh = FakeGh()
        item = {"kind": "plan", "mode": "roadmap"}
        p.claim(gh, item)
        self.assertEqual(gh.labels_of[("issue", item["tracking"])], {p.PLANNING, p.WORKING})
        with self.assertRaises(p.StaleState):
            p.claim(gh, {"kind": "plan", "mode": "roadmap"})


class ReleaseTests(unittest.TestCase):
    def release(self, fake, *argv):
        import contextlib
        import io
        orig = p.Gh
        try:
            p.Gh = lambda *a, **k: fake
            with contextlib.redirect_stdout(io.StringIO()):
                p.main(["release", *argv])
        finally:
            p.Gh = orig

    def test_an_agents_release_keeps_the_owners_hold(self):
        # Triage ends every run with `release issue N`; the owner's hold stays.
        gh = FakeGh(issues={5: {p.AGENT_TASK, p.WORKING, p.HUMAN_HOLDS}})
        self.release(gh, "issue", "5")
        self.assertEqual(gh.labels_of[("issue", 5)], {p.AGENT_TASK, p.HUMAN_HOLDS})

    def test_the_owner_hands_an_issue_back_with_hold(self):
        gh = FakeGh(issues={5: {p.AGENT_TASK, p.HUMAN_HOLDS}})
        self.release(gh, "issue", "5", "--hold")
        self.assertEqual(gh.labels_of[("issue", 5)], {p.AGENT_TASK})

    def test_a_refunded_round_goes_with_the_claim(self):
        gh = FakeGh(prs={105: {p.WORKING, "fix-round-2"}})
        self.release(gh, "pr", "105", "--round-label", "fix-round-2")
        self.assertEqual(gh.labels_of[("pr", 105)], set())


class AutoMergeTests(unittest.TestCase):
    def test_merge_is_pinned_and_falls_back_on_a_clean_pr(self):
        gh = FakeGh(responses=[(lambda a: a[:2] == ["pr", "merge"] and "--auto" in a,
                                p.GhError("GraphQL: Pull request is in clean status"))])
        gh.enable_automerge(105, "abc")
        merge = [w for w in gh.writes if w[:2] == ["pr", "merge"]][0]
        self.assertNotIn("--auto", merge)
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], "abc")

    def test_other_merge_errors_are_not_swallowed(self):
        gh = FakeGh(responses=[(lambda a: a[:2] == ["pr", "merge"], p.GhError("forbidden"))])
        with self.assertRaises(p.GhError):
            gh.enable_automerge(105, "abc")


class OpenPrsTests(unittest.TestCase):
    def test_gh_json_becomes_the_snapshot(self):
        listing = [{
            "number": 105, "title": "t", "headRefName": "agent/5-x", "headRefOid": "abc",
            "isDraft": False, "labels": [{"name": "fix-round-1"}], "mergeable": "MERGEABLE",
            "updatedAt": "2026-09-27T11:00:00Z", "autoMergeRequest": None,
            "isCrossRepository": False, "closingIssuesReferences": [{"number": 5}],
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "ci", "workflowName": "CI", "status": "COMPLETED",
                 "conclusion": "FAILURE", "startedAt": "2026-09-27T10:00:00Z",
                 "detailsUrl": "https://github.com/o/r/actions/runs/42/job/1"},
                {"__typename": "StatusContext", "context": p.REVIEW_STATUS, "state": "SUCCESS",
                 "description": "attempt 1", "createdAt": "2026-09-27T10:30:00Z"}]}]
        pages = [[{"user": {"login": "bot"}, "state": "COMMENTED", "commit_id": "abc",
                   "submitted_at": "2026-09-27T10:40:00Z"}],
                 [{"user": None, "state": "APPROVED", "commit_id": "old", "submitted_at": "x"}]]
        gh = FakeGh(responses=[
            (lambda a: a[:2] == ["pr", "list"], json.dumps(listing)),
            (lambda a: "--slurp" in a, json.dumps(pages)),
            (lambda a: a[:2] == ["run", "view"], json.dumps({"attempt": 2})),
        ])
        [pr] = gh.open_prs()
        self.assertEqual(pr["checks"], "failure")          # ci failed; allowlist etc. would be missing too
        self.assertEqual(pr["failedRuns"], [{"name": "ci", "run": 42, "attempt": 2}])
        self.assertEqual(pr["reviewAttempts"], 1)
        self.assertEqual(len(pr["reviews"]), 2)             # both pages
        self.assertEqual(pr["reviews"][1]["login"], None)   # a deleted ("ghost") account
        self.assertEqual((pr["closingIssues"], pr["crossRepo"]), ([5], False))


class RemoteBranchTests(unittest.TestCase):
    def test_a_failed_ls_remote_is_unknown_not_empty(self):
        orig = p.subprocess.run
        try:
            p.subprocess.run = lambda *a, **k: SimpleNamespace(returncode=128, stdout="", stderr="auth")
            self.assertIsNone(p.remote_agent_branches())
            p.subprocess.run = lambda *a, **k: SimpleNamespace(
                returncode=0, stdout="abc\trefs/heads/agent/5-x\n", stderr="")
            self.assertEqual(p.remote_agent_branches(), {"agent/5-x"})
        finally:
            p.subprocess.run = orig


class IssueStateTests(unittest.TestCase):
    def test_a_failed_lookup_is_unknown_and_blocks(self):
        gh = FakeGh(responses=[(lambda a: a[0] == "api", p.GhError("HTTP 502: bad gateway"))])
        self.assertEqual(gh.issue_state(3), "unknown")
        body = "## Blocked by\n\n#3\n"
        result = p.lint_body(5, body, lambda b: [], gh.issue_state)
        self.assertEqual(result.blockers_open, [3])

    def test_a_deleted_blocker_does_not_block(self):
        gh = FakeGh(responses=[(lambda a: a[0] == "api", p.GhError("HTTP 404: Not Found"))])
        self.assertIsNone(gh.issue_state(3))


class BashPathTests(unittest.TestCase):
    def test_an_override_wins(self):
        self.assertEqual(p.bash_path(env={"PIPELINE_BASH": "/x/bash"}), "/x/bash")

    @unittest.skipUnless(p.os.name == "nt", "Windows discovery")
    def test_wsl_bash_is_never_chosen(self):
        env = {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\nope", "LOCALAPPDATA": r"C:\nope"}
        wsl = r"C:\Windows\System32\bash.exe"
        got = p.bash_path(env=env, which=lambda name: wsl if name == "bash" else None,
                          exists=lambda path: path == wsl)
        self.assertIsNone(got)

    @unittest.skipUnless(p.os.name == "nt", "Windows discovery")
    def test_git_bash_is_found_beside_git(self):
        env = {"SystemRoot": r"C:\Windows"}
        got = p.bash_path(env=env, which=lambda name: r"D:\tools\Git\cmd\git.exe" if name == "git" else None,
                          exists=lambda path: path.lower() == r"d:\tools\git\bin\bash.exe")
        self.assertEqual(got.lower(), r"d:\tools\git\bin\bash.exe")


if __name__ == "__main__":
    unittest.main()
