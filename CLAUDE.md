# <Project name> — Project Guidelines

<!-- EDIT: one or two sentences on what the project is, its stack, its targets.
Everything in this file is read by every agent at the start of every job, so
keep it to what binds: pointers, rules, decisions. Detail belongs in the docs it
points at. -->

---

## Rule zero: the design docs are canonical

**Before implementing or changing a system, read its design doc.** If a change
would contradict a documented decision, **say so and ask** rather than silently
diverging. If a decision genuinely changes, **update the doc in the same
change** — docs going stale is how a project loses its coherence.

### Document map

| Area | Doc |
|---|---|
| Roadmap — what comes next, and the human gates | `docs/ROADMAP.md` |
| How work is cut up and judged | `CONTRIBUTING-agents.md` |
| How work moves: agents, labels, review, merge, setup | `docs/Pipeline.md` |
| Test command, worktrees, platform hazards | `docs/AgentEnvironment.md` |
| Why each rule exists — read before changing or removing one | `docs/LESSONS.md` |
| Bringing the pipeline into a project, and upgrading it | `docs/ADOPTING.md` |
| *(your design docs)* | |

---

## Rule one: work comes from an issue

Code changes are cut into **agent-sized issues** filed from
`.github/ISSUE_TEMPLATE/agent-task.md`. The full contract is
`CONTRIBUTING-agents.md` — read it before writing code.

Work moves through an autonomous pipeline — a planner files issues,
implementers work them, a reviewer bot approves, GitHub merges — described in
`docs/Pipeline.md`. **GitHub labels are the state** (`status:ready`,
`status:in-progress`, …); read them rather than inferring state from branches or
worktrees.

### Working an issue — the issue is your scope

- **Context** names the doc sections that apply. Read exactly those.
- **Interface** is signatures to conform to, quoted verbatim.
- **Files in scope** is an allowlist. Read anything; write only what is listed.
- **Non-goals** names the adjacent thing you will be tempted to fix. Leave it.
- **Definition of done** is commands that exit 0. Do not claim an item passed if
  you did not watch it pass.

**Four things are escalated, never solved:** the work needs a file outside the
allowlist; the issue contradicts a design doc or a locked decision; the quoted
interface does not match the repo; a done-condition has no harness.

---

## Locked decisions

<!-- EDIT: the decisions that are easy to violate by accident and must not be
contradicted without raising it first. Architecture rules, product rules,
anything an agent might "helpfully" change. Keep each to one or two lines. -->

- *(decision)*

---

## Working conventions

- Branches are `agent/<issue-number>-<slug>`; commits are conventional
  (`feat(area):`, `fix(area):`, `docs(area):`, `test(area):`, `chore(area):`).
  One issue per PR, described per `.github/pull_request_template.md`.
- **Nobody judges their own work, and nobody pushes to the default branch**:
  branch protection requires the required checks plus an approval from the
  reviewer bot, and GitHub merges on that. The one exception is deliberate: the
  reviewer fixes mechanical defects inside the allowlist and then approves
  them (`docs/Pipeline.md` § Merging says why "require approval of the most
  recent push" is off).
- **Parallel work happens in worktrees, never in one checkout** — see
  `docs/AgentEnvironment.md`.
- Generated files say so in a header. Never edit one; edit its generator.
