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
| `gh`, authenticated as the owner | every issue, PR and label operation | `gh auth status` |
| `jq` | the allowlist guard reads its hook payload with it | `jq --version` |
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
gh auth login
```

- `python3` is often the Microsoft Store alias, which prints an install prompt
  instead of running. `.claude/bin/pipeline` tests each candidate and uses the
  one that starts; call the shim, not `python3`.
- Claude Code's Bash tool runs Git Bash. `.gitattributes` pins scripts to LF,
  because `core.autocrlf=true` would check them out with CRLF, which bash
  cannot run.
- Claude Code hands the allowlist guard Windows paths (`E:\...`); the guard
  converts them with `cygpath`.
- Never pipe a PowerShell test runner through `2>&1`: PowerShell 5.1 turns
  native stderr into errors and reports a passing run as failed.

### macOS and Linux

`brew install gh jq` (or the distribution's packages), then `gh auth login`.

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

---

## Machine-wide shared state

Anything a test writes to a fixed per-user location (an app-data directory, a
fixed temp filename) is shared between every worktree and every concurrent run
on the machine. Give test scratch paths a process-unique component, and consider
a source-scan test that fails when a fixed path reappears.
