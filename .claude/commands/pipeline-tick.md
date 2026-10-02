---
description: One cycle of the coding pipeline -- survey GitHub, claim work, spawn the planner, implementers, reviewers and triage it calls for, report
model: sonnet
effort: low
---

Run one pipeline tick. `docs/Pipeline.md` is the contract;
you are only the dispatcher. **Decide nothing yourself** and never go looking
for other work — the script decides, you spawn and report.

## 1. Survey, bookkeeping and claims

```bash
.claude/bin/pipeline run --apply
```

It first fast-forwards this checkout to the default branch on GitHub (when the
checkout is clean and on it), then surveys, and prints JSON. If the command fails (no `gh`, not authenticated, network),
report the error verbatim in one line and stop — do not retry more than once.

- `"paused": true` → report "paused: <pause_reason>", then everything in
  `awaiting_human`, and stop. A paused tick still turns off auto-merge on open
  PRs and still lists what needs the owner.
- `setup_problems` → report them first; still dispatch whatever is listed.
- `dispatch` empty → skip § 2 and go straight to § 3.

## 2. Spawn

For every entry in `dispatch`, spawn one agent, **all in a single message** so
they run in parallel, and wait for them. Pass only what the entry says — the
agents' contracts are their own files, and instructions you add compete with
them.

| `kind` | `subagent_type` | Prompt |
|---|---|---|
| `implement` | `github-issue-resolver` | `Implement issue #<issue> ("<title>").` plus ` Resume branch <branch>.` when `resume` is true |
| `fix` | `github-issue-resolver` | `Fix PR #<pr> on branch <branch> for issue #<issue>. Reason: <reason>. Fix round <round>. Reviewer bot: <reviewer>.` |
| `review` | `github-pr-reviewer` | `Review PR #<pr> (branch <branch>, issue #<issue>).` |
| `triage` | `github-triage` | `Triage issue #<issue>. It is here because of <reason>.` |
| `plan` | `github-planner` | `Mode: <mode>.` plus ` Idea issue #<issue>.` when `issue` is set, plus ` Planning issue #<tracking>.` when `tracking` is set |

**An agent type is not available** (agent definitions load at session start):
say so in the report, then give back its claim, so the next tick can retry
without having spent anything:

| `kind` | Command |
|---|---|
| `review` | `.claude/bin/pipeline release pr <pr>` |
| `fix` | `.claude/bin/pipeline release pr <pr> --round-label <round_label>` |
| `implement` | `.claude/bin/pipeline set-status <issue> status:ready` — nothing when `resume` is true |
| `triage`, `plan` with `issue` | `.claude/bin/pipeline release issue <issue>` |
| `plan` with `tracking` | `gh issue close <tracking> --comment "planner not available"` |

**When every agent has returned**, run § 1 again: agents finish at different
times, and a PR that turned green or got its verdict while you waited should not
wait for the next scheduled tick. Spawn what it dispatches and wait again. Stop
when it dispatches nothing, and after three rounds in any case -- the next tick
picks up the rest.

**An agent returned an error or stopped without saying what it did:** release
only the hold — `.claude/bin/pipeline release pr <pr>` for review and fix,
`release issue <issue>` for triage and plan — and never refund a round: the
agent may have pushed before it failed.

## 3. Report

A short block, nothing else:

- **Dispatched** — one line per agent: kind, number, and the result it returned (PR URL, verdict, escalation).
- **Waiting / in flight / deferred** — counts, with numbers.
- **Needs you** — every `awaiting_human` entry, and every command an agent's report names as refused. This is the only list the owner has to act on.
- Any `FAILED` or `SKIPPED` line from `ops_done` or `claims`, verbatim.
- If `status_issue` is a number, one line linking it; if it starts with `FAILED`, that line verbatim.

If nothing was dispatched and nothing needs the owner, one line: "Pipeline idle."
