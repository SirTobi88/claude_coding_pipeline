# Why the pipeline looks the way it does

Every rule in this repository was paid for. This file records what went wrong
before each one existed, measured in the project the pipeline was extracted from
(a Godot game built by one developer plus Claude, ~250 agent-authored PRs). If
you are tempted to delete a rule, read its entry first.

---

## Flow

**Every state without a next owner lands on a human.** The first version
automated the implementer and the reviewer and nothing between them. A PR with
requested changes, a review that only commented, an escalation, a merge
conflict, and every merge ended on the owner's desk. The fix was not smarter
agents but a state table where every row names who acts next
(`docs/Pipeline.md` § *States*) — including triage for escalations and fix
passes for rejected PRs.

**Labels are the state, not branches or worktrees.** "In progress" used to mean
"there is a branch named after it, or a worktree on this machine". That only
worked on one machine and broke whenever work was abandoned. GitHub labels are
visible from anywhere, including a phone, and a claim is a label set *before*
an agent starts.

**Claims need a timeout.** An agent that crashes leaves its claim behind. Every
claim (`pipeline:working`, `status:in-progress`) goes stale after a configured
number of hours and is then treated as abandoned.

**Readiness is decided by a machine.** "Is this issue ready?" used to be judged
by whichever agent read it next, differently each time. issue-lint applies one
bar, on every edit, and labels the result.

---

## Merging

**A checklist is not a gate.** Review close-out was a checklist the reviewer was
trusted to follow. Across the first 129 merged PRs, 95 had no recorded verdict
and 5 were merged over a standing `REQUEST_CHANGES`. Once branch protection
enforced it, it stopped happening.

**You cannot approve your own PR, so the reviewer needs its own account.** All
agents push with the owner's credentials. GitHub refuses an approval from a PR's
author (HTTP 422), so every "review" was a comment and branch protection could
not require one. A second account used only by the reviewer makes the approval
real — and makes "nobody judges their own work" something GitHub enforces.

**Enforce at the platform, not in the shell.** Before branch protection was
available, a merge gate was built as a hook that read command text, then as a
`gh` wrapper. Each round of review found another way around it: heredocs,
`eval`, quoting, a different binary on `PATH`. It grew to 1,500 lines and never
merged. Branch protection made the whole thing unnecessary in one API call.

**Include admins.** Agents run as the owner, who is an admin. Without
`enforce_admins`, every rule binds everyone except the actor it exists for.

**Pin required checks to GitHub Actions.** A required check matched by name
alone can be satisfied by a commit status anyone with write access posts by
hand.

**Read a verdict at the head commit, and only there.** The first PR watcher
compared the newest commit's time to the newest verdict's time. The reviewer
posts its verdict and *then* pushes a fix, so the head moved past the verdict
and the PR looked unreviewed forever — on a five-minute timer that re-spawned an
Opus reviewer against the same PR indefinitely. Reviews now carry the commit
they judged, and an approval of an older commit means "review again".

**Direct pushes happen when they are allowed.** Six commits went straight to the
default branch after the initial setup, one of them a feature implementation.
Protection with admins included ended that.

---

## Issues

**Line-number anchors rot.** Issues quoted interfaces as `file.gd L157–182`.
The next merge shifted the lines, and "the quoted interface does not match the
repo" is a mandatory escalation — so a correct issue became a blocked one.
Anchor on symbol names; issue-lint warns on line anchors.

**Interfaces land before implementations.** An agent asked to "design a sensible
API" designs a different one than the next agent expects. Pin signatures in the
issue, or land them first as a stub-plus-failing-test issue and block the
implementations on it.

**Small review fixes belong in the PR, not in a new issue.** One afternoon
produced a chain of three issue → PR → review → merge cycles, each to fix a
stale comment the previous review had spotted. The reviewer now fixes
mechanical defects in the files a PR touches before approving.

**Prefer structural checks to ever-sharper regexes.** A source-scan guard against
concatenated translation strings was tightened in six separate PRs as new
spellings slipped past it. A regex guard over free-form code is an arms race;
where a check can be structural (a parser, a type, a required section), make it
structural.

---

## The allowlist

**One parser, used everywhere.** The edit-time guard, the CI job and the
scheduler all read *Files in scope* through `issue_scope.sh`. Two parsers that
disagree do not announce it; they surface as a correct PR rejected for a file it
was allowed to touch.

**Only the leading token of a list item is a path.** Issues routinely name other
files in prose ("that file belongs to #12"). Sweeping every backticked token
allowlisted exactly the files an issue said were someone else's.

**CI judges a PR with the base branch's parser.** Otherwise a PR could loosen
the rule that judges it.

**Generated companions are covered by their source.** In Godot every script has
a generated `.uid` and every asset a generated `.import`; until the parser knew
that, every script and asset PR failed the allowlist on a file nobody chose to
write.

**A fail-open guard is silently off when its tools are missing.** The guard lets
writes through when it cannot run, so a session never bricks on a network
blip. On a machine without `gh` and `jq`, that meant no guard at all, with
nothing to say so. The docs now make both tools a precondition.

---

## The machine

**Machine facts live in one file.** When the project moved from Windows to macOS,
hardcoded drive paths and a Windows-only test command sat in five files and none
were updated. Every spawned agent got a command that could not run — and "not
verified locally, CI green" quietly became acceptable, turning the definition of
done from *reproduced* into *asserted*.

**Test on the platform that runs the pipeline.** The allowlist guard was written
and tested on macOS and Linux. On Windows, Claude Code hands it `E:\...` paths;
it read them as relative and **blocked every write** on every agent branch. It
went unnoticed only because the missing tools had switched it off.

**Discover tools by pattern, not by exact name.** The test runner looked for the
vendored engine by its exact patch-version filename and stopped finding it the
day the binary was updated.

**Worktrees do not contain gitignored files.** A vendored binary under a
gitignored `tools/` directory exists in the main checkout and in no worktree —
where every agent works. The runner now also searches the main checkout.

**Two agents in one directory collide regardless of their allowlists.** A
working tree holds one branch, and a shared build cache raced between concurrent
test runs, surfacing as parse errors that looked like code bugs. Every agent
gets its own worktree.

**Per-user app-data is shared between worktrees.** Tests wrote fixtures to a
fixed path in the engine's per-user data directory, which every worktree
resolves to the same place. Concurrent suites truncated each other's files: 4 of
16 concurrent runs failed, in suites the branch never touched. Scratch paths now
carry the process id, and a source scan forbids the fixed form.

**A running app keeps its old `PATH`.** Tools installed while the Claude desktop
app runs are invisible to it — and to every agent and scheduled run it spawns —
until the app is fully quit from the tray, not just closed.

**Small Windows traps.** `python3` is often the Microsoft Store alias. Notepad
saves `token` as `token.txt`. PowerShell 5.1 turns native stderr into errors, so
`2>&1` makes a passing test run look failed. `core.autocrlf=true` checks scripts
out with CRLF, which bash cannot run, and marks LF-written generated files as
modified after every run.

---

## The planner

**Human gates must be explicit.** The roadmap is what the planner reads. A step
waiting on a human (a playtest verdict, a release call) has to say so, or the
planner plans past it. When the only next step is gated, the planner opens a
single `pipeline:idle` issue naming the gate and stops asking until it is
closed.
