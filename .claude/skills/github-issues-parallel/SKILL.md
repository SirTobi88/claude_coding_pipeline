---
name: github-issues-parallel
description: Interactive fan-out -- survey every ready agent-task issue, work out which may run together (disjoint Files in scope), show the plan, and on a go-ahead spawn one github-issue-resolver per issue, each in its own worktree, each ending at an open PR. The autonomous equivalent is /pipeline-tick. Use when the user says "work all the open issues", "run the ready issues in parallel", or "clear the backlog in parallel".
---

# Working the ready issues in parallel (interactively)

The autonomous pipeline already does this on every tick
(`.claude/bin/pipeline run`, `docs/Pipeline.md`). Use this skill when a human
wants to trigger a fan-out now and see the plan first. It follows the tick's
labels, so the two never hand out the same issue twice.

Two things make parallel work safe, and both must hold:

- **Disjoint allowlists** make two issues *logically* independent.
- **Separate worktrees** make them *mechanically* independent — a working tree
  holds one branch, and build caches race between concurrent processes.

## 1. Survey

```bash
.claude/bin/pipeline run          # read-only: what a tick would dispatch now
gh issue list --label status:ready --state open --json number,title,body
```

The dry run already applies every exclusion (`human-decision`, `asset`,
blocked, in flight), the readiness lint, the overlap check and the concurrency
limits from `.claude/pipeline/config.json`. Its `dispatch` list is the plan;
`deferred` says what waits behind what.

## 2. Show the plan, then stop

Present:

- **Spawning now** — issue, title, allowlist in one line each.
- **Deferred** — and behind what (a shared file, the concurrency cap).
- **Excluded / not ready** — and why.
- **The PRs this opens** — "this opens N pull requests on `<owner/repo>`".

Get an explicit go-ahead that names the count. Pushing N branches and opening N
PRs is outward-facing.

## 3. Claim, then spawn

For each issue, claim it first so the tick does not take it too:

```bash
.claude/bin/pipeline claim issue <N>
```

Then spawn one `github-issue-resolver` per issue, **all in a single message**
so they run concurrently, each with the prompt
`Implement issue #<N> ("<title>").` — nothing more; the agent's contract is its
own file.

If worktree isolation is refused because git cannot verify the worktree
(`detected dubious ownership`), register the worktree path as described in
`docs/AgentEnvironment.md` and retry.

## 4. Report

When the agents return: one table — issue, branch, outcome, PR link. **Every
agent should have a PR**; an empty cell means it escalated or stopped early, so
say which. Escalations are the most valuable output: they are issues that were
wrong. The tick picks up the PRs for review on its own.

Nothing here merges or approves anything. The reviewer bot and GitHub do.
