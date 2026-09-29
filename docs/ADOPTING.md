# Adopting the pipeline in a project

Two ways in: start a **new** repository from this template, or copy the
pipeline into an **existing** one. Either way, the setup at the end is the same.

---

## A. New project

On GitHub: **Use this template → Create a new repository**. Then clone it and
continue at *C. Fit it to the project*.

## B. Existing project

Copy these into the project root, keeping the paths:

| Path | What it is |
|---|---|
| `.claude/agents/` | planner, implementer, reviewer, triage |
| `.claude/commands/pipeline-tick.md` | the dispatcher |
| `.claude/skills/` | review, issue creation, interactive helpers |
| `.claude/hooks/` | the allowlist guard, its parser and tests |
| `.claude/bin/` | `pipeline` and `gh-reviewer` shims |
| `.claude/pipeline/` | the decision logic, its config and tests |
| `.claude/settings.json` | hook registration and pipeline permissions — **merge** with an existing one |
| `.github/workflows/pr-contract.yml`, `issue-lint.yml` | as is |
| `.github/workflows/ci.yml` | **merge**: your build/test job must be named `ci`, plus the `tooling` job |
| `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md` | as is |
| `.gitattributes` | **merge** the script lines |
| `CONTRIBUTING-agents.md` | the contract — fill in *Project rules* |
| `docs/Pipeline.md`, `docs/AgentEnvironment.md`, `docs/ROADMAP.md` | edit the last two |
| `CLAUDE.md` | **merge** the pipeline sections into yours |

```bash
# from a clone of this repository, into ../my-project:
cp -r .claude .github CONTRIBUTING-agents.md docs ../my-project/
# then merge CLAUDE.md, .gitattributes, settings.json and ci.yml by hand
```

Add `.claude/worktrees/`, `.claude/tmp/` and `.claude/settings.local.json` to
`.gitignore`.

---

## C. Fit it to the project

Every project-specific choice is in one of these places. Nothing else needs
editing.

| Where | What to set |
|---|---|
| `.claude/pipeline/config.json` | `project`, `default_branch`, `test_command`, `required_checks`, `roadmap_docs`, `reviewer_token_file`, `agent_login` (**required when the repository belongs to an organisation**: the login whose token the agents use; on a personal repository it defaults to the owner), `control_paths` (add any file of yours that decides what agents may do), `limits` |
| `.claude/settings.json` | if your `test_command` differs, replace the `Bash(./run_tests.sh *)` rule with it, and the matching entry in `USED` in `.claude/pipeline/tests/test_settings.py` |
| `run_tests.sh` (or your `test_command`) | runs the whole suite; finds its own tools; first line says what ran, last line is a summary; exit code is the result |
| `.github/workflows/ci.yml` → job `ci` | install your toolchain (pinned versions), run the test command |
| `CONTRIBUTING-agents.md` § *Project rules* | architecture rules, never-hand-edit files, one-at-a-time files, and what enforces each |
| `.claude/hooks/lib/issue_scope.sh` → `SCOPE_COMPANION_SUFFIXES` | generated files that ship with their source (e.g. `.uid .import` for Godot) |
| `CLAUDE.md` | what the project is, the document map, locked decisions |
| `docs/AgentEnvironment.md` | the machine: tools, how to read a test run, platform hazards |
| `docs/ROADMAP.md` | ordered steps and explicit human gates — what the planner reads |
| `.gitattributes` | LF for generated files your toolchain writes with LF |

**The one rule for the test command:** agents and CI must run the *same*
command. That is what makes "CI green at this commit" count as a reproduction of
the definition of done.

---

## D. Setup (once per repository)

Follow `docs/Pipeline.md` § *Setup*:

1. Tools on the pipeline machine: `gh`, `jq`, Python ≥ 3.9, bash, your
   toolchain. Fully restart the Claude app afterwards.
2. The agents' token: fine-grained, this repository only, no Administration and
   no Workflows permission, as the credential the tick runs with.
3. A reviewer bot account with a classic `repo` token at `reviewer_token_file`.
4. `.claude/bin/pipeline setup-repo --dry-run`, then without `--dry-run`, with
   your own login. Requires branch protection on your plan (public repo, or
   GitHub Pro/Team).
5. One tick by hand in `dontAsk` mode: `/pipeline-tick`. Then schedule it,
   still in `dontAsk`.

Commit the adoption itself **before** step 4 — afterwards the default branch only
accepts reviewed pull requests.

### Upgrading a repository that already runs the pipeline

Some upgrades change how a required check is triggered — the move of
`allowlist` and `contract` from `pull_request` to `pull_request_target` is one.
The pull request that makes such a change gets neither check: `pull_request`
now finds no job in its own copy of the workflow, and `pull_request_target`
runs the default branch's copy, which does not listen to that event yet. With
the checks required, it can never merge. For that one pull request, drop the
two checks from the protection (with your own login):

```bash
gh api -X PATCH repos/{owner}/{repo}/branches/main/protection/required_status_checks \
  -F strict=false -f 'contexts[]=ci' -f 'contexts[]=tooling'
```

Merge it once `ci` and `tooling` are green and the bot has approved, then run
`.claude/bin/pipeline setup-repo` again, which restores all four.

---

## E. First run checklist

`.claude/bin/pipeline doctor` checks most of this in one go; it exits 0 when
nothing fails.

- [ ] `./run_tests.sh` exits 0 locally and in CI (`ci` job).
- [ ] `tooling` job is green (hook and pipeline tests).
- [ ] `.claude/bin/gh-reviewer api user --jq .login` prints the bot.
- [ ] In the tick's environment, `gh auth status` names a fine-grained
      (`github_pat_…`) token.
- [ ] `.claude/bin/pipeline run` (dry run) prints JSON with no `setup_problems`.
- [ ] Open a test issue labelled `idea` ("add a CHANGELOG.md") and run
      `/pipeline-tick` a few times: planner → issue → lint `status:ready` →
      implementer → PR → CI → reviewer → auto-merge.
- [ ] Label any open issue `pipeline:pause` and confirm the tick reports paused.
