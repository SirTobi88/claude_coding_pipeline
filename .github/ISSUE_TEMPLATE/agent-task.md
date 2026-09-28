---
name: Agent task
about: A single seam, sized for one coding agent and one branch
title: "[area] "
labels: agent-task
assignees: ''
---

<!--
Read CONTRIBUTING-agents.md before filing. Every section below is required;
issue-lint checks them and labels the issue status:ready, status:blocked or
status:needs-spec. Delete the HTML comments as you fill this in.
-->

## Goal

<!-- One sentence, in the imperative. If it needs two sentences joined by
"and", it is two issues. -->

## Why

<!-- Two or three sentences: what this unblocks, and what breaks if it is done
wrong. The agent uses this to make judgement calls the issue does not cover. -->

## Context

<!-- Specific anchors, not "read the docs". Section headings and symbol names,
not line numbers -- line numbers rot on the next merge. -->

| What | Where |
|---|---|
| Design decision | `docs/____.md` § ____ |
| Existing code to match | `src/____` `function_name()` |

Repo-wide rules that always apply: `CLAUDE.md`, `CONTRIBUTING-agents.md`.

## Interface

<!-- THE MOST IMPORTANT SECTION. Paste the actual signatures, data shapes and
constants the agent must conform to -- verbatim, not by reference. If the seam
does not exist yet, file an interface issue (stubs + failing test) first and
list it under Blocked by. "Design a sensible API" is not an interface. -->

```
# Pre-existing, do not change:

# To be implemented by this issue:
```

## Files in scope

<!-- Allowlist. The agent may create or modify ONLY these; everything else is
read-only. One path per list item, leading token only -- prose after it is not
read as a path. A directory entry ends with `/`. -->

- [ ] `path/to/file` (modify)
- [ ] `path/to/new_file` (create)
- [ ] `tests/path/to/test_file` (create)

## Non-goals

<!-- What NOT to do. Name the adjacent thing the agent will be tempted to fix. -->

- Do not
- Do not

## Definition of done

<!-- Every line must be checkable by running something. "Works correctly" is
not a definition of done. If a line cannot be a command, the harness it needs
does not exist yet -- file that first. -->

- [ ] The test command passes, including new test `____`
- [ ] <!-- a named assertion, e.g. "output is byte-identical to tests/golden/x.txt" -->
- [ ] No file outside **Files in scope** is modified

## Blocked by

<!-- Issue numbers (#12), or "nothing". An open issue here keeps this one
status:blocked until it closes. -->

## Size

- [ ] Small — one file, one function
- [ ] Standard — one new unit or one subsystem seam
- [ ] Too big — split before filing
