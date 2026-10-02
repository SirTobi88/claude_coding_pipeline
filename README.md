# Claude coding pipeline

A template for running software work through Claude Code agents with as little
human involvement as possible: a **planner** files issues, **implementers** on
an efficient model work them in isolated worktrees, a **reviewer** on a strong
model judges every change and approves it under its own GitHub account, and
**GitHub** merges the moment the approval and every required check are green.

The owner is needed only for decisions no agent should make: design questions,
art and other human deliverables, and milestone calls.

It was extracted from a real project (a Godot game, one developer plus Claude,
~250 agent-authored PRs) after the first version of the same idea kept landing
on its owner's desk. `docs/LESSONS.md` records what went wrong and which rule
fixed it.

---

## How it works

```mermaid
flowchart LR
    idea["idea / roadmap"] --> planner["Planner<br/>(Opus)"]
    planner -->|files| issue["agent-task issue"]
    issue --> lint{"issue-lint<br/>(Actions)"}
    lint -->|needs-spec| triage["Triage<br/>(Opus)"]
    triage --> lint
    lint -->|ready| impl["Implementer<br/>(Sonnet, worktree)"]
    impl -->|escalated| triage
    impl -->|PR| ci{"required checks<br/>ci · tooling · allowlist · contract"}
    ci -->|red| fix["Fix pass<br/>(Sonnet, max 2)"]
    fix --> ci
    ci -->|green| review["Reviewer<br/>(Opus, as bot)"]
    review -->|changes requested| fix
    review -->|approve + auto-merge| merge(["GitHub merges"])
    review -->|needs human| human(["Owner"])
    triage -->|design decision| human
```

**Three ideas carry the design:**

1. **Every state has a next owner.** GitHub labels are the state machine
   (`status:ready`, `status:in-progress`, `status:in-review`,
   `status:escalated`, `status:needs-human`, …). A deterministic script decides
   what each state needs; agents only make judgement calls. There is no local
   state file and no worktree scan: any machine sees what the pipeline sees.
2. **The platform enforces the rules, not the agents' good behaviour.** Branch
   protection requires the checks and one approval from someone other than the
   author. Every agent pushes as the owner, so only the reviewer bot's approval
   counts. Nobody — agent or owner — can merge unreviewed or red code, or push to
   the default branch.
3. **Work is cut into single seams with a declared blast radius.** Each issue
   pins its interface and lists the files it may touch. A hook refuses
   out-of-scope edits as they happen; CI refuses them in the diff. That is what
   makes parallel agents safe and reviews checkable.

### Roles

| Role | Where | Model | Job |
|---|---|---|---|
| Tick | `/pipeline-tick` on a schedule | Sonnet, low effort | Survey GitHub, claim work, spawn agents, report |
| Planner | `.claude/agents/github-planner.md` | Opus | `idea` issues and the roadmap → agent-task issues, interface first |
| Implementer | `.claude/agents/github-issue-resolver.md` | Sonnet | One issue → one PR; or one fix pass on a PR |
| Reviewer | `.claude/agents/github-pr-reviewer.md` + skill | Opus | Review against the contract, fix mechanical defects, approve as the bot |
| Triage | `.claude/agents/github-triage.md` | Opus | Answer escalations and repair rejected specs, once |
| Decision logic | `.claude/pipeline/pipeline.py` | — | What is ready, what needs review / a fix / nothing; tested offline |
| CI | `.github/workflows/` | — | Tests, allowlist, PR shape, issue readiness |

### What reaches the owner

The tick's *Needs you* list, nothing else: design contradictions, second
escalations after triage, PRs that used all their fix or conflict passes,
reviews that never reach a verdict, CI that never finishes, approvals that
never merge, `human-decision` and `asset` issues, anything touching the
pipeline's own files, PRs from forks, bound to no issue or aimed at a branch
other than the default, a red default branch, missing or drifted branch
protection (the tick then holds everything), and roadmap steps marked as human
gates. Hand-offs mention you, posted as the reviewer bot where possible, so
GitHub notifies you.

**Answering.** Answer in a comment, then add the `human:answered` label. Do not
just remove `status:needs-human`: the label is how the tick knows you answered
(`docs/Pipeline.md` § *Answering*).

**Watching.** Every tick rewrites one issue labelled `pipeline:status`: when it
ran, *Needs you*, what it dispatched, and seven days of counts. Pin it; if its
time is old, no tick is running.

**Stopping.** The kill switch is one label: `pipeline:pause` on any open issue.
The tick then starts nothing and turns off auto-merge on every open PR; agents
already running finish (`docs/Pipeline.md` § *Holding, pausing, stopping*).

---

## Quickstart

1. **Get the files** — *Use this template* for a new repo, or copy them into an
   existing one (`docs/ADOPTING.md` § B).
2. **Fit it to the project** — `.claude/pipeline/config.json`, `run_tests.sh`,
   the `ci` job, *Project rules* in `CONTRIBUTING-agents.md`, `CLAUDE.md`,
   `docs/ROADMAP.md` (`docs/ADOPTING.md` § C).
3. **Set up once** — tools, a restricted token for the agents, a reviewer bot
   account and token (`docs/Pipeline.md` § *Setup*). Commit the adoption
   first, then run `.claude/bin/pipeline setup-repo` in a terminal with your
   own login: afterwards the default branch accepts only reviewed pull
   requests.
4. **Check, then run a tick** — `.claude/bin/pipeline doctor`, then
   `/pipeline-tick` by hand in `dontAsk` mode. Schedule a fresh session per
   tick from the OS scheduler, as the pipeline's OS user:
   `PIPELINE_TICK=1 claude -p "/pipeline-tick" --permission-mode dontAsk`
   (a desktop app scheduled task works too: `docs/Pipeline.md` § *Setup*,
   step 5).
5. **Feed it** — open an issue labelled `idea` with one line of what you want.

## Requirements

- Claude Code (the tick and the agents run locally; they spawn subagents in git
  worktrees).
- GitHub with **branch protection** available: a public repository, or GitHub
  Pro/Team for a private one.
- A second GitHub account for the reviewer bot.
- A fine-grained token for the agents: this repository only,
  Administration read-only (so the tick can see branch protection is still
  on), no Administration write and no Workflows permission. The platform rules
  hold because the agents cannot change them, as long as this token is the
  only GitHub credential the tick's OS user holds (`docs/Pipeline.md`
  § *What binds an agent, and what only guides it*).
- On the machine running the tick: `gh` (authenticated with that token), `jq`,
  Python ≥ 3.9, bash (Git Bash on Windows), `awk`, `tr`, `sed`, `grep`, and the
  project's toolchain.

---

## Repository layout

```
.claude/
  agents/        planner, implementer (resolver), reviewer, triage
  commands/      pipeline-tick.md — the dispatcher
  skills/        github-pr-review, github-issue-create, interactive helpers
  hooks/         allowlist_guard.sh, bash_guard.sh, lib/issue_scope.sh (the one
                 parser), lib/pr_allowlist.sh (the allowlist check) + tests
  bin/           pipeline (Python shim), gh-reviewer (gh as the bot)
  pipeline/      pipeline.py, config.json, tests/
  settings.json  hook registration, permissions for unattended runs
.github/
  workflows/     ci.yml (ci, tooling), pr-contract.yml (allowlist, contract), issue-lint.yml,
                 portability.yml (the hooks on macOS and Windows)
  ISSUE_TEMPLATE/ agent-task.md, asset-task.md
  pull_request_template.md
CHANGELOG.md              what each merged wave changed
CLAUDE.md                 project guidelines template
CONTRIBUTING-agents.md    the contract: seams, allowlist, DoD, escalation, review routing
docs/
  Pipeline.md             the flow: roles, states, merging, tick, setup
  ADOPTING.md             how to bring it into a project, and what to customise
  AgentEnvironment.md     machine facts template
  ROADMAP.md              roadmap template the planner reads
  LESSONS.md              why each rule exists
run_tests.sh              the single test entry point (here: the pipeline's own tests)
```

## Useful commands

```bash
.claude/bin/pipeline run              # dry run: what a tick would do now
.claude/bin/pipeline run --apply      # one tick's bookkeeping and claims (the tick runs this)
.claude/bin/pipeline lint 42          # is issue #42 ready, and if not, why
.claude/bin/pipeline checks 12 --wait # PR #12's required checks, read the way the tick reads them
.claude/bin/pipeline claim issue 42 --interactive  # work #42 by hand; the tick leaves it alone
.claude/bin/pipeline release issue 42 --hold       # hand it back to the pipeline
.claude/bin/pipeline setup-repo --dry-run          # every call setup-repo would make
.claude/bin/pipeline setup-repo       # labels, settings, protection, bot access (in a terminal, your own login)
.claude/bin/pipeline doctor           # is every part of the setup in place
.claude/bin/pipeline stats --days 7   # what the pipeline did (default: the last 30 days)
.claude/bin/gh-reviewer api user      # is the reviewer token working
./run_tests.sh                        # the test entry point
```

## Cost and limits

- Every tick is a Claude session; every dispatched job is a subagent run. Opus
  runs (planner, reviewer, triage) dominate. Tune `limits` in
  `.claude/pipeline/config.json` and the schedule to your budget;
  `max_agent_runs_per_day` (default 50) is the hard ceiling, and
  `pipeline stats` shows where the runs went.
- The optional `portability` workflow runs on macOS and Windows runners, which
  cost ten and two times the Linux minutes on a private repository. It runs
  only when the hooks or the pipeline change.
- The tick runs on your machine. Started by the OS scheduler, it runs while the
  machine is on; as a desktop app scheduled task, only while the app runs
  (missed runs catch up on the next start); in a `/loop` session, only while
  you watch it.
- The allowlist guard only sees edits made through Claude's file-editing tools;
  shell writes are caught by the CI `allowlist` job instead.
- The hooks, the permission rules and the bot's token file are guard rails,
  not boundaries: every agent runs as the same OS user and could read the
  bot's token. What holds against a determined agent is branch protection and
  the agents' restricted token (`docs/Pipeline.md` § *What binds an agent, and
  what only guides it*).
- The pipeline trusts the design docs. If they are vague, the planner files
  vague issues and triage escalates them to you — the fix is better docs, not a
  smarter agent.
