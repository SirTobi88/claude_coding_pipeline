---
name: github-issue-create
description: Draft a single-seam agent-task GitHub issue (per .github/ISSUE_TEMPLATE/agent-task.md and CONTRIBUTING-agents.md's "for the top agent" rules) from a described piece of work, pin its interface, check its allowlist against every open issue, then file it with gh. Interactive mode shows the draft and waits for a yes; autonomous mode (the pipeline's planner, triage and reviewer) files directly and lets issue-lint judge readiness. Use when the user says "file an issue for X", "create an issue to...", "add this to the backlog", or "write up an issue for Y".
---

# Filing a GitHub issue

This is the **top-agent** side of `CONTRIBUTING-agents.md`: seam design done
*before* work is handed out. Pin the interface, declare the blast radius, make
the definition of done a command. An issue this skill files should never need a
human to "fix it up" — if a section cannot be filled honestly, it is not ready.

Input: a description of the work — a sentence, a design-doc pointer, a bug, a
follow-up. If it bundles more than one seam ("and" joining two changes), split
it: a task that touches three subsystems is not one issue, and neither is "and".

## Two modes

**Interactive** (a human asked for the issue in this conversation): show the
complete draft and file only on an explicit yes.

**Autonomous** (invoked by `github-planner`, `github-triage` or
`github-pr-reviewer` — `docs/Pipeline.md`): nobody is watching, so nothing here
waits for an answer.

- **File directly** with `gh issue create` (§ 5). No draft shown, no confirmation.
- **Label `agent-task`** (or `asset`, § 0). **Never set a `status:*` label** —
  issue-lint judges the issue on creation and sets `status:ready`,
  `status:blocked` or `status:needs-spec` itself; a rejected spec goes to triage.
- **An unpinned seam is not a reason to stop** (§ 2): file the interface as its
  own issue first — stubs plus the failing test — and put its number in this
  issue's **Blocked by**. Interfaces land before implementations.
- A Files-in-scope collision with an open issue (§ 3) becomes a **Blocked by**
  on the issue that holds the file, never a shared allowlist.
- Anything that needs a design decision is not filed: the calling agent raises
  it as `status:needs-human` instead.

## 0. Does this need an asset? Then it is two issues

Work that a human or specialist must *produce* — art, audio, copy, a legal text
— is a different seam from the code that consumes it. Model it as a **parent
with a sub-issue**:

- **Parent**: the code seam, a normal `agent-task`.
- **Child**: the deliverable, labelled `asset`, from
  `.github/ISSUE_TEMPLATE/asset-task.md`, pinned by **file paths and a format
  contract** instead of signatures, with a DoD split into mechanical and human
  checks.

The child does not block the parent by default — code lands against a
placeholder and the real asset replaces it. An `asset` issue is **never**
auto-assigned; the pipeline skips it and the owner routes it.

## 1. Pin the seam

- **Goal**: one imperative sentence. If it needs "and", it is two issues.
- **Why**: what this unblocks, what breaks if done wrong — enough for an agent
  to make the judgement calls the issue does not spell out.
- **Context**: specific section headings in the design docs, not whole files.
  Confirm each heading exists before citing it — a wrong anchor is worse than
  none.

## 2. Pin the interface — the most important step

Read the code the issue touches. For everything the issue calls "pre-existing,
do not change", quote it **verbatim** from the repo.

**Anchor on symbols, not line numbers.** `market.py` `refresh_row()` still means
the same thing after the next merge; `market.py L511–536` does not, and a stale
anchor turns into a mandatory "interface does not match the repo" escalation.
issue-lint warns on line anchors in Interface and Context.

If the seam does not exist yet, the issue is not ready as an implementation
issue. Interactive: say so, and offer to file the interface issue first.
Autonomous: file the interface issue first and block this one on it.

## 3. Files in scope — check for collisions

Draft the allowlist, then compare it against every open agent-task issue:

```bash
gh issue list --label agent-task --state open --json number,title,body,labels
```

Any overlap is a defect unless the two are an **ordered handoff**
(`CONTRIBUTING-agents.md` § *Ordered handoffs*): the later one's Blocked by
names the earlier, and both say which holds the file first. Files the project
marks as one-at-a-time (see *Project rules*) are never shared.

A disjoint allowlist makes two issues *logically* parallel-safe. The
*mechanical* half — two agents in one directory — is solved by worktrees at work
time, so do not serialise issues over a collision a worktree already solves.

## 4. Non-goals, Definition of done, Blocked by, Size

- **Non-goals**: name the adjacent thing an agent will be tempted to fix.
- **Definition of done**: every line is a command that exits 0 or a named
  artefact. "Works correctly" is not a line — if it cannot be a command, the
  missing harness is what should be filed first.
- **Blocked by**: real issue numbers, or "nothing".
- **Size**: if it does not fit "one branch, one review sitting", split it.

## 5. File it

```bash
gh issue create --title "[area] <summary>" --body-file <tmpfile> --label agent-task
```

Only add labels or milestones that already exist (`gh label list`). Delete the
temp file afterwards.

## 6. After filing

Report the issue number and URL, and anything still open: an interface issue
this one waits on, a collision resolved by Blocked by, a question raised to the
owner.
