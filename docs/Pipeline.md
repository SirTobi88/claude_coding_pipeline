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

GitHub labels are the state. There is no local state file, no worktree scan, no
branch-name inference: any machine, or a phone, sees exactly what the pipeline
sees.

### Issues (`agent-task`)

```
            ┌──────────── issue-lint (GitHub Actions, on every edit) ─────────────┐
 opened ──▶ │ status:needs-spec ◀──▶ status:blocked ◀──▶ status:ready             │
            └──────┬────────────────────────────────────────┬──────────────────────┘
                   │ triage repairs the spec                │ tick claims it
                   ▼                                        ▼
            status:ready                          status:in-progress ──▶ status:in-review ──▶ closed by merge
                                                          │                     │
                                                          ▼                     ▼ PR closed unmerged
                                                  status:escalated ◀────────────┘
                                                          │ triage answers once
                                                          ├──▶ status:ready / status:blocked
                                                          └──▶ status:needs-human   (second time, or a design decision)
```

| Label | Set by | Next owner |
|---|---|---|
| *(none)* | a new issue | issue-lint |
| `status:needs-spec` | issue-lint | triage |
| `status:blocked` | issue-lint | issue-lint, when the blocker closes |
| `status:ready` | issue-lint, triage | tick → implementer |
| `status:in-progress` | tick (the claim) | implementer |
| `status:in-review` | implementer, tick | reviewer, via the PR |
| `status:escalated` | implementer, tick | triage |
| `status:needs-human` | triage, tick, reviewer, planner | **the owner** |

Issue-lint owns only the first three. It never touches an issue that is in
progress, in review, escalated or waiting on a human. A second escalation or
spec rejection after triage has answered once (`triaged` label) goes straight to
`status:needs-human`.

### Pull requests

| Condition at the PR's head commit | Next owner |
|---|---|
| draft | nobody — not ready |
| merge conflict | implementer, fix pass (`conflict`, does not spend a round) |
| CI running | wait |
| CI failed | implementer, fix pass (`ci-failed`) |
| CI green, no bot verdict at this commit | reviewer |
| bot requested changes at this commit | implementer, fix pass (`review`) |
| bot approved at this commit | GitHub auto-merge |
| `status:needs-human` | **the owner** |

A PR gets `max_fix_rounds` fix passes (default 2, labels `fix-round-1`,
`fix-round-2`). One more failure labels it `status:needs-human`. Conflicts are
mechanical and do not count. A verdict is only ever read **at the head commit**
— an approval of an older commit means "review again", never "approved".

### Markers

| Label | Meaning |
|---|---|
| `pipeline:working` | An agent holds this issue or PR right now. The tick leaves it alone until it goes stale (`stale_working_hours`), then treats it as abandoned. |
| `pipeline:pause` | On **any** open issue: every tick does nothing. The kill switch — open an issue with it from your phone. |
| `pipeline:idle` | The planner found nothing it may plan without a human. Roadmap planning stops until you close this issue. |
| `idea` | Raw input for the planner. Any one-liner is enough. |
| `spec-defect` | The review handed a PR back because the *issue* was underspecified. A countable signal for improving issues. |
| `triaged` | Triage has answered this issue once. |

---

## Merging: what GitHub enforces

Nothing merges by hand, and nobody — agent or owner — pushes to the default
branch. Branch protection (applied by `pipeline setup-repo`):

- **Required checks**, pinned to GitHub Actions so a hand-posted commit status
  cannot stand in for them (`required_checks`, default `ci`, `tooling`,
  `allowlist`, `contract`).
- **One approving review** from someone other than the author. Every agent
  pushes as the owner's account, so **only the reviewer bot's approval counts**
  — this is what makes "never judge your own work" a platform rule rather than a
  sentence in a doc.
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

---

## The tick

`/pipeline-tick` runs one cycle:

1. `.claude/bin/pipeline run --apply` — surveys GitHub, applies bookkeeping
   (lint results, stale claims, exhausted fix rounds, missing auto-merge),
   **claims** each work item by label, and prints the dispatch list as JSON.
2. Spawns one agent per dispatch item, in parallel, and waits for them.
3. Reports: what was dispatched, what came back, and everything waiting on the
   owner.

A claim is a label set *before* the agent starts, so an overlapping tick cannot
hand the same item out twice. Limits (config): `max_parallel_implement`
implementers and `max_parallel_review` reviewers at a time, one planner run per
tick, and two issues whose **Files in scope** overlap are never in flight
together.

`.claude/bin/pipeline run` without `--apply` is a dry run: it prints what a tick
would do and changes nothing. Schedule the real tick with the Claude desktop
app's scheduled tasks or `/loop 30m /pipeline-tick`.

---

## Setup

One time, in this order. `docs/ADOPTING.md` walks through it for an existing
project.

**1. Tools on the machine that runs the tick.** `gh` (authenticated as the
owner), `jq`, Python ≥ 3.9, bash, and the project's own toolchain. See
`docs/AgentEnvironment.md`. After installing, **fully quit and reopen** the
Claude app — a running app keeps its old `PATH`.

**2. The reviewer bot account.** A second GitHub account that only the reviewer
agent acts as (GitHub's terms allow one machine account per person).

- Create a free account (e.g. `<owner>-bot`) in a private browser window, with
  its own e-mail (Gmail `you+bot@gmail.com` works). Turn on 2FA.
- Signed in as the bot, create a **classic** personal access token with the
  `repo` scope. Classic, because a fine-grained token cannot be scoped to a
  repository the account has not yet been invited to.
- Save it, and nothing else, to the path in `reviewer_token_file`
  (`.claude/bin/pipeline config reviewer_token_file` prints it). Never commit
  it or paste it into a chat. On Windows, Notepad appends `.txt` — rename it.
- Check: `.claude/bin/gh-reviewer api user --jq .login` prints the bot's login.

**3. Apply the repository settings** (needs a repository plan with branch
protection: public, or GitHub Pro/Team for private):

```bash
.claude/bin/pipeline setup-repo --dry-run   # shows every call it would make
.claude/bin/pipeline setup-repo
```

It creates the labels, turns on auto-merge and branch deletion, allows squash
merges only, protects the default branch as described above, and invites the
bot as a collaborator with write access, accepting with its token. Idempotent.
**After this, nobody can push to the default branch directly** — including you.

**4. Try one tick by hand**, then schedule it.

---

## What stays with the owner

The pipeline stops and labels `status:needs-human` rather than guessing when:

- an issue carries `human-decision`, or work would contradict a design doc or a
  locked decision;
- an asset must be produced (`asset` issues are never auto-assigned);
- triage has already answered once, or a PR has used all its fix passes;
- the roadmap's next step is behind a human gate. The planner says so in one
  `pipeline:idle` issue and stops planning until it is closed.

Everything else is the pipeline's.
