# The coding pipeline

This file owns **how work moves**: which agent picks up what, which label says
so, and what GitHub enforces on the way to the default branch.
`CONTRIBUTING-agents.md` owns *how a seam is cut and judged*;
`docs/AgentEnvironment.md` owns *the machine*. Neither restates this file.

The goal is a pipeline that runs without a human in the loop for anything but
decisions only a human can make. Everything below follows from one lesson:
**every state that has no next owner lands on a person.** So every state here
has one.

---

## Roles

| Role | Who | Model | Does |
|---|---|---|---|
| **Tick** | `/pipeline-tick`, on a schedule | Sonnet, low effort | Runs `.claude/bin/pipeline run --apply`, spawns what it returns, reports. Decides nothing itself. |
| **Planner** | `github-planner` agent | Opus | Turns `idea` issues and the roadmap into agent-task issues. |
| **Implementer** | `github-issue-resolver` agent | Sonnet | Works one issue to a PR, or one fix pass on an existing PR. |
| **Reviewer** | `github-pr-reviewer` agent, **as the bot account** | Opus | Reviews one PR, fixes mechanical defects, approves or requests changes. |
| **Triage** | `github-triage` agent | Opus | Answers escalations and repairs specs the lint rejected. |
| **GitHub** | branch protection + Actions | — | Runs CI, lints issues, and merges an approved PR the moment its checks are green. |
| **Human** | the owner | — | Only `status:needs-human`, `human-decision`, `asset`, and milestone calls. |

`.claude/pipeline/pipeline.py` makes every decision that does not need
judgement — what is ready, what needs a review, a fix or nothing — and is
unit-tested offline. The agents make the judgement calls and nothing else.
Project-specific values (default branch, required checks, test command, roadmap
docs, token path, limits) live in `.claude/pipeline/config.json`.

---

## States

GitHub labels are the state. There is no local state file and no worktree
scan: any machine, or a phone, sees exactly what the pipeline sees. The one
exception is deliberate: a claim that went stale is judged by whether its
`agent/<N>-` branch exists on GitHub, because pushed work must not be started
over beside.

`decide()` in `.claude/pipeline/pipeline.py` is the executable form of this
section; its tests replay each row.

### Issues (`agent-task`)

```
            ┌──────────── issue-lint (GitHub Actions, on every edit) ─────────────┐
 opened ──▶ │ status:needs-spec ◀──▶ status:blocked ◀──▶ status:ready             │
            └──────┬────────────────────────────────────────┬──────────────────────┘
                   │ triage repairs the spec                │ tick claims it
                   ▼                                        ▼
            (none) ──▶ lint                       status:in-progress ──▶ status:in-review ──▶ closed by merge
                                                   │   │    ▲ stale, no PR:     │   ▲
                                                   │   │    │ resume / re-queue │   │ triage answered:
                                                   │   └────┘ once (attempt-1)  │   │ lint → in-review
                                                   ▼                            ▼   │ (or blocked)
                                            status:escalated ◀── PR closed unmerged,
                                                   │             ended twice without a PR
                                                   │ triage answers once
                                                   ├──▶ (none) → lint → ready / blocked / in-review
                                                   └──▶ status:needs-human   (second time, or a design decision)
```

| Label | Set by | Next owner |
|---|---|---|
| *(none)* | a new issue, triage | issue-lint, or the tick's lint |
| `status:needs-spec` | issue-lint | triage |
| `status:blocked` | issue-lint | issue-lint, when the blocker closes |
| `status:ready` | issue-lint | tick → implementer |
| `status:in-progress` | tick (the claim) | implementer |
| `status:in-review` | implementer, tick | reviewer, via the PR |
| `status:escalated` | implementer, tick | triage |
| `status:needs-human` | triage, tick, reviewer, planner | **the owner** |

**Exactly one status label.** When an issue carries two — you added
`status:needs-human` without removing `status:ready`, or two writers raced —
the one that stops the most counts (needs-human, escalated, in-review,
in-progress, needs-spec, blocked, ready) and the tick removes the other. Every
status the tick writes is compare-and-set: it re-reads the labels and writes
nothing if the issue moved since the survey.

Issue-lint keeps **one comment** on each issue, marked `<!-- issue-lint -->`
and rewritten in place: what blocks it, otherwise what is worth fixing
(line-number anchors, a list the parser will read differently than a person),
otherwise that it is ready.

Issue-lint owns only the first three. It never touches an issue that is in
progress, in review, escalated, waiting on a human, or held by an agent
(`pipeline:working`). A second escalation or spec rejection after triage has
answered once (`triaged` label) goes straight to `status:needs-human`.

**An implementer that ends without a PR.** When `status:in-progress` has not
moved for `stale_in_progress_hours`: if the issue's `agent/<N>-` branch is on
GitHub, an implementer resumes that branch; if not, the issue goes back to
lint. Either way it gets `attempt-1`. The second time, it is escalated — to
the owner if triage has already answered once. While `git ls-remote` fails, no
stale claim is touched.

**Back from triage with a PR open.** Lint judges the repaired issue: blocked
stays blocked and its PR waits; anything else returns to `status:in-review`,
and the PR resumes.

### Pull requests

A pull request moves only while its issue lets it. In order:

| Condition | Next owner |
|---|---|
| from a fork | **the owner** — agents never review or fix an outsider's branch |
| targets a branch other than the default | **the owner** — merging it lands nothing on the default branch and closes no issue; retarget it once its base has merged |
| `status:needs-human` | **the owner** — `human:answered` hands it back (§ What stays with the owner) |
| bound to no issue (no `agent/<N>-` branch, nothing it closes) | **the owner** (a draft just waits) |
| a second open PR for the same issue | **the owner**, labelled `status:needs-human`: close one |
| its issue is closed | **the owner**: close the PR, or reopen the issue |
| its issue is `human-decision`, `asset`, or touches a control path | **the owner** (§ What stays with the owner) |
| its issue is `needs-human` / escalated or needs-spec / blocked / being re-judged | the owner / wait for triage / wait for the blocker / wait one tick |
| an agent holds it (`pipeline:working`, fresh) | that agent |
| draft, its issue back in review | implementer, fix pass (`draft-resume`): finish it and mark it ready |
| merge conflict | implementer, fix pass (`conflict`) — own count, `max_conflict_rounds` |
| CI running | wait — until `stale_waiting_hours` after the newest required check started, then **the owner** |
| CI failed, first attempt | the tick re-runs the failed runs once |
| CI failed again | implementer, fix pass (`ci-failed`); waits while the default branch is red |
| CI green, no bot verdict at this commit | reviewer — `max_review_attempts` per head, then **the owner** |
| bot requested changes at this commit | implementer, fix pass (`review`) |
| bot left only comments, `max_comment_only_reviews` times | **the owner** |
| bot approved at this commit | GitHub auto-merge, pinned to that commit — **the owner** if it has not merged after `stale_waiting_hours` |

"CI" means the `required_checks` and nothing else — exactly what branch
protection judges. A required check that has not reported yet is pending, not
green; an optional check that fails sends nothing to a fix pass; of several runs
of one check, only the latest counts, and a cancelled one is pending.

A PR gets `max_fix_rounds` fix passes (default 2, labels `fix-round-1`, …) and
`max_conflict_rounds` conflict passes (`conflict-round-1`, …). One more
failure labels it `status:needs-human`. To hand such a PR back, answer it with
`human:answered` (§ What stays with the owner): the tick restores its rounds. A verdict is only ever read **at
the head commit** — an approval of an older commit means "review again", never
"approved". Each review dispatched is counted in a `pipeline/review` commit
status on the head, so a new push starts the count again.

Agents fix only their own branches: a PR whose issue the owner holds
(`pipeline:human-holds`), or that closes an ordinary issue, is reviewed but
never gets a fix pass — its failures go to the owner.

### The default branch

When a required check fails on the default branch's head, every PR's CI runs
against broken code and a fix pass would chase a failure it did not cause. The
tick opens one `pipeline:main-red` issue for the owner, holds `ci-failed` fix
passes and new implementations — reviews and conflict passes go on — and closes
the issue once the default branch is green again. With `strict: false`
protection two individually green PRs can merge into a red default branch; this
is where that surfaces.

### Markers

| Label | Meaning |
|---|---|
| `pipeline:working` | An agent holds this issue or PR right now. The tick leaves it alone until it goes stale (`stale_working_hours`), then treats it as abandoned. |
| `pipeline:reviewing` | With `pipeline:working` on a PR: the holder is the reviewer, so it counts against `max_parallel_review`, not `max_parallel_fix`. |
| `human:answered` | You answered what the pipeline asked. The next tick resumes the item and removes the label (§ What stays with the owner). |
| `pipeline:human-holds` | The owner works this issue by hand (`pipeline claim issue N --interactive`). Never reset, never sent a fix pass. `pipeline release issue N --hold` hands it back. |
| `pipeline:planning` | The roadmap planner is running. It closes this issue when done; the tick closes it after `stale_working_hours`. |
| `pipeline:main-red` | The default branch is red. Opened and closed by the tick. |
| `pipeline:status` | The pipeline's heartbeat issue. Every tick rewrites its body (§ The tick). |
| `pipeline:pause` | On **any** open issue: the tick starts nothing and turns off auto-merge on every open PR (§ The tick). The kill switch — open an issue with it from your phone. |
| `pipeline:idle` | The planner found nothing it may plan without a human. Roadmap planning stops until you close this issue; the tick lists it under *Needs you*. |
| `attempt-1` | An implementer ended once on this issue without a PR or an escalation. |
| `idea` | Raw input for the planner. Any one-liner is enough. |
| `spec-defect` | The review handed a PR back because the *issue* was underspecified. A countable signal for improving issues. |
| `triaged` | Triage has answered this issue once. |

---

## Merging: what GitHub enforces

Only the reviewer bot merges — by enabling auto-merge, or directly when GitHub
says the PR is already clean — and nobody, agent or owner, pushes to the
default branch. Branch protection (applied by `pipeline setup-repo`) applies to
every merge either way:

- **Required checks**, pinned to GitHub Actions so a hand-posted commit status
  cannot stand in for them (`required_checks`, default `ci`, `tooling`,
  `allowlist`, `contract`). `allowlist` and `contract` run on
  `pull_request_target`: GitHub takes their workflow file and checkout from the
  default branch, so a pull request cannot rewrite the checks that judge it.
  `ci` and `tooling` must run the pull request's code, so they run its
  workflow file too — which is why the agents' token has no Workflows
  permission (§ Setup): an agent cannot push a workflow file, including a new
  one whose job borrows a required check's name. That limits the agents, not
  outsiders: a fork's pull request could carry such a workflow, so `setup-repo`
  holds outside contributors' workflow runs for approval, `allowlist` refuses
  any unbound pull request that touches a pipeline control path, and the tick
  hands every fork's pull request to the owner instead of the reviewer.
- **One approving review** from someone other than the author. Every agent
  pushes as the owner's account, so **only the reviewer bot's approval counts**
  — this is what makes "never judge your own work" a platform rule rather than a
  sentence in a doc. The same holds for any other collaborator with write
  access: their approval merges too, so give write access to nobody whose
  approval should not.
- **Stale approvals are dismissed** when new commits land, so an approval is
  always an approval of the code that merges.
- **Admins are included.** Agents run as the owner, an admin; without this the
  rules above would not bind the one actor they exist for.
- Linear history, squash merges only, head branches deleted on merge.
- *Not* "require approval of the most recent push": the reviewer pushes
  mechanical fixes and then approves, and that setting would refuse it.

The reviewer approves the exact commit it reviewed (the review carries its
`commit_id`) and enables auto-merge; GitHub merges when the last required check
goes green.

### What binds an agent, and what only guides it

Every agent runs on the owner's machine, so anything the machine's credentials
can do, an agent can try. The rules above hold because of the credentials, not
because agents follow instructions:

| Layer | What it stops | Holds against a determined agent? |
|---|---|---|
| Branch protection, admins included | unreviewed, red or directly pushed code on the default branch | yes |
| The agents' token: this repository only, no Administration, no Workflows (§ Setup, step 2) | changing protection or settings; pushing a workflow file | yes — **if** the pipeline's OS user holds no other GitHub credential. With the token in `GH_TOKEN` on your own account, your own login stays in the keyring, one command away |
| The `allowlist` check: bound issue, Files in scope, control paths only under `human-decision` | out-of-scope diffs; agents editing the pipeline's own files | the scope rules, yes. The control-path rule rests on a label, and the agents' token may label issues — against a determined agent it is a guard rail |
| The bot's token | an agent approving its own work | no — every agent runs as the same OS user and can read the file. What holds is that the bot is a separate account; what keeps agents from using it is the next row |
| `.claude/hooks/bash_guard.sh`, `allowlist_guard.sh` | acting as the bot outside the reviewer, editing branch protection, force pushes, an implementer editing its own issue, a subagent applying `human-decision`, out-of-scope edits | no — they read command text and catch the accident, not the workaround (`docs/LESSONS.md` § Merging) |
| `.claude/settings.json`, `dontAsk` mode | any command no allow rule names, and the denied ones | no — rules match text |
| Agent prompts | everything else | no |

The bash guard lets only the `github-pr-reviewer` subagent use
`.claude/bin/gh-reviewer`, plus a main session that is not the scheduled tick —
the owner running the review skill by hand. The tick's session is recognised by
`PIPELINE_TICK=1` in its environment (§ Setup, step 5). `gh-reviewer` itself
runs only the calls a review needs.

Making the bot's token a boundary needs a different shape: reviews run as a
separately scheduled session under a second OS user that alone can read the
token, and the main tick only marks pull requests ready for review. The
template does not do that yet.

---

## The tick

`/pipeline-tick` runs one cycle:

1. `.claude/bin/pipeline run --apply` — fast-forwards the tick's checkout to
   the default branch on GitHub (when it is clean and on it), so this tick's
   agents read today's prompts and branch from today's code — then surveys GitHub (issues, PRs, the
   default branch's checks), applies bookkeeping (lint results, stale claims,
   exhausted rounds, re-runs, missing auto-merge), **claims** each work item by
   label, and prints the dispatch list as JSON.
   Its `setup_problems` also name what is wrong with the machine the tick runs
   on and that no pull request would show: a `.claude/settings.local.json`,
   uncommitted changes in the tick's checkout, a checkout not on the default
   branch, a missing `jq` or bash.
2. Spawns one agent per dispatch item, in parallel, and waits for them.
3. Reports: what was dispatched, what came back, and everything waiting on the
   owner.

A claim is a label set *before* the agent starts. Each claim re-reads the item
first and is skipped when another tick, an agent or a person has taken or moved
it since the survey — so an overlapping tick loses the race instead of handing
the same item out twice. The window between that read and the write is a
second, not the survey's minute; two ticks that start in the same second can
still collide, so schedule one tick, not several.

Limits (`limits` in `.claude/pipeline/config.json`), each counting the agents
already running as well as those this tick starts:

| Limit | Default | What it bounds |
|---|---|---|
| `max_parallel_implement` | 3 | implementers on new issues |
| `max_parallel_fix` | 3 | fix passes (ci-failed, review, conflict, draft-resume) |
| `max_parallel_review` | 2 | reviewers |
| `max_parallel_triage` | 2 | triage runs |
| `max_dispatch_per_tick` | 8 | agents one tick starts, all kinds together |
| `max_fix_rounds`, `max_conflict_rounds` | 2, 2 | passes per PR before the owner |
| `max_review_attempts` | 2 | reviews at one head that end without a verdict |
| `max_comment_only_reviews` | 2 | comment-only reviews at one head |
| `stale_in_progress_hours`, `stale_working_hours` | 6, 4 | when a claim counts as abandoned |
| `stale_waiting_hours` | 12 | CI that never finishes; an approval that never merges |
| `max_agent_runs_per_day` | 50 | agents all ticks start in 24 hours together, counted in the tick log; 0 turns it off |

One planner runs at a time — an idea, or the roadmap through its
`pipeline:planning` issue — and two issues whose **Files in scope** overlap are
never in flight together; an issue in review counts, since a fix pass on it
edits the same files.

`.claude/bin/pipeline run` without `--apply` is a dry run: it prints what a tick
would do and changes nothing. How to schedule the real tick, and in which
permission mode, is § Setup step 5.

When the agents a tick started have all returned, the tick surveys again —
at most three rounds — so a PR that turned green meanwhile does not wait half
an hour for its review.

### Holding, pausing, stopping

| What | How | What stops |
|---|---|---|
| **Hold** | automatic, while branch protection on the default branch is missing or differs from what `setup-repo` sets (`require_protection`, default true) | everything the pause stops; the fix is `setup-repo` in a terminal |
| **Pause** | `pipeline:pause` on any open issue — or, where no agent can reach it, the file `pipeline/pause` in the git directory (`touch "$(git rev-parse --git-common-dir)/pipeline/pause"`) | new agents, bookkeeping, and auto-merge: the tick turns it off on every open PR, and turns it back on for approved PRs once unpaused. *Needs you* is still reported |
| **Stop** | quit the Claude app, or end the `claude` processes | the agents already running — a pause does not reach them |

### What the tick leaves behind

- **The tick log**, one JSON line per `run --apply` in
  `<git dir>/pipeline/ticks.jsonl`: what was dispatched, what failed, whether it
  was paused. In no branch and no PR; shared by every worktree. It counts the
  daily budget.
- **The status issue** (`pipeline:status`), rewritten every tick: when the last
  tick ran, *Needs you*, what was dispatched, setup problems, and seven days of
  counts. Pin it; if its time is old, no tick is running.
- **`pipeline stats [--days N]`** — merged PRs, time from PR to merge and from
  issue to close, how many needed a fix pass or were a spec defect, how many
  issues went to triage, and agent runs by kind. The signals `docs/LESSONS.md`
  learned to count.
- **`pipeline doctor`** — every part of the setup, one line each: tools, the
  login the tick uses, the bot's token and access, repository settings, branch
  protection, whether that login can read CI, the Actions token, labels, that
  each required check exists as a workflow job, the allow rule for the test
  command, the checkout, worktrees, and config typos. Exit 1 while anything
  fails.
- **`pipeline checks <PR> [--wait]`** — the required checks at a pull
  request's head, with their run ids, read the way the tick reads them. Exit 0
  green, 1 red, 8 still running, like `gh pr checks`, which the agents' token
  cannot read (§ Setup, step 2).

---

## Setup

One time, in this order. `docs/ADOPTING.md` walks through it for an existing
project.

**1. Tools on the machine that runs the tick.** `gh` (authenticated with the
agents' token, step 2), `jq`, Python ≥ 3.9, bash, and the project's own toolchain. See
`docs/AgentEnvironment.md`. After installing, **fully quit and reopen** the
Claude app — a running app keeps its old `PATH`.

**2. The agents' token.** The agents must not hold the owner's full
credentials: with them, any agent could remove branch protection, and every
other rule here would be advice.

- Signed in as the owner, create a **fine-grained** personal access token.
  Repository access: **only this repository**. Permissions: Contents,
  Issues, Pull requests, Actions (to read CI and re-run a failed check) and
  Commit statuses (to count review attempts) read and write;
  Administration **read-only**, so each tick can check branch protection is
  still on. **No Administration write, no Workflows.** Set an expiry, and a
  reminder to renew it.
- A fine-grained token has **no Checks permission**, so on a private
  repository it cannot read check runs, `gh pr checks`, or a pull request's
  `statusCheckRollup`. The pipeline reads CI through the Actions API instead
  (every required check is an Actions job), and agents ask
  `.claude/bin/pipeline checks <PR>` rather than `gh pr checks`. `doctor`'s
  *CI readable* line fails if the token cannot.
- Make it the only credential the tick and its agents see: a separate OS user
  for the pipeline, logged in with `gh auth login --with-token < token-file`
  and `gh auth setup-git` (so `git push` uses it too), with no other GitHub
  login, SSH key or credential-manager entry. Only then does this step bind.
  Setting it as `GH_TOKEN` for the scheduled task on your own account keeps
  the agents' own commands on the token, but your full login stays in the
  keyring, and anything an agent runs can reach it — a guard rail, not a
  boundary (§ Merging).
- Check, in the tick's environment: `gh auth status` names a fine-grained token
  (`github_pat_…`) and nothing else.
- The agents cannot push a change to `.github/workflows/`, and a pull request
  whose branch must absorb one (a conflict pass after a workflow change on the
  default branch) is rejected on push. That escalates; you update the branch
  with your own login (`gh pr update-branch <N>`).
- `setup-repo` (step 4) needs Administration, so run it with your own login,
  not this token.

**3. The reviewer bot account.** A second GitHub account that only the reviewer
agent acts as (GitHub's terms allow one machine account per person).

- Create a free account (e.g. `<owner>-bot`) in a private browser window, with
  its own e-mail (Gmail `you+bot@gmail.com` works). Turn on 2FA.
- Signed in as the bot, create a **classic** personal access token with the
  `repo` scope and an expiry. Classic, because a fine-grained token cannot be
  scoped to a repository the account has not yet been invited to. The `repo`
  scope reaches every repository the bot can see, so use this bot for this
  repository only. An expired token stops every approval: renew it before the
  date.
- Save it, and nothing else, to the path in `reviewer_token_file`
  (`.claude/bin/pipeline config reviewer_token_file` prints it). Never commit
  it or paste it into a chat. On Windows, Notepad appends `.txt` — rename it.
- Check: `.claude/bin/gh-reviewer api user --jq .login` prints the bot's login.
  The wrapper runs only the calls a review needs (§ Merging).

**4. Apply the repository settings** (needs a repository plan with branch
protection: public, or GitHub Pro/Team for private), with your own login:

```bash
.claude/bin/pipeline setup-repo --dry-run   # shows every call it would make
.claude/bin/pipeline setup-repo
```

It creates the labels, turns on auto-merge and branch deletion, allows squash
merges only, makes the Actions token read-only and unable to approve pull
requests, holds workflow runs from outside contributors' forks for approval,
protects the default branch as described above, reads the protection back and
prints a `DRIFT` line for anything GitHub did not take, and invites the bot as
a collaborator with write access, accepting with its token only that
repository's invitation. Idempotent. **After this, nobody can push to the
default branch directly** — including you. The bash guard refuses `setup-repo`
from a Claude session; run it in a terminal.

**5. Schedule the tick in `dontAsk` mode.** Unattended, a permission prompt is
a hang: nobody answers it, and every claimed item waits out its staleness
timer. In `dontAsk` mode a command that no allow rule in `.claude/settings.json`
names is refused instead, and the agent carries on or escalates.

- Preferred: a fresh session per tick from the OS scheduler (Task Scheduler,
  cron), run as the pipeline's OS user (step 2):
  `PIPELINE_TICK=1 claude -p "/pipeline-tick" --permission-mode dontAsk`.
  `PIPELINE_TICK=1` tells the bash guard this main session is the tick, which
  never acts as the reviewer bot itself.
- A Claude desktop scheduled task uses the default mode of the settings it
  starts with. For the pipeline's OS user, set
  `"permissions": {"defaultMode": "dontAsk"}` in its user settings and
  `PIPELINE_TICK=1` in its environment.
- `/loop 30m /pipeline-tick` keeps one conversation for weeks, so each tick pays
  for the ones before it. Use it only while you watch.
- Not `bypassPermissions`: it switches the allow list off, and only the hooks
  remain (§ Merging).

Run `.claude/bin/pipeline doctor` first, then one tick by hand in that mode. A
refused command shows in the report.
`.claude/pipeline/tests/test_settings.py` checks a hand-kept list of the
commands the prompts use against the allow and deny rules; when a prompt starts
using a new command, add it there and to the rules together.

---

## What stays with the owner

The pipeline stops rather than guessing, and lists the item under *Needs you*
in the tick's report — labelling it `status:needs-human` where it would
otherwise move on — when:

- an issue carries `human-decision`, or work would contradict a design doc or a
  locked decision;
- an issue's **Files in scope** touches a pipeline control path
  (`control_paths` in `.claude/pipeline/config.json`: `.github/`, `.claude/`,
  `run_tests.sh`, `CLAUDE.md`, `CONTRIBUTING-agents.md`, `docs/Pipeline.md`).
  These files decide what agents may do; an agent that may edit them can
  loosen them. The tick never dispatches such an issue, nor a fix pass or a
  review on its pull request. You work it yourself (the `github-issue-fetch`
  skill), label it `human-decision` — the `allowlist` check accepts a change to
  a control path only for an issue with that label, and the bash guard refuses
  that label to every agent — and review the pull request yourself with the
  `github-pr-review` skill;
- a pull request comes from a fork, or is bound to no issue. The agents
  neither review nor fix it. A pull request from the agents' account — on a
  personal repository, that is also yours — must be bound to an issue, or
  `allowlist` fails it;
- an asset must be produced (`asset` issues are never auto-assigned);
- triage has already answered once, or a PR has used all its fix passes;
- the roadmap's next step is behind a human gate. The planner says so in one
  `pipeline:idle` issue and stops planning until it is closed;
- a decision nobody has made yet — a design question, or two docs that
  disagree. Agents file it as a *question issue* (`agent-task` +
  `status:needs-human`) instead of work that assumes an answer.

Everything else is the pipeline's.

**You hear about it.** A hand-off comment mentions you and is posted as the
reviewer bot where it can be — GitHub does not notify you of comments made by
your own account, and every agent writes as you.

### Answering

Answer in a **comment**, then add the **`human:answered`** label. Do not just
remove `status:needs-human`: the label is how the tick knows you answered, and
what it does next depends on the item.

| Waiting on you | What the tick does with your answer |
|---|---|
| an `agent-task` issue (a question, an escalation after triage, an implementer that ended twice) | sets it `status:escalated` and sends it to triage — even if triage answered it before — which writes your answer into the issue body; lint takes it from there |
| an `idea` | hands it back to the planner, which reads your comment |
| a pull request | removes `status:needs-human`, gives it its fix and conflict rounds back, and stops counting the comment-only reviews and review attempts so far at this head (a `pipeline/answered` commit status records where they stood). The reviewer reads your comment on its next pass |

The rest you resolve directly: close a PR bound to no issue or merge it after
your own review, close the `pipeline:idle` issue once its gate is open, fix a
red default branch (the tick closes `pipeline:main-red` itself), work
`human-decision` issues with `github-issue-fetch`.
