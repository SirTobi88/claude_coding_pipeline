# Working on this project as a coding agent

This file is the contract between the **top agent** — which designs seams — the
**task agents**, which each fill exactly one seam on one branch, and the
**reviewer**, whose approval is the only one that can merge.

`CLAUDE.md` still applies in full and outranks this file. This document says
*how work is cut up and judged*; `docs/Pipeline.md` says *how work moves* —
which agent picks up what, and which label says so.

---

## The unit of work is a seam, not a line count

A task agent gets one issue. That issue names one place where the code must
change, quotes the interface on both sides of it, and lists the files the agent
may touch. Whether that comes to 40 lines or 300 is not the point — the point is
that the change has **one boundary**, so it can be reviewed by checking that
boundary rather than by reading the whole diff.

- **A task that touches three subsystems is not one issue**, however few lines.
- **Splitting a coherent change to hit a size target makes things worse.** Two
  half-implemented seams cost more to review than one whole one.
- **If an issue cannot name its interface, it is not ready.** That is a
  top-agent failure, not a task-agent failure.

## Blast radius is declared up front

Every issue carries a **Files in scope** allowlist. The agent may create or
modify those files and nothing else. Read anything; write only what is listed.

If the work turns out to need a file that is not listed — **stop and escalate**
(below). Widening the list yourself is the single most common way an
agent-authored branch becomes unmergeable.

Two things check it. `.claude/hooks/allowlist_guard.sh` refuses an out-of-scope
write through the Edit and Write tools before it happens; it does not see a
write made through the shell, so it catches the accident, not the workaround.
The `allowlist` job in `.github/workflows/pr-contract.yml` checks the whole diff
whichever way it was written, and it is a required check. Both read the issue
through the same parser (`.claude/hooks/lib/issue_scope.sh`). An implementer
never edits its own issue — widening the list is triage's answer to an
escalation — and `.claude/hooks/bash_guard.sh` refuses an implementer's
`gh issue edit`. On an
`agent/<N>-` branch both take the issue from the branch name, never from the PR
body.

## Definition of done means a command that exits 0

An issue is done when something can be *run* that proves it, not when the code
looks right.

- The project's test command (`test_command` in `.claude/pipeline/config.json`)
  passes, including the new test the issue names.
- Where the issue names a number — a timing, a size, a threshold — the number is
  produced and recorded, not asserted in prose.
- Where behaviour must not change, there is a **golden file** and the test is
  equality against it.

If an issue's DoD cannot be expressed as a command, the missing thing is a test
harness, and that harness is its own issue that must land first.

**Reproduce every DoD line and watch it pass.** If you genuinely cannot run one,
say which and why in the PR's *Not verified* section. If your run and CI
disagree, report both.

## When you are blocked

Comment on the issue, set `status:escalated`
(`.claude/bin/pipeline set-status <N> status:escalated`), and stop. The four
things that are always escalated rather than solved:

1. The work needs a file outside the allowlist.
2. The issue contradicts a design doc or a locked decision.
3. The interface in the issue does not match what is actually in the repo.
4. A DoD item cannot be verified because the harness for it does not exist.

An escalation is not a dead end: the triage agent answers it — widening the
allowlist when the file belongs to the same seam and nothing in flight holds it,
re-quoting a drifted interface, filing a missing harness — and puts the issue
back in the queue. What triage cannot decide within the design docs goes to the
owner as one precise question.

An issue that comes back with a precise question is a good outcome. An issue
that comes back with a plausible guess baked into 300 lines is the expensive one.

## The design docs are canonical

- The issue cites specific doc sections. Those are binding.
- If implementing the issue would contradict a documented decision, **escalate**
  — do not implement your reading of it and mention the conflict afterwards.
- Do not edit design docs unless the issue's Files in scope says you may. When a
  decision genuinely changes, the doc update ships in the same change — but the
  decision itself is made by a human.

## Branches, commits, PRs

- Branch: `agent/<issue-number>-<slug>`.
- Conventional commits: `feat(area):`, `fix(area):`, `docs(area):`,
  `test(area):`, `chore(area):`.
- One issue per PR, referenced as `Closes #<N>`. The description follows
  `.github/pull_request_template.md` — what changed, which DoD items were
  verified and how, what was **not** verified, what you left alone. The
  `contract` job checks all four sections are there.

## Who closes a PR out

**GitHub does, and only on an approval the author could not have given.** The
rule is about authorship, not agency: nobody may be the sole judge of their own
work. A checklist the reviewer is merely trusted to follow does not hold up —
the project this pipeline came from measured it — so it is enforced by the
platform (`docs/Pipeline.md` § *Merging*):

- The default branch requires the required checks and **one approving review
  from someone other than the author**, admins included, with stale approvals
  dismissed on every new commit.
- Every agent pushes as the owner's account, so the only approval that can count
  is the **reviewer bot's** — and only the reviewer agent acts as the bot
  (`.claude/bin/gh-reviewer`; `.claude/hooks/bash_guard.sh` refuses it to every
  other agent and to the scheduled tick's own session). `docs/Pipeline.md`
  § *What binds an agent* says which of these rules the platform enforces and
  which are guard rails.
- The reviewer approves the exact commit it reviewed and enables auto-merge.
  Nobody runs a merge by hand, and nobody pushes to the default branch.

---

## Project rules

<!--
EDIT THIS SECTION for your project. It is what the reviewer checks in
`github-pr-review` § 4 and § 5. For each rule, say whether a test enforces it
(and which) or whether it is "enforced by review". Examples of the kind of rule
that belongs here, from the project this pipeline came from (a Godot game):

- Architecture: "Simulation is plain data; engine nodes are presentation only.
  Nothing under sim/ extends Node or reads delta." -- enforced by
  test/architecture/test_sim_is_plain_data.gd.
- Never hand-edit: ".tscn and .tres scene files are made in the editor or by an
  @tool script, and the issue says which." -- enforced by review.
- One-at-a-time file: "project.godot is where parallel branches collide; only
  one issue in flight may list it." -- enforced by the planner's overlap check.
- Shared machine state: "user:// is shared between worktrees; tests use
  test_tmp.gd for scratch paths." -- enforced by a source-scan test.
- Generated companions: "x.gd.uid ships with x.gd" -- set
  SCOPE_COMPANION_SUFFIXES in .claude/hooks/lib/issue_scope.sh.
-->

| Rule | Enforced by |
|---|---|
| *(add your project's rules)* | |

---

## What is enforced mechanically

Once a rule is here, its prose elsewhere should shrink to a sentence and a
pointer. A rule whose enforcement you cannot find is a rule you will assume does
not exist.

| Rule | Enforced by | When |
|---|---|---|
| Files in scope is an allowlist | `.claude/hooks/allowlist_guard.sh` | before an Edit or Write |
| Files in scope is an allowlist; one issue per PR; every PR from the agents' account is bound to an issue that is open and in flight | `allowlist` job, `pr-contract.yml` (required) | every push and description edit |
| A pipeline control path changes only under a `human-decision` issue, and never through an unbound pull request | `allowlist` job; the tick dispatches no agent onto such an issue or its PR. The label itself is guarded only by `bash_guard.sh` while the agents' token may label issues | every push; every tick |
| Agents neither review nor fix a fork's PR or one bound to no issue | the tick (`decide()` lists them under *Needs you*) | every tick |
| The PR description has its four sections | `contract` job, `pr-contract.yml` (required) | every push and description edit |
| An issue is ready only when its template is filled | `issue-lint.yml` → `status:ready` / `blocked` / `needs-spec` | every issue edit, and when a blocker closes |
| Nobody approves their own work; nothing merges red | branch protection (`pipeline setup-repo`) | every merge |
| Only the reviewer acts as the bot; an implementer does not edit issues; no agent applies `human-decision`; nobody force-pushes or edits branch protection from a session | `.claude/hooks/bash_guard.sh` — a guard rail, not a boundary | before a Bash command |
| Agents cannot change protection or push workflow files | the agents' token (`docs/Pipeline.md` § Setup) | always |
| The checks above do what this section says | `.claude/hooks/test/`, `.claude/pipeline/tests/` | `tooling` job |

Four things about the allowlist checks are worth stating plainly:

- **The edit-time guard fails open; CI does not.** A guard that bricks a session
  when the network blinks gets switched off, so when it cannot read the issue it
  lets the write through, and CI is the backstop. Without `gh` and `jq` on the
  machine the guard is effectively off — the tick's `setup_problems` say so.
- **CI judges a PR with the default branch's code, not the PR's.** `allowlist`
  and `contract` run on `pull_request_target`: the workflow file, the parser
  and the rules all come from the default branch, and nothing from the pull
  request is executed. A PR cannot loosen the rule that judges it.
- **A required check is only as trusted as the workflow that produces it.**
  Branch protection matches a check by name and app, so a PR that added a
  workflow with a job named `allowlist` could produce one. The agents' token
  has no Workflows permission, so an agent cannot push any workflow file. A
  fork is not bound by that token: its workflow runs wait for the owner's
  approval (`setup-repo`), and the tick never hands its PR to an agent.
- **On an `agent/<N>-` branch the issue is read off the branch name**, never off
  the PR body, which the author writes.

---

## For the top agent

The top agent is the `github-planner` agent, with `github-triage` repairing what
it or anyone else gets wrong. A human can play the role; the rules are the same.

**Your job is seam design, not review.** Before an issue is handed out, its
signatures are pinned: either they exist in the repo, or an earlier *interface
issue* lands them as stubs plus the failing test and the implementation issue is
**Blocked by** it. The task agent fills a hole whose shape is already fixed.

issue-lint marks an issue `status:ready` when the template is filled and nothing
it is blocked by is open. What lint cannot check is yours to get right:

- The **Interface** contains real signatures, verbatim, that exist in the repo
  or in an interface issue this one is blocked by. Anchor on symbol names, not
  line numbers.
- The **Files in scope** allowlist is complete, and no other open issue shares a
  file with it — unless the two are an **ordered handoff** (below).
- Every **Definition of done** line is a command or a named artefact.
- **Non-goals** names the adjacent thing the agent will be tempted to fix.

### Ordered handoffs — when two issues may share a file

One file on two allowlists is normally a defect. It is allowed when **all** of
these hold:

- The later issue's **Blocked by** names the earlier one, so they can never be
  in flight together.
- **Both** issues say so, naming each other and which one holds the file first.
- The earlier issue's Non-goals tells it not to touch what the later one needs.

**Never write that an issue is the "sole holder" of a file.** That claim cannot
be verified when written and ages into a lie that makes the *other* issue's
legitimate edit look like a violation. Say which issue holds it **first**.

### What a reviewer repairs, and what it hands back

**Five findings are hand-back-only.** Request changes — do not fix it yourself —
when a PR modifies a file outside the allowlist (CI already fails it), asserts a
DoD item it did not verify, breaks a project rule, hand-edits a file the project
says must never be hand-edited, or silently resolves a design contradiction
(that one goes to the owner as NEEDS_HUMAN, since its fix is a decision). Fixing
these yourself hides the signal that the issue was underspecified.

A hand-back does not park the PR: REQUEST_CHANGES sends it to a fix pass by the
implementer, at most twice. The evidence is kept a different way — when the root
cause is the *issue*, the reviewer labels the PR `spec-defect`, which is
countable where a review comment is not.

**Everything else is the reviewer's to land.** An ordinary quality finding — a
missing guard, an off-by-one, a stale comment in a touched file — is *fixed* on
the branch, not written up. The routing question is not how big the finding is,
but:

- **A defect in the change under review** → fixed on the branch when mechanical,
  otherwise a REQUEST_CHANGES task.
- **Adjacent work the change revealed** → a follow-up issue, filed before the
  verdict and linked from it.

**Never file a follow-up issue for a defect in the diff in front of you.** That
is how a real bug launders itself past review.

The reviewer's procedure is `.claude/skills/github-pr-review/SKILL.md`.
