# Changelog

This file records this template's merged pipeline waves: each entry is a
commit that changed what the autonomous pipeline does, read from that
commit's own message. The history begins at commit `b56e41a`, the initial
template commit; the project publishes no version tags, so entries below are
waves, not releases.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Wave 22] - 2026-10-02

Commit `d6f2b6f` — gh-reviewer acts only on this repository, whatever the environment holds.

### Changed
- `gh-reviewer` now runs `gh` with an environment of its own: every exported
  variable is cleared in bash, PATH is fixed and `gh` is found on it, HOME and
  `GH_CONFIG_DIR` are an empty directory of the checkout's own, and the working
  directory is the repository root, while proxies, certificates and Windows
  start-up variables still pass and the token is read with bash and never
  reaches a command line.
- The config directory is a fresh one per call, removed afterwards.
- `gh-reviewer` now finds itself from a backslash path, as Windows hands it, and
  reads the review body relative to where the caller stands, refusing a review
  from outside the checkout.

### Fixed
- The script's directory no longer comes from the caller's PATH.

## [Wave 21] - 2026-10-02

Commit `041ec9c` — a .claude/bin/ command is spelled relative only, pinned by a test.

### Added
- `test_settings.py` has a new test that a `gh-reviewer` call and a `pipeline`
  call are allowed in their relative spelling and not as a Unix absolute path,
  a Windows absolute path or with a `./` prefix.

### Changed
- `docs/AgentEnvironment.md` § Permissions now says a `.claude/bin/` command is
  written exactly `.claude/bin/<name> …` from the repository root, never by an
  absolute path and never with a `./` prefix, because the allow rules match
  that text.
- The *refused in practice* list in that section now includes the reviewer's
  `gh-reviewer` call by its absolute path, from the first scheduled tick on
  2026-10-01.

## [Wave 20] - 2026-10-02

Commit `5f19095` — bash_guard reads a newline as a command separator.

### Fixed
- `bash_guard` now reads a newline as a command separator, so a command behind
  a newline is judged like one behind `;` or `&&`.
- The guard now checks every `git push` in a command, not only the first, so an
  allowed push no longer carries a forbidden one behind a newline, `;` or `&&`,
  and single-line commands with two pushes get stricter too, by the owner's
  decision.

## [Wave 19] - 2026-10-01

Commit `5904898` — record the first upgrade as a recipe and four lessons.

### Added
- `docs/ADOPTING.md` and `docs/LESSONS.md` now record the first upgrade of an
  adopting project as a recipe and four lessons.
- The upgrade recipe puts the project-rules step ahead of the verbatim copy,
  includes all issue templates and the `test_settings.py` USED exception from
  section C, points at the trigger-move hazard, and runs `setup-repo` with the
  owner's login.

## [Wave 18] - 2026-10-01

Commit `f13fc2d` — the tick holds outside its environment, unless switched off.

### Added
- The tick now holds outside its environment, unless that check is switched
  off.
- A dry run now says plainly whether the tick would hold.
- The environment check judges only the active login on the repository's host:
  it asks `gh auth status` for `--active --hostname <host>` (`GH_HOST`, else
  origin's host) and decides on what that prints.

### Fixed
- Doctor's gh login line now judges only the active login on the repository's
  host, the same way the tick's check does.

## [Wave 17] - 2026-10-01

Commit `a3b1ab6` — gh-reviewer matches each call whole, not by a glob.

### Changed
- `gh-reviewer` now pins the review's fields and the endpoint's segments: the body is a
  relative `.md` path that never leaves the checkout, `commit_id` is hex, the
  event is one of three, each exactly once; owner and repo admit no dot
  segments or percent-encoding; and `pr view` takes the number alone.

## [Wave 16] - 2026-10-01

Commit `4c34be3` — classify gh errors by stderr and HTTP status, not the message.

### Changed
- `GhError` now carries gh's own stderr and an `http_status` parsed from
  `(HTTP 404)` or `HTTP 404:` in it, and `issue_state`, `protection_state` and
  `enable_automerge` decide on those instead of on the message, which names the
  request path.

### Fixed
- During a GitHub hiccup, an issue blocked by #404, #410 or #1410 now stays
  blocked instead of turning ready.

## [Wave 15] - 2026-10-01

Commit `0297a56` — how to run a tick by hand in the right environment.

### Added
- `docs/AgentEnvironment.md` has a new section with the commands for bash and
  PowerShell to run a tick by hand, what `gh auth status` and doctor must show
  before `/pipeline-tick`, and the traps an adoption hit: a token path that is
  a folder, bash not on PATH in PowerShell, a review started from the tick's
  window or the wrong directory, and a session older than the last `git pull`.
- That section's `gh auth status` check asks for `(GH_TOKEN)` as the active
  account, and doctor's gh login line must be OK too, since a non-fine-grained
  login is only a warning there.
- The section also says `git push` does not read `GH_TOKEN`, checks the token
  file exists before reading it, creates the PowerShell token folder first, and
  keeps a review by hand off PRs the tick has claimed.

## [Wave 14] - 2026-10-01

Commit `dd3ccf8` — fewer process spawns per Bash call and per edit.

### Changed
- `bash_guard` now reads the agent type, kept to one line, and the command with
  one `jq` call, skips its two greps unless the command names `gh-reviewer` or `setup-repo`,
  and finds its own directory and the token name without subshells, taking a
  call from 311 to 178 ms on Windows.
- `allowlist_guard` now asks git once for root, prefix and branch, and
  `issue_scope.sh` does in the shell what it started `sed`, `tr`, `wc` and `cut`
  for, taking an edit from 472 to 363 ms, with the same exit code for every
  payload.
- `scope_jq` now holds the CR fix for `jq` on Windows, and `pr_allowlist.sh`
  calls it instead of spelling the pipe out six times.

## [Wave 13] - 2026-10-01

Commit `d7c9f28` — the reviewer calls gh-reviewer by its relative path only.

### Changed
- The review skill's § 0 `dontAsk` rules now say to call the bot as
  `.claude/bin/gh-reviewer`, from the repository root, never by an absolute
  path, because the allow rule `Bash(.claude/bin/gh-reviewer *)` matches only
  the relative form and the first scheduled tick's reviewer was refused on the
  absolute path.

## [Wave 12] - 2026-10-01

Commit `3578e84` — drop the Write(...) allow rules, Edit rules cover every file tool.

### Changed
- `test_settings.py` now looks at `Edit(...)` rules only for its two file-rule
  tests and has a new test that fails on any `Write(...)` allow rule.

### Fixed
- `.claude/settings.json` no longer has the `Write(/.claude/worktrees/**)` and
  `Write(/.pipeline-tmp/**)` allow rules, because Claude Code matches file
  permissions on `Edit(path)` rules only, for every file-editing tool, and
  warned about both at the start of every tick.

## [Wave 11] - 2026-10-01

Commit `4b1810e` — one helper per repeated gh call, one scope parse per issue per run.

### Changed
- `Gh.paginate`, `open_issues_labelled` and `collaborator_permission` now
  replace five, three and two hand-written copies of the same `gh` call, and
  `LintResult.clean`, `LintResult.blocked_by`, `bot_reviews_at`,
  `dispatch_label` and `runs_by_kind` replace the rest.
- `cmd_run` now applies the ops in one place, and `cached_allowlist` parses
  each issue body once per run, where `decide()` and the lint asked for it up to
  four times, each a bash start.
- Five unread constants and the test-only `rollup_state()` are removed, and
  `FakeGh` now takes a canned answer that is a function of the args, so
  `CallableFakeGh` goes.
- The plan is unchanged: a dry run makes the same plan as the previous
  version's against the same GitHub state.

## [Wave 10] - 2026-10-01

Commit `9b68f2d` — issue-lint starts no runner for issues without agent-task.

### Changed
- issue-lint no longer starts a runner for an issue without `agent-task`, since
  every tick rewrites the status issue and each rewrite started a runner that
  only printed "skipped".
- A closed issue of any kind still starts the relint, and an issue that gains
  `agent-task` later fires `labeled` with it.

## [Wave 9] - 2026-10-01

Commit `cb7111d` — a done-check the reviewer cannot run goes to the owner.

### Added
- The review skill's § 3 table now has a row for a command and what it must
  print, judged by its output rather than its exit code.

### Changed
- A done-check the reviewer cannot run because `dontAsk` refuses it is no
  longer treated like a missing tool to name while approving the rest: unless
  CI covers the line, the verdict is now NEEDS_HUMAN naming the command, since
  CI does not run a grep-style check and the line would otherwise merge
  unchecked under an approval that reads as if it had been checked.
- The review skill's § 3 now says such a line is neither a hand-back nor a
  missing tool and leaves it to § 0, so it cannot end in REQUEST_CHANGES and
  fix passes that cannot fix anything.
- `docs/AgentEnvironment.md` § Permissions now says the same for the reviewer.

## [Wave 8] - 2026-10-01

Commit `4b28cde` — a done-check may be a command and what it must print.

### Changed
- `CONTRIBUTING-agents.md`, `CLAUDE.md`, the agent-task template and
  `github-issue-create` § 4 now say a done-check line is a plain command that
  exits 0, or a command and what it must print, because an absence written as
  `grep -c '<text>' <file>` prints `0` with exit 1 and a reader of the old
  contract would have failed a correct absence.
- `github-issue-create` § 4 now offers `git grep -n` only for a presence,
  since it prints nothing for an absence rather than `0`; an absence is always
  `grep -c`.

## [Wave 7] - 2026-10-01

Commit `d3691d4` — tell agents what dontAsk refuses, and never to stop silently.

### Added
- `docs/AgentEnvironment.md` § Permissions now says what `dontAsk` refuses and
  what to do instead: one plain command per call, files through the Edit and
  Write tools, `git -C` only with the relative worktree path, and what to do
  with a refused command.
- `github-issue-create` § 4 now says done-checks are plain commands and an
  absence is a count that prints `0`.

### Changed
- The resolver, triage and planner prompts and the review skill carry the same
  rule, each with what a refusal means for its own job: the implementer
  commits, pushes and escalates naming the command, triage rewrites the line or
  hands the issue to the owner, and the reviewer treats it like a missing tool.
- `.claude/settings.json` now allows `grep` and `git grep`, so those checks no
  longer depend on Claude Code's own read-only list, and
  `.claude/pipeline/tests/test_settings.py` holds the plain forms the prompts
  recommend.

### Fixed
- An implementer whose done-checks `dontAsk` refuses no longer stops silently:
  in the first full run of a downstream project one stopped with no commit, no
  PR and no escalation and left the issue claimed for hours, because no prompt
  had said what that mode refuses.

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
- Scratch files moved from `.claude/tmp/` to `.pipeline-tmp/`, because
  `dontAsk` refuses every write under `.claude/` except `.claude/worktrees/`,
  whatever the allow rules say; an adopter's `.gitignore` has to follow.

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
