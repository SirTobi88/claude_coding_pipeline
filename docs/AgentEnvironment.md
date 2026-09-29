# The agent environment — how to actually run things here

<!-- EDIT for your project. This file owns the machine-facing facts: which
command runs the tests, how to read the result, how a worktree is set up, and
which hazards are real on which platform. Nothing else in the repo should
hardcode a machine path, a shell, or a binary name -- those live here, and
everywhere else links to this file. -->

This file owns the **machine-facing** facts. When they lived in several files at
once, a change of machine updated none of them and every agent was handed a
command that could not run. So: **nothing else hardcodes a machine path, a
shell, or a binary name.**

---

## The machine that runs the pipeline

| Tool | Why | Check |
|---|---|---|
| `gh`, authenticated with the agents' token (`docs/Pipeline.md` § Setup, step 2) | every issue, PR and label operation | `gh auth status` names a `github_pat_…` token |
| `jq` | both hooks read their payload with it | `jq --version` |
| Python ≥ 3.9 | `.claude/bin/pipeline` | `.claude/bin/pipeline --help` |
| bash | the hooks, the shims, the test entry point | on Windows, Git for Windows' Git Bash |
| the reviewer token | `.claude/bin/gh-reviewer` | `.claude/bin/gh-reviewer api user --jq .login` |
| *(your toolchain)* | the test command | *(its version command)* |

**Without `gh` and `jq`, the allowlist guard fails open** — it lets every write
through by design. Check both before scheduling the tick.

After installing a tool, **fully quit the Claude desktop app** (tray icon →
Quit) and reopen it. Closing the window keeps the app running with its old
`PATH`, and every agent it spawns inherits that.

### Windows

```powershell
winget install --id GitHub.cli
winget install --id jqlang.jq
# as the pipeline's OS user, with the agents' token (docs/Pipeline.md § Setup, step 2):
gh auth login --with-token < agents-token.txt
gh auth setup-git
```

Your own `gh auth login` belongs in your own account's terminal, for
`setup-repo`, and never on the pipeline's OS user.

- `python3` is often the Microsoft Store alias, which prints an install prompt
  instead of running. `.claude/bin/pipeline` tests each candidate and uses the
  one that starts; call the shim, not `python3`.
- Claude Code's Bash tool runs Git Bash, which tolerates CRLF scripts. Linux
  CI, macOS and WSL do not (`$'\r': command not found`). `.gitattributes` pins
  scripts to LF, so a checkout with `core.autocrlf=true` stays runnable
  everywhere; a file committed before it existed keeps CRLF until renormalised
  (`docs/ADOPTING.md` § B).
- Claude Code hands the allowlist guard Windows paths (`E:\...`); the guard
  converts them with `cygpath`.
- Never pipe a PowerShell test runner through `2>&1`: PowerShell 5.1 turns
  native stderr into errors and reports a passing run as failed.

### macOS and Linux

`brew install gh jq` (or the distribution's packages), then, as the pipeline's
OS user, `gh auth login --with-token < agents-token.txt` and
`gh auth setup-git`.

---

## Permissions

The scheduled tick runs in `dontAsk` mode (`docs/Pipeline.md` § Setup, step
5): a tool call that `.claude/settings.json` does not allow is refused, not
asked about. What that allows, and why:

- **Commands** — the ones the agent prompts use, and no more; `deny` rules
  refuse force pushes, git options that run commands or write files, and
  writes through `gh api`. `.claude/pipeline/tests/test_settings.py` checks a
  hand-kept list of the prompts' commands against the rules: when a prompt
  starts using a new command, add it to that list and to the rules together.
  Rules match text, so they limit the usual commands; they are not a boundary
  (`docs/Pipeline.md` § *What binds an agent*).
- **Edits** — inside `.claude/worktrees/` (every implementer and reviewer works
  in one) and `.claude/tmp/`, nowhere else.
- **Scratch files** — PR bodies, issue bodies and review reports go to
  `.claude/tmp/` (gitignored), named after their issue or PR
  (`.claude/tmp/pr-12.md`) so two agents never share one.

`.claude/settings.local.json` is machine-local and in no pull request, yet it
can widen those permissions or turn the hooks off for every tick. The tick
reports it when it exists; keep the pipeline's machine free of one, or know
exactly what it holds.

---

## Running the tests

```bash
./run_tests.sh
```

<!-- EDIT: describe your runner -- how it finds its tools, what its first and
last lines look like, which output is benign noise. -->

Exit 0 means every test passed. Judge a run by its exit code and its summary
line, never by grepping for "error".

---

## Worktrees

Every agent works in its own git worktree under `.claude/worktrees/`
(gitignored). A working tree holds one branch, and build caches race between
concurrent processes, so two agents in one directory collide regardless of how
disjoint their allowlists are. **A result verified in a tree that also holds
another change proves nothing.**

Gitignored tool directories (a vendored binary under `tools/`, say) do **not**
exist inside a worktree. A test runner that needs one should also look in the
main checkout: `git rev-parse --path-format=absolute --git-common-dir` names
it.

If git inside a worktree aborts with `detected dubious ownership` (some
filesystems record no ownership), register every worktree at once — derived,
never a pasted absolute path:

```bash
git config --global --add safe.directory "$(git rev-parse --show-toplevel)/.claude/worktrees/*"
```

Cleanup: `git worktree remove --force .claude/worktrees/<name>`. `--force` is
expected: a tree that ran tests is never clean.

Who makes which tree:

- **Implementers** get one from Claude Code (`isolation: worktree`); a tree
  with commits is kept after the agent ends. The branch it holds is why a fix
  pass checks out a differently named local branch (`agent/<N>-fix-<P>`).
- **The reviewer** makes a detached one, `.claude/worktrees/review-<N>`, and
  removes it when done; a leftover is removed at the start of the next review.
- The allowlist guard refuses a write from one tree into another, the main
  checkout included.

## The pipeline's files outside the repository

| What | Where | Written by |
|---|---|---|
| Tick log | `<git dir>/pipeline/ticks.jsonl` | every `pipeline run --apply` |
| Kill file | `<git dir>/pipeline/pause` | you (`touch`); the tick pauses while it exists |
| Allowlist cache | `$TMPDIR/pipeline-scope-*.list`, five minutes | the guards |
| The bot's token | `reviewer_token_file` in `.claude/pipeline/config.json` | you, once |

`<git dir>` is the checkout's common git directory, shared by every worktree:
`git rev-parse --path-format=absolute --git-common-dir`.

---

## Machine-wide shared state

Anything a test writes to a fixed per-user location (an app-data directory, a
fixed temp filename) is shared between every worktree and every concurrent run
on the machine. Give test scratch paths a process-unique component, and consider
a source-scan test that fails when a fixed path reappears.
