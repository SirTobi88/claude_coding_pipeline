---
description: One cycle of the coding pipeline -- survey GitHub, claim work, spawn the planner, implementers, reviewers and triage it calls for, report
model: sonnet
---

Run one pipeline tick. `docs/Pipeline.md` is the contract;
you are only the dispatcher. **Decide nothing yourself** and never go looking
for other work — the script decides, you spawn and report.

## 1. Survey, bookkeeping and claims

```bash
.claude/bin/pipeline run --apply
```

It prints JSON. If the command fails (no `gh`, not authenticated, network),
report the error verbatim in one line and stop — do not retry more than once.

- `"paused": true` → report "paused (`pipeline:pause`)" and stop.
- `setup_problems` → report them first; still dispatch whatever is listed.
- `dispatch` empty → report the one-line summary below and stop.

## 2. Spawn

For every entry in `dispatch`, spawn one agent, **all in a single message** so
they run in parallel, and wait for them. Pass only what the entry says — the
agents' contracts are their own files, and instructions you add compete with
them.

| `kind` | `subagent_type` | Prompt |
|---|---|---|
| `implement` | `github-issue-resolver` | `Implement issue #<issue> ("<title>").` |
| `fix` | `github-issue-resolver` | `Fix PR #<pr> on branch <branch> for issue #<issue>. Reason: <reason>. Fix round <round>.` |
| `review` | `github-pr-reviewer` | `Review PR #<pr> (branch <branch>, issue #<issue>).` |
| `triage` | `github-triage` | `Triage issue #<issue>. It is here because of <reason>.` |
| `plan` | `github-planner` | `Mode: <mode>.` plus ` Idea issue #<issue>.` when `issue` is set |

If an agent type is not available (agent definitions load at session start),
say so in the report, then release its claim so the next tick can retry:
`.claude/bin/pipeline release pr <pr>` for review/fix, `release issue <issue>`
for triage/plan, and `set-status <issue> status:ready` for implement.

## 3. Report

A short block, nothing else:

- **Dispatched** — one line per agent: kind, number, and the result it returned (PR URL, verdict, escalation).
- **Waiting / in flight / deferred** — counts, with numbers.
- **Needs you** — every `awaiting_human` entry. This is the only list the owner has to act on.
- Any `FAILED` line from `ops_done` or `claims`, verbatim.

If nothing was dispatched and nothing needs the owner, one line: "Pipeline idle."
