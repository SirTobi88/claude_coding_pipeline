---
name: github-issue-fetch
description: Fetch a GitHub issue filed from this repo's agent-task template (.github/ISSUE_TEMPLATE/agent-task.md), verify it is actually ready to work per CONTRIBUTING-agents.md, claim it, create the agent/<n>-<slug> worktree, and hand back a scoped summary before any code is written. Interactive counterpart of the pipeline's implementer. Use when the user says "work on issue #N", "pick up issue N", pastes an issue URL, or asks what an issue's scope is.
---

# Working a GitHub issue (interactively)

Get from "issue number" to "worktree ready, scope understood, nothing assumed" —
and refuse, loudly, when the issue itself is not ready.

This is the interactive path. The autonomous pipeline (`/pipeline-tick`,
`docs/Pipeline.md`) hands issues to `github-issue-resolver` itself. So the two
never work one issue twice: skip an issue labelled `status:in-progress` or
`status:in-review` unless the user insists, and claim the issue once you create
the branch (§ 5).

Argument: `$ARGUMENTS` is an issue number, `#N`, or an issue URL. If empty, ask.

## 1. Fetch

```bash
gh issue view <N> --json number,title,body,url,state,labels,assignees,milestone
```

`gh` unauthenticated or no GitHub `origin` → say so and stop. `CLOSED` → ask
whether to proceed.

## 2. Parse the template sections

Split the body on its `## ` headings (Goal, Why, Context, Interface, Files in
scope, Non-goals, Definition of done, Blocked by, Size). A section is **not
ready** if it is empty, still holds the template's placeholder text, or — for
Interface — has no code block with real signatures. `issue-lint`
(`.claude/bin/pipeline lint <N>`) applies the same bar and prints what is
missing.

## 3. Readiness gate — stop if any hold

- Interface empty or without real signatures.
- Files in scope empty.
- **Blocked by** names an issue that is still open (`gh issue view <M> --json state`).
- The issue lacks `agent-task`, or does not look like the template at all.

Report exactly what is missing and stop.

## 4. Cross-check the interface against the repo

For everything the Interface calls pre-existing, `Read` the file and confirm it
matches what is quoted. A mismatch is one of the four things that are always
escalated, never guessed past — say so and stop.

## 5. Claim it and create the worktree

```bash
git fetch origin
git worktree add .claude/worktrees/<N>-<slug> -b agent/<N>-<slug> origin/<default-branch>
.claude/bin/pipeline claim issue <N>
```

`<slug>` is a short kebab-case form of the title. Default to a worktree, never
the parent checkout: a working tree holds one branch, and shared build caches
race between concurrent processes. `docs/AgentEnvironment.md` covers the
machine-specific parts (e.g. registering worktrees as `safe.directory`).

## 6. Hand back the scoped summary

- **Goal** and **Why**
- **Context** — the exact doc sections to read, nothing wider
- **Interface**, verbatim
- **Files in scope** — as the allowlist it is; everything else is read-only
- **Non-goals**
- **Definition of done** — as literal commands
- the four escalations: a file outside the allowlist, a design-doc contradiction,
  an interface mismatch, a DoD with no harness

Do not implement anything in this skill's own run.
