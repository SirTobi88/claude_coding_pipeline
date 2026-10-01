# Changelog

This file records this template's merged pipeline waves: each entry is a
commit that changed what the autonomous pipeline does, read from that
commit's own message. The history begins at commit `b56e41a`, the initial
template commit; the project publishes no version tags, so entries below are
waves, not releases.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Wave 6] - 2026-09-29

Commit `864c235` — what the first live tick found.

### Added
- A new `pipeline checks <PR> [--wait]` command replaces `gh pr checks` in the
  resolver, triage, and review prompts, and `pipeline doctor` now reports
  whether the login can read CI.

### Changed
- CI is now read through the Actions API, from a commit's Actions jobs and
  commit statuses, because a fine-grained token has no Checks permission and
  `statusCheckRollup`, check runs, and `gh pr checks` are unreadable to it on a
  private repository, which killed the first live tick on its third survey.
- Scratch files now live in `.pipeline-tmp/` instead of `.claude/tmp/`, since
  `dontAsk` refuses every write under `.claude/` except `.claude/worktrees/`,
  so an adopter's `.gitignore` has to follow.

### Fixed
- The hook suite now clears the environment variables the hooks read, so it
  passes inside a tick (`PIPELINE_TICK=1`) as it does in a terminal.

## [Wave 5] - 2026-09-29

Commit `466aeb6` — docs that say what the pipeline does.

### Fixed
- issue-lint now keeps one persistent note per issue, marked and rewritten in
  place, so a warning such as a missing line-number anchor is no longer
  dropped once an issue is otherwise ready.
- A Size checkbox ticked "Too big" now fails issue-lint instead of passing,
  matching what the issue template already claimed it checked.
- The tick now recognizes a PR aimed at a branch other than the default,
  lists it under Needs you, and sends it no agent, since merging it would
  land nothing on the default branch and close no issue.

## [Wave 4] - 2026-09-29

Commit `f307b00` — an unattended pipeline you can see and stop.

### Added
- Each `pipeline run --apply` now appends one JSON line describing what it
  dispatched to a tick log shared by every worktree.
- A `pipeline:status` issue is now rewritten on every tick with the time of
  the run, what needs the owner, what was dispatched, and seven days of
  counts.
- A new `pipeline stats` command reports merged PRs, PR-to-merge and
  issue-to-close times, the fix-pass rate, spec defects, and agent runs by
  kind.
- A new `pipeline doctor` command checks tools, tokens, repository settings,
  branch protection, required checks, and worktrees, and exits nonzero on any
  failure.
- The tick now holds and dispatches nothing while branch protection is
  missing or has drifted, and pausing turns off auto-merge on open PRs so an
  existing approval can no longer complete a merge.

## [Wave 3] - 2026-09-29

Commit `415e0ab` — one allowlist grammar, and a harder edit-time guard.

### Changed
- The grammar for parsing a `## Files in scope` list now lives in one place,
  `issue_scope.sh`, and both the bash guard and the Python lint follow it
  instead of each guessing at markdown lists independently.
- issue-lint now warns when its own parser would read an issue's Files in
  scope differently from a person, such as a list with no top-level item that
  starts with a path, two paths in one item, or a directory written without a
  trailing slash.

### Fixed
- The edit-time guard now takes the repo-relative path from git's own
  spelling, so a write on a case-insensitive filesystem is no longer mistaken
  for a file outside the repository.
- The edit-time guard now covers MultiEdit and NotebookEdit calls and refuses
  a write from an agent's worktree into any other checkout of the same
  repository, including the main checkout.
- The edit-time guard now re-reads a just-widened issue past its cache and
  fails closed when the issue names no parseable path, instead of trusting a
  stale allowlist.

## [Wave 2] - 2026-09-29

Commit `eec6b2d` — prompts safe to follow unattended, and a way for the owner
to answer.

### Added
- The owner can now answer an escalated issue, idea, or PR with a comment and
  the `human:answered` label, which hands it back to triage, the planner, or
  its fix and conflict rounds instead of leaving it stuck.
- `pipeline run --apply` now fast-forwards a clean checkout of the default
  branch before surveying, so agents read today's prompts and branch from
  today's code.

### Fixed
- Resolvers, triage, and the planner now treat GitHub comments as data rather
  than instructions, acting only on the owner's or the reviewer bot's own
  comments.
- The review skill now writes the reviewed commit SHA literally instead of
  through a shell variable that did not survive between calls, so the
  head-commit check no longer always failed.
- The bash guard now normalizes `git -C <dir>`, so a push routed through it
  meets the push rules, and refuses setup-repo where it is called rather than
  wherever its name appears.

## [Wave 1] - 2026-09-29

Commit `594f3cd` — give every state a next owner (the state machine).

### Added
- A red default branch now opens one `pipeline:main-red` issue and holds
  ci-failed fixes and new implementations until it is green again.
- Fix passes and conflict passes are now capped per PR and review attempts
  per PR head, and a PR stuck past a cap goes to the owner instead of
  retrying indefinitely.

### Changed
- `decide()` now moves a PR only while its issue allows it, holding it
  whenever the issue is escalated, needs-spec, blocked, being re-judged, or
  held by triage.
- A stale in-progress claim now resumes a pushed branch or is re-queued once
  before escalating, instead of sitting at "implementer working" forever.
- Only the required checks now gate a PR: a missing required check is
  pending, an optional check's failure is ignored, and only a check's latest
  run counts.

## [Wave 0] - 2026-09-28

Commit `992f60a` — close the agents' trust boundary and judge PRs from main.

### Fixed
- Only the reviewer subagent can now run `gh-reviewer` and approve a PR as
  the bot; no other agent session, and never the tick itself, can approve its
  own work.
- Agents acting through the owner's admin login no longer have `gh api`
  broadly pre-approved; pushes, edits, and API calls are narrowed to the rules
  an agent actually needs.
- The `allowlist` and `contract` checks now run from the default branch's own
  workflow file instead of the PR's copy, so a PR can no longer rewrite
  `allowlist` to pass.
- A pipeline run now reports local state that no PR shows, such as a dirty or
  off-branch checkout, leftover `settings.local.json`, or a missing `jq` or
  `bash`.
