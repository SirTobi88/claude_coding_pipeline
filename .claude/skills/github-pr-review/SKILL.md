---
name: github-pr-review
description: Review a pull request against this repo's CONTRIBUTING-agents.md contract -- the issue as the contract, CI read at the head commit (it is the reproduction of the suite), the definition-of-done lines CI does not cover reproduced by hand, the project's own rules, silent design-doc contradictions -- then delegate to the built-in code-review skill, fix mechanical defects on the branch, and submit ONE real review as the reviewer bot (.claude/bin/gh-reviewer) pinned to the reviewed commit -- APPROVE (and enable auto-merge), REQUEST_CHANGES (the fix pass's task list), or NEEDS_HUMAN (one precise question). GitHub merges an approved PR once its required checks are green. Never for a branch it authored. Use when the user says "review PR #N", "check this pull request", or points at a PR to review; the pipeline's github-pr-reviewer agent runs it.
---

# Reviewing a pull request

You are the only approval that can merge anything: branch protection requires
one approving review from someone other than the author, every agent pushes as
the owner's account, and only the reviewer bot's review counts
(`docs/Pipeline.md` § *Merging*). So the review is the gate, and it is judged
by **this** checklist — not by "does the diff look fine".

Argument: `$ARGUMENTS` is a PR number, URL or branch. If empty and a human is
present, ask; in the pipeline the prompt always names it.

**Every GitHub write that carries the verdict goes through
`.claude/bin/gh-reviewer`.** Plain `gh` is the owner — the author — and GitHub
ignores an author's approval. Reads may use either. Never print or copy the
token the wrapper reads.

## 0. Setup

```bash
.claude/bin/gh-reviewer api user --jq .login      # must print the bot's login
gh pr view <N> --json number,title,body,headRefName,headRefOid,baseRefName,state,url,isDraft
```

- The wrapper fails → **stop**. Report the setup problem and release the claim
  (§ 11). Never fall back to posting a verdict as the owner.
- Not `OPEN`, or a draft → stop and release.
- The branch is `agent/<issue>-<slug>`: that issue is the contract.
  `gh issue view <issue> --json body,state,labels`. No issue (a non-agent
  branch that closes nothing) → review against the PR description alone, and
  say so in the verdict.
- If you created this branch yourself earlier in this session, you are its
  author: stop and say so.

Record `sha` = `headRefOid`. **Everything you verify is about this commit.**

## 1. Worktree

Review in a worktree, never by switching the parent checkout:

```bash
git fetch origin <headRefName>
git worktree add .claude/worktrees/review-<N> -b review-<N> --track origin/<headRefName>
```

A distinct local name (`review-<N>`) keeps the branch pushable for § 8 while the
implementer may still hold `agent/<n>-<slug>` in its own worktree. If git aborts
with `detected dubious ownership`, see `docs/AgentEnvironment.md`.

## 2. CI at the head commit

```bash
gh pr checks <N>
```

The pipeline only dispatches a review when every check at the head is green, so
normally this is a confirmation. It is also the **reproduction** of the suite:
CI runs the project's test command from a clean checkout at exactly this commit
— which is what "reproduce the definition of done, do not trust the
checkboxes" asks for, done by a machine that cannot misread a run.

- Any check red or still running → not yours yet. Release (§ 11) and stop; the
  next tick routes a red check to a fix pass.
- `allowlist` green means every changed file is in the issue's **Files in
  scope** and the PR closes exactly its own issue. `contract` green means the
  description has its four sections. Do not re-derive either.

## 3. Definition of done — every line accounted for

Go through the issue's DoD line by line and put each in one bucket:

| Line | How it is verified |
|---|---|
| The test command passes; a named test exists; a golden file is unchanged | CI (§ 2). Confirm the **named** test really exists in the diff and ran (`gh run view <run-id> --log \| grep <test_name>`). A test the DoD names but the diff lacks is a failed line even when CI is green. |
| `git diff --name-only` matches Files in scope | the `allowlist` check |
| A named number — a timing, a size, a threshold | reproduce it with the command the DoD names, in the worktree, and compare against the claim, not just that a number exists |
| A screenshot or recording | look at it in the PR; judge it against what the line asks |

A line the PR claims as verified that you cannot reproduce — and CI does not
cover — is a hand-back (§ 8d): *"asserts a DoD item it did not verify"*. A line
you cannot reproduce only because this machine lacks a tool is **not** the
author's fault: say so in the verdict and judge the rest.

## 4. The project's own rules

`CONTRIBUTING-agents.md` § *Project rules* lists this project's
architecture rules and hazards. Check each against the diff. The ones a test
already enforces in CI need no second look; the ones marked "enforced by review"
are yours.

## 5. Generic hazards

- A changed file that carries a "generated — do not edit" header without its
  generator changing too — § 8d.
- A file type the project says must never be hand-edited (listed in *Project
  rules*) — § 8d unless the issue explicitly says how it is produced.
- A new global name (class, module, symbol registered globally) — search the
  repo for collisions.
- Secrets, credentials or tokens in the diff — § 8d, and say so first.

## 6. Design-doc contradictions

Read the issue's cited Context sections against the diff. Did the PR silently
settle a documented decision differently — a changed constant, a locked decision
in `CLAUDE.md` violated, a number the docs leave open now baked in?

This is judgement. A **clear** contradiction whose fix is a choice between two
defensible designs is not yours or the author's to make: verdict NEEDS_HUMAN
(§ 9) with the doc line and the question. A contradiction with an obvious fix
inside the allowlist is an ordinary finding.

## 7. Code review

Invoke the built-in `code-review` skill on this PR at the default level, for
bugs, simplification and efficiency. **Do not pass `--comment`** (it posts as
the owner, whose comments the pipeline ignores) **or `--fix`** (it writes before
the verdict exists and ignores the allowlist). Carry its findings into § 8.

## 8. Route every finding

The question that picks the route is not "how big is it?" but **"is this a
defect in this change, or adjacent work this change revealed?"**

**a. Fix it on the branch** — the default for a mechanical defect: a wrong
bound, a missing guard, a typo'd constant, a stale comment or doc line *in the
files this PR touches*. Allowed when all hold:

- the fix stays inside the issue's **Files in scope** (the allowlist binds you
  exactly as it binds the author — CI will reject you otherwise);
- the fix decides nothing the author would reasonably decide differently;
- it is a defect in this change.

```bash
git commit -am "fix(<area>): <what>"
git push origin HEAD:<headRefName>
sha="$(git rev-parse HEAD)"
gh pr checks <N> --watch --interval 30
```

Push with the explicit refspec (the local branch is `review-<N>`). Your verdict
in § 9 is about the new `sha`, once its checks are green. Say in the verdict
what you changed.

**Small doc and comment fixes go here too, not into a follow-up issue.** Filing
a new issue for a stale comment in the diff in front of you costs a whole
issue → PR → review → merge cycle for a ten-second change.

**b. Required change the author should make** — a real defect that is not
mechanical, or has two defensible repairs. It goes into a REQUEST_CHANGES
verdict as a concrete task: file, what is wrong, what done looks like. The
pipeline's fix pass works from exactly that list.

**c. Optional improvement** — worth mentioning, not worth blocking. Put it in
the APPROVE body as non-blocking.

**d. Contract failure — hand back.** A DoD claim that cannot be reproduced, a
project rule broken, a generated or never-hand-edit file edited by hand. Do not
repair these yourself: repairing them hides the signal that the issue was
underspecified (`CONTRIBUTING-agents.md` § *What a reviewer repairs*). Verdict
REQUEST_CHANGES naming the failure. If the root cause is the *issue* — it never
said how a file should be produced, it named no harness — also label the PR
`spec-defect` (`.claude/bin/gh-reviewer pr edit <N> --add-label spec-defect`)
so the pattern is countable.

**e. Adjacent work** — a new seam, something outside this issue's allowlist, a
design decision: file a follow-up issue with the `github-issue-create` skill in
autonomous mode **before** you submit the verdict, and name its number in the
verdict. Never use this route for a defect in the diff itself — that is how a
bug launders itself into the default branch. A route-e finding does not block
APPROVE.

## 9. The verdict — one review, as the bot, pinned to the commit

Write the report to a temp file. First line `**Verdict: <VERDICT>**`, then: what
you checked (§ 2–7, one line each), each finding with its route, what you fixed,
follow-up issues filed.

Before submitting, confirm the head is still the commit you reviewed:

```bash
head="$(gh pr view <N> --json headRefOid --jq .headRefOid)"
[ "$head" = "$sha" ] || echo "head moved"
```

If it moved (someone pushed while you reviewed), **submit nothing**: release
(§ 11) and stop. The next tick reviews the new head. An approval must never land
on code you did not read.

Submit through the API so the review names the commit explicitly:

```bash
.claude/bin/gh-reviewer api "repos/{owner}/{repo}/pulls/<N>/reviews" \
  -f commit_id="$sha" -f event=<EVENT> -F body=@report.md
```

| Verdict | `event` | Then |
|---|---|---|
| **APPROVE** — § 2–6 clean, code review found nothing blocking, every § 8a fix is green in CI | `APPROVE` | enable auto-merge (below) |
| **REQUEST_CHANGES** — any § 8b or § 8d finding | `REQUEST_CHANGES` | nothing; the pipeline dispatches a fix pass |
| **NEEDS_HUMAN** — § 6 decision, or a DoD line only the owner can judge | `COMMENT` | `.claude/bin/gh-reviewer pr edit <N> --add-label status:needs-human` |

NEEDS_HUMAN's report ends with **one precise question** and the options you see
— something the owner can answer in a line.

Enable auto-merge on APPROVE:

```bash
.claude/bin/gh-reviewer pr merge <N> --auto --squash --delete-branch --match-head-commit "$sha"
```

GitHub merges the moment every required check is green. If it answers that the
PR is already in a clean state, merge directly with the same flags minus
`--auto`; branch protection still enforces every rule either way.

## 10. Blocked by

If the issue's **Blocked by** names an issue that is still open, the PR should
not have been worked yet. Verdict NEEDS_HUMAN with that fact; do not approve
code built on an interface that has not landed.

## 11. Release and clean up

```bash
.claude/bin/pipeline release pr <N>
git log origin/<headRefName>..HEAD      # must be empty: push before you remove
git worktree remove --force .claude/worktrees/review-<N>
git branch -D review-<N>
```

`--force` is expected — a test run leaves caches and reports behind. On Windows,
a just-exited test process can hold a handle for a second; retry once.
