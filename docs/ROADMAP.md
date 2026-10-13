# Roadmap

<!-- EDIT for your project. The planner agent reads this file (and any others
listed in `roadmap_docs` in .claude/pipeline/config.json) when the queue is
empty, and files the next step as issues -- unless the step is behind a human
gate. So write it for that reader:

- Order the steps. The planner takes the first one not done.
- Mark a step done with its issue numbers, e.g. "~~Add login~~ -- done (#12, #14)".
- Mark human gates explicitly: "GATE (human): ...". The planner never plans past
  one; it opens a single `pipeline:idle` issue naming the gate instead.
- Keep decided and undecided apart. Anything under "Open questions" is not
  decided by filing an issue that assumes an answer.

This repository is the template itself: what follows is the template's own
roadmap. A project that adopts the pipeline replaces everything below.
-->

## Goal

A template that a second project can adopt and then run unattended: the tick
runs on a schedule, agents implement and review their own work, and the owner
acts only on the *Needs you* list — design decisions, control paths and human
gates. **Done for now** means: the pipeline has run a second project for a
week with the owner touching nothing but *Needs you*, and every rule that week
showed missing is written down in `docs/LESSONS.md` and fixed.

## Steps

1. ~~**Catch up the changelog.**~~ -- done (#103, #104). `CHANGELOG.md`
   stopped at Wave 9 (2026-10-01); it now has entries for Waves 10 to 35.
2. ~~**Reviewer reports without detours.**~~ -- done (#106, #115). The
   reviewer writes its report into the `.pipeline-tmp/` of the checkout its
   session works in (the root, or its own worktree; never `review-<N>`, the
   pull request's tree), and reviewers run at the repository root.
3. ~~**Retire `max_comment_only_reviews`.**~~ -- done (#105). Since #81 one
   comment-only bot review hands the PR to the owner, so the limit could no
   longer fire; it is gone from `Limits`, `config.json` and `docs/Pipeline.md`.
4. ~~**One source for the machine's tool list.**~~ -- done (#107, #108, #116).
   The list is named once: `MACHINE_TOOLS` and `GUARD_TOOLS` in `pipeline.py`,
   the table in `docs/AgentEnvironment.md`, held together by tests; the other
   docs point at the table.
5. **GATE (human):** adopt the pipeline in a second repository with
   `docs/ADOPTING.md` — its own copy of the files, its own `setup-repo`,
   schedule and launcher; the pipeline is per repository, and `gh-reviewer`
   acts only on the checkout it sits in. Let it run unattended for a week, and
   write a verdict here: what reached *Needs you* that should not have, and
   what went wrong silently.
6. Fix what the verdict in step 5 names, one issue per finding, each with its
   lesson in `docs/LESSONS.md`.

## Decided

- This repository's tick runs on the owner's full gh login, not a
  fine-grained agents' token: the token needs a second OS user, which the
  owner declined (2026-10-03). `require_tick_environment` stays false here;
  the template's default stays true. Not to be reopened by an issue.

## Open questions

- Should commands an agent reports as refused reach the status issue, through
  a new input path into the script? Declined for now (#81 option c); revisit
  if the week in step 5 shows refusals going unseen.
- A verb interface for `gh-reviewer` (#63 option C) was declined; the
  argument-checking wrapper stays unless step 5 shows it failing.
