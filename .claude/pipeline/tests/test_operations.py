"""Running the pipeline for weeks: config, pause, protection, budget, the tick
log, the status issue, stats and doctor (docs/Pipeline.md § The tick).

    python -m unittest discover -s .claude/pipeline/tests
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline as p  # noqa: E402
from test_github import FakeGh  # noqa: E402
from test_pipeline import LIM, NOW, OLD, issue, ops, pr, review, run  # noqa: E402


class ConfigTests(unittest.TestCase):
    def load(self, text):
        warnings = []
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(text, encoding="utf-8")
            return p.load_config(path, warnings), warnings

    def test_broken_json_names_the_place(self):
        with self.assertRaises(SystemExit) as e:
            self.load('{"limits": {"max_fix_rounds": 2,}}')
        self.assertIn("line 1", str(e.exception))

    def test_a_misspelt_key_is_a_warning_not_silence(self):
        cfg, warnings = self.load('{"limits": {"max_parallel_reviews": 5}, "projekt": "x"}')
        self.assertEqual(cfg["limits"]["max_parallel_review"], p.DEFAULT_CONFIG["limits"]["max_parallel_review"])
        self.assertEqual(len(warnings), 2, warnings)

    def test_a_limit_must_be_a_number(self):
        with self.assertRaises(SystemExit):
            self.load('{"limits": {"max_fix_rounds": "two"}}')


class PauseTests(unittest.TestCase):
    def test_a_paused_tick_still_reports_and_turns_auto_merge_off(self):
        # Pause used to return at once: no Needs-you list, and approved PRs
        # kept merging onto a red default branch.
        plan = run([issue(1, [p.PAUSE]), issue(5, [p.AGENT_TASK, p.NEEDS_HUMAN])],
                   [pr(106, reviews=[review("APPROVED")], auto=True), pr(107)])
        self.assertTrue(plan.paused)
        self.assertEqual(plan.dispatch, [])
        self.assertEqual([(o["op"], o["number"]) for o in plan.ops], [("disable-automerge", 106)])
        self.assertTrue(any("#5" in w for w in plan.awaiting_human))

    def test_the_local_kill_file_names_itself(self):
        plan = run([issue(5, [p.AGENT_TASK, p.READY])], paused="the local kill file /x/pause")
        self.assertTrue(plan.paused)
        self.assertIn("kill file", plan.pause_reason)

    def test_missing_protection_holds_everything(self):
        plan = run([issue(5, [p.AGENT_TASK, p.READY])],
                   protection={"state": "missing", "problems": ["no branch protection"]})
        self.assertTrue(plan.paused)
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(any("setup-repo" in w for w in plan.awaiting_human))

    def test_drift_holds_everything(self):
        plan = run([issue(5, [p.AGENT_TASK, p.READY])],
                   protection={"state": "drift", "problems": ["enforce_admins is False, want True"]})
        self.assertIn("enforce_admins", plan.pause_reason)

    def test_protection_can_be_waived_to_try_the_pipeline(self):
        plan = run([issue(5, [p.AGENT_TASK, p.READY])], require_protection=False,
                   protection={"state": "missing", "problems": ["no branch protection"]})
        self.assertFalse(plan.paused)
        self.assertEqual(plan.dispatch[0]["kind"], "implement")

    def test_unreadable_protection_is_only_a_warning(self):
        plan = run([issue(5, [p.AGENT_TASK, p.READY])], protection={"state": "unreadable", "problems": []})
        self.assertFalse(plan.paused)
        self.assertTrue(any("Administration" in x for x in plan.setup_problems))


class BudgetTests(unittest.TestCase):
    def test_the_daily_budget_trims_the_dispatch(self):
        lim = p.Limits(**{**LIM.__dict__, "max_agent_runs_per_day": 10, "max_parallel_fix": 9})
        plan = run([], [pr(101 + k, checks="failure") for k in range(5)], limits=lim, runs_today=8)
        self.assertEqual(len(plan.dispatch), 2)
        self.assertEqual(sum("daily budget" in d for d in plan.deferred), 3)
        self.assertTrue(any("daily budget" in x for x in plan.setup_problems))

    def test_zero_means_no_daily_budget(self):
        lim = p.Limits(**{**LIM.__dict__, "max_agent_runs_per_day": 0})
        plan = run([issue(5, [p.AGENT_TASK, p.READY])], limits=lim, runs_today=10_000)
        self.assertEqual(len(plan.dispatch), 1)


class TickLogTests(unittest.TestCase):
    def test_entries_round_trip_and_filter_by_time(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d) / "pipeline"
            old = {"at": "2026-09-01T00:00:00Z", "dispatched": ["fix PR #1"]}
            p.append_tick_log(directory, old)
            out = {"dispatch": [{"kind": "review", "pr": 105}, {"kind": "implement", "issue": 5}],
                   "awaiting_human": ["x"], "ops_done": ["FAILED add-label 5: boom"], "claims": []}
            p.append_tick_log(directory, p.tick_entry(out, NOW))
            (directory / "ticks.jsonl").open("a", encoding="utf-8").write("not json\n")
            recent = p.read_tick_log(directory, NOW - timedelta(days=1))
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["dispatched"], ["review PR #105", "implement #5"])
        self.assertEqual(p.runs_in(recent), 2)
        self.assertEqual(recent[0]["failed"], ["FAILED add-label 5: boom"])

    def test_no_directory_is_no_log(self):
        self.assertEqual(p.read_tick_log(None), [])
        p.append_tick_log(None, {"at": "x"})     # does nothing, raises nothing


class StatusIssueTests(unittest.TestCase):
    def test_the_body_says_when_and_what(self):
        out = {"paused": True, "pause_reason": "`pipeline:pause` on an open issue",
               "awaiting_human": ["#5: status:needs-human"], "dispatch": [{"kind": "fix", "pr": 105}],
               "setup_problems": [], "in_flight": ["a"], "waiting": [], "deferred": []}
        week = [{"dispatched": ["fix PR #1", "review PR #2"], "failed": []},
                {"dispatched": ["fix PR #3"], "failed": ["FAILED x"]}]
        body = p.status_body(out, week, NOW)
        self.assertIn("2026-09-27 12:00 UTC", body)
        self.assertIn("paused", body)
        self.assertIn("- #5: status:needs-human", body)
        self.assertIn("3 agent runs (fix 2, review 1)", body)
        self.assertIn("1 failed writes", body)

    def test_it_is_created_once_and_then_rewritten(self):
        gh = FakeGh()
        n = gh.upsert_status_issue("first")
        self.assertEqual(gh.upsert_status_issue("second"), n)
        edits = [w for w in gh.writes if w[:2] == ["issue", "edit"]]
        self.assertEqual(len(edits), 1)

    def test_the_tick_ignores_its_own_status_issue(self):
        plan = run([issue(9, [p.STATUS_ISSUE])])
        self.assertEqual([w for w in plan.awaiting_human if "#9" in w], [])


class StatsTests(unittest.TestCase):
    def test_the_signals(self):
        def at(h):
            return (NOW - timedelta(hours=h)).isoformat().replace("+00:00", "Z")
        merged = [
            {"number": 1, "labels": [], "createdAt": at(10), "mergedAt": at(8)},
            {"number": 2, "labels": [{"name": "fix-round-1"}, {"name": p.SPEC_DEFECT}],
             "createdAt": at(30), "mergedAt": at(20)},
            {"number": 3, "labels": [], "createdAt": at(2000), "mergedAt": at(1900)},   # outside
        ]
        closed = [{"number": 5, "labels": [{"name": p.TRIAGED}], "createdAt": at(50), "closedAt": at(20)}]
        ticks = [{"dispatched": ["fix PR #2", "review PR #1"]}, {"dispatched": ["review PR #2"]}]
        s = p.compute_stats(merged, closed, ticks, NOW, days=30)
        self.assertEqual(s["merged_prs"], 2)
        self.assertEqual(s["median_hours_pr_open_to_merge"], 6.0)
        self.assertEqual((s["prs_that_needed_a_fix_pass"], s["fix_pass_rate"], s["spec_defects"]), (1, 0.5, 1))
        self.assertEqual((s["issues_closed"], s["issues_that_went_to_triage"]), (1, 1))
        self.assertEqual(s["agent_runs"], {"fix": 1, "review": 2})


class DoctorTests(unittest.TestCase):
    def test_workflow_jobs_are_found_by_name(self):
        jobs = p.workflow_jobs(p.REPO_ROOT)
        for check in ("ci", "tooling", "allowlist", "contract"):
            self.assertIn(check, jobs)

    def test_the_report_names_what_is_missing(self):
        responses = [
            (lambda a: a == ["api", "repos/{owner}/{repo}"],
             json.dumps({"allow_auto_merge": False, "delete_branch_on_merge": True, "allow_squash_merge": True,
                         "allow_merge_commit": False, "allow_rebase_merge": False, "owner": {"type": "User"}})),
            (lambda a: "protection" in a[1], p.GhError("gh api repos ...: Branch not protected (HTTP 404)")),
            (lambda a: a[:2] == ["api", "apps/github-actions"], json.dumps({"id": 15368})),
            (lambda a: "actions/permissions/workflow" in a[1],
             json.dumps({"default_workflow_permissions": "write", "can_approve_pull_request_reviews": True})),
            (lambda a: a[:2] == ["label", "list"], json.dumps([{"name": p.AGENT_TASK}])),
        ]
        gh = FakeGh(responses=responses)
        gh.reviewer_login = lambda: None

        def fake_run(args, **_):
            if args[1:3] == ["auth", "status"]:
                return SimpleNamespace(returncode=0, stdout="Token: gho_****", stderr="")
            return SimpleNamespace(returncode=0, stdout="worktree /x\n", stderr="")
        report = {name: (level, detail) for level, name, detail in
                  p.doctor_report(gh, p.REPO_ROOT, which=lambda t: "/usr/bin/" + t, run=fake_run)}
        self.assertEqual(report["gh login"][0], "warn")
        self.assertEqual(report["reviewer bot"][0], "fail")
        self.assertEqual(report["repository settings"][0], "warn")
        self.assertIn("allow_auto_merge", report["repository settings"][1])
        self.assertEqual(report[f"branch protection on {p.DEFAULT_BRANCH}"][0], "fail")
        self.assertEqual(report["Actions token"][0], "fail")
        self.assertEqual(report["labels"][0], "warn")
        self.assertEqual(report["required checks"][0], "ok")
        self.assertEqual(report["test command allowed"][0], "ok")


class ProtectionStateTests(unittest.TestCase):
    def test_states(self):
        missing = FakeGh(responses=[(lambda a: "protection" in a[1],
                                     p.GhError("gh api ...: Branch not protected (HTTP 404)"))])
        self.assertEqual(missing.protection_state()["state"], "missing")
        forbidden = FakeGh(responses=[(lambda a: "protection" in a[1],
                                       p.GhError("gh api ...: Resource not accessible (HTTP 403)"))])
        self.assertEqual(forbidden.protection_state()["state"], "unreadable")
        payload = p.protection_payload(15368)
        good = dict(payload)
        for key in ("enforce_admins", "required_linear_history", "allow_force_pushes", "allow_deletions"):
            good[key] = {"enabled": payload[key]}
        ok = FakeGh(responses=[(lambda a: "protection" in a[1], json.dumps(good)),
                               (lambda a: a[:2] == ["api", "apps/github-actions"], json.dumps({"id": 15368}))])
        self.assertEqual(ok.protection_state(), {"state": "ok", "problems": []})


if __name__ == "__main__":
    unittest.main()
