---
name: github_clean-branches
description: Find and delete local and remote (origin) git branches that are done with — merged into main, or whose PR was merged/closed — while protecting main, the current branch, and anything with unmerged work. Understands this repo's git worktree workflow (.claude/worktrees/, per CLAUDE.md) and detaches a branch's worktree before deleting it. Always previews a dry-run report before touching anything. Use when the user says "clean up branches", "delete merged branches", "prune my branches", "tidy up origin", or asks to get rid of stale/leftover feature branches, including the debris branches that git worktree add sometimes leaves behind.
---

# Cleaning up local and remote branches

This repo accumulates a lot of short-lived branches — one per issue, one per PR,
plus whatever `git worktree add` leaves behind (see the `worktree-agent-*` style
names that turn up when a worktree is created without an explicit `-b`). This
skill's job is to figure out, from actual evidence, which branches are genuinely
done with — and to leave everything else alone.

Deleting a branch on `origin` is a change other people (and other agents,
mid-`gh issue view`) can see, and it's the kind of action described in the
top-level safety rules as needing explicit confirmation before you do it. So the
shape of this skill is always **report first, delete second, and never both in
the same breath** — regardless of what permission mode the session is running
in. Local `git branch -d` is low-risk (it already refuses to delete anything
with unmerged work), but keep it on the same confirm-then-execute gate as the
remote delete so there's one clear moment where the user sees the whole plan.

## 1. Sync and orient

```bash
git fetch --prune origin
```

`--prune` matters here: it drops local `origin/<branch>` tracking refs for
branches already deleted on GitHub (e.g. by "delete branch on merge"), so the
classification step below isn't working from stale information.

Then gather the three things every later step depends on:

```bash
# Default branch (protect this one unconditionally)
git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@'
# falls back to: gh repo view --json defaultBranchRef -q .defaultBranchRef.name

# Current branch in *this* checkout (protect this one unconditionally)
git branch --show-current

# Every worktree and the branch checked out in it
git worktree list --porcelain
```

Parse the worktree output into a `branch -> path` map (`worktree <path>` lines
followed by `branch refs/heads/<name>` lines). A branch checked out in *any*
worktree — not just the current one — cannot be `git branch -d`'d until that
worktree is removed, including the primary checkout itself (which is just
`git worktree list`'s first entry).

## 2. Enumerate and classify every branch

List candidates:

```bash
git for-each-ref --format='%(refname:short)' refs/heads/
git for-each-ref --format='%(refname:short)' refs/remotes/origin/   # then strip the origin/ prefix, drop HEAD
```

Pull PR state in one call rather than one `gh` call per branch:

```bash
gh pr list --state all --limit 500 --json number,headRefName,state,mergedAt,baseRefName
```

For every branch name in the union of local and remote lists, skip it entirely
(don't even list it as a candidate) if it's the default branch or the current
branch. Otherwise classify it by evidence, strongest first:

- **merged** — `git merge-base --is-ancestor <ref> origin/<default>` succeeds
  (check the local ref if one exists, the remote ref otherwise), **or** the
  matching PR's `state` is `MERGED`. Ancestry is the safer signal (it's true
  regardless of how the merge happened); the PR check catches squash-merges,
  which leave no ancestry trail at all. This repo merges **squash-only** and
  deletes head branches on merge (`pipeline setup-repo`), so nearly every
  merged branch is classified by its PR record, and what remains for this
  skill is leftovers: branches from before that setting, and local branches.
- **abandoned** — the matching PR's `state` is `CLOSED` and it is *not* merged
  by ancestry. This is real but unmerged work that a human decided not to land.
  Treat it as a separate, opt-in category (see step 4) rather than folding it
  into "merged" — the risk profile is different even though the repo's git log
  proves this project *does* actually close PRs without merging.
- **active/unknown** — neither of the above. No matching PR, or an open one, or
  a local-only branch nobody ever pushed. Leave these alone and just report
  them; guessing here is exactly the kind of silent judgement call the delete
  gate exists to avoid.

For every branch, also note whether it's checked out in a worktree (from the
step 1 map) — this doesn't change its classification, it changes what step 5
has to do before it can delete it.

## 3. Present the dry-run report before touching anything

Group by classification, and within "merged"/"abandoned" note local-only /
remote-only / both, plus the worktree path if one is attached. This is the
report the user is actually approving — don't summarize it away. Something
like:

```
Merged into main (safe to delete):
  agent/83-player-state       local + origin   [worktree: .claude/worktrees/83-player-state]
  chore/uploader-resume-honesty  origin only
  worktree-agent-a1d6e38645f66ea97  local only   (stray worktree-add branch, merged via #88)

Closed without merging (unmerged work — opt in):
  fix/82-issue-id-and-bounds-checks   local + origin   (PR #82 closed, not merged)

Left alone (open PR / no PR / can't tell):
  some-wip-branch             local only, no matching PR
```

If a branch's classification came from ancestry rather than a `gh` PR record
(e.g. `gh` isn't authenticated, or there's simply no PR for it), say so — the
user may want to eyeball those before agreeing.

## 4. Ask before deleting anything

Confirm explicitly:

1. Proceed with the "merged" list? (This is the expected default yes.)
2. Include the "abandoned" (closed-without-merge) list too, or leave those for
   the user to handle by hand? Default to **no** unless asked — it's unmerged
   code, and "the PR was closed" isn't always "this was rejected"; sometimes
   it's "we'll come back to it."
3. If any candidate branch only exists on `origin` (no local copy) or only
   locally (never pushed), that's already visible in the report from step 3 —
   no separate question needed, just delete on whichever side(s) it exists.

Don't reuse a "yes" from a previous run of this skill in the same session for a
different branch list — fetch/classify/report/confirm is one unit every time
this is invoked.

## 5. Execute, in this order, per branch

1. **If the branch has a worktree attached**, remove it first — you cannot
   `git branch -d` a checked-out branch, and force-deleting the branch out from
   under a live worktree corrupts it rather than cleaning it up.

   ```bash
   git worktree remove <path> --force
   ```

   `--force` is required even for a merged branch's worktree, because a test run
   usually leaves ignored build caches and reports behind, so a tree that ran
   tests is never clean. On Windows, removal can fail once with `Permission
   denied` while a just-exited test process still holds a file handle — wait a
   moment and retry once rather than investigating further. See
   `docs/AgentEnvironment.md`.

2. **Delete the local branch**, if one exists:

   ```bash
   git branch -d <branch>
   ```

   Use `-d`, not `-D`, by default — it refuses to delete anything Git can't
   verify is merged, which is a real safety net against a bad classification.
   Only fall back to `-D` when the branch was classified as merged *via the PR
   record* rather than ancestry (the squash-merge case), since `-d` will
   correctly-but-uselessly refuse those; say explicitly when you do this.

3. **Delete the remote branch**, if one exists:

   ```bash
   git push origin --delete <branch>
   ```

   This is the step that's actually outward-facing — it's a push, visible to
   anyone else with the repo. It only runs after the confirmation in step 4,
   never speculatively.

Don't let one branch's failure abort the batch. If `git branch -d` refuses
because Git disagrees with the classification, or a worktree removal fails
twice, skip that branch, record why, and keep going with the rest.

## 6. Final summary

Report three lists: what was actually deleted (local/remote/both, per branch),
what was skipped and why (failed delete, worktree removal failure, user opted
out of the abandoned category), and — briefly — what was left alone as
active/unknown, so the user knows the skill didn't just quietly ignore
branches it wasn't sure about.
