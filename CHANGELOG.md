# Changelog

This file records this template's merged pipeline waves: each entry is a
commit that changed what the autonomous pipeline does, read from that
commit's own message. The history begins at commit `b56e41a`, the initial
template commit; the project publishes no version tags, so entries below are
waves, not releases.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Wave 35] - 2026-10-03

Commit `c9ba6ac` — a failed answer write stops the target's remaining answer writes.

### Changed
- The idea and agent-task answered branches now take `human:answered` off first
  and mark their writes `after_status`.
- `claim()` now refuses an owner-answered triage until the answer is recorded.

### Fixed
- `apply_ops()` now stops a target's `after_status` writes once one of them
  fails, not only when the answer status fails.
- Every failed answer write now leaves either the untouched answered state or an
  item waiting on the owner, never a stray `human:answered`.

## [Wave 34] - 2026-10-03

Commit `e6974f7` — the answered branch takes human:answered off first on purpose.

### Added
- A new test, `OwnerAnswerTests.test_answered_comes_off_first_and_needs_human_last`,
  pins that a PR's label removals start with `human:answered`, end with
  `status:needs-human` and have the round labels in between.

### Changed
- The answered branch of `decide()` now removes labels in an explicit order,
  `human:answered` first, then the round labels (sorted), then
  `status:needs-human` last, and a comment says why.
- That order means a run that stops between removals leaves the PR waiting on
  the owner, who is asked again, and never leaves a stray `human:answered` that
  would answer the next question on that PR by itself. A single removal that
  fails does not stop the others; that case is #98.
- Before, the order held only because `sorted()` happens to put `human:answered`
  before `status:needs-human`.

### Fixed
- Two test corrections from the review of #95: a comment on
  `test_a_failed_answer_status_keeps_the_labels` no longer claims an op the test
  does not have, and a `post-status` op in the hand-off test is now built with
  `"kind": "pr"`, as `decide()` has emitted it since #88.

## [Wave 33] - 2026-10-02

Commit `c777523` — the owner's answer on a PR survives a failed answered-status write.

### Fixed
- The answered status is now queued before the label removals, and `apply_ops`
  skips those removals (`after_status`) when the status write failed, so the
  labels stay and the next tick runs the answered branch again.

## [Wave 32] - 2026-10-02

Commit `89bc6c2` — doctor and preflight report awk, tr, sed or grep missing.

### Added
- `doctor` and `preflight` now report `awk`, `tr`, `sed` or `grep` missing.
- They ask the guard's own bash (`bash_path()`) with `command -v`, not Python's
  `which`, which looks in PowerShell's PATH, where Git's `awk`, `tr`, `sed` and
  `grep` are not, so a healthy Windows machine would show four failing lines.
- `guard_tools_missing()` returns `None` when the bash lookup exits non-zero or
  times out, rather than reading its empty output as all four tools found.

## [Wave 31] - 2026-10-02

Commit `a1022e0` — one comment-only bot review hands the PR to the owner.

### Changed
- `decide()` now treats every comment-only review by the bot as a NEEDS_HUMAN
  verdict: when the PR has no `status:needs-human`, it adds the label, posts the
  bot comment that mentions the owner, and lists the PR under *Needs you*.
- Nothing more is dispatched on that PR at that head.
- `docs/Pipeline.md` § Pull requests now says a comment-only review at a commit
  goes to the owner, and that the tick adds `status:needs-human` if the reviewer
  could not.

## [Wave 30] - 2026-10-02

Commit `a54a4dd` — the guard refuses when a tool it needs is missing or silent.

### Fixed
- The guard now checks at startup that `tr`, `sed`, `grep` and `awk` exist, and
  refuses an empty normalized command.
- Its `awk` pass now says `P` when nothing is refused, so an `awk` that prints
  nothing refuses too.
- A `tr` that empties jq's output now refuses, and a CR after `awk`'s verdict is
  dropped. jq's carriage returns are still dropped with `tr`, which is linear:
  stripping them in bash is quadratic in bash 3.2 under UTF-8, and a 45 KB CRLF
  command took over a minute, past the hook's timeout, which skips the guard.

## [Wave 29] - 2026-10-02

Commit `93b40ff` — a finding wins over a refused command, and a refusal after the verdict is reported.

### Changed
- In `github-pr-review` § 0, the refused-command bullet now says "unless
  something else already asks for changes" before NEEDS_HUMAN, so a known bug
  goes to a fix pass.
- That bullet gains a closing sentence: a command refused after the verdict is
  posted, such as the label edit after a COMMENT or enabling auto-merge after an
  APPROVE, is not a new verdict, so the reviewer posts nothing more and names the
  command in its report.
- § 9's verdict table now ends the NEEDS_HUMAN row "or a command § 0 found
  refused when no § 8b or § 8d finding asks for changes".
- `docs/AgentEnvironment.md` § Permissions gains a sentence saying the same, and
  that the tick lists such a command under *Needs you*.
- `.claude/commands/pipeline-tick.md` § 3 now lists under *Needs you* every
  command an agent's report names as refused.

## [Wave 28] - 2026-10-02

Commit `fce145a` — gh-reviewer hands gh a private copy of the checked body.

### Changed
- `gh-reviewer` now requires the resolved review body to be a `.md` file itself.
- `gh` now reads a private copy of the body in the per-call directory instead of
  the resolved file, and a swap between the checks and the copy is still posted,
  which the script's comments now say.
- A missing `realpath` now says so and exits 3.
- `pr view` is no longer a call `gh-reviewer` runs, since the skill reads with
  plain `gh`.

## [Wave 27] - 2026-10-02

Commit `81d0ce5` — refuse a push the guard could not check.

### Fixed
- A push is now refused when the guard could not check it: an `awk` that failed
  or was missing left the verdict empty and the push went through unchecked,
  a force push to main included.
- `push_refuse` now has a default arm, so an `awk` that exits 0 but prints
  something other than `F`, `X` or `A` refuses too.

## [Wave 26] - 2026-10-02

Commit `634e087` — the review skill's reads go through plain gh.

### Changed
- The preamble of `github-pr-review` no longer says reads "may use either"; it
  now says "Reads use plain `gh`: the wrapper refuses every call this skill does
  not make."
- The reason is that since #21, #59 and #63 `gh-reviewer` runs only the review's
  own calls, so a read through it would be refused.

## [Wave 25] - 2026-10-02

Commit `77ae6e5` — the reviewer answers NEEDS_HUMAN for any refused command the review needs.

### Changed
- `github-pr-review` § 0 has a new bullet: any other command the review needs
  that is refused even in plain form leads to NEEDS_HUMAN, naming the command,
  for example `gh run view --log`, `.claude/bin/pipeline checks` or a
  `gh-reviewer` write.
- If the refused command is the one that posts the verdict, the reviewer
  releases and names the command in its report.
- § 9's verdict table: the NEEDS_HUMAN row adds "or a command § 0 found refused".
- `docs/AgentEnvironment.md` § Permissions: the end of the *A refused command*
  bullet now covers any command the review needs, not only a done-check.

## [Wave 24] - 2026-10-02

Commit `f927bc8` — gh-reviewer takes only full SHAs and a body file that resolves inside the checkout.

### Added
- `test_gh_reviewer.py` has two new tests: `test_only_full_shas`, and
  `test_the_body_must_be_a_regular_file_inside_the_checkout`.
- Its in-checkout symlink case is for macOS and Linux: on Windows, Python makes
  a native symlink whose `C:\` target MSYS's `realpath` does not map to `/c/`,
  so the wrapper refuses it, the safe direction.

### Changed
- `gh-reviewer` now accepts `commit_id` and `--match-head-commit` only as
  exactly 40 lowercase hex digits.
- The review body, taken relative to where the caller stands and resolved with
  `realpath`, must now be a regular file under the wrapper's own checkout root,
  otherwise the wrapper exits 4 and nothing reaches `gh`, which covers a missing
  file, a directory and a `.md` symlink pointing out of the checkout.
- `gh` is now given the resolved file, relative to the root, so a symlink inside
  the checkout is read through to its target.

## [Wave 23] - 2026-10-02

Commit `3c37d13` — the push check gives main's verdicts in one linear awk pass.

### Fixed
- The push check now gives its verdicts in one linear `awk` pass over the
  command's words, instead of re-reading up to the next separator for every
  `git push`; only its cost changes.
- The per-push rule is unchanged word for word: 6000 random commands give the
  same verdict as main's guard, and 6000 push mentions take 0.27 s instead of
  minutes.

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
  refuses a review from outside the checkout; the review body is still read
  relative to where the caller stands, though `gh` now runs in the root.

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
- `gh-reviewer` now pins the review's fields and the endpoint's segments: the
  body is a relative `.md` path that never leaves the checkout, `commit_id` is
  hex, the event is one of three, each exactly once; owner and repo admit no
  dot segments or percent-encoding; and `pr view` takes the number alone.

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
  one `jq` call, skips its two greps unless the command names `gh-reviewer` or
  `setup-repo`, and finds its own directory and the token name without
  subshells, taking a call from 311 to 178 ms on Windows.
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
