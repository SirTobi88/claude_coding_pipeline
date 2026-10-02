"""Offline tests for .claude/pipeline/pipeline.py.

    python -m unittest discover -s .claude/pipeline/tests

No network and no gh: `decide` and the lint are pure functions over dicts.
One case runs the real allowlist parser through bash, and skips when no bash
is on PATH.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline as p  # noqa: E402

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
RECENT = (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
OLD = (NOW - timedelta(hours=12)).isoformat().replace("+00:00", "Z")
REVIEWER = "project-reviewer-bot"

READY_BODY = """## Goal

Add the thing.

## Why

Because the slice needs it.

## Context

| What | Where |
|---|---|
| Design | `Design/Systems/Economy.md` § *Pricing model* |

## Interface

```gdscript
func quote_buy(t: int, g: int, qty: float) -> float
```

## Files in scope

- [ ] `src/pricing/world.py` (modify)
- [ ] `tests/pricing/test_world.py` (create)

## Non-goals

- Do not touch the tick.

## Definition of done

- [ ] `./run_tests.sh` passes

## Blocked by

nothing
"""

TEMPLATE_BODY = """## Goal

<!-- One sentence -->

## Why

## Context

| What | Where |
|---|---|
| Design decision | `Design/Systems/____.md` § ____ |

## Interface

```gdscript
# Pre-existing, do not change:

# To be implemented by this issue:
```

## Files in scope

- [ ] `path/to/file.gd` (modify)

## Non-goals

- Do not
- Do not

## Definition of done

- [ ] `./run_tests.sh` passes, including new test `____`

## Blocked by
"""


def fake_allowlist(body):
    out = []
    in_scope = False
    for line in body.splitlines():
        if line.startswith("## "):
            in_scope = line.strip() == "## Files in scope"
            continue
        if in_scope and "`" in line:
            out.append(line.split("`")[1])
    return out


def issue(n, labels=(), body=READY_BODY, updated=RECENT, title=None):
    return {"number": n, "title": title or f"issue {n}", "labels": [{"name": l} for l in labels],
            "body": body, "updatedAt": updated}


def pr(n, branch=None, head="abc", checks="success", reviews=(), labels=(), mergeable="MERGEABLE",
       draft=False, auto=False, updated=RECENT):
    return {"number": n, "headRefName": branch or f"agent/{n - 100}-x", "headRefOid": head,
            "isDraft": draft, "labels": [{"name": l} for l in labels], "mergeable": mergeable,
            "updatedAt": updated, "checks": checks, "autoMerge": auto, "closingIssues": [],
            "reviews": list(reviews)}


def review(state, commit="abc", login=REVIEWER, at="2026-09-27T11:00:00Z"):
    return {"login": login, "state": state, "commit": commit, "submitted_at": at}


def lint_with(states=None):
    states = states or {}
    return lambda i: p.lint_body(i["number"], i.get("body", ""), fake_allowlist,
                                 lambda n: states.get(n, "closed"))


# The shipped defaults, not whatever config.json a project set: a project that
# raises a limit must not turn these tests red.
LIM = p.Limits.from_config(p.DEFAULT_CONFIG["limits"])


def run(issues=(), prs=(), reviewer=REVIEWER, branches=(), states=None, auto_issues=True, **snap):
    """decide() over a snapshot. Every PR's agent/<N>- issue is added, in review,
    unless the test brings its own -- a PR without its issue is its own case."""
    issues = list(issues)
    if auto_issues:
        have = {i["number"] for i in issues}
        for pr_ in prs:
            n = p.branch_issue(pr_.get("headRefName", ""))
            if n and n not in have:
                issues.append(issue(n, [p.AGENT_TASK, p.IN_REVIEW]))
                have.add(n)
    base = {"now": NOW, "issues": issues, "prs": list(prs), "reviewer_login": reviewer,
            "remote_branches": set(branches) if branches is not None else None, "limits": LIM}
    base.update(snap)
    return p.decide(base, fake_allowlist, lint_with(states))


def owners(plan):
    """Everything the plan says about an item, as one string to search."""
    return " ".join(plan.waiting + plan.in_flight + plan.awaiting_human + plan.deferred
                    + [o.get("why", "") for o in plan.ops])


def assert_every_pr_has_an_owner(test, plan, prs):
    """The invariant docs/Pipeline.md is built on: every open item ends a tick
    dispatched, in flight, waiting, deferred or with the owner -- and no item
    is handed to two agents at once."""
    said = owners(plan)
    handled = {d["pr"] for d in plan.dispatch if d.get("pr")}
    for x in prs:
        test.assertTrue(x["number"] in handled or f"PR #{x['number']}" in said,
                        f"PR #{x['number']} has no next owner: {plan.to_json()}")
    keys = [("pr", d["pr"]) if d.get("pr") else ("issue", d.get("issue")) for d in plan.dispatch
            if d.get("pr") or d.get("issue")]
    test.assertEqual(len(keys), len(set(keys)), f"an item dispatched twice: {plan.dispatch}")


def kinds(plan):
    return [(d["kind"], d.get("issue"), d.get("pr")) for d in plan.dispatch]


def ops(plan, op=None):
    return [o for o in plan.ops if op is None or o["op"] == op]


class LintTests(unittest.TestCase):
    def lint(self, body, states=None, n=10):
        return p.lint_body(n, body, fake_allowlist, lambda k: (states or {}).get(k, "closed"))

    def test_complete_issue_is_ready(self):
        r = self.lint(READY_BODY)
        self.assertEqual(r.problems, [])
        self.assertEqual(r.status, p.READY)

    def test_untouched_template_needs_spec_and_names_each_gap(self):
        r = self.lint(TEMPLATE_BODY)
        self.assertEqual(r.status, p.NEEDS_SPEC)
        joined = "\n".join(r.problems)
        for name in ("Goal", "Why", "Context", "Interface", "Files in scope", "Non-goals",
                     "Definition of done"):
            self.assertIn(name, joined)

    def test_comment_only_interface_is_not_an_interface(self):
        body = READY_BODY.replace("func quote_buy(t: int, g: int, qty: float) -> float",
                                  "# nothing yet")
        self.assertIn("`## Interface` has no code block with real signatures", self.lint(body).problems)

    def test_missing_section_is_named(self):
        body = READY_BODY.replace("## Non-goals\n\n- Do not touch the tick.\n\n", "")
        self.assertIn("missing section `## Non-goals`", self.lint(body).problems)

    def test_open_blocker_blocks(self):
        body = READY_BODY.replace("nothing", "#42 must land first")
        r = self.lint(body, {42: "open"})
        self.assertEqual((r.status, r.blockers_open), (p.BLOCKED, [42]))

    def test_closed_issues_named_in_blocked_by_do_not_block(self):
        body = READY_BODY.replace("nothing", "nothing -- VS-17 #82 and VS-18 #83 are both merged.")
        self.assertEqual(self.lint(body).status, p.READY)

    def test_self_reference_is_not_a_blocker(self):
        body = READY_BODY.replace("nothing", "see #10")
        self.assertEqual(self.lint(body, {10: "open"}, n=10).status, p.READY)

    def test_line_number_anchors_warn_but_do_not_fail(self):
        body = READY_BODY.replace("§ *Pricing model*", "L157–182")
        r = self.lint(body)
        self.assertEqual(r.status, p.READY)
        self.assertTrue(any("line numbers" in w for w in r.warnings))

    def test_level_three_headings_stay_inside_their_section(self):
        body = READY_BODY.replace("## Interface\n", "## Interface\n\n### Unchanged\n")
        self.assertEqual(self.lint(body).status, p.READY)


class PrBodyTests(unittest.TestCase):
    GOOD = ("## What changed\nA thing.\n\n## Definition of done — verified\n`./run_tests.sh` "
            "exit 0\n\n## Not verified\nNothing.\n\n## Left alone\nThe tick.\n\nCloses #12\n")

    def test_filled_template_passes(self):
        self.assertEqual(p.check_pr_body(self.GOOD), [])

    def test_empty_template_fails_on_every_section(self):
        tpl = Path(__file__).resolve().parents[3] / ".github" / "pull_request_template.md"
        missing = p.check_pr_body(tpl.read_text(encoding="utf-8"))
        self.assertEqual(len(missing), 4)

    def test_missing_section_is_named(self):
        body = self.GOOD.replace("## Left alone\nThe tick.\n", "")
        self.assertEqual(p.check_pr_body(body), ["missing section `## Left alone`"])


class OverlapTests(unittest.TestCase):
    def test_same_file(self):
        self.assertTrue(p.paths_overlap("package.json", "package.json"))

    def test_directory_covers_file(self):
        self.assertTrue(p.paths_overlap("Art/UI/", "Art/UI/frame.svg"))
        self.assertTrue(p.paths_overlap("vendor/lib/**", "vendor/lib/deep/x.py"))

    def test_siblings_do_not_overlap(self):
        self.assertFalse(p.paths_overlap("src/trade.py", "src/player.py"))


def run_(name, conclusion="SUCCESS", status="COMPLETED", at="2026-09-27T11:00:00Z", wf="CI", run_id=1):
    return {"__typename": "CheckRun", "name": name, "workflowName": wf, "status": status,
            "conclusion": conclusion, "startedAt": at,
            "detailsUrl": f"https://github.com/o/r/actions/runs/{run_id}/job/9"}


class RollupTests(unittest.TestCase):
    REQ = ("ci", "allowlist")

    def test_a_missing_required_check_is_pending_not_green(self):
        # Right after a push, ci finishes before pr-contract has even queued.
        self.assertEqual(p.rollup_checks([run_("ci")], self.REQ)["state"], "pending")

    def test_an_optional_failure_does_not_fail_the_pr(self):
        rollup = [run_("ci"), run_("allowlist"), run_("lint-docs", "FAILURE")]
        self.assertEqual(p.rollup_checks(rollup, self.REQ)["state"], "success")

    def test_only_the_latest_run_of_a_check_counts(self):
        # A description edit re-runs `contract`; the old failure must not stick.
        rollup = [run_("ci"), run_("allowlist", "FAILURE", at="2026-09-27T10:00:00Z"),
                  run_("allowlist", "SUCCESS", at="2026-09-27T10:05:00Z")]
        self.assertEqual(p.rollup_checks(rollup, self.REQ)["state"], "success")
        rollup[1], rollup[2] = rollup[2], rollup[1]      # the order of the list does not matter
        self.assertEqual(p.rollup_checks(rollup, self.REQ)["state"], "success")

    def test_a_queued_re_run_beats_the_failure_it_replaces(self):
        # A queued run has no start time yet; the old failure must not win.
        queued = run_("ci", "", status="QUEUED", at=None)
        for rollup in ([run_("ci", "FAILURE"), queued, run_("allowlist")],
                       [queued, run_("ci", "FAILURE"), run_("allowlist")]):
            self.assertEqual(p.rollup_checks(rollup, self.REQ)["state"], "pending")

    def test_a_cancelled_run_is_pending(self):
        rollup = [run_("ci", "CANCELLED"), run_("allowlist")]
        self.assertEqual(p.rollup_checks(rollup, self.REQ)["state"], "pending")

    def test_failed_runs_carry_their_run_id(self):
        out = p.rollup_checks([run_("ci", "FAILURE", run_id=77), run_("allowlist")], self.REQ)
        self.assertEqual((out["state"], out["failed"]), ("failure", [{"name": "ci", "run": 77, "attempt": None}]))
        rerun = dict(run_("ci", "FAILURE", run_id=77), runAttempt=2)
        self.assertEqual(p.rollup_checks([rerun, run_("allowlist")], self.REQ)["failed"],
                         [{"name": "ci", "run": 77, "attempt": 2}])

    def test_the_answer_status_carries_its_baseline(self):
        rollup = [run_("ci"), run_("allowlist"),
                  {"__typename": "StatusContext", "context": p.ANSWERED_STATUS, "state": "SUCCESS",
                   "description": "comments=2 attempts=1", "createdAt": "2026-09-27T11:00:00Z"}]
        out = p.rollup_checks(rollup, self.REQ)
        self.assertEqual((out["state"], out["answeredComments"], out["answeredAttempts"]),
                         ("success", 2, 1))

    def test_the_review_status_counts_attempts_and_is_not_a_check(self):
        rollup = [run_("ci"), run_("allowlist"),
                  {"__typename": "StatusContext", "context": p.REVIEW_STATUS, "state": "SUCCESS",
                   "description": "attempt 2", "createdAt": "2026-09-27T11:00:00Z"}]
        out = p.rollup_checks(rollup, self.REQ)
        self.assertEqual((out["state"], out["reviewAttempts"]), ("success", 2))

    def test_states(self):
        ok = {"__typename": "CheckRun", "name": "a", "status": "COMPLETED", "conclusion": "SUCCESS"}
        running = {"__typename": "CheckRun", "name": "b", "status": "IN_PROGRESS", "conclusion": ""}
        failed = {"__typename": "CheckRun", "name": "c", "status": "COMPLETED", "conclusion": "FAILURE"}
        status_ok = {"__typename": "StatusContext", "state": "SUCCESS", "context": "x"}
        def state(rollup):
            return p.rollup_checks(rollup, ())["state"]   # no required checks: every check counts
        self.assertEqual(state([]), "pending")
        self.assertEqual(state([ok, status_ok]), "success")
        self.assertEqual(state([ok, running]), "pending")
        self.assertEqual(state([running, failed]), "failure")


class DecideIssueTests(unittest.TestCase):
    def test_pause_stops_everything(self):
        plan = run([issue(1, [p.PAUSE]), issue(2, [p.AGENT_TASK, p.READY])])
        self.assertTrue(plan.paused)
        self.assertEqual((plan.dispatch, plan.ops), ([], []))

    def test_ready_issue_is_implemented(self):
        plan = run([issue(5, [p.AGENT_TASK, p.READY])])
        self.assertEqual(kinds(plan), [("implement", 5, None)])

    def test_unlabelled_agent_task_is_linted_then_implemented(self):
        plan = run([issue(5, [p.AGENT_TASK])])
        self.assertEqual(ops(plan, "set-status")[0]["status"], p.READY)
        self.assertEqual(kinds(plan), [("implement", 5, None)])

    def test_bad_spec_gets_needs_spec_and_a_comment(self):
        plan = run([issue(5, [p.AGENT_TASK], body=TEMPLATE_BODY)])
        self.assertEqual(ops(plan, "set-status")[0]["status"], p.NEEDS_SPEC)
        self.assertIn("issue-lint", ops(plan, "lint-note")[0]["body"])
        self.assertEqual(plan.dispatch, [])

    def test_blocked_becomes_ready_when_blocker_closes(self):
        body = READY_BODY.replace("nothing", "#3")
        plan = run([issue(5, [p.AGENT_TASK, p.BLOCKED], body=body)], states={3: "closed"})
        self.assertEqual(kinds(plan), [("implement", 5, None)])

    def test_shared_file_is_deferred(self):
        plan = run([issue(5, [p.AGENT_TASK, p.READY]), issue(6, [p.AGENT_TASK, p.READY])])
        self.assertEqual(kinds(plan), [("implement", 5, None)])
        self.assertTrue(any("#6" in d for d in plan.deferred))

    def test_shared_file_with_work_in_flight_is_deferred(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS]), issue(6, [p.AGENT_TASK, p.READY])])
        self.assertEqual(plan.dispatch, [])

    def test_implementer_cap(self):
        issues = [issue(n, [p.AGENT_TASK, p.READY], body=READY_BODY.replace("world.py", f"w{n}.py"))
                  for n in range(1, 6)]
        plan = run(issues)
        self.assertEqual(len(plan.dispatch), LIM.max_parallel_implement)

    def test_human_decision_and_asset_are_never_dispatched(self):
        plan = run([issue(5, [p.AGENT_TASK, p.READY, p.HUMAN_DECISION]),
                    issue(6, [p.AGENT_TASK, p.ASSET, p.READY])])
        self.assertEqual([d for d in plan.dispatch if d.get("issue") in (5, 6)], [])
        self.assertEqual(len(plan.awaiting_human), 2)

    def test_in_progress_with_open_pr_moves_to_in_review(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS])], [pr(105, branch="agent/5-x", checks="pending")])
        self.assertEqual(ops(plan, "set-status")[0]["status"], p.IN_REVIEW)

    def test_stale_in_progress_without_branch_is_reset_once(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS], updated=OLD)])
        self.assertEqual(ops(plan, "set-status")[0]["status"], None)
        self.assertEqual(ops(plan, "set-status")[0]["expect"], p.IN_PROGRESS)
        self.assertEqual(ops(plan, "lint")[0]["number"], 5)
        self.assertEqual(ops(plan, "add-label")[0]["label"], p.ATTEMPT)

    def test_stale_in_progress_with_a_pushed_branch_is_resumed(self):
        # It used to stay "implementer working" forever: a branch, no PR, no one.
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS], updated=OLD)], branches={"agent/5-x"})
        self.assertEqual([(d["kind"], d.get("branch"), d.get("resume")) for d in plan.dispatch],
                         [("implement", "agent/5-x", True)])
        self.assertEqual(ops(plan, "add-label")[0]["label"], p.ATTEMPT)

    def test_a_second_stale_end_escalates(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS, p.ATTEMPT], updated=OLD)],
                   branches={"agent/5-x"})
        self.assertEqual(plan.dispatch, [])
        self.assertEqual(ops(plan, "set-status")[0]["status"], p.ESCALATED)
        self.assertIn("agent/5-x", ops(plan, "comment")[0]["body"])

    def test_a_second_stale_end_after_triage_goes_to_the_owner(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS, p.ATTEMPT, p.TRIAGED], updated=OLD)])
        self.assertEqual(ops(plan, "set-status")[0]["status"], p.NEEDS_HUMAN)

    def test_unknown_branches_leave_a_stale_claim_alone(self):
        # git ls-remote failed: "no branch" would be a guess, and a wrong one
        # starts a second implementer beside pushed work.
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS], updated=OLD)], branches=None)
        self.assertEqual((plan.dispatch, ops(plan)), ([], []))
        self.assertTrue(any("ls-remote" in x for x in plan.setup_problems))

    def test_the_owner_holding_an_issue_is_never_reset(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS, p.HUMAN_HOLDS], updated=OLD)])
        self.assertEqual((plan.dispatch, ops(plan)), ([], []))
        self.assertTrue(any("owner holds" in x for x in plan.in_flight))

    def test_in_review_without_pr_escalates(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_REVIEW])])
        self.assertEqual(ops(plan, "set-status")[0]["status"], p.ESCALATED)

    def test_escalation_goes_to_triage(self):
        plan = run([issue(5, [p.AGENT_TASK, p.ESCALATED])])
        self.assertEqual(kinds(plan), [("triage", 5, None)])

    def test_second_escalation_after_triage_goes_to_a_human(self):
        plan = run([issue(5, [p.AGENT_TASK, p.ESCALATED, p.TRIAGED])])
        self.assertEqual(plan.dispatch, [])
        self.assertEqual(ops(plan, "set-status")[0]["status"], p.NEEDS_HUMAN)

    def test_triage_in_flight_is_left_alone(self):
        plan = run([issue(5, [p.AGENT_TASK, p.ESCALATED, p.WORKING])])
        self.assertEqual(plan.dispatch, [])

    def test_idea_goes_to_the_planner_once_per_tick(self):
        plan = run([issue(7, [p.IDEA]), issue(8, [p.IDEA])])
        self.assertEqual(kinds(plan), [("plan", 7, None)])

    def test_idea_waiting_on_the_owner_is_left_alone(self):
        plan = run([issue(7, [p.IDEA, p.NEEDS_HUMAN])])
        self.assertEqual([d for d in plan.dispatch if d.get("mode") == "idea"], [])
        self.assertEqual(len(plan.awaiting_human), 1)

    def test_empty_queue_asks_the_planner_for_a_batch(self):
        plan = run([])
        self.assertEqual([(d["kind"], d["mode"]) for d in plan.dispatch], [("plan", "roadmap")])

    def test_idle_issue_suppresses_roadmap_planning(self):
        plan = run([issue(9, [p.IDLE, p.NEEDS_HUMAN])])
        self.assertEqual(plan.dispatch, [])

    def test_needs_human_issues_do_not_block_planning(self):
        plan = run([issue(9, [p.AGENT_TASK, p.NEEDS_HUMAN])])
        self.assertEqual([d["kind"] for d in plan.dispatch], ["plan"])


class DecidePrTests(unittest.TestCase):
    def test_green_unreviewed_pr_is_reviewed(self):
        plan = run([], [pr(105)])
        self.assertEqual(kinds(plan), [("review", 5, 105)])

    def test_ci_running_waits(self):
        plan = run([], [pr(105, checks="pending")])
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(plan.waiting)

    def test_ci_failure_is_fixed_and_counted(self):
        plan = run([], [pr(105, checks="failure")])
        self.assertEqual(plan.dispatch[0]["kind"], "fix")
        self.assertEqual((plan.dispatch[0]["reason"], plan.dispatch[0]["round"]), ("ci-failed", 1))

    def test_fix_rounds_run_out_into_needs_human(self):
        plan = run([], [pr(105, checks="failure", labels=["fix-round-1", "fix-round-2"])])
        self.assertEqual(plan.dispatch, [])
        self.assertEqual(ops(plan, "add-label")[0]["label"], p.NEEDS_HUMAN)

    def test_conflict_passes_have_their_own_count(self):
        plan = run([], [pr(105, mergeable="CONFLICTING", labels=["fix-round-2"])])
        d = plan.dispatch[0]
        self.assertEqual((d["reason"], d["round"], d["round_label"]), ("conflict", 1, "conflict-round-1"))

    def test_conflict_passes_run_out_too(self):
        # They used to be free, so a conflict one pass could not resolve looped forever.
        plan = run([], [pr(105, mergeable="CONFLICTING", labels=["conflict-round-1", "conflict-round-2"])])
        self.assertEqual(plan.dispatch, [])
        self.assertEqual(ops(plan, "add-label")[0]["label"], p.NEEDS_HUMAN)

    def test_changes_requested_at_head_is_fixed(self):
        plan = run([], [pr(105, reviews=[review("CHANGES_REQUESTED")])])
        self.assertEqual(plan.dispatch[0]["reason"], "review")

    def test_approval_at_head_enables_auto_merge(self):
        plan = run([], [pr(105, reviews=[review("APPROVED")])])
        self.assertEqual(plan.dispatch, [])
        self.assertEqual(ops(plan, "enable-automerge")[0]["number"], 105)

    def test_approval_with_auto_merge_just_waits(self):
        plan = run([], [pr(105, reviews=[review("APPROVED")], auto=True)])
        self.assertEqual((plan.dispatch, plan.ops), ([], []))

    def test_approval_of_an_older_head_means_review_again(self):
        plan = run([], [pr(105, head="new", reviews=[review("APPROVED", commit="old")])])
        self.assertEqual(kinds(plan), [("review", 5, 105)])

    def test_someone_elses_approval_does_not_count(self):
        plan = run([], [pr(105, reviews=[review("APPROVED", login="someone")])])
        self.assertEqual(kinds(plan), [("review", 5, 105)])

    def test_latest_verdict_wins(self):
        plan = run([], [pr(105, reviews=[review("CHANGES_REQUESTED", at="2026-09-27T10:00:00Z"),
                                         review("APPROVED", at="2026-09-27T11:00:00Z")])])
        self.assertEqual(ops(plan, "enable-automerge")[0]["number"], 105)

    def test_comment_only_reviews_twice_go_to_a_human(self):
        plan = run([], [pr(105, reviews=[review("COMMENTED"), review("COMMENTED")])])
        self.assertEqual(ops(plan, "add-label")[0]["label"], p.NEEDS_HUMAN)

    def test_working_pr_is_left_alone_until_stale(self):
        self.assertEqual(run([], [pr(105, labels=[p.WORKING])]).dispatch, [])
        plan = run([], [pr(105, labels=[p.WORKING], updated=OLD)])
        self.assertEqual(ops(plan, "remove-label")[0]["label"], p.WORKING)
        self.assertEqual(kinds(plan), [("review", 5, 105)])

    def test_needs_human_prs_are_skipped(self):
        plan = run([], [pr(106, labels=[p.NEEDS_HUMAN])])
        self.assertEqual(plan.dispatch, [])

    def test_a_draft_whose_issue_is_back_in_the_queue_is_finished(self):
        # The draft an escalation left behind used to wait forever.
        plan = run([], [pr(105, draft=True)])
        self.assertEqual([(d["kind"], d["reason"]) for d in plan.dispatch], [("fix", "draft-resume")])

    def test_a_draft_is_not_resumed_while_its_issue_is_in_progress(self):
        # The implementer may be between opening the draft and escalating.
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS])], [pr(105, draft=True)])
        self.assertEqual([d for d in plan.dispatch if d.get("pr")], [])

    def test_a_draft_bound_to_no_issue_just_waits(self):
        plan = run([], [pr(105, branch="wip/x", draft=True)])
        self.assertEqual([d for d in plan.dispatch if d.get("pr")], [])
        self.assertTrue(any("draft" in w for w in plan.waiting))

    def test_review_cap(self):
        plan = run([], [pr(101 + k) for k in range(LIM.max_parallel_review + 2)])
        self.assertEqual(len(plan.dispatch), LIM.max_parallel_review)

    def test_no_reviewer_identity_blocks_reviews_but_not_fixes(self):
        plan = run([], [pr(105), pr(106, checks="failure")], reviewer=None)
        self.assertEqual([d["kind"] for d in plan.dispatch], ["fix"])
        self.assertTrue(plan.setup_problems)

    def test_open_pr_keeps_the_planner_quiet(self):
        plan = run([], [pr(105, checks="pending")])
        self.assertEqual(plan.dispatch, [])



class PrFollowsItsIssueTests(unittest.TestCase):
    """A PR moves only while its issue says it may (docs/Pipeline.md § Pull requests)."""

    def test_escalated_issue_holds_its_pr(self):
        # A fix pass escalated: triage and a second fix pass used to run in
        # parallel, and the second burned the last round on the same wall.
        rev = [review("CHANGES_REQUESTED")]
        plan = run([issue(5, [p.AGENT_TASK, p.ESCALATED])], [pr(105, reviews=rev, labels=["fix-round-1"])])
        self.assertEqual(kinds(plan), [("triage", 5, None)])
        self.assertTrue(any("PR #105" in w and "triage" in w for w in plan.waiting))

    def test_needs_human_issue_parks_its_pr(self):
        plan = run([issue(5, [p.AGENT_TASK, p.NEEDS_HUMAN])], [pr(105, checks="failure")])
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(any("PR #105" in w for w in plan.awaiting_human))

    def test_triage_answer_blocked_holds_the_pr_and_keeps_blocked(self):
        # Triage blocked #5 on #7; the open PR used to override that to in-review.
        body = READY_BODY.replace("nothing", "#7")
        plan = run([issue(5, [p.AGENT_TASK], body=body), issue(7, [p.AGENT_TASK, p.IN_PROGRESS],
                    body=scoped("src/other.py"))], [pr(105)], states={7: "open"})
        self.assertEqual([o["status"] for o in ops(plan, "set-status")], [p.BLOCKED])
        self.assertEqual([d for d in plan.dispatch if d.get("pr")], [])

    def test_triage_answer_ready_sends_the_issue_back_to_review(self):
        plan = run([issue(5, [p.AGENT_TASK, p.TRIAGED])], [pr(105)])
        self.assertEqual([o["status"] for o in ops(plan, "set-status")], [p.IN_REVIEW])
        self.assertTrue(any("re-judged" in w for w in plan.waiting))

    def test_a_pr_whose_issue_is_closed_goes_to_the_owner(self):
        plan = run([], [pr(105)], auto_issues=False)
        self.assertEqual([x for x in plan.dispatch if x.get("pr")], [])
        self.assertTrue(any("#5 is closed" in w for w in plan.awaiting_human))

    def test_but_not_when_the_survey_was_cut_short(self):
        plan = run([], [pr(105)], auto_issues=False, truncated=True)
        self.assertEqual(plan.awaiting_human, [])
        self.assertTrue(plan.setup_problems)

    def test_a_truncated_survey_does_not_escalate_in_review_issues(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_REVIEW])], truncated=True)
        self.assertEqual(ops(plan, "set-status"), [])

    def test_a_second_pr_for_one_issue_goes_to_the_owner(self):
        plan = run([], [pr(105), pr(106, branch="agent/5-again")])
        self.assertEqual(kinds(plan), [("review", 5, 105)])
        self.assertEqual([o["number"] for o in ops(plan, "add-label")], [106])

    def test_the_owners_pr_is_reviewed_but_never_fixed_by_an_agent(self):
        owned = issue(5, [p.AGENT_TASK, p.IN_REVIEW, p.HUMAN_HOLDS])
        self.assertEqual(kinds(run([owned], [pr(105)])), [("review", 5, 105)])
        plan = run([owned], [pr(105, checks="failure")])
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(any("owner is working" in w for w in plan.awaiting_human))

    def test_every_pr_has_an_owner(self):
        prs = [pr(101), pr(102, checks="failure"), pr(103, draft=True), pr(104, checks="pending"),
               pr(105, labels=[p.NEEDS_HUMAN]), pr(106, mergeable="CONFLICTING"),
               pr(107, branch="chore/x"), dict(pr(108), crossRepo=True),
               pr(109, labels=[p.WORKING]), pr(110, reviews=[review("APPROVED")], auto=True)]
        plan = run([], prs)
        assert_every_pr_has_an_owner(self, plan, prs)


class ChecksTests(unittest.TestCase):
    def test_a_first_failure_is_re_run_before_a_fix(self):
        plan = run([], [dict(pr(105, checks="failure"),
                             failedRuns=[{"name": "ci", "run": 9, "attempt": 1}])])
        self.assertEqual(plan.dispatch, [])
        self.assertEqual([(o["op"], o["run"]) for o in ops(plan, "rerun")], [("rerun", 9)])

    def test_a_failure_after_a_re_run_gets_a_fix(self):
        plan = run([], [dict(pr(105, checks="failure"),
                             failedRuns=[{"name": "ci", "run": 9, "attempt": 2}])])
        self.assertEqual(plan.dispatch[0]["reason"], "ci-failed")
        self.assertEqual(ops(plan, "rerun"), [])

    def test_ci_that_never_finishes_goes_to_the_owner(self):
        old_head = (NOW - timedelta(hours=LIM.stale_waiting.total_seconds() / 3600 + 1)).isoformat()
        plan = run([], [dict(pr(105, checks="pending"), headAt=old_head)])
        self.assertEqual(ops(plan, "add-label")[0]["label"], p.NEEDS_HUMAN)

    def test_a_re_run_on_an_old_commit_is_not_stuck(self):
        # The tick re-ran a check on a commit from yesterday: the clock starts
        # when the check did, not when the commit was made.
        day_old = (NOW - timedelta(days=1)).isoformat()
        plan = run([], [dict(pr(105, checks="pending"), headAt=day_old,
                             checksSince=(NOW - timedelta(minutes=5)).isoformat())])
        self.assertEqual(ops(plan, "add-label"), [])
        self.assertTrue(any("CI running" in w for w in plan.waiting))

    def test_jobs_of_one_run_are_re_run_once(self):
        plan = run([], [dict(pr(105, checks="failure"),
                             failedRuns=[{"name": "ci", "run": 9, "attempt": 1},
                                         {"name": "tooling", "run": 9, "attempt": 1}])])
        self.assertEqual([o["run"] for o in ops(plan, "rerun")], [9])

    def test_an_approval_that_never_merges_goes_to_the_owner(self):
        at = (NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z")
        plan = run([], [pr(105, reviews=[review("APPROVED", at=at)], auto=True)])
        self.assertEqual(ops(plan, "add-label")[0]["label"], p.NEEDS_HUMAN)

    def test_auto_merge_is_pinned_to_the_approved_head(self):
        plan = run([], [pr(105, head="abc", reviews=[review("APPROVED")])])
        self.assertEqual(ops(plan, "enable-automerge")[0]["head"], "abc")

    def test_reviews_that_never_end_in_a_verdict_go_to_the_owner(self):
        plan = run([], [dict(pr(105), reviewAttempts=LIM.max_review_attempts)])
        self.assertEqual(plan.dispatch, [])
        self.assertEqual(ops(plan, "add-label")[0]["label"], p.NEEDS_HUMAN)

    def test_a_review_carries_its_head_and_attempt(self):
        plan = run([], [dict(pr(105, head="abc"), reviewAttempts=1)])
        d = plan.dispatch[0]
        self.assertEqual((d["head"], d["attempt"]), ("abc", 2))

    def test_comment_only_hand_off_explains_itself(self):
        plan = run([], [pr(105, reviews=[review("COMMENTED"), review("COMMENTED")])])
        self.assertIn("no verdict", ops(plan, "comment")[0]["body"])

    def test_one_comment_only_review_goes_to_the_owner(self):
        # A bot COMMENT review is a NEEDS_HUMAN verdict; without the label the
        # reviewer's label edit was refused. One review is enough (#81).
        plan = run([], [pr(105, reviews=[review("COMMENTED")])])
        self.assertEqual([o["label"] for o in ops(plan, "add-label")], [p.NEEDS_HUMAN])
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(any("PR #105" in w for w in plan.awaiting_human), plan.awaiting_human)

    def test_a_new_comment_review_after_the_answer_goes_to_the_owner(self):
        # The owner answered the first question; the reviewer asked again at
        # the same head, and its label edit was refused once more.
        again = dict(pr(105, reviews=[review("COMMENTED"), review("COMMENTED")]), answeredComments=1)
        plan = run([], [again])
        self.assertEqual([o["label"] for o in ops(plan, "add-label")], [p.NEEDS_HUMAN])
        self.assertEqual(plan.dispatch, [])


class MainHealthTests(unittest.TestCase):
    RED = {"sha": "deadbeef00", "red": ["ci"]}

    def test_red_main_holds_fixes_and_implementations_and_opens_one_issue(self):
        plan = run([issue(5, [p.AGENT_TASK, p.READY], body=scoped("src/new.py"))],
                   [pr(105, checks="failure")], main=self.RED)
        self.assertEqual(plan.dispatch, [])
        self.assertEqual([o["op"] for o in plan.ops if o["op"] == "create-issue"], ["create-issue"])
        self.assertTrue(any("is red" in w for w in plan.awaiting_human))

    def test_the_red_issue_is_opened_once(self):
        plan = run([issue(9, [p.MAIN_RED, p.NEEDS_HUMAN])], main=self.RED)
        self.assertEqual(ops(plan, "create-issue"), [])

    def test_green_again_closes_it(self):
        plan = run([issue(9, [p.MAIN_RED, p.NEEDS_HUMAN])], main={"sha": "cafe", "red": [], "state": "success"})
        self.assertEqual([o["number"] for o in ops(plan, "close-issue")], [9])

    def test_still_running_does_not_close_it(self):
        plan = run([issue(9, [p.MAIN_RED, p.NEEDS_HUMAN])], main={"sha": "cafe", "red": [], "state": "pending"})
        self.assertEqual(ops(plan, "close-issue"), [])

    def test_reviews_go_on_while_main_is_red(self):
        plan = run([], [pr(105)], main=self.RED)
        self.assertEqual(kinds(plan), [("review", 5, 105)])


class LabelInvariantTests(unittest.TestCase):
    def test_precedence_picks_the_status_that_stops_most(self):
        self.assertEqual(p.status_of({p.READY, p.NEEDS_HUMAN}), p.NEEDS_HUMAN)
        self.assertEqual(p.status_of({p.READY, p.IN_PROGRESS}), p.IN_PROGRESS)

    def test_two_status_labels_are_normalised(self):
        # The owner added needs-human by hand; ready stayed. It used to be
        # dispatched anyway, and the claim deleted the owner's label.
        plan = run([issue(5, [p.AGENT_TASK, p.READY, p.NEEDS_HUMAN])])
        self.assertEqual([d for d in plan.dispatch if d.get("issue") == 5], [])
        self.assertEqual(ops(plan, "set-status")[0]["status"], p.NEEDS_HUMAN)

    def test_a_fresh_claim_on_a_ready_issue_is_respected(self):
        # Triage is still finishing: its WORKING claim must stop an implementer.
        plan = run([issue(5, [p.AGENT_TASK, p.READY, p.WORKING])])
        self.assertEqual(plan.dispatch, [])

    def test_a_stale_idea_claim_is_removed_before_the_next_planner(self):
        plan = run([issue(7, [p.IDEA, p.WORKING], updated=OLD)])
        self.assertEqual(ops(plan, "remove-label")[0]["label"], p.WORKING)
        self.assertEqual(kinds(plan), [("plan", 7, None)])

    def test_every_round_the_limits_allow_has_a_label(self):
        for k in range(1, p.LIMITS.max_fix_rounds + 1):
            self.assertIn(f"{p.FIX_ROUND}{k}", p.LABELS)
        for k in range(1, p.LIMITS.max_conflict_rounds + 1):
            self.assertIn(f"{p.CONFLICT_ROUND}{k}", p.LABELS)


class CapTests(unittest.TestCase):
    def test_fix_passes_are_capped(self):
        # Five red PRs used to start five implementers at once.
        plan = run([], [pr(101 + k, checks="failure") for k in range(5)])
        self.assertEqual(len(plan.dispatch), LIM.max_parallel_fix)
        self.assertEqual(sum("fix cap" in d for d in plan.deferred), 5 - LIM.max_parallel_fix)

    def test_reviews_already_running_count_against_the_cap(self):
        running = [pr(101 + k, labels=[p.WORKING, p.REVIEWING]) for k in range(LIM.max_parallel_review)]
        plan = run([], running + [pr(110)])
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(any("review cap" in d for d in plan.deferred))

    def test_triage_is_capped(self):
        issues = [issue(n, [p.AGENT_TASK, p.ESCALATED]) for n in range(1, 5)]
        plan = run(issues)
        self.assertEqual(len(plan.dispatch), LIM.max_parallel_triage)

    def test_overlap_counts_an_escalated_issues_open_pr(self):
        plan = run([issue(5, [p.AGENT_TASK, p.ESCALATED]), issue(6, [p.AGENT_TASK, p.READY])], [pr(105)])
        self.assertEqual([d for d in plan.dispatch if d.get("issue") == 6], [])

    def test_overlap_counts_work_in_review(self):
        # #6 shares a file with #5, whose PR may still get fix passes.
        plan = run([issue(6, [p.AGENT_TASK, p.READY])], [pr(105)])
        self.assertEqual([d for d in plan.dispatch if d.get("issue") == 6], [])
        self.assertTrue(any("#6" in d for d in plan.deferred))

    def test_one_planner_at_a_time(self):
        plan = run([issue(7, [p.IDEA, p.WORKING]), issue(8, [p.IDEA])])
        self.assertEqual(plan.dispatch, [])

    def test_a_running_roadmap_planner_blocks_another(self):
        plan = run([issue(9, [p.PLANNING, p.WORKING])])
        self.assertEqual(plan.dispatch, [])

    def test_a_stale_planning_issue_is_closed(self):
        plan = run([issue(9, [p.PLANNING, p.WORKING], updated=OLD)])
        self.assertEqual([o["number"] for o in ops(plan, "close-issue")], [9])

    def test_a_tick_dispatches_at_most_the_cap(self):
        lim = p.Limits(**{**LIM.__dict__, "max_dispatch_per_tick": 2, "max_parallel_fix": 9})
        plan = run([], [pr(101 + k, checks="failure") for k in range(5)], limits=lim)
        self.assertEqual(len(plan.dispatch), 2)
        self.assertEqual(sum("dispatch cap" in d for d in plan.deferred), 3)

    def test_an_idle_gate_is_reported(self):
        plan = run([issue(9, [p.IDLE, p.NEEDS_HUMAN])])
        self.assertTrue(any("roadmap gate" in w for w in plan.awaiting_human))



class OwnerAnswerTests(unittest.TestCase):
    """docs/Pipeline.md § What stays with the owner: the owner answers with a
    comment and `human:answered`, and the item resumes where it stopped."""

    def test_an_answered_pr_gets_its_rounds_back(self):
        prs = [pr(105, labels=[p.NEEDS_HUMAN, p.ANSWERED, "fix-round-1", "fix-round-2"],
                  reviews=[review("COMMENTED"), review("COMMENTED")])]
        plan = run([], prs)
        removed = sorted(o["label"] for o in ops(plan, "remove-label"))
        self.assertEqual(removed, sorted([p.NEEDS_HUMAN, p.ANSWERED, "fix-round-1", "fix-round-2"]))
        status = ops(plan, "post-status")[0]
        self.assertEqual((status["context"], status["sha"]), (p.ANSWERED_STATUS, "abc"))
        self.assertIn("comments=2", status["description"])
        self.assertEqual(plan.dispatch, [])

    def test_after_the_answer_old_comment_reviews_no_longer_lock_the_pr(self):
        # Two comment-only reviews used to re-label the PR every tick, however
        # often the owner took the label off.
        answered = dict(pr(105, reviews=[review("COMMENTED"), review("COMMENTED")]),
                        answeredComments=2, reviewAttempts=2, answeredAttempts=2)
        plan = run([], [answered])
        self.assertEqual(kinds(plan), [("review", 5, 105)])
        self.assertEqual(ops(plan, "add-label"), [])

    def test_an_answered_issue_goes_back_to_triage_even_if_triaged(self):
        plan = run([issue(5, [p.AGENT_TASK, p.NEEDS_HUMAN, p.ANSWERED, p.TRIAGED])])
        self.assertEqual([(o["status"], o["expect"]) for o in ops(plan, "set-status")],
                         [(p.ESCALATED, p.NEEDS_HUMAN)])
        self.assertEqual(sorted(o["label"] for o in ops(plan, "remove-label")), [p.ANSWERED, p.TRIAGED])
        self.assertEqual([(d["kind"], d["reason"]) for d in plan.dispatch], [("triage", "owner-answered")])

    def test_an_answered_idea_goes_back_to_the_planner(self):
        plan = run([issue(7, [p.IDEA, p.NEEDS_HUMAN, p.ANSWERED])])
        self.assertEqual(sorted(o["label"] for o in ops(plan, "remove-label")), [p.ANSWERED, p.NEEDS_HUMAN])
        self.assertEqual(plan.dispatch, [])       # not the roadmap planner either

    def test_hand_offs_mention_the_owner_and_go_out_as_the_bot(self):
        plan = run([], [pr(105, checks="failure", labels=["fix-round-1", "fix-round-2"])],
                   owner_login="SirTobi88")
        note = ops(plan, "comment")[0]
        self.assertTrue(note["body"].startswith("@SirTobi88 "))
        self.assertTrue(note["as_bot"])
        self.assertIn(p.ANSWERED, note["body"])

    def test_ordinary_comments_are_not_hand_offs(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_REVIEW])], owner_login="SirTobi88")
        self.assertFalse(ops(plan, "comment")[0]["as_bot"])

    def test_fix_passes_learn_who_the_reviewer_is(self):
        plan = run([], [pr(105, reviews=[review("CHANGES_REQUESTED")])])
        self.assertEqual(plan.dispatch[0]["reviewer"], REVIEWER)


class NeedsYouTests(unittest.TestCase):
    def test_owner_work_without_agent_task_is_listed(self):
        # asset-task.md files an asset without agent-task; it used to vanish.
        plan = run([issue(5, [p.ASSET]), issue(6, [p.HUMAN_DECISION])])
        self.assertEqual(len([w for w in plan.awaiting_human if "#5" in w or "#6" in w]), 2)

    def test_any_needs_human_issue_is_listed(self):
        plan = run([issue(5, [p.NEEDS_HUMAN])])
        self.assertTrue(any("#5" in w for w in plan.awaiting_human))

    def test_the_red_main_issue_is_listed_once(self):
        plan = run([issue(9, [p.MAIN_RED, p.NEEDS_HUMAN])], main={"sha": "d", "red": ["ci"]})
        self.assertEqual(sum("red" in w for w in plan.awaiting_human), 1)


class SyncTests(unittest.TestCase):
    def fake(self, branch=p.DEFAULT_BRANCH, dirty="", fetch_rc=0, merge_rc=0):
        from types import SimpleNamespace
        calls = []

        def run(args, **_):
            calls.append(args[1])
            rc = {"fetch": fetch_rc, "merge": merge_rc}.get(args[1], 0)
            out = {"rev-parse": branch + "\n", "status": dirty}.get(args[1], "")
            return SimpleNamespace(returncode=rc, stdout=out, stderr="boom" if rc else "")
        return run, calls

    def test_a_clean_default_checkout_is_fast_forwarded(self):
        run_, calls = self.fake()
        self.assertEqual(p.sync_checkout(Path("."), run=run_), [])
        self.assertIn("merge", calls)

    def test_a_dirty_or_other_checkout_is_left_alone(self):
        for kw in ({"dirty": " M x.py"}, {"branch": "feature/x"}):
            run_, calls = self.fake(**kw)
            self.assertEqual(p.sync_checkout(Path("."), run=run_), [])
            self.assertNotIn("merge", calls)

    def test_failures_are_reported(self):
        run_, _ = self.fake(fetch_rc=1)
        self.assertTrue(p.sync_checkout(Path("."), run=run_))
        run_, _ = self.fake(merge_rc=1)
        self.assertIn("fast-forward", p.sync_checkout(Path("."), run=run_)[0])


class GrammarTests(unittest.TestCase):
    """The Python half of the one grammar (issue_scope.sh documents it)."""

    def test_a_heading_inside_a_code_fence_is_content(self):
        body = READY_BODY.replace("## Interface\n\n```gdscript\n",
                                  "## Interface\n\n```gdscript\n## not a heading\n")
        sections = p.split_sections(body)
        self.assertIn("## not a heading", sections["interface"])
        self.assertEqual(p.lint_body(5, body, fake_allowlist, lambda n: "closed").problems, [])

    def test_the_first_section_of_a_name_counts(self):
        body = "## Files in scope\n\n- `a.py`\n\n## Files in scope\n\n- `b.py`\n"
        self.assertIn("a.py", p.split_sections(body)["files in scope"])

    def test_list_items_are_the_top_level_ones(self):
        text = "- [ ] `a.py` (x)\n  - `note.py`\n+ `b.py`\n1. `c.py`\n2) d.py\nprose `e.py`"
        self.assertEqual(p.list_items(text), ["`a.py` (x)", "`b.py`", "`c.py`", "d.py"])

    def lint(self, scope_text, repo_root=None):
        body = READY_BODY.split("## Files in scope")[0] + "## Files in scope\n\n" + scope_text + \
            "\n\n## Non-goals" + READY_BODY.split("## Non-goals")[1]
        kw = {"repo_root": repo_root} if repo_root else {}
        return p.lint_body(5, body, lambda b: ["x.py"], lambda n: "closed", **kw)

    def test_prose_instead_of_a_list_is_warned_about(self):
        r = self.lint("Edit `src/a.py`, but `src/b.py` belongs to #12.")
        self.assertTrue(any("every backticked path" in w for w in r.warnings), r.warnings)

    def test_paths_only_under_group_headings_are_warned_about(self):
        r = self.lint("- Source:\n  - `src/a.py`\n- Tests:\n  - `tests/b.py`")
        self.assertTrue(any("every backticked path" in w for w in r.warnings), r.warnings)

    def test_a_proper_list_gets_no_grammar_warning(self):
        r = self.lint("- [ ] `src/a.py` (modify) -- not `src/b.py`\n  - a note")
        self.assertEqual([w for w in r.warnings if "path" in w], [])

    def test_a_second_path_in_an_item_is_warned_about(self):
        r = self.lint("- `src/a.py`, `src/b.py` (modify)")
        self.assertTrue(any("only the first path" in w for w in r.warnings), r.warnings)

    def test_an_existing_directory_without_a_slash_is_warned_about(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".github").mkdir()
            body = READY_BODY
            r = p.lint_body(5, body, lambda b: [".github", "src/a.py"], lambda n: "closed",
                            repo_root=Path(d))
        self.assertTrue(any("`.github/`" in w for w in r.warnings), r.warnings)

    def test_bracket_globs_and_companions_overlap(self):
        self.assertTrue(p.paths_overlap("src/[ab].py", "src/a.py", companions=()))
        self.assertTrue(p.paths_overlap("src/a.gd", "src/a.gd.uid", companions=(".uid",)))
        self.assertFalse(p.paths_overlap("src/a.gd", "src/a.gd.uid", companions=()))



class LintNoteTests(unittest.TestCase):
    def result(self, problems=(), warnings=()):
        return p.LintResult(problems=list(problems), warnings=list(warnings))

    def test_the_note_says_what_matters_most(self):
        blocked = p.lint_comment(5, self.result(["missing section `## Goal`"], ["L12 rots"]))
        self.assertIn("not ready", blocked)
        self.assertIn("Also worth fixing", blocked)
        warned = p.lint_comment(5, self.result(warnings=["`## Interface` anchors on line numbers"]))
        self.assertIn("is ready, but worth fixing", warned)
        self.assertEqual(p.lint_comment(5, self.result()), f"{p.LINT_MARKER}\n**issue-lint: #5 is ready.**")

    def test_a_ready_issue_with_warnings_gets_a_note(self):
        # Line anchors were warned about and then dropped when the issue was
        # otherwise ready -- the escalation came later, when the lines moved.
        body = READY_BODY.replace("## Context\n", "## Context\n\nSee world.py L12-30.\n")
        plan = run([issue(5, [p.AGENT_TASK], body=body)])
        note = ops(plan, "lint-note")[0]
        self.assertIn("line numbers", note["body"])
        self.assertFalse(note["clean"])

    def test_a_clean_issue_gets_a_clean_note(self):
        plan = run([issue(5, [p.AGENT_TASK])])
        self.assertTrue(ops(plan, "lint-note")[0]["clean"])

    def test_too_big_is_a_problem_other_sizes_are_not(self):
        big = READY_BODY + "\n## Size\n\n- [ ] Small\n- [x] Too big — split before filing\n"
        self.assertTrue(any("Too big" in x for x in p.lint_body(5, big, fake_allowlist, lambda n: "closed").problems))
        small = READY_BODY + "\n## Size\n\n- [x] Small — one file\n- [ ] Too big — split before filing\n"
        self.assertEqual(p.lint_body(5, small, fake_allowlist, lambda n: "closed").problems, [])


class BaseBranchTests(unittest.TestCase):
    def test_a_stacked_pr_goes_to_the_owner_and_gets_no_agent(self):
        # PR #9 was merged into its stacked base: nothing reached main, and
        # its "Closes #8" did nothing.
        plan = run([], [dict(pr(105, checks="failure"), base="fix/other-branch")])
        self.assertEqual([d for d in plan.dispatch if d.get("pr")], [])
        self.assertTrue(any("targets fix/other-branch" in w for w in plan.awaiting_human))

    def test_a_pr_on_the_default_branch_is_unaffected(self):
        plan = run([], [dict(pr(105), base=p.DEFAULT_BRANCH)])
        self.assertEqual(kinds(plan), [("review", 5, 105)])


@unittest.skipUnless(p.bash_path(), "bash not available")
class RealParserTests(unittest.TestCase):
    def test_bash_and_python_agree_on_what_an_entry_covers(self):
        # Two readings of one allowlist that disagree do not announce it; they
        # schedule two issues together that write one file. Wherever the bash
        # parser lets an entry cover a path, the scheduler must see an overlap.
        import subprocess
        table = [
            ("src/a.py", "src/a.py"), ("src/", "src/x/y.py"), ("src/ui", "src/ui/x.gd"),
            ("src/*.py", "src/a.py"), ("src/*.py", "src/d/a.py"), ("src/**", "src/d/e/a.py"),
            ("docs/**/*.md", "docs/x.md"), ("docs/**/*.md", "docs/a/b/x.md"), ("**/x.gd", "x.gd"),
            ("**/x.gd", "deep/x.gd"), ("src/[ab].py", "src/a.py"), (".github/", ".github/workflows/ci.yml"),
            (".github", ".github/ci.yml"), ("a b/c.png", "a b/c.png"), ("src/a.py", "src/a.pyc"),
            ("github/", ".github/x"), ("src/x.gd", "src/x.gd.uid"),
        ]
        script = '. "$1"; shift; while [ $# -gt 1 ]; do _scope_entry_covers "$1" "$2" && echo yes || echo no; shift 2; done'
        args = [a for pair in table for a in pair]
        out = subprocess.run([p.bash_path(), "-c", script, "_", p.SCOPE_LIB.as_posix(), *args],
                             capture_output=True, text=True, encoding="utf-8").stdout.split()
        self.assertEqual(len(out), len(table))
        for (entry, path), covered in zip(table, out):
            if covered == "yes":
                self.assertTrue(p.paths_overlap(entry, path, companions=()),
                                f"bash: `{entry}` covers `{path}`, the scheduler sees no overlap")

    def test_a_parser_that_fails_raises_instead_of_reading_empty(self):
        # A bash that cannot source the parser used to return [] silently, and
        # every ready issue was linted needs-spec.
        orig = p.SCOPE_LIB
        try:
            p.SCOPE_LIB = Path(__file__).parent / "no-such-parser.sh"
            with self.assertRaises(RuntimeError):
                p.parse_allowlist("## Files in scope\n\n- `a.py`\n")
        finally:
            p.SCOPE_LIB = orig

    def test_parser_reads_the_leading_token_only(self):
        body = ("## Files in scope\n\n- [ ] `src/a.py` (modify) -- not "
                "`src/b.py`, that is #12's\n- `assets/ui/` (create)\n\n## Non-goals\n")
        self.assertEqual(p.parse_allowlist(body), ["assets/ui/", "src/a.py"])


class ConfigTests(unittest.TestCase):
    def test_missing_file_means_defaults(self):
        cfg = p.load_config(Path(__file__).parent / "no-such-config.json")
        self.assertEqual(cfg, p.DEFAULT_CONFIG)

    def test_partial_config_keeps_the_other_defaults(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"default_branch": "trunk",
                                        "limits": {"max_fix_rounds": 5}}), encoding="utf-8")
            cfg = p.load_config(path)
        self.assertEqual(cfg["default_branch"], "trunk")
        self.assertEqual(cfg["limits"]["max_fix_rounds"], 5)
        self.assertEqual(cfg["limits"]["max_parallel_review"],
                         p.DEFAULT_CONFIG["limits"]["max_parallel_review"])
        self.assertEqual(cfg["required_checks"], p.DEFAULT_CONFIG["required_checks"])

    def test_shipped_config_is_valid_and_complete(self):
        shipped = p.load_config()
        self.assertEqual(set(shipped), set(p.DEFAULT_CONFIG))
        self.assertIn("ci", shipped["required_checks"])

    def test_the_pipeline_itself_is_a_control_path(self):
        # Removing these would let an agent issue loosen the rules agents run by.
        for path in (".github/", ".claude/"):
            self.assertIn(path, p.load_config()["control_paths"])


def scoped(*paths):
    """READY_BODY with a different Files in scope."""
    scope = "\n".join(f"- [ ] `{x}` (modify)" for x in paths)
    head, rest = READY_BODY.split("## Files in scope", 1)
    return head + "## Files in scope\n\n" + scope + "\n\n## Non-goals" + rest.split("## Non-goals", 1)[1]


class ControlPathTests(unittest.TestCase):
    def test_an_issue_touching_the_pipeline_is_never_dispatched(self):
        plan = run([issue(5, ["agent-task"], body=scoped(".github/workflows/ci.yml", "src/a.py"))])
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(any("#5" in w and ".github/" in w for w in plan.awaiting_human), plan.awaiting_human)

    def test_a_directory_entry_that_contains_a_control_path_counts(self):
        plan = run([issue(5, ["agent-task"], body=scoped("docs/"))])
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(any("docs/Pipeline.md" in w for w in plan.awaiting_human), plan.awaiting_human)

    def test_ordinary_scope_is_dispatched(self):
        plan = run([issue(5, ["agent-task"], body=scoped("src/a.py", "docs/Economy.md"))])
        self.assertEqual(kinds(plan), [("implement", 5, None)])

    def test_control_paths_touched(self):
        self.assertEqual(p.control_paths_touched(["src/a.py"]), [])
        self.assertEqual(p.control_paths_touched([".claude/hooks/x.sh", "run_tests.sh"]),
                         [".claude/", "run_tests.sh"])
        # `.github/` is not `github/`: only a leading ./ or / is dropped.
        self.assertEqual(p.control_paths_touched(["github/x.py", "claude/y.md"]), [])

    def test_an_unreadable_scope_is_deferred_not_dispatched(self):
        def broken(_body):
            raise RuntimeError("bash not found")
        snap = {"now": NOW, "issues": [issue(5, ["agent-task"])], "prs": [],
                "reviewer_login": REVIEWER, "remote_branches": set()}
        plan = p.decide(snap, broken, lint_with())
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(any("#5" in d for d in plan.deferred), plan.deferred)


class PrAdmissionTests(unittest.TestCase):
    """Which pull requests the agents may touch at all."""

    def test_a_fork_pr_goes_to_the_owner(self):
        fork = dict(pr(105, checks="failure"), crossRepo=True)
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS])], [fork])
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(any("PR #105" in w and "fork" in w for w in plan.awaiting_human))
        # ...and never stands in for the issue's own pull request.
        self.assertNotIn(p.IN_REVIEW, [o.get("status") for o in ops(plan, "set-status")])

    def test_a_pr_bound_to_no_issue_goes_to_the_owner(self):
        plan = run([], [pr(105, branch="chore/tidy")])
        self.assertEqual([d for d in plan.dispatch if d.get("pr")], [])
        # ...and, not being pipeline work, does not keep the planner idle.
        self.assertEqual([d["kind"] for d in plan.dispatch], ["plan"])
        self.assertTrue(any("PR #105" in w and "no issue" in w for w in plan.awaiting_human))

    def test_no_fix_pass_on_a_human_decision_issue(self):
        plan = run([issue(5, [p.AGENT_TASK, p.HUMAN_DECISION, p.IN_REVIEW])], [pr(105, checks="failure")])
        self.assertEqual(plan.dispatch, [])
        self.assertTrue(any("owner-held issue #5" in w for w in plan.awaiting_human))

    def test_no_review_of_a_control_path_issue(self):
        owner = issue(5, [p.AGENT_TASK, p.IN_REVIEW], body=scoped(".claude/settings.json"))
        plan = run([owner], [pr(105)])
        self.assertEqual(plan.dispatch, [])

    def test_an_ordinary_agent_pr_is_still_reviewed(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_REVIEW])], [pr(105)])
        self.assertEqual(kinds(plan), [("review", 5, 105)])


def as_github_returns_it(payload):
    """A protection payload in the GET shape: booleans wrapped as {"enabled": x}."""
    out = dict(payload)
    for key in ("enforce_admins", "required_linear_history", "allow_force_pushes", "allow_deletions"):
        out[key] = {"enabled": payload[key]}
    return out


class ProtectionTests(unittest.TestCase):
    # These pin the rules GitHub enforces. An agent PR that "simplifies" the
    # payload changes a test too -- and both are control paths, so a human
    # sees it (docs/Pipeline.md § What stays with the owner).
    def test_payload_is_pinned(self):
        payload = p.protection_payload(4242)
        self.assertTrue(payload["enforce_admins"])
        self.assertTrue(payload["required_linear_history"])
        self.assertFalse(payload["allow_force_pushes"])
        self.assertFalse(payload["allow_deletions"])
        reviews = payload["required_pull_request_reviews"]
        self.assertEqual(reviews["required_approving_review_count"], 1)
        self.assertTrue(reviews["dismiss_stale_reviews"])
        self.assertFalse(reviews["require_last_push_approval"])
        self.assertEqual({c["context"] for c in payload["required_status_checks"]["checks"]},
                         set(p.REQUIRED_CHECKS))
        self.assertEqual({c["app_id"] for c in payload["required_status_checks"]["checks"]}, {4242})

    def test_no_drift_when_github_matches(self):
        payload = p.protection_payload()
        self.assertEqual(p.protection_drift(as_github_returns_it(payload), payload), [])

    def test_drift_is_reported(self):
        payload = p.protection_payload()
        actual = as_github_returns_it(payload)
        actual["enforce_admins"] = {"enabled": False}
        actual["required_status_checks"] = {"checks": [{"context": "ci", "app_id": 1}]}
        drift = p.protection_drift(actual, payload)
        self.assertTrue(any("enforce_admins" in d for d in drift), drift)
        self.assertTrue(any("allowlist" in d and "missing" in d for d in drift), drift)
        self.assertTrue(any("app 1" in d for d in drift), drift)

    def test_no_protection_at_all(self):
        self.assertEqual(p.protection_drift(None, p.protection_payload()), ["no branch protection"])


class PreflightTests(unittest.TestCase):
    def fake_git(self, status="", branch=p.DEFAULT_BRANCH):
        from types import SimpleNamespace

        def run(args, **_):
            out = status if "status" in args else branch + "\n"
            return SimpleNamespace(returncode=0, stdout=out)
        return run

    def check(self, root, **git):
        return p.preflight(root, run=self.fake_git(**git), which=lambda _: "/usr/bin/x",
                           bash=lambda: "/bin/bash")

    def test_clean_machine(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.check(Path(d)), [])

    def test_missing_text_tools_are_reported(self):
        # Without them the bash guard refuses every Bash command (#78), so the
        # tick should say so (#85).
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            problems = p.preflight(Path(d), run=self.fake_git(),
                                   which=lambda t: None if t in ("tr", "grep") else "/usr/bin/" + t,
                                   bash=lambda: "/bin/bash")
        named = [x for x in problems if "tr" in x.split(" not on PATH")[0]]
        self.assertEqual(len(named), 1, problems)
        self.assertIn("grep", named[0])
        self.assertIn("bash guard", named[0])

    def test_local_settings_dirty_tree_and_wrong_branch_are_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".claude").mkdir()
            (Path(d) / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")
            problems = self.check(Path(d), status=" M .claude/hooks/bash_guard.sh\n", branch="feature/x")
        self.assertEqual(len(problems), 3, problems)
        self.assertTrue(any("settings.local.json" in x for x in problems))
        self.assertTrue(any("bash_guard.sh" in x for x in problems))
        self.assertTrue(any("feature/x" in x for x in problems))

    def test_missing_jq_is_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            problems = p.preflight(Path(d), run=self.fake_git(), which=lambda _: None,
                                   bash=lambda: "/bin/bash")
        self.assertTrue(any("jq" in x for x in problems), problems)


if __name__ == "__main__":
    unittest.main()
