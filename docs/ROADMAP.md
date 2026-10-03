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

1. **Catch up the changelog.** `CHANGELOG.md` stops at Wave 9 (2026-10-01).
   Add one entry per merged wave since, read from each merge commit's message.
2. **Reviewer reports without detours.** A reviewer whose session is bound to
   another worktree cannot write `.pipeline-tmp/` at the repository root (the
   allowlist guard refuses the cross-checkout write), and every review since
   #59 worked around it. Make the review skill write its report into its own
   review worktree's `.pipeline-tmp/` and submit from there, now that
   `gh-reviewer` resolves the body against the caller's directory.
3. **Retire `max_comment_only_reviews`.** Since #81 one comment-only bot review
   hands the PR to the owner, so the limit can no longer fire: remove it from
   `Limits`, `config.json` and the limits table in `docs/Pipeline.md`.
4. **One source for the machine's tool list.** gh, git, jq, bash, awk, tr, sed
   and grep are listed in `docs/AgentEnvironment.md`, `docs/ADOPTING.md`,
   `docs/Pipeline.md`, `README.md`, `doctor_report()`, `preflight()` and
   `bash_guard.sh`. Name the list once (`GUARD_TOOLS` and the doctor tools in
   `pipeline.py`, the table in `docs/AgentEnvironment.md`) and make the other
   docs point at it.
5. **GATE (human):** adopt the pipeline in a second repository with
   `docs/ADOPTING.md` — its own copy of the files, its own `setup-repo`,
   schedule and launcher; the pipeline is per repository, and `gh-reviewer`
   acts only on the checkout it sits in. Let it run unattended for a week, and
   write a verdict here: what reached *Needs you* that should not have, and
   what went wrong silently.
6. Fix what the verdict in step 5 names, one issue per finding, each with its
   lesson in `docs/LESSONS.md`.

Steps 2–4 touch control paths (`.claude/`, `docs/Pipeline.md`): the planner
files them, and the owner works them under `human-decision`.

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
