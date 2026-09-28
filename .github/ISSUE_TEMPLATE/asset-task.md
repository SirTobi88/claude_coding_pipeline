---
name: Asset task
about: A deliverable a human or specialist produces (art, audio, copy, data) -- the seam the consuming code does not cover
title: "[asset] "
labels: asset
assignees: ''
---

<!--
This is NOT the agent-task template with different words:

  1. There is no Interface section. An asset seam is pinned by FILE PATHS and
     a format contract, because the consuming code reads those paths and a
     rename means the swap silently misses.
  2. "Definition of done" cannot be only commands. Some of what makes an asset
     right is a person looking at it. Keep mechanical and human checks apart,
     and never describe a human check as a mechanical one.

An asset issue is NEVER auto-assigned: the pipeline skips the `asset` label.
"Who can work this" is where that decision is made explicit.
-->

## Goal

<!-- One sentence. Which deliverable, replacing what, at which paths. -->

## Why

<!-- What consumes it, and what is there today (a placeholder, nothing, an
earlier pass). If this is a quality upgrade, say what specifically is wrong. -->

## Parent issue

<!-- The code issue this is the asset half of, or "none". Code normally lands
against a placeholder, so this does NOT block the parent unless the parent's own
Blocked by says so. -->

## Deliverables

| Path | What | Format / size |
|---|---|---|
| `____` | source | ____ |
| `____` | export | ____ |

**Format contract** — the constraints that are not negotiable:

- <!-- e.g. transparent centre, margins stated in the PR -->
- <!-- e.g. readable at 16 px and in greyscale -->

## What "good enough" means

<!-- Required. Mechanical checks pass for a placeholder too, so say concretely
what distinguishes finished work from a stand-in. -->

## Who can work this

- [ ] **Procedural / scripted** — a generator committed alongside its output.
- [ ] **Specialist agent** — say which capability it needs.
- [ ] **Human** — anything where the judgement *is* the deliverable.

## Files in scope

- [ ] `____/` (create)

## Non-goals

- Do not change the consuming code.
- Do not rename or re-path anything in Deliverables — the binding is by path.

## Definition of done

**Mechanical** — commands that exit 0:

- [ ] The test command passes
- [ ] No file outside **Files in scope** is modified

**Human** — shown in the PR:

- [ ] <!-- e.g. a screenshot at the target resolution -->

## Blocked by

## Size

- [ ] Small
- [ ] Standard
- [ ] Too big — split before filing
