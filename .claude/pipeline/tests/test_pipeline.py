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


def run(issues=(), prs=(), reviewer=REVIEWER, branches=(), states=None):
    snap = {"now": NOW, "issues": list(issues), "prs": list(prs),
            "reviewer_login": reviewer, "remote_branches": set(branches)}
    return p.decide(snap, fake_allowlist, lint_with(states))


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


class RollupTests(unittest.TestCase):
    def test_states(self):
        ok = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}
        running = {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": ""}
        failed = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}
        status_ok = {"__typename": "StatusContext", "state": "SUCCESS", "context": "x"}
        self.assertEqual(p.rollup_state([]), "pending")
        self.assertEqual(p.rollup_state([ok, status_ok]), "success")
        self.assertEqual(p.rollup_state([ok, running]), "pending")
        self.assertEqual(p.rollup_state([running, failed]), "failure")


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
        self.assertIn("issue-lint", ops(plan, "comment")[0]["body"])
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
        self.assertEqual(len(plan.dispatch), p.MAX_PARALLEL_IMPLEMENT)

    def test_human_decision_and_asset_are_never_dispatched(self):
        plan = run([issue(5, [p.AGENT_TASK, p.READY, p.HUMAN_DECISION]),
                    issue(6, [p.AGENT_TASK, p.ASSET, p.READY])])
        self.assertEqual([d for d in plan.dispatch if d.get("issue") in (5, 6)], [])
        self.assertEqual(len(plan.awaiting_human), 2)

    def test_in_progress_with_open_pr_moves_to_in_review(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS])], [pr(105, branch="agent/5-x", checks="pending")])
        self.assertEqual(ops(plan, "set-status")[0]["status"], p.IN_REVIEW)

    def test_stale_in_progress_without_branch_is_reset(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS], updated=OLD)])
        self.assertEqual(ops(plan, "set-status")[0]["status"], None)
        self.assertEqual(ops(plan, "lint")[0]["number"], 5)

    def test_stale_in_progress_with_branch_is_left_alone(self):
        plan = run([issue(5, [p.AGENT_TASK, p.IN_PROGRESS], updated=OLD)], branches={"agent/5-x"})
        self.assertEqual(ops(plan), [])

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

    def test_conflict_is_fixed_without_spending_a_round(self):
        plan = run([], [pr(105, mergeable="CONFLICTING", labels=["fix-round-2"])])
        self.assertEqual((plan.dispatch[0]["reason"], plan.dispatch[0]["round"]), ("conflict", 2))

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

    def test_draft_and_needs_human_prs_are_skipped(self):
        plan = run([], [pr(105, draft=True), pr(106, labels=[p.NEEDS_HUMAN])])
        self.assertEqual(plan.dispatch, [])

    def test_review_cap(self):
        plan = run([], [pr(101 + k) for k in range(p.MAX_PARALLEL_REVIEW + 2)])
        self.assertEqual(len(plan.dispatch), p.MAX_PARALLEL_REVIEW)

    def test_no_reviewer_identity_blocks_reviews_but_not_fixes(self):
        plan = run([], [pr(105), pr(106, checks="failure")], reviewer=None)
        self.assertEqual([d["kind"] for d in plan.dispatch], ["fix"])
        self.assertTrue(plan.setup_problems)

    def test_open_pr_keeps_the_planner_quiet(self):
        plan = run([], [pr(105, checks="pending")])
        self.assertEqual(plan.dispatch, [])


@unittest.skipUnless(p.bash_path(), "bash not available")
class RealParserTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
