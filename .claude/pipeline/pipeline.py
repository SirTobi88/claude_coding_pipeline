#!/usr/bin/env python3
"""The coding pipeline's deterministic half.

Agents make judgement calls; this file makes every decision that does not need
one: which issue is ready, which pull request needs a review, a fix or nothing,
and which labels say so. docs/Pipeline.md is the contract; this is its
executable form. Everything project-specific lives in config.json beside it.

    pipeline.py run [--apply]           one tick: bookkeeping + the dispatch list (JSON)
    pipeline.py lint ISSUE [--apply]    readiness of one agent-task issue
    pipeline.py relint [--apply]        lint every open issue whose status lint owns
    pipeline.py claim issue N [--interactive] | pr N [--round K]
    pipeline.py release issue|pr N [--round-label L] [--hold]   drop a claim when an agent finishes
    pipeline.py set-status ISSUE STATUS set one status label (or "none"), dropping the rest
    pipeline.py check-pr-body           PR description shape, body on stdin (CI)
    pipeline.py setup-repo [--dry-run]  labels, repo settings, branch protection, bot access
    pipeline.py checks PR [--wait]      the required checks at a PR's head (exit 0 green, 1 red, 8 pending)
    pipeline.py doctor                  check the whole setup, one line per check
    pipeline.py stats [--days N]        what the pipeline did: cycle time, fix rounds, escalations
    pipeline.py config KEY              print one value from config.json (for the shims)

Everything that decides is a pure function over plain dicts and is tested
offline in tests/. Everything that talks to GitHub goes through `Gh`.
Standard library only -- no jq, no third-party packages.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SCOPE_LIB = REPO_ROOT / ".claude" / "hooks" / "lib" / "issue_scope.sh"

# --- configuration -----------------------------------------------------------

DEFAULT_CONFIG = {
    "project": "my-project",
    "default_branch": "main",
    "test_command": "./run_tests.sh",
    "required_checks": ["ci", "tooling", "allowlist", "contract"],
    "roadmap_docs": ["docs/ROADMAP.md"],
    "reviewer_token_file": "~/.config/claude-pipeline/my-project/reviewer-token",
    # The account the agents push and open pull requests as. Empty means the
    # repository owner, which is who the agents run as today.
    "agent_login": "",
    # Files that decide what the agents may do. An issue whose Files in scope
    # touches one is the owner's work, never dispatched (docs/Pipeline.md
    # § What stays with the owner), and the `allowlist` check refuses a change
    # to one unless its issue carries `human-decision`.
    "control_paths": [".github/", ".claude/", "run_tests.sh", "CLAUDE.md",
                      "CONTRIBUTING-agents.md", "docs/Pipeline.md"],
    # Without branch protection nothing enforces the gate, so the tick holds
    # all work while protection is missing or has drifted. Turn off only to try
    # the pipeline out on a repository that cannot have protection.
    "require_protection": True,
    "limits": {
        "max_parallel_implement": 3,
        "max_parallel_review": 2,
        "max_parallel_fix": 3,
        "max_parallel_triage": 2,
        "max_dispatch_per_tick": 8,
        "max_agent_runs_per_day": 50,
        "max_fix_rounds": 2,
        "max_conflict_rounds": 2,
        "max_review_attempts": 2,
        "max_comment_only_reviews": 2,
        "stale_in_progress_hours": 6,
        "stale_working_hours": 4,
        "stale_waiting_hours": 12,
    },
}


CONFIG_WARNINGS: list[str] = []


def load_config(path: Path = HERE / "config.json", warnings: list[str] | None = None) -> dict:
    """config.json over the defaults; a missing file means all defaults.

    A file that is not JSON, or a limit that is not a number, stops every
    command with the file and the place -- not a traceback from an import.
    A key nobody reads (a typo: `max_parallel_reviews`) is a warning, which
    `pipeline run` reports: silently keeping the default is how a limit that
    was meant to change does not.
    """
    warnings = CONFIG_WARNINGS if warnings is None else warnings
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except json.JSONDecodeError as e:
        raise SystemExit(f"pipeline: {path} is not valid JSON: {e.msg} at line {e.lineno}, "
                         f"column {e.colno}")
    for key in sorted(set(user) - set(DEFAULT_CONFIG)):
        warnings.append(f"{path.name}: unknown key `{key}` is ignored")
    for key in sorted(set(user.get("limits", {})) - set(DEFAULT_CONFIG["limits"])):
        warnings.append(f"{path.name}: unknown limit `{key}` is ignored")
    limits = {**cfg["limits"], **{k: v for k, v in user.get("limits", {}).items()
                                  if k in DEFAULT_CONFIG["limits"]}}
    for key, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SystemExit(f"pipeline: {path}: limit `{key}` must be a number, not {value!r}")
    cfg.update(user)
    cfg["limits"] = limits
    return cfg


CONFIG = load_config()

# --- the label vocabulary ----------------------------------------------------

READY = "status:ready"
BLOCKED = "status:blocked"
NEEDS_SPEC = "status:needs-spec"
IN_PROGRESS = "status:in-progress"
IN_REVIEW = "status:in-review"
ESCALATED = "status:escalated"
NEEDS_HUMAN = "status:needs-human"
STATUSES = (READY, BLOCKED, NEEDS_SPEC, IN_PROGRESS, IN_REVIEW, ESCALATED, NEEDS_HUMAN)
# Exactly one status label is the invariant. When an issue carries two -- the
# owner added needs-human without removing ready, or two writers raced -- the
# one that stops the most wins, and the tick removes the other.
STATUS_PRECEDENCE = (NEEDS_HUMAN, ESCALATED, IN_REVIEW, IN_PROGRESS, NEEDS_SPEC, BLOCKED, READY)
# Lint may move an issue between these on its own. Every other status belongs
# to whoever set it, and lint never touches it.
LINT_OWNED = {None, READY, BLOCKED, NEEDS_SPEC}

AGENT_TASK = "agent-task"
ASSET = "asset"
HUMAN_DECISION = "human-decision"
IDEA = "idea"
PAUSE = "pipeline:pause"
IDLE = "pipeline:idle"
WORKING = "pipeline:working"
REVIEWING = "pipeline:reviewing"
HUMAN_HOLDS = "pipeline:human-holds"
PLANNING = "pipeline:planning"
MAIN_RED = "pipeline:main-red"
ATTEMPT = "attempt-1"
# The owner answered a question the pipeline asked (docs/Pipeline.md § What
# stays with the owner): the tick resumes the item and removes the label.
ANSWERED = "human:answered"
STATUS_ISSUE = "pipeline:status"
SPEC_DEFECT = "spec-defect"
TRIAGED = "triaged"
FIX_ROUND = "fix-round-"
CONFLICT_ROUND = "conflict-round-"
# A commit status on a pull request's head, one per review dispatched there.
# Being a status, it belongs to that commit: a new push starts the count again.
REVIEW_STATUS = "pipeline/review"
# Posted when the owner answers on a pull request: the comment-only reviews and
# review attempts at that head up to the answer, so only later ones count.
ANSWERED_STATUS = "pipeline/answered"

LABELS = {
    READY: ("0e8a16", "Lint passed and nothing blocks it: an implementer may take it"),
    BLOCKED: ("c5def5", "Lint passed, but Blocked by names an issue that is still open"),
    NEEDS_SPEC: ("fbca04", "Lint failed: a required section is missing or placeholder"),
    IN_PROGRESS: ("1d76db", "Claimed by the pipeline; an implementer is working it"),
    IN_REVIEW: ("5319e7", "A pull request for this issue is open"),
    ESCALATED: ("d93f0b", "The implementer stopped on one of the four escalations"),
    NEEDS_HUMAN: ("b60205", "A human decision is required; the pipeline skips this"),
    IDEA: ("bfdadc", "Raw input for the planner agent: turned into agent-task issues"),
    PAUSE: ("000000", "On any open issue: every pipeline tick does nothing"),
    IDLE: ("ededed", "The planner found nothing it may plan without a human"),
    WORKING: ("c2e0c6", "An agent holds this right now; the next tick leaves it alone"),
    REVIEWING: ("c2e0c6", "The agent holding this pull request is the reviewer"),
    HUMAN_HOLDS: ("bfd4f2", "The owner is working this by hand; the pipeline neither resets nor fixes it"),
    PLANNING: ("ededed", "The roadmap planner is running; closed when it finishes"),
    MAIN_RED: ("b60205", "The default branch is red; fix passes wait until it is green"),
    ATTEMPT: ("fbca04", "An implementer ended once without a PR or an escalation"),
    ANSWERED: ("0e8a16", "The owner answered: the next tick resumes this and removes the label"),
    STATUS_ISSUE: ("ededed", "The pipeline's heartbeat: every tick rewrites this issue's body"),
    SPEC_DEFECT: ("e99695", "Review handed this back because the issue was underspecified"),
    TRIAGED: ("d4c5f9", "Triage has answered this once; a second escalation goes to a human"),
    AGENT_TASK: ("0052cc", "A single seam, sized for one coding agent and one branch"),
    ASSET: ("fef2c0", "Art or audio deliverable; never auto-assigned to an agent"),
    HUMAN_DECISION: ("b60205", "Needs a human judgement call; never handed to an agent"),
}

# --- limits ------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    """config.json `limits`. decide() takes them from the snapshot, so tests
    need not depend on whatever a project set in its config."""
    max_parallel_implement: int
    max_parallel_review: int
    max_parallel_fix: int
    max_parallel_triage: int
    max_dispatch_per_tick: int
    max_agent_runs_per_day: int
    max_fix_rounds: int
    max_conflict_rounds: int
    max_review_attempts: int
    max_comment_only_reviews: int
    stale_in_progress: timedelta
    stale_working: timedelta
    stale_waiting: timedelta

    @classmethod
    def from_config(cls, raw: dict) -> "Limits":
        return cls(**{k: int(raw[k]) for k in (
            "max_parallel_implement", "max_parallel_review", "max_parallel_fix",
            "max_parallel_triage", "max_dispatch_per_tick", "max_agent_runs_per_day", "max_fix_rounds",
            "max_conflict_rounds", "max_review_attempts", "max_comment_only_reviews")},
            stale_in_progress=timedelta(hours=float(raw["stale_in_progress_hours"])),
            stale_working=timedelta(hours=float(raw["stale_working_hours"])),
            stale_waiting=timedelta(hours=float(raw["stale_waiting_hours"])))


LIMITS = Limits.from_config(CONFIG["limits"])
MAX_FIX_ROUNDS = LIMITS.max_fix_rounds
MAX_PARALLEL_IMPLEMENT = LIMITS.max_parallel_implement
MAX_PARALLEL_REVIEW = LIMITS.max_parallel_review
STALE_IN_PROGRESS = LIMITS.stale_in_progress
STALE_WORKING = LIMITS.stale_working

# Round labels exist for every round the limits allow: a label the claim adds
# must exist, or the claim fails every tick and the pull request never moves.
for _k in range(1, max(LIMITS.max_fix_rounds, 1) + 1):
    LABELS[f"{FIX_ROUND}{_k}"] = ("f9d0c4", f"Fix pass {_k} has been dispatched for this pull request")
for _k in range(1, max(LIMITS.max_conflict_rounds, 1) + 1):
    LABELS[f"{CONFLICT_ROUND}{_k}"] = ("f9d0c4", f"Conflict pass {_k} has been dispatched for this pull request")

# Required checks, pinned to the GitHub Actions app so that a commit status
# posted by hand under the same name cannot satisfy them. 15368 is GitHub
# Actions' app id on github.com.
REQUIRED_CHECKS = tuple(CONFIG["required_checks"])
DEFAULT_BRANCH = CONFIG["default_branch"]
GITHUB_ACTIONS_APP_ID = 15368
CONTROL_PATHS = tuple(CONFIG["control_paths"])

TEMPLATE_SECTIONS = (
    "Goal", "Why", "Context", "Interface", "Files in scope",
    "Non-goals", "Definition of done", "Blocked by",
)
PR_SECTIONS = ("What changed", "Definition of done", "Not verified", "Left alone")

AGENT_BRANCH = re.compile(r"^agent/(\d+)-")


# --- small helpers -------------------------------------------------------------

def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def labels_of(item: dict) -> set[str]:
    return {l["name"] if isinstance(l, dict) else l for l in item.get("labels", [])}


def status_of(labels: set[str]) -> str | None:
    """The issue's status: with more than one label, the one that stops the most."""
    for s in STATUS_PRECEDENCE:
        if s in labels:
            return s
    return None


def fix_rounds(labels: set[str], prefix: str = FIX_ROUND) -> int:
    rounds = [int(l[len(prefix):]) for l in labels
              if l.startswith(prefix) and l[len(prefix):].isdigit()]
    return max(rounds, default=0)


def branch_issue(branch: str) -> int | None:
    m = AGENT_BRANCH.match(branch or "")
    return int(m.group(1)) if m else None


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def split_sections(body: str) -> dict[str, str]:
    """Level-2 headings to their text, keyed lower-case.

    The same reading as issue_scope.sh: `###` is content, a `## ` line inside a
    fenced code block is content (an Interface that pastes Markdown or a shell
    comment is not cut in two), and of two sections with one name the first
    counts.
    """
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    fence = False

    def close() -> None:
        if current is not None and current not in sections:
            sections[current] = "\n".join(buf).strip()

    for line in strip_comments(body or "").splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fence = not fence
        m = None if fence else re.match(r"^##\s+(?!#)(.+?)\s*$", line)
        if m:
            close()
            current = m.group(1).strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    close()
    return sections


def list_items(text: str) -> list[str]:
    """The top-level list items of a section, as issue_scope.sh reads them:
    at the list's least indentation, marker and checkbox removed."""
    found = []
    fence = False
    for line in (text or "").replace("\t", "    ").splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fence = not fence
            continue
        m = None if fence else re.match(r"^( *)([-*+]|\d+[.)])[ \t]+(.*)$", line)
        if m:
            found.append((len(m.group(1)), re.sub(r"^\[[ xX]\][ \t]+", "", m.group(3))))
    least = min((ind for ind, _ in found), default=0)
    return [t for ind, t in found if ind == least]


def find_section(sections: dict[str, str], name: str) -> str | None:
    """Exact heading first, then a heading that starts with the name."""
    key = name.lower()
    if key in sections:
        return sections[key]
    for k, v in sections.items():
        if k.startswith(key):
            return v
    return None


def meaningful(text: str | None) -> bool:
    if text is None:
        return False
    if "____" in text or "path/to/" in text:
        return False
    stripped = re.sub(r"^\s*[-*]\s*(\[[ xX]\])?", "", text, flags=re.M)
    stripped = re.sub(r"^\s*(Do not|Do not\.)\s*$", "", stripped, flags=re.M)
    return len(re.findall(r"[A-Za-z0-9]", stripped)) >= 3


def interface_has_code(text: str | None) -> bool:
    if not text:
        return False
    for block in re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S):
        for line in block.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("//"):
                return True
    return False


# --- allowlists (one parser: .claude/hooks/lib/issue_scope.sh) ------------------

def bash_path(env=os.environ, which=shutil.which, exists=lambda p: Path(p).exists()) -> str | None:
    """A bash that sees this checkout's paths.

    On Windows, PATH can put WSL's System32\\bash.exe ahead of Git Bash, and WSL
    cannot read `E:/...`. So: $PIPELINE_BASH if set, then the bash of the Git
    whose `git` is on PATH (wherever it is installed), then the usual install
    locations, then PATH -- never one under the Windows directory.
    """
    if env.get("PIPELINE_BASH"):
        return env["PIPELINE_BASH"]
    if os.name != "nt":
        return which("bash")
    candidates = []
    git = which("git")
    if git:
        root = Path(git).resolve().parent
        # <root>\cmd\git.exe, <root>\bin\git.exe, <root>\mingw64\bin\git.exe
        for up in (root.parent, root.parent.parent):
            candidates += [up / "bin" / "bash.exe", up / "usr" / "bin" / "bash.exe"]
    for base in (env.get("ProgramFiles", r"C:\Program Files"),
                 os.path.join(env.get("LOCALAPPDATA", ""), "Programs")):
        candidates += [Path(base) / "Git" / "bin" / "bash.exe", Path(base) / "Git" / "usr" / "bin" / "bash.exe"]
    found = which("bash")
    if found:
        candidates.append(Path(found))
    windir = env.get("SystemRoot", r"C:\Windows").lower()
    for c in candidates:
        if str(c).lower().startswith(windir):
            continue
        if exists(str(c)):
            return str(c)
    return None


def parse_allowlist(body: str) -> list[str]:
    """Files in scope, as the edit-time guard and the CI job read them."""
    bash = bash_path()
    if not bash:
        raise RuntimeError("bash not found: needed to run .claude/hooks/lib/issue_scope.sh")
    lib = SCOPE_LIB.as_posix()
    proc = subprocess.run(
        [bash, "-c", '. "$1"; scope_parse_allowlist', "_", lib],
        input=body or "", capture_output=True, text=True, encoding="utf-8",
    )
    # A bash that cannot source the parser prints nothing, which would read as
    # "names no path": every issue linted needs-spec, and the overlap guard off.
    if proc.returncode != 0:
        raise RuntimeError(f"the allowlist parser failed under {bash} "
                           f"(exit {proc.returncode}): {proc.stderr.strip()[:200]}")
    return [p for p in proc.stdout.splitlines() if p.strip()]


def _is_dir_entry(entry: str) -> bool:
    return entry.endswith("/") or "." not in entry.rstrip("/").rsplit("/", 1)[-1]


def paths_overlap(a: str, b: str, companions: tuple[str, ...] | None = None) -> bool:
    """Could two allowlist entries name the same file? Conservative on globs.

    Wherever issue_scope.sh's `_scope_entry_covers` says an entry covers a
    path, this says the two overlap (a test holds the two together). A
    generated companion (`x.gd.uid`) is its source's file here too: two issues
    may not both be able to write it.
    """
    # Drop a leading `./` or `/` only. `lstrip("./")` stripped every leading
    # dot, so `.github/` and `github/` read as the same directory.
    a, b = (re.sub(r"^(\./)+", "", s.strip()).lstrip("/") for s in (a, b))
    for suffix in (companion_suffixes() if companions is None else companions):
        a, b = (x[: -len(suffix)] if suffix and x.endswith(suffix) else x for x in (a, b))
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        if any(c in x for c in "*?["):
            prefix = re.split(r"[*?\[]", x, maxsplit=1)[0]
            if y.startswith(prefix) or prefix.startswith(y.rstrip("/") + "/"):
                return True
        elif _is_dir_entry(x) and y.startswith(x.rstrip("/") + "/"):
            return True
    return False


_COMPANIONS: tuple[str, ...] | None = None


def companion_suffixes() -> tuple[str, ...]:
    """SCOPE_COMPANION_SUFFIXES from issue_scope.sh -- one setting, read where it lives."""
    global _COMPANIONS
    if _COMPANIONS is None:
        bash = bash_path()
        try:
            out = subprocess.run([bash, "-c", '. "$1"; printf %s "$SCOPE_COMPANION_SUFFIXES"', "_",
                                  SCOPE_LIB.as_posix()], capture_output=True, text=True,
                                 encoding="utf-8").stdout if bash else ""
        except OSError:
            out = ""
        _COMPANIONS = tuple(out.split())
    return _COMPANIONS


def scopes_overlap(xs: list[str], ys: list[str]) -> bool:
    return any(paths_overlap(x, y) for x in xs for y in ys)


def control_paths_touched(scope: list[str], control=CONTROL_PATHS) -> list[str]:
    """The control paths an allowlist could write to."""
    return sorted({c for c in control for s in scope if paths_overlap(c, s)})


# --- lint ------------------------------------------------------------------------

@dataclass
class LintResult:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers_open: list[int] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.problems:
            return NEEDS_SPEC
        if self.blockers_open:
            return BLOCKED
        return READY


def blocked_by_numbers(text: str | None, self_number: int) -> list[int]:
    if not text:
        return []
    return sorted({int(n) for n in re.findall(r"(?<![\w/])#(\d+)", text)} - {self_number})


def lint_body(number: int, body: str, allowlist_fn, state_fn, repo_root: Path = REPO_ROOT) -> LintResult:
    """Readiness per .github/ISSUE_TEMPLATE/agent-task.md and CONTRIBUTING-agents.md."""
    r = LintResult()
    sections = split_sections(body)
    for name in TEMPLATE_SECTIONS:
        text = find_section(sections, name)
        if text is None:
            r.problems.append(f"missing section `## {name}`")
        elif name == "Interface":
            if not interface_has_code(text):
                r.problems.append("`## Interface` has no code block with real signatures")
        elif name == "Blocked by":
            pass
        elif not meaningful(text):
            r.problems.append(f"`## {name}` is empty or still holds template placeholders")

    scope = find_section(sections, "Files in scope")
    if scope is not None and meaningful(scope):
        try:
            entries = allowlist_fn(body)
            if not entries:
                r.problems.append("`## Files in scope` names no parseable path")
        except RuntimeError as e:
            entries = []
            r.warnings.append(f"allowlist not checked: {e}")
        # What the parser will read differently from what a reader sees.
        items = list_items(scope)

        def leads_with_path(item: str) -> bool:
            m = re.match(r"`([^`]+)`", item) or re.match(r"(\S+)", item)
            return bool(m and re.search(r"[/.]", m.group(1)))
        if not any(leads_with_path(i) for i in items):
            r.warnings.append("no top-level list item in `## Files in scope` starts with a path, so "
                              "every backticked path in the section counts -- including one the "
                              "prose says is not this issue's. Write one path per top-level item")
        for item in items:
            # Two paths listed together -- "`a.py`, `b.py`" -- not a path and
            # a note about another file, which the grammar is built to allow.
            m = re.match(r"`([^`]+)`\s*(?:,|;|&|\+|and)\s*`([^`]+)`", item.strip())
            if m and re.search(r"[/.]", m.group(2)):
                r.warnings.append(f"only the first path of an item counts: `{m.group(1)}`, not "
                                  f"`{m.group(2)}` -- one path per item")
        for e in entries:
            # `src/ui` covers what is under it only when it has no extension
            # in its last segment; `.github` does and is read as a file.
            if not e.endswith("/") and not re.search(r"[*?\[]", e) and _is_dir_entry(e) is False \
                    and (repo_root / e).is_dir():
                r.warnings.append(f"`{e}` is a directory, but reads as a file; write `{e}/` to "
                                  "allow what is under it")

    size = find_section(sections, "Size") or ""
    if re.search(r"^\s*[-*]\s*\[[xX]\]\s*Too big", size, flags=re.M):
        r.problems.append("`## Size` is ticked *Too big*: split it into single seams before it is "
                          "handed out")

    for sec in ("Interface", "Context"):
        text = find_section(sections, sec) or ""
        if re.search(r"\bL\d+(\s*[–-]\s*\d+)?\b", text):
            r.warnings.append(f"`## {sec}` anchors on line numbers, which rot; "
                              "prefer symbol names")

    for n in blocked_by_numbers(find_section(sections, "Blocked by"), number):
        state = state_fn(n)
        if state == "unknown":
            # A rate limit or a 502 is not "closed": treating it so would hand
            # out work built on an interface that has not landed.
            r.warnings.append(f"could not read #{n}; treated as still blocking")
            r.blockers_open.append(n)
        elif state == "open":
            r.blockers_open.append(n)
    return r


LINT_MARKER = "<!-- issue-lint -->"


def lint_comment(number: int, result: LintResult) -> str:
    """The issue's one lint note: what blocks it, else what is worth fixing,
    else that it is ready. Rewritten in place on every lint, never repeated --
    and a ready issue with warnings gets one too: a line-number anchor that
    nobody is told about is the escalation it causes later."""
    lines = [LINT_MARKER]
    if result.problems:
        lines += [f"**issue-lint: #{number} is not ready to hand out.**", ""]
        lines += [f"- {p}" for p in result.problems]
        if result.warnings:
            lines += ["", "Also worth fixing:"] + [f"- {w}" for w in result.warnings]
    elif result.warnings:
        lines += [f"**issue-lint: #{number} is ready, but worth fixing:**", ""]
        lines += [f"- {w}" for w in result.warnings]
    else:
        lines += [f"**issue-lint: #{number} is ready.**"]
        return "\n".join(lines)
    lines += ["", "Edit the issue body; the lint re-runs on every edit. "
              "The template is `.github/ISSUE_TEMPLATE/agent-task.md`."]
    return "\n".join(lines)


def check_pr_body(body: str) -> list[str]:
    """What the PR description is missing, per .github/pull_request_template.md."""
    sections = split_sections(body)
    missing = []
    for name in PR_SECTIONS:
        text = find_section(sections, name)
        if text is not None:
            # The closing reference trails the last section; it is not its content.
            text = re.sub(r"(?im)^\s*(clos|fix|resolv)\w*:?\s+#\d*\s*$", "", text)
        if text is None:
            missing.append(f"missing section `## {name}`")
        elif not re.search(r"[A-Za-z0-9]", text):
            missing.append(f"`## {name}` is empty")
    return missing


# --- the decision ----------------------------------------------------------------

@dataclass
class Plan:
    paused: bool = False
    pause_reason: str = ""
    setup_problems: list[str] = field(default_factory=list)
    ops: list[dict] = field(default_factory=list)        # bookkeeping, applied in order
    dispatch: list[dict] = field(default_factory=list)   # agents to spawn
    waiting: list[str] = field(default_factory=list)
    in_flight: list[str] = field(default_factory=list)
    awaiting_human: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {k: getattr(self, k) for k in (
            "paused", "pause_reason", "setup_problems", "ops", "dispatch",
            "waiting", "in_flight", "awaiting_human", "deferred")}


def latest_verdict(reviews: list[dict], reviewer: str | None, head: str) -> tuple[str | None, int]:
    """(APPROVED | CHANGES_REQUESTED | None, comment-only reviews at head) by the reviewer bot."""
    if not reviewer:
        return None, 0
    mine = [r for r in reviews
            if (r.get("login") or "").lower() == reviewer.lower() and r.get("commit") == head]
    mine.sort(key=lambda r: r.get("submitted_at") or "")
    verdict = None
    comments = 0
    for r in mine:
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED"):
            verdict = r["state"]
        elif r.get("state") == "COMMENTED":
            comments += 1
    return verdict, comments


def approved_at(reviews: list[dict], reviewer: str | None, head: str) -> datetime | None:
    """When the bot last approved this head."""
    times = [parse_time(r.get("submitted_at")) for r in reviews
             if (r.get("login") or "").lower() == (reviewer or "").lower()
             and r.get("commit") == head and r.get("state") == "APPROVED"]
    times = [t for t in times if t]
    return max(times) if times else None


def decide(snap: dict, allowlist_fn, lint_fn) -> Plan:
    """One tick's plan from a snapshot. Pure: no GitHub, no clock but snap['now'].

    The snapshot: `now`, `issues`, `prs`, `reviewer_login`, `remote_branches`
    (the agent/ branches on origin, or None when they could not be listed),
    and optionally `paused`, `truncated` (a list was cut at its limit), `main`
    ({"sha", "red": [failed required checks]} for the default branch, or None
    when unknown) and `limits`.

    Every open item ends the tick with a next owner: dispatched, in flight,
    waiting on something named, deferred by a limit, or awaiting the owner.
    docs/Pipeline.md § States is this function's contract.
    """
    plan = Plan()
    lim: Limits = snap.get("limits") or LIMITS
    now: datetime = snap["now"]
    issues: list[dict] = snap["issues"]
    prs: list[dict] = snap["prs"]
    reviewer = snap.get("reviewer_login")
    remote_branches = snap.get("remote_branches", set())
    truncated = bool(snap.get("truncated"))
    main = snap.get("main") or {}
    main_red = list(main.get("red") or [])

    # A pause no longer returns at once: the tick still says what waits on the
    # owner, and still turns off the auto-merges it would otherwise let run.
    pause_why = ""
    if any(PAUSE in labels_of(i) for i in issues) or snap.get("paused") is True:
        pause_why = f"`{PAUSE}` on an open issue"
    elif snap.get("paused"):
        pause_why = str(snap["paused"])
    protection = snap.get("protection") or {}
    if protection.get("state") in ("missing", "drift") and snap.get("require_protection", True):
        # Nothing enforces the gate: an approval could merge red code, an agent
        # could push to the default branch. Hold everything until it is back.
        why = "; ".join(protection.get("problems") or [protection["state"]])
        pause_why = pause_why or f"branch protection {protection['state']} ({why})"
        plan.awaiting_human.append(
            f"branch protection on {DEFAULT_BRANCH} is {protection['state']}: {why} -- run "
            "`.claude/bin/pipeline setup-repo` in a terminal (or set require_protection false)")
    elif protection.get("state") == "unreadable":
        plan.setup_problems.append(
            "cannot read branch protection (the agents' token needs Administration: read) -- "
            "the tick cannot tell whether the gate is on")
    if not reviewer:
        plan.setup_problems.append(
            f"reviewer bot login unknown: no token at {CONFIG['reviewer_token_file']} "
            "and no PIPELINE_REVIEWER_LOGIN -- reviews and auto-merge are skipped")
    if truncated:
        plan.setup_problems.append(
            "more open issues or pull requests than one survey reads: transitions that "
            "would follow from something being absent (a closed PR, a closed issue) are "
            "skipped this tick")
    if remote_branches is None:
        plan.setup_problems.append(
            "could not list agent/ branches on origin (git ls-remote failed): stale "
            "implementer claims are left alone this tick")

    def stale(item: dict, age: timedelta) -> bool:
        t = parse_time(item.get("updatedAt"))
        return t is None or now - t > age

    def fresh_working(item: dict) -> bool:
        return WORKING in labels_of(item) and not stale(item, lim.stale_working)

    def issue_status(n: int, status: str | None, expect, why: str) -> None:
        # `expect` is the status this tick saw. apply_ops re-reads the labels
        # and skips the write when someone moved the issue in the meantime.
        plan.ops.append({"op": "set-status", "number": n, "status": status,
                         "expect": expect, "why": why})

    owner = snap.get("owner_login")
    answer_how = (f"Answer in a comment and add the `{ANSWERED}` label; the next tick picks it "
                  "up from there.")

    def comment(kind: str, n: int, body: str, notify: bool = False) -> None:
        # A hand-off mentions the owner, and is posted as the bot when there is
        # one: GitHub does not notify you of your own account's comments, and
        # every agent writes as you.
        if notify and owner:
            body = f"@{owner} {body}"
        plan.ops.append({"op": "comment", "kind": kind, "number": n, "body": body,
                         "as_bot": notify})

    def pr_to_owner(n: int, why: str, text: str) -> None:
        plan.ops.append({"op": "add-label", "kind": "pr", "number": n, "label": NEEDS_HUMAN,
                         "why": why})
        comment("pr", n, f"**Pipeline:** {text} Handing this to a human (`{NEEDS_HUMAN}`). "
                         + answer_how, notify=True)
        plan.awaiting_human.append(f"PR #{n}: {why}")

    issue_by_n = {i["number"]: i for i in issues}
    count = {"fix": 0, "review": 0, "triage": 0}
    sha7 = (main.get("sha") or "")[:7]

    # ---- the default branch ----
    main_issue = next((i for i in issues if MAIN_RED in labels_of(i)), None)
    if main_red:
        plan.awaiting_human.append(
            f"{DEFAULT_BRANCH} is red at {sha7} ({', '.join(main_red)}): fix passes and new "
            "implementations wait until it is green")
        if main_issue is None:
            plan.ops.append({
                "op": "create-issue", "labels": [MAIN_RED, NEEDS_HUMAN],
                "title": f"[pipeline] {DEFAULT_BRANCH} is red at {sha7}",
                "body": (f"**Pipeline:** the required checks {', '.join(main_red)} fail on "
                         f"`{DEFAULT_BRANCH}` at {main.get('sha')}. Every pull request's CI "
                         "runs against it, so the pipeline holds fix passes and new "
                         "implementations until it is green again, and closes this issue then."),
                "why": f"{DEFAULT_BRANCH} red"})
    elif main_issue is not None and main.get("state") == "success":
        # Only green closes it: a head whose checks are still running is not
        # proof that the breakage is gone.
        plan.ops.append({"op": "close-issue", "number": main_issue["number"],
                         "body": f"**Pipeline:** `{DEFAULT_BRANCH}` is green again at {sha7}.",
                         "why": f"{DEFAULT_BRANCH} green"})

    # ---- pull requests ----
    pr_issue: dict[int, int | None] = {}
    by_issue: dict[int, list[dict]] = {}
    for pr in prs:
        if pr.get("crossRepo"):
            continue
        n_i = branch_issue(pr.get("headRefName", "")) or next(iter(pr.get("closingIssues") or []), None)
        pr_issue[pr["number"]] = n_i
        if n_i:
            by_issue.setdefault(n_i, []).append(pr)
    open_pr_by_issue = {n_i: min(ps, key=lambda p: p["number"]) for n_i, ps in by_issue.items()}
    reviews_running = sum(1 for p in prs if fresh_working(p) and REVIEWING in labels_of(p))
    fixes_running = sum(1 for p in prs if fresh_working(p) and REVIEWING not in labels_of(p))

    for pr in sorted(prs, key=lambda p: p["number"]):
        n = pr["number"]
        tag = f"PR #{n}"
        labels = labels_of(pr)
        if pr.get("crossRepo"):
            # A fork's branch is nobody's claim, whatever it is called; its
            # code is an outsider's. Agents neither review nor fix it.
            plan.awaiting_human.append(f"{tag}: from a fork -- review it yourself")
            continue
        if (pr.get("base") or DEFAULT_BRANCH) != DEFAULT_BRANCH:
            # A stacked PR: merging it lands nothing on the default branch and
            # closes no issue, however "Closes #N" its body says. Once its base
            # has merged, point it at the default branch.
            plan.awaiting_human.append(
                f"{tag}: targets {pr['base']}, not {DEFAULT_BRANCH} -- merging it lands nothing on "
                f"{DEFAULT_BRANCH} and closes no issue; retarget it once {pr['base']} has merged")
            continue
        issue_n = pr_issue.get(n)
        if NEEDS_HUMAN in labels and ANSWERED in labels:
            # The owner answered: the PR gets its rounds back, and the reviews
            # that ended without a verdict up to now stop counting.
            comment_only = latest_verdict(pr.get("reviews", []), reviewer, pr.get("headRefOid", ""))[1]
            for label in sorted(l for l in labels if l in (NEEDS_HUMAN, ANSWERED)
                                or l.startswith((FIX_ROUND, CONFLICT_ROUND))):
                plan.ops.append({"op": "remove-label", "kind": "pr", "number": n, "label": label,
                                 "why": "the owner answered"})
            if pr.get("headRefOid"):
                plan.ops.append({"op": "post-status", "number": n, "sha": pr["headRefOid"],
                                 "context": ANSWERED_STATUS,
                                 "description": f"comments={comment_only} "
                                                f"attempts={int(pr.get('reviewAttempts') or 0)}",
                                 "why": "the owner answered"})
            plan.waiting.append(f"{tag}: the owner answered -- it resumes next tick")
            continue
        if NEEDS_HUMAN in labels:
            plan.awaiting_human.append(f"{tag}: {NEEDS_HUMAN}")
            continue
        if not issue_n:
            if pr.get("isDraft"):
                plan.waiting.append(f"{tag}: draft")
            else:
                # No issue, no allowlist, no contract to review against.
                plan.awaiting_human.append(f"{tag}: bound to no issue -- review it yourself "
                                           "(github-pr-review skill)")
            continue
        first = open_pr_by_issue[issue_n]
        if first is not pr:
            pr_to_owner(n, f"a second open PR for #{issue_n}",
                        f"#{first['number']} already works #{issue_n}; two branches on one "
                        "issue collide. Close one of them.")
            continue
        issue = issue_by_n.get(issue_n)
        if issue is None:
            if truncated:
                plan.waiting.append(f"{tag}: issue #{issue_n} not in this survey")
            else:
                plan.awaiting_human.append(
                    f"{tag}: its issue #{issue_n} is closed -- close the PR, or reopen the issue")
            continue
        ilabels = labels_of(issue)
        held = ilabels & {HUMAN_DECISION, ASSET}
        scope = _safe_scope(allowlist_fn, issue.get("body", ""))
        if held or (scope != ["*"] and control_paths_touched(scope)):
            # The owner's work: an agent pass on it could edit the files only
            # the owner may (docs/Pipeline.md § What stays with the owner).
            plan.awaiting_human.append(f"{tag}: works owner-held issue #{issue_n} -- "
                                       "review and fix it yourself")
            continue
        agent_issue = AGENT_TASK in ilabels
        human_holds = HUMAN_HOLDS in ilabels
        if agent_issue:
            # The issue says whether its PR may move. Escalated means triage is
            # rewriting the contract the PR is judged by; a fix pass now would
            # work to the old one, and burn a round doing it.
            istatus = status_of(ilabels)
            if istatus == NEEDS_HUMAN:
                plan.awaiting_human.append(f"{tag}: its issue #{issue_n} waits on you")
                continue
            if istatus in (ESCALATED, NEEDS_SPEC) or fresh_working(issue):
                plan.waiting.append(f"{tag}: its issue #{issue_n} is with triage")
                continue
            if istatus == BLOCKED:
                plan.waiting.append(f"{tag}: its issue #{issue_n} is blocked")
                continue
            if istatus not in (IN_PROGRESS, IN_REVIEW):
                plan.waiting.append(f"{tag}: its issue #{issue_n} is being re-judged")
                continue
        # Agents fix only their own branches: an ordinary issue's PR is a
        # person's, and so is one the owner holds.
        fixable = agent_issue and not human_holds

        if WORKING in labels:
            if not stale(pr, lim.stale_working):
                plan.in_flight.append(f"{tag}: an agent holds it")
                continue
            plan.ops.append({"op": "remove-label", "kind": "pr", "number": n, "label": WORKING,
                             "why": "stale pipeline:working"})
            if REVIEWING in labels:
                plan.ops.append({"op": "remove-label", "kind": "pr", "number": n,
                                 "label": REVIEWING, "why": "stale claim"})

        base = {"pr": n, "issue": issue_n, "branch": pr.get("headRefName")}

        def fix(reason: str, prefix: str = FIX_ROUND, limit: int = lim.max_fix_rounds) -> None:
            if not fixable:
                who = (f"the owner is working #{issue_n}" if human_holds
                       else "not an agent's branch")
                plan.awaiting_human.append(f"{tag}: {reason} -- {who}")
                return
            rounds = fix_rounds(labels, prefix)
            if rounds >= limit:
                kind = "conflict passes" if prefix == CONFLICT_ROUND else "fix rounds"
                pr_to_owner(n, f"{reason}: {kind} exhausted",
                            f"{reason} again after {rounds} {kind}.")
                return
            if fixes_running + count["fix"] >= lim.max_parallel_fix:
                plan.deferred.append(f"{tag}: fix cap ({lim.max_parallel_fix}) reached")
                return
            count["fix"] += 1
            plan.dispatch.append({**base, "kind": "fix", "agent": "github-issue-resolver",
                                  "reason": reason, "round": rounds + 1,
                                  "round_label": f"{prefix}{rounds + 1}", "reviewer": reviewer})

        if pr.get("isDraft"):
            # A draft from an escalation whose issue is back in the queue: the
            # work on it is kept, finished and marked ready -- otherwise it is
            # a draft nobody ever picks up again.
            # In review, not in progress: an in-progress issue may still have
            # its implementer between opening the draft and escalating.
            if fixable and status_of(ilabels) == IN_REVIEW:
                fix("draft-resume")
            else:
                plan.waiting.append(f"{tag}: draft")
            continue
        if pr.get("mergeable") == "CONFLICTING":
            fix("conflict", CONFLICT_ROUND, lim.max_conflict_rounds)
            continue
        checks = pr.get("checks", "pending")
        if checks == "pending":
            # Since the newest check started, or the head commit when none has:
            # a re-run on an old commit is fresh work, not a stuck one.
            since = parse_time(pr.get("checksSince") or pr.get("headAt"))
            if since and now - since > lim.stale_waiting:
                pr_to_owner(n, "required checks still not finished",
                            f"the required checks have not finished in "
                            f"{int((now - since).total_seconds() // 3600)} hours at "
                            f"{pr.get('headRefOid', '')[:7]} -- a workflow that never started, "
                            "or one waiting for approval?")
            else:
                plan.waiting.append(f"{tag}: CI running")
            continue
        if checks == "failure":
            failed_runs = pr.get("failedRuns") or []
            first_tries = [r for r in failed_runs if int(r.get("attempt") or 1) <= 1 and r.get("run")]
            if failed_runs and len(first_tries) == len(failed_runs):
                # One re-run before an agent run: a runner hiccup costs CI
                # minutes, not a fix round -- and an `allowlist` failure that
                # triage has since answered by widening the issue turns green.
                # Jobs of one workflow share a run: re-run each run once.
                for run_id in sorted({r["run"] for r in first_tries}):
                    names = ", ".join(r.get("name") or "?" for r in first_tries if r["run"] == run_id)
                    plan.ops.append({"op": "rerun", "kind": "pr", "number": n, "run": run_id,
                                     "why": f"{names} failed on its first attempt"})
                plan.waiting.append(f"{tag}: CI re-run")
            elif main_red:
                plan.waiting.append(f"{tag}: CI failed while {DEFAULT_BRANCH} is red")
            else:
                fix("ci-failed")
            continue

        if not reviewer:
            plan.waiting.append(f"{tag}: CI green, no reviewer identity")
            continue
        head = pr.get("headRefOid", "")
        verdict, comment_only = latest_verdict(pr.get("reviews", []), reviewer, head)
        if verdict == "APPROVED":
            if pr.get("autoMerge"):
                since = approved_at(pr.get("reviews", []), reviewer, head)
                if since and now - since > lim.stale_waiting:
                    pr_to_owner(n, "approved, but not merged",
                                f"approved {int((now - since).total_seconds() // 3600)} hours "
                                "ago with auto-merge on, and GitHub has not merged it.")
                else:
                    plan.waiting.append(f"{tag}: approved, auto-merge pending")
            else:
                plan.ops.append({"op": "enable-automerge", "number": n, "head": head,
                                 "why": "approved at head without auto-merge"})
            continue
        if verdict == "CHANGES_REQUESTED":
            fix("review")
            continue
        comment_only -= int(pr.get("answeredComments") or 0)
        if comment_only >= lim.max_comment_only_reviews:
            pr_to_owner(n, f"no verdict after {comment_only} reviews",
                        f"the reviewer answered this head {comment_only} times with a comment "
                        "and no verdict.")
            continue
        attempts = int(pr.get("reviewAttempts") or 0)
        if attempts - int(pr.get("answeredAttempts") or 0) >= lim.max_review_attempts:
            pr_to_owner(n, f"review ended without a verdict {attempts} times",
                        f"a review was dispatched {attempts} times at {head[:7]} and none "
                        "submitted a verdict.")
            continue
        if reviews_running + count["review"] >= lim.max_parallel_review:
            plan.deferred.append(f"{tag}: review cap ({lim.max_parallel_review}) reached")
            continue
        count["review"] += 1
        plan.dispatch.append({**base, "kind": "review", "agent": "github-pr-reviewer",
                              "reason": "CI green, no verdict at head", "head": head,
                              "attempt": attempts + 1})

    # ---- issues ----
    active_statuses = {READY, BLOCKED, NEEDS_SPEC, IN_PROGRESS, IN_REVIEW, ESCALATED}
    active = 0
    in_progress_scopes: list[list[str]] = []
    review_scopes: list[list[str]] = []
    candidates: list[tuple[dict, list[str]]] = []
    idea_candidates: list[int] = []
    idle_open = any(IDLE in labels_of(i) for i in issues)
    ideas_open = False
    planner_busy = False
    planning_open = False
    triage_running = sum(1 for i in issues if AGENT_TASK in labels_of(i) and fresh_working(i)
                         and status_of(labels_of(i)) in (ESCALATED, NEEDS_SPEC))

    for issue in sorted(issues, key=lambda i: i["number"]):
        n = issue["number"]
        tag = f"#{n}"
        labels = labels_of(issue)

        if IDEA in labels:
            if NEEDS_HUMAN in labels and ANSWERED in labels:
                # The planner reads the idea's comments; the answer is there.
                for label in (NEEDS_HUMAN, ANSWERED):
                    plan.ops.append({"op": "remove-label", "kind": "issue", "number": n,
                                     "label": label, "why": "the owner answered"})
                plan.waiting.append(f"{tag}: the owner answered -- planned next tick")
                ideas_open = True
                continue
            if NEEDS_HUMAN in labels:
                plan.awaiting_human.append(f"{tag}: idea waiting on an answer")
                continue
            ideas_open = True
            if fresh_working(issue):
                plan.in_flight.append(f"{tag}: planner holds this idea")
                planner_busy = True
                continue
            if WORKING in labels:
                # Removed first, so the claim is a real label change that
                # restarts the staleness clock.
                plan.ops.append({"op": "remove-label", "kind": "issue", "number": n,
                                 "label": WORKING, "why": "stale pipeline:working"})
            idea_candidates.append(n)
            continue
        if PLANNING in labels:
            if fresh_working(issue):
                plan.in_flight.append(f"{tag}: roadmap planner running")
                planner_busy = planning_open = True
            else:
                plan.ops.append({"op": "close-issue", "number": n,
                                 "body": "**Pipeline:** the roadmap planner did not finish; "
                                         "the next empty tick plans again.",
                                 "why": "stale roadmap planning"})
            continue
        if IDLE in labels:
            plan.awaiting_human.append(f"{tag}: roadmap gate (pipeline:idle) -- planning waits on you")
            continue
        if labels & {HUMAN_DECISION, ASSET}:
            # With or without agent-task: the owner's either way.
            plan.awaiting_human.append(f"{tag}: {', '.join(sorted(labels & {HUMAN_DECISION, ASSET}))}")
            continue
        if AGENT_TASK not in labels:
            if NEEDS_HUMAN in labels and MAIN_RED not in labels:
                plan.awaiting_human.append(f"{tag}: {NEEDS_HUMAN}")
            continue

        status = status_of(labels)
        if sum(1 for s in STATUSES if s in labels) > 1:
            issue_status(n, status, status, "more than one status label")
        if status in active_statuses:
            active += 1
        pr = open_pr_by_issue.get(n)
        if pr:
            # Whatever the issue's state, its open PR may get another pass on
            # these files: a new issue that shares one waits.
            review_scopes.append(_safe_scope(allowlist_fn, issue.get("body", "")))

        if status == NEEDS_HUMAN and ANSWERED in labels:
            # The owner answered the question. Triage works the answer into the
            # issue, however often it has answered before: this is new input.
            issue_status(n, ESCALATED, NEEDS_HUMAN, "the owner answered")
            for label in sorted({ANSWERED, TRIAGED} & labels):
                plan.ops.append({"op": "remove-label", "kind": "issue", "number": n,
                                 "label": label, "why": "the owner answered"})
            if triage_running + count["triage"] < lim.max_parallel_triage and not fresh_working(issue):
                count["triage"] += 1
                plan.dispatch.append({"kind": "triage", "agent": "github-triage", "issue": n,
                                      "reason": "owner-answered"})
            else:
                plan.waiting.append(f"{tag}: the owner answered -- triage next tick")
            continue
        if status == NEEDS_HUMAN:
            plan.awaiting_human.append(f"{tag}: {NEEDS_HUMAN}")
            continue
        if fresh_working(issue):
            plan.in_flight.append(f"{tag}: an agent holds it")
            continue
        if WORKING in labels:
            plan.ops.append({"op": "remove-label", "kind": "issue", "number": n,
                             "label": WORKING, "why": "stale pipeline:working"})

        if status in (ESCALATED, NEEDS_SPEC):
            if TRIAGED in labels:
                issue_status(n, NEEDS_HUMAN, status, f"{status} again after triage")
                comment("issue", n, f"**Pipeline:** `{status}` a second time after triage. "
                                    f"Handing this to a human (`{NEEDS_HUMAN}`). " + answer_how,
                        notify=True)
                plan.awaiting_human.append(f"{tag}: {status} twice")
            elif triage_running + count["triage"] >= lim.max_parallel_triage:
                plan.deferred.append(f"{tag}: triage cap ({lim.max_parallel_triage}) reached")
            else:
                count["triage"] += 1
                plan.dispatch.append({"kind": "triage", "agent": "github-triage", "issue": n,
                                      "reason": status})
        elif status == IN_PROGRESS:
            scope = _safe_scope(allowlist_fn, issue.get("body", ""))
            if pr:
                issue_status(n, IN_REVIEW, IN_PROGRESS, f"PR #{pr['number']} is open")
            elif HUMAN_HOLDS in labels:
                in_progress_scopes.append(scope)
                plan.in_flight.append(f"{tag}: the owner holds it")
            elif (not stale(issue, lim.stale_in_progress) or remote_branches is None
                  or truncated):
                in_progress_scopes.append(scope)
                plan.in_flight.append(f"{tag}: implementer working")
            else:
                branches = sorted(b for b in remote_branches if b.startswith(f"agent/{n}-"))
                where = f" Its branch: `{'`, `'.join(branches)}`." if branches else ""
                if ATTEMPT in labels:
                    # Ended twice without a PR or an escalation: something the
                    # implementer cannot see is wrong. A third run would not help.
                    target = NEEDS_HUMAN if TRIAGED in labels else ESCALATED
                    issue_status(n, target, IN_PROGRESS, "implementer ended twice without a PR")
                    comment("issue", n, "**Pipeline:** an implementer ended on this issue twice "
                                        f"without opening a PR or escalating.{where} "
                                        + (f"Handing this to a human. {answer_how}"
                                           if target == NEEDS_HUMAN
                                           else "Triage decides what is wrong with it."),
                            notify=target == NEEDS_HUMAN)
                    if target == NEEDS_HUMAN:
                        plan.awaiting_human.append(f"{tag}: implementer ended twice")
                elif branches:
                    # Work was pushed and the implementer stopped before the
                    # PR. Resume that branch rather than start over beside it.
                    plan.ops.append({"op": "add-label", "kind": "issue", "number": n,
                                     "label": ATTEMPT, "why": "implementer ended without a PR"})
                    in_progress_scopes.append(scope)
                    plan.dispatch.append({"kind": "implement", "agent": "github-issue-resolver",
                                          "issue": n, "title": issue.get("title", ""),
                                          "branch": branches[-1], "resume": True,
                                          "reason": "resume a branch without a PR", "must": True})
                else:
                    plan.ops.append({"op": "add-label", "kind": "issue", "number": n,
                                     "label": ATTEMPT, "why": "implementer ended without a PR"})
                    issue_status(n, None, IN_PROGRESS,
                                 "in progress for hours with no branch and no PR")
                    plan.ops.append({"op": "lint", "number": n})
        elif status == IN_REVIEW:
            if pr:
                pass
            elif truncated:
                plan.waiting.append(f"{tag}: its PR is not in this survey")
            else:
                issue_status(n, ESCALATED, IN_REVIEW, "its PR was closed without merging")
                comment("issue", n, "**Pipeline:** the pull request for this issue was closed "
                                    "without merging while the issue stayed open. Triage decides "
                                    "whether to re-run it, amend it, or close it.")
        elif status in LINT_OWNED:
            result: LintResult = lint_fn(issue)
            desired = result.status
            if pr:
                # Back from triage with its PR still open: the PR resumes, unless
                # the answer was to wait for a blocker or the spec is still short.
                target = desired if desired in (BLOCKED, NEEDS_SPEC) else IN_REVIEW
                if target != status:
                    issue_status(n, target, status, f"PR #{pr['number']} is open"
                                 if target == IN_REVIEW else "; ".join(result.problems)
                                 or f"blocked by #{', #'.join(map(str, result.blockers_open))}")
                    if target == NEEDS_SPEC:
                        plan.ops.append({"op": "lint-note", "number": n,
                                         "body": lint_comment(n, result), "clean": False})
                if target == BLOCKED:
                    plan.waiting.append(f"{tag}: blocked by #{', #'.join(map(str, result.blockers_open))}")
                continue
            if desired != status:
                issue_status(n, desired, status, "; ".join(result.problems) or
                             (f"blocked by #{', #'.join(map(str, result.blockers_open))}"
                              if result.blockers_open else "lint passed"))
                plan.ops.append({"op": "lint-note", "number": n, "body": lint_comment(n, result),
                                 "clean": not (result.problems or result.warnings)})
                if desired in active_statuses and status not in active_statuses:
                    active += 1
            if desired == READY:
                scope = _safe_scope(allowlist_fn, issue.get("body", ""))
                touched = control_paths_touched(scope) if scope != ["*"] else []
                if scope == ["*"]:
                    # Unreadable scope: it could name anything, a control path
                    # included, and no overlap check can clear it.
                    plan.deferred.append(f"{tag}: Files in scope could not be parsed")
                elif touched:
                    # An agent that may edit the pipeline's own rules can
                    # loosen them; that change is the owner's to make.
                    plan.awaiting_human.append(
                        f"{tag}: Files in scope touches pipeline control paths "
                        f"({', '.join(touched)}) -- the owner works it, labelled {HUMAN_DECISION}")
                else:
                    candidates.append((issue, scope))
            elif desired == BLOCKED:
                plan.waiting.append(f"{tag}: blocked by #{', #'.join(map(str, result.blockers_open))}")

    # ---- new implementations ----
    # The cap counts implementers; the overlap check also counts work whose PR
    # is open -- a fix pass there edits the same files.
    slots = lim.max_parallel_implement - len(in_progress_scopes)
    taken = in_progress_scopes + review_scopes
    for issue, scope in candidates:
        tag = f"#{issue['number']}"
        if main_red:
            plan.deferred.append(f"{tag}: {DEFAULT_BRANCH} is red")
            continue
        if slots <= 0:
            plan.deferred.append(f"{tag}: implementer cap ({lim.max_parallel_implement}) reached")
            continue
        if any(scopes_overlap(scope, s) for s in taken):
            plan.deferred.append(f"{tag}: shares a file in Files in scope with work in flight")
            continue
        taken.append(scope)
        slots -= 1
        plan.dispatch.append({"kind": "implement", "agent": "github-issue-resolver",
                              "issue": issue["number"], "title": issue.get("title", ""),
                              "reason": READY})

    # ---- planning: one planner at a time ----
    if idea_candidates and not planner_busy:
        planner_busy = True
        plan.dispatch.append({"kind": "plan", "agent": "github-planner", "mode": "idea",
                              "issue": idea_candidates[0], "reason": "idea waiting"})
    pipeline_prs = [p for p in prs if not p.get("crossRepo") and NEEDS_HUMAN not in labels_of(p)
                    and AGENT_TASK in labels_of(issue_by_n.get(pr_issue.get(p["number"]) or -1, {}))]
    if (not plan.dispatch and active == 0 and not pipeline_prs and not idle_open
            and not ideas_open and not planner_busy and not planning_open and not main_red):
        plan.dispatch.append({"kind": "plan", "agent": "github-planner", "mode": "roadmap",
                              "reason": "queue empty"})

    # ---- one tick's budget, and one day's ----
    def trim(cap: int, why: str) -> None:
        if len(plan.dispatch) <= cap:
            return
        kept, room = [], cap - sum(1 for d in plan.dispatch if d.get("must"))
        for d in plan.dispatch:
            if d.get("must") or room > 0:
                kept.append(d)
                room -= 0 if d.get("must") else 1
            else:
                what = f"PR #{d['pr']}" if d.get("pr") else f"#{d.get('issue')}"
                plan.deferred.append(f"{what}: {why}")
        plan.dispatch = kept

    trim(lim.max_dispatch_per_tick, f"dispatch cap ({lim.max_dispatch_per_tick}) reached")
    if lim.max_agent_runs_per_day > 0:
        left = max(0, lim.max_agent_runs_per_day - int(snap.get("runs_today") or 0))
        if len(plan.dispatch) > left:
            plan.setup_problems.append(
                f"daily budget: {lim.max_agent_runs_per_day} agent runs in 24 hours reached -- "
                "raise max_agent_runs_per_day, or wait")
        trim(left, f"daily budget ({lim.max_agent_runs_per_day} runs in 24 h) reached")

    if pause_why:
        plan.paused = True
        plan.pause_reason = pause_why
        plan.dispatch = []
        # Approved PRs would still merge the moment their checks go green.
        # Unpausing turns auto-merge back on: an approval at the head without
        # it is already an op of its own.
        plan.ops = [{"op": "disable-automerge", "kind": "pr", "number": p_["number"],
                     "why": "the pipeline is paused"}
                    for p_ in prs if p_.get("autoMerge") and not p_.get("crossRepo")]
    return plan


def _safe_scope(allowlist_fn, body: str) -> list[str]:
    try:
        return allowlist_fn(body)
    except RuntimeError:
        return ["*"]  # unknown scope: treat as overlapping everything


# --- GitHub ------------------------------------------------------------------

class GhError(RuntimeError):
    pass


class StaleState(GhError):
    """The labels moved since the survey, so the write was not made."""


ANY = object()      # set_status(expect=ANY): write whatever the issue's status is now
ISSUE_LIMIT = 500   # open issues one survey reads
PR_LIMIT = 200      # open pull requests one survey reads


def reviewer_token_path() -> Path:
    return Path(os.path.expanduser(os.environ.get("PIPELINE_REVIEWER_TOKEN_FILE")
                                   or CONFIG["reviewer_token_file"]))


def reviewer_token() -> str | None:
    token = os.environ.get("PIPELINE_REVIEWER_TOKEN", "").strip()
    if token:
        return token
    try:
        return reviewer_token_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


class Gh:
    def __init__(self, dry_run: bool = False):
        self.gh = shutil.which("gh")
        if not self.gh:
            raise GhError("gh is not on PATH -- see docs/AgentEnvironment.md")
        self.dry_run = dry_run
        self.log: list[str] = []

    def _run(self, args: list[str], input: str | None = None, as_reviewer: bool = False,
             check: bool = True, mutating: bool = False) -> str:
        if mutating and self.dry_run:
            self.log.append("DRY-RUN gh " + " ".join(args))
            return ""
        env = dict(os.environ)
        if as_reviewer:
            token = reviewer_token()
            if not token:
                raise GhError(f"no reviewer token at {reviewer_token_path()} (docs/Pipeline.md § Setup)")
            env["GH_TOKEN"] = token
        proc = subprocess.run([self.gh, *args], input=input, capture_output=True, text=True,
                              encoding="utf-8", env=env)
        if check and proc.returncode != 0:
            raise GhError(f"gh {' '.join(args[:3])} ...: {proc.stderr.strip()[:400]}")
        return proc.stdout

    def json(self, args: list[str], as_reviewer: bool = False):
        out = self._run(args, as_reviewer=as_reviewer)
        return json.loads(out) if out.strip() else None

    # reads
    def open_issues(self) -> list[dict]:
        return self.json(["issue", "list", "--state", "open", "--limit", str(ISSUE_LIMIT),
                          "--json", "number,title,labels,body,updatedAt"]) or []

    def paused(self) -> bool:
        # Asked on its own, so the kill switch works however many issues are open.
        return bool(self.json(["issue", "list", "--label", PAUSE, "--state", "open",
                               "--limit", "1", "--json", "number"]))

    def issue(self, n: int) -> dict:
        return self.json(["issue", "view", str(n), "--json", "number,title,labels,body,updatedAt,state"])

    def labels_now(self, kind: str, n: int) -> set[str]:
        """An issue's or pull request's labels as they are now, not at the survey."""
        return labels_of(self.json([kind if kind == "pr" else "issue", "view", str(n),
                                    "--json", "labels"]) or {})

    def issue_state(self, n: int) -> str | None:
        """open, closed, None (no such issue), or unknown (could not ask)."""
        try:
            data = self.json(["api", f"repos/{{owner}}/{{repo}}/issues/{n}"])
            return (data or {}).get("state")
        except GhError as e:
            text = str(e)
            if "404" in text or "410" in text or "Not Found" in text:
                return None
            return "unknown"

    def ci_rollup(self, sha: str) -> list[dict]:
        """Every check on one commit, in the shape of GraphQL's statusCheckRollup.

        Read through the Actions API and the commit statuses, not through
        check runs: a fine-grained token has no Checks permission, so on a
        private repository check runs -- and statusCheckRollup, `gh pr checks`
        -- are unreadable to the token the tick runs with. Every required
        check is an Actions job, and a job's name is its check's name; the
        pipeline's own pipeline/* results are commit statuses. Only the latest
        attempt of each run counts, as it does for branch protection.
        """
        rollup: list[dict] = []
        pages = self.json(["api", "--paginate", "--slurp",
                           f"repos/{{owner}}/{{repo}}/actions/runs?head_sha={sha}&per_page=100"]) or []
        for run in (r for page in pages for r in (page or {}).get("workflow_runs") or []):
            jobs = self.json(["api", "--paginate", "--slurp",
                              f"repos/{{owner}}/{{repo}}/actions/runs/{run['id']}/jobs"
                              "?filter=latest&per_page=100"]) or []
            for job in (j for page in jobs for j in (page or {}).get("jobs") or []):
                rollup.append({"__typename": "CheckRun", "name": job.get("name"),
                               "workflowName": run.get("name") or "",
                               "status": (job.get("status") or "").upper(),
                               "conclusion": (job.get("conclusion") or "").upper(),
                               "startedAt": job.get("started_at"), "completedAt": job.get("completed_at"),
                               "detailsUrl": job.get("html_url") or "",
                               "runAttempt": job.get("run_attempt") or run.get("run_attempt")})
        statuses = self.json(["api", "--paginate", "--slurp",
                              f"repos/{{owner}}/{{repo}}/commits/{sha}/statuses?per_page=100"]) or []
        for st in (x for page in statuses for x in (page or [])):
            rollup.append({"__typename": "StatusContext", "context": st.get("context"),
                           "state": (st.get("state") or "").upper(),
                           "description": st.get("description") or "", "createdAt": st.get("created_at")})
        return rollup

    def head_sha(self, ref: str) -> str:
        return self._run(["api", f"repos/{{owner}}/{{repo}}/commits/{ref}", "--jq", ".sha"]).strip()

    def open_prs(self) -> list[dict]:
        raw = self.json(["pr", "list", "--state", "open", "--limit", str(PR_LIMIT), "--json",
                         "number,title,baseRefName,headRefName,headRefOid,isDraft,labels,mergeable,"
                         "updatedAt,autoMergeRequest,closingIssuesReferences,"
                         "isCrossRepository"]) or []
        prs = []
        for p in raw:
            pages = self.json(["api", "--paginate", "--slurp",
                               f"repos/{{owner}}/{{repo}}/pulls/{p['number']}/reviews?per_page=100"]) or []
            reviews = [r for page in pages for r in (page or [])]
            checks = rollup_checks(self.ci_rollup(p["headRefOid"]) if p.get("headRefOid") else [],
                                   REQUIRED_CHECKS)
            item = {
                "number": p["number"], "title": p.get("title", ""),
                "headRefName": p.get("headRefName", ""), "headRefOid": p.get("headRefOid", ""),
                "base": p.get("baseRefName") or DEFAULT_BRANCH,
                "isDraft": p.get("isDraft", False), "labels": p.get("labels", []),
                "crossRepo": bool(p.get("isCrossRepository")),
                "mergeable": p.get("mergeable"), "updatedAt": p.get("updatedAt"),
                "checks": checks["state"],
                "checksSince": checks["since"],
                "reviewAttempts": checks["reviewAttempts"],
                "answeredComments": checks["answeredComments"],
                "answeredAttempts": checks["answeredAttempts"],
                "autoMerge": p.get("autoMergeRequest") is not None,
                "closingIssues": [c["number"] for c in p.get("closingIssuesReferences") or []],
                "reviews": [{"login": (r.get("user") or {}).get("login"), "state": r.get("state"),
                             "commit": r.get("commit_id"), "submitted_at": r.get("submitted_at")}
                            for r in reviews],
            }
            if checks["state"] == "failure":
                # Which failed runs were already re-run: decide() re-runs a
                # first attempt once before it spends an agent on it. An
                # unknown attempt counts as re-run: better a fix pass than a loop.
                item["failedRuns"] = [{"name": f["name"], "run": f["run"], "attempt": f["attempt"] or 99}
                                      for f in checks["failed"]]
            elif checks["state"] == "pending" and item["headRefOid"]:
                try:
                    item["headAt"] = self._run(
                        ["api", f"repos/{{owner}}/{{repo}}/commits/{item['headRefOid']}",
                         "--jq", ".commit.committer.date"]).strip() or None
                except GhError:
                    item["headAt"] = None
            prs.append(item)
        return prs

    def reviewer_login(self) -> str | None:
        env = os.environ.get("PIPELINE_REVIEWER_LOGIN", "").strip()
        if env:
            # Still no token means every review fails at the wrapper: better
            # a setup problem than a reviewer dispatched every tick for nothing.
            return env if reviewer_token() else None
        if not reviewer_token():
            return None
        try:
            return (self.json(["api", "user"], as_reviewer=True) or {}).get("login")
        except GhError:
            return None

    def main_health(self) -> dict | None:
        """The default branch's head and which required checks fail there."""
        try:
            sha = self.head_sha(DEFAULT_BRANCH)
            rollup = self.ci_rollup(sha) if sha else []
        except GhError:
            return None
        # Only the required checks that run on a push count: allowlist and
        # contract never run on the default branch, and their absence is not red.
        present = {r.get("name") for r in rollup}
        judged = tuple(c for c in REQUIRED_CHECKS if c in present)
        checks = rollup_checks(rollup, judged) if judged else {"state": "pending", "failed": []}
        return {"sha": sha, "state": checks["state"],
                "red": sorted({f["name"] for f in checks["failed"]})}

    def labels(self) -> set[str]:
        data = self.json(["label", "list", "--limit", "300", "--json", "name"]) or []
        return {l["name"] for l in data}

    # writes
    def ensure_labels(self, only_missing: bool = True) -> list[str]:
        have = self.labels()
        made = []
        for name, (color, desc) in LABELS.items():
            if only_missing and name in have:
                continue
            self._run(["label", "create", name, "--color", color, "--description", desc,
                       "--force"], mutating=True)
            made.append(name)
        return made

    def set_status(self, n: int, status: str | None, expect=ANY) -> None:
        """Make `status` the issue's one status label.

        Reads the labels first, so every other status label goes -- not only
        the ones the survey saw -- and, with `expect`, writes nothing when the
        issue's status is no longer the one the caller decided on.
        """
        current = self.labels_now("issue", n)
        if expect is not ANY and status_of(current) != expect:
            raise StaleState(f"#{n} is {status_of(current)} now, not {expect}")
        args = ["issue", "edit", str(n)]
        for s in STATUSES:
            if s in current and s != status:
                args += ["--remove-label", s]
        if status and status not in current:
            args += ["--add-label", status]
        if len(args) > 3:
            self._run(args, mutating=True)

    def edit_labels(self, kind: str, n: int, add: list[str] = (), remove: list[str] = ()) -> None:
        args = [kind if kind == "pr" else "issue", "edit", str(n)]
        for l in add:
            args += ["--add-label", l]
        for l in remove:
            args += ["--remove-label", l]
        if len(args) > 3:
            self._run(args, mutating=True)

    def comment(self, kind: str, n: int, body: str, as_bot: bool = False) -> None:
        args = [kind if kind == "pr" else "issue", "comment", str(n), "--body-file", "-"]
        if as_bot and reviewer_token():
            try:
                self._run(args, input=body, as_reviewer=True, mutating=True)
                return
            except GhError:
                pass  # the bot cannot comment here: still say it, as the agents
        self._run(args, input=body, mutating=True)

    def protection_state(self) -> dict:
        """ok | missing | drift | unreadable, and what drifted."""
        try:
            actual = self.json(["api", f"repos/{{owner}}/{{repo}}/branches/{DEFAULT_BRANCH}/protection"])
        except GhError as e:
            text = str(e)
            if "404" in text or "not protected" in text.lower():
                return {"state": "missing", "problems": ["no branch protection"]}
            return {"state": "unreadable", "problems": [text[:200]]}
        drift = protection_drift(actual, protection_payload(actions_app_id(self)))
        return {"state": "drift" if drift else "ok", "problems": drift}

    def upsert_lint_note(self, n: int, body: str, clean: bool = False) -> None:
        """Keep one lint note per issue: edit it when it changes, add it when
        there is something to say, leave a clean issue without one alone."""
        pages = self.json(["api", "--paginate", "--slurp",
                           f"repos/{{owner}}/{{repo}}/issues/{n}/comments?per_page=100"]) or []
        notes = [c for page in pages for c in (page or [])
                 if (c.get("body") or "").lstrip().startswith(LINT_MARKER)]
        if notes:
            note = notes[-1]
            if (note.get("body") or "").strip() == body.strip():
                return
            try:
                self._run(["api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/comments/{note['id']}",
                           "-F", "body=@-"], input=body, mutating=True)
                return
            except GhError:
                pass  # someone else's note this token may not edit: add a fresh one
        if not clean or notes:
            self.comment("issue", n, body)

    def disable_automerge(self, n: int) -> None:
        self._run(["pr", "merge", str(n), "--disable-auto"], mutating=True)

    def upsert_status_issue(self, body: str) -> int | None:
        found = self.json(["issue", "list", "--label", STATUS_ISSUE, "--state", "open",
                           "--limit", "1", "--json", "number"]) or []
        if found:
            n = found[0]["number"]
            self._run(["issue", "edit", str(n), "--body-file", "-"], input=body, mutating=True)
            return n
        return self.create_issue("[pipeline] Status", body, [STATUS_ISSUE])

    def owner_login(self) -> str | None:
        try:
            return (self.json(["repo", "view", "--json", "owner"]) or {}).get("owner", {}).get("login")
        except GhError:
            return None

    def create_issue(self, title: str, body: str, labels: list[str]) -> int | None:
        args = ["issue", "create", "--title", title, "--body-file", "-"]
        for l in labels:
            args += ["--label", l]
        out = self._run(args, input=body, mutating=True)
        m = re.search(r"/issues/(\d+)", out)
        return int(m.group(1)) if m else None

    def close_issue(self, n: int, body: str | None = None) -> None:
        args = ["issue", "close", str(n)]
        if body:
            args += ["--comment", body]
        self._run(args, mutating=True)

    def rerun(self, run_id: int) -> None:
        self._run(["run", "rerun", str(run_id), "--failed"], mutating=True)

    def post_status(self, sha: str, context: str, description: str) -> None:
        self._run(["api", f"repos/{{owner}}/{{repo}}/statuses/{sha}", "-f", "state=success",
                   "-f", f"context={context}", "-f", f"description={description}"], mutating=True)

    def enable_automerge(self, n: int, head: str | None = None) -> None:
        args = ["pr", "merge", str(n), "--squash", "--delete-branch"]
        if head:
            # Merge only the commit the bot approved.
            args += ["--match-head-commit", head]
        try:
            self._run(args[:3] + ["--auto"] + args[3:], as_reviewer=True, mutating=True)
        except GhError as e:
            # GitHub refuses auto-merge on a pull request that could merge now.
            if "clean status" not in str(e):
                raise
            self._run(args, as_reviewer=True, mutating=True)


def rollup_checks(rollup: list[dict], required=REQUIRED_CHECKS) -> dict:
    """The required checks at a pull request's head.

    {"state": pending | success | failure, "failed": [{"name", "run", "attempt"}],
     "missing": [...], "judged": [{"name", "outcome", "run", "attempt"}],
     "reviewAttempts": n, ...}

    Branch protection judges the required checks and nothing else, so neither
    does this: an optional check that fails does not send a PR to a fix pass,
    and a required one that has not reported yet is pending, not green. Of
    several runs of one check -- a re-run, an `edited` event -- only the
    latest counts, and a cancelled one is pending: something superseded it or
    will. With no required checks given, every check counts.
    """
    latest: dict[tuple, dict] = {}
    for c in rollup:
        if c.get("__typename") == "StatusContext" or ("context" in c and "status" not in c):
            name = c.get("context") or ""
            key = ("", "status", name)
            when = c.get("createdAt") or ""
            s = (c.get("state") or "").upper()
            outcome = ("pending" if s in ("PENDING", "EXPECTED", "") else
                       "success" if s == "SUCCESS" else "failure")
            run = attempt = None
        else:
            name = c.get("name") or ""
            key = (c.get("workflowName") or "", "run", name)
            when = c.get("startedAt") or c.get("completedAt") or ""
            conclusion = (c.get("conclusion") or "").upper()
            if (c.get("status") or "").upper() != "COMPLETED":
                outcome = "pending"
            elif conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):
                outcome = "success"
            elif conclusion in ("CANCELLED", "STALE"):
                outcome = "pending"
            else:
                outcome = "failure"
            m = re.search(r"/actions/runs/(\d+)", c.get("detailsUrl") or "")
            run = int(m.group(1)) if m else None
            attempt = c.get("runAttempt")
        # A run with no start time yet is one just queued -- a re-run, say --
        # so it is newer than anything that has a time.
        if not when and outcome == "pending":
            when = "9999"
        if key not in latest or when >= latest[key]["when"]:
            latest[key] = {"name": name, "outcome": outcome, "when": when, "run": run,
                           "attempt": attempt, "description": c.get("description") or ""}

    attempts = 0
    answered = {"comments": 0, "attempts": 0}
    for e in latest.values():
        if e["name"] == REVIEW_STATUS:
            m = re.search(r"attempt (\d+)", e["description"])
            attempts = max(attempts, int(m.group(1)) if m else 1)
        elif e["name"] == ANSWERED_STATUS:
            for key in answered:
                m = re.search(rf"{key}=(\d+)", e["description"])
                answered[key] = int(m.group(1)) if m else 0
    judged = [e for e in latest.values() if not e["name"].startswith("pipeline/")]
    missing: list[str] = []
    if required:
        judged = [e for e in judged if e["name"] in required]
        missing = [r for r in required if r not in {e["name"] for e in judged}]
    failed = [e for e in judged if e["outcome"] == "failure"]
    pending = [e for e in judged if e["outcome"] == "pending"]
    state = "failure" if failed else "pending" if (pending or missing or not judged) else "success"
    started = max((e["when"] for e in judged if e["when"] and e["when"] != "9999"), default="") or None
    return {"state": state,
            "failed": [{"name": e["name"], "run": e["run"], "attempt": e["attempt"]} for e in failed],
            "missing": missing, "reviewAttempts": attempts, "since": started,
            "judged": sorted(({k: e[k] for k in ("name", "outcome", "run", "attempt")} for e in judged),
                             key=lambda e: e["name"]),
            "answeredComments": answered["comments"], "answeredAttempts": answered["attempts"]}


def rollup_state(rollup: list[dict], required=()) -> str:
    """pending | success | failure over a head's checks (every check, by default)."""
    return rollup_checks(rollup, required)["state"]


def remote_agent_branches() -> set[str] | None:
    """agent/ branches on origin, or None when origin could not be asked."""
    proc = subprocess.run(["git", "ls-remote", "--heads", "origin", "agent/*"],
                          capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT)
    if proc.returncode != 0:
        # An empty set here would read as "no branch": every stale claim with
        # pushed work would be reset and handed out again beside it.
        return None
    out = set()
    for line in proc.stdout.splitlines():
        parts = line.split("refs/heads/", 1)
        if len(parts) == 2:
            out.add(parts[1].strip())
    return out


def preflight(root: Path = REPO_ROOT, run=subprocess.run, which=shutil.which,
              bash=bash_path) -> list[str]:
    """What is wrong with the machine and checkout the tick runs from.

    Warnings for the report, never a block: each is something no pull request
    shows, because it lives outside the repository's history -- and every tick
    and every agent runs with it.
    """
    problems = []
    if (root / ".claude" / "settings.local.json").exists():
        problems.append(".claude/settings.local.json exists: it can widen permissions or turn off "
                        "hooks for every tick and agent -- check it holds only what you meant "
                        "(docs/AgentEnvironment.md § Permissions)")
    if not which("jq"):
        problems.append("jq not on PATH: the allowlist guard and the bash guard cannot read "
                        "their input (docs/AgentEnvironment.md)")
    if not bash():
        problems.append("bash not found: the hooks and the allowlist parser cannot run")

    def git(*args: str) -> str | None:
        proc = run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")
        return proc.stdout if proc.returncode == 0 else None

    # Untracked files count too: a new agent, skill or hook file changes what
    # runs as surely as an edit does.
    dirty = [line[3:] for line in (git("status", "--porcelain", "--untracked-files=normal") or "").splitlines()]
    if dirty:
        problems.append(f"the tick's checkout has uncommitted changes to {', '.join(dirty[:5])}"
                        f"{' ...' if len(dirty) > 5 else ''}: the tick and every agent run this "
                        "code, and no pull request shows it")
    branch = (git("rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    if branch and branch != DEFAULT_BRANCH:
        problems.append(f"the tick's checkout is on {branch}, not {DEFAULT_BRANCH}: the tick runs "
                        "that branch's pipeline, and implementer worktrees start from it")
    return problems


def sync_checkout(root: Path = REPO_ROOT, run=subprocess.run) -> list[str]:
    """Bring the tick's checkout up to the default branch on GitHub.

    The tick runs this checkout's pipeline.py, its agents read its prompts, and
    implementer worktrees start from it; left alone, it never moves past the
    day it was cloned. Fast-forward only, and only a clean checkout on the
    default branch: anything else is reported, never overwritten.
    """
    def git(*args: str):
        return run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")

    fetched = git("fetch", "--prune", "origin", DEFAULT_BRANCH)
    if fetched.returncode != 0:
        return [f"could not fetch origin/{DEFAULT_BRANCH}: {(fetched.stderr or '').strip()[:200]}"]
    branch = (git("rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip()
    dirty = (git("status", "--porcelain", "--untracked-files=no").stdout or "").strip()
    if branch != DEFAULT_BRANCH or dirty:
        return []  # preflight() reports both; neither is this function's to fix
    merged = git("merge", "--ff-only", f"origin/{DEFAULT_BRANCH}")
    if merged.returncode != 0:
        return [f"the tick's checkout cannot fast-forward to origin/{DEFAULT_BRANCH} (local "
                f"commits?): {(merged.stderr or '').strip()[:200]}"]
    return []


# --- the tick log -----------------------------------------------------------------
#
# One JSON line per `pipeline run --apply`, under the git directory -- in no
# branch and no pull request, shared by every worktree of the checkout. It is
# the daily budget's counter, `stats`' memory of agent runs, and the evidence
# when the pipeline did something strange at 3 a.m.

def pipeline_dir(root: Path = REPO_ROOT, run=subprocess.run) -> Path | None:
    proc = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root,
               capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip()) / "pipeline"


def read_tick_log(directory: Path | None, since: datetime | None = None) -> list[dict]:
    if directory is None:
        return []
    entries = []
    try:
        lines = (directory / "ticks.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        at = parse_time(e.get("at"))
        if since is None or (at and at >= since):
            entries.append(e)
    return entries


def append_tick_log(directory: Path | None, entry: dict) -> None:
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "ticks.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def runs_in(entries: list[dict]) -> int:
    return sum(len(e.get("dispatched") or []) for e in entries)


def tick_entry(out: dict, now: datetime) -> dict:
    return {
        "at": now.isoformat().replace("+00:00", "Z"),
        "paused": out.get("pause_reason") or ("paused" if out.get("paused") else ""),
        "dispatched": [f"{d['kind']} " + (f"PR #{d['pr']}" if d.get("pr") else f"#{d.get('issue') or ''}")
                       for d in out.get("dispatch", [])],
        "awaiting_human": len(out.get("awaiting_human", [])),
        "failed": [x for x in out.get("ops_done", []) + out.get("claims", []) if x.startswith("FAILED")],
        "setup_problems": len(out.get("setup_problems", [])),
    }


def status_body(out: dict, week: list[dict], now: datetime) -> str:
    """The status issue: what the owner opens on a phone to see if the pipeline lives."""
    def section(title: str, items: list[str], empty: str) -> list[str]:
        lines = [f"### {title} ({len(items)})", ""]
        lines += [f"- {x}" for x in items] if items else [f"_{empty}_"]
        return lines + [""]

    kinds: dict[str, int] = {}
    for e in week:
        for d in e.get("dispatched") or []:
            k = d.split()[0]
            kinds[k] = kinds.get(k, 0) + 1
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "<!-- Rewritten by every tick (docs/Pipeline.md § The tick). Edits here are overwritten. -->",
        f"**Last tick:** {stamp}" + (f" -- **paused:** {out['pause_reason']}" if out.get("paused") else ""),
        "",
        "If this time is old, no tick has run since: check the scheduled task and the machine.",
        "",
    ]
    lines += section("Needs you", out.get("awaiting_human", []), "nothing")
    lines += section("Dispatched in this tick", [f"{d['kind']} " + (f"PR #{d['pr']}" if d.get("pr")
                                                  else f"#{d.get('issue') or ''}")
                                                  for d in out.get("dispatch", [])], "nothing")
    lines += section("Setup problems", out.get("setup_problems", []), "none")
    lines += [f"**Now:** {len(out.get('in_flight', []))} in flight, "
              f"{len(out.get('waiting', []))} waiting, {len(out.get('deferred', []))} deferred.", ""]
    lines += [f"**Last 7 days:** {len(week)} ticks, {runs_in(week)} agent runs"
              + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) + ")" if kinds else "")
              + f", {sum(len(e.get('failed') or []) for e in week)} failed writes."]
    return "\n".join(lines) + "\n"


def compute_stats(merged: list[dict], closed: list[dict], ticks: list[dict], now: datetime,
                  days: int) -> dict:
    """What the pipeline did in the window -- the signals LESSONS.md learned to count."""
    def hours(a, b) -> float | None:
        ta, tb = parse_time(a), parse_time(b)
        return (tb - ta).total_seconds() / 3600 if ta and tb else None

    def median(xs: list[float]) -> float | None:
        xs = sorted(x for x in xs if x is not None)
        if not xs:
            return None
        mid = len(xs) // 2
        return round(xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2, 1)

    since = now - timedelta(days=days)
    prs = [q for q in merged if (parse_time(q.get("mergedAt")) or since) >= since]
    issues = [i for i in closed if (parse_time(i.get("closedAt")) or since) >= since]
    rounds = [q for q in prs if any(l.startswith((FIX_ROUND, CONFLICT_ROUND)) for l in labels_of(q))]
    kinds: dict[str, int] = {}
    for e in ticks:
        for d in e.get("dispatched") or []:
            kinds[d.split()[0]] = kinds.get(d.split()[0], 0) + 1
    return {
        "days": days,
        "merged_prs": len(prs),
        "median_hours_pr_open_to_merge": median([hours(q.get("createdAt"), q.get("mergedAt")) for q in prs]),
        "median_hours_issue_open_to_close": median([hours(i.get("createdAt"), i.get("closedAt"))
                                                    for i in issues]),
        "prs_that_needed_a_fix_pass": len(rounds),
        "fix_pass_rate": round(len(rounds) / len(prs), 2) if prs else None,
        "spec_defects": sum(1 for q in prs if SPEC_DEFECT in labels_of(q)),
        "issues_closed": len(issues),
        "issues_that_went_to_triage": sum(1 for i in issues if TRIAGED in labels_of(i)),
        "ticks": len(ticks),
        "agent_runs": kinds,
    }


# --- doctor -----------------------------------------------------------------------

def workflow_jobs(root: Path = REPO_ROOT) -> set[str]:
    """Job ids in .github/workflows/*.yml, read without a YAML library: every
    key indented two spaces under a top-level `jobs:`."""
    jobs: set[str] = set()
    for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        in_jobs = False
        for line in wf.read_text(encoding="utf-8").splitlines():
            if re.match(r"^jobs:\s*$", line):
                in_jobs = True
            elif re.match(r"^\S", line):
                in_jobs = False
            elif in_jobs:
                m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
                if m:
                    jobs.add(m.group(1))
    return jobs


def doctor_report(gh: "Gh", root: Path = REPO_ROOT, which=shutil.which,
                  run=subprocess.run) -> list[tuple[str, str, str]]:
    """(ok | warn | fail, check, detail) for everything the pipeline needs."""
    out: list[tuple[str, str, str]] = []

    def add(level: str, name: str, detail: str) -> None:
        out.append((level, name, detail))

    for tool in ("gh", "git", "jq"):
        add("ok" if which(tool) else "fail", f"tool: {tool}",
            "on PATH" if which(tool) else "missing -- docs/AgentEnvironment.md")
    add("ok" if bash_path() else "fail", "tool: bash", bash_path() or "no usable bash")

    auth = run([gh.gh, "auth", "status"], capture_output=True, text=True, encoding="utf-8")
    text = (auth.stdout or "") + (auth.stderr or "")
    if auth.returncode != 0:
        add("fail", "gh login", "not logged in -- gh auth login")
    elif "github_pat_" in text:
        add("ok", "gh login", "a fine-grained token")
    else:
        add("warn", "gh login", "not a fine-grained token: if the tick runs with this login, the "
                                "agents hold its full rights (docs/Pipeline.md § Setup, step 2)")

    login = gh.reviewer_login()
    add("ok" if login else "fail", "reviewer bot",
        f"token works, login {login}" if login else f"no working token at {reviewer_token_path()}")

    try:
        repo = gh.json(["api", "repos/{owner}/{repo}"]) or {}
    except GhError as e:
        repo = {}
        add("fail", "repository", str(e)[:200])
    if repo:
        bad = [k for k, want in (("allow_auto_merge", True), ("delete_branch_on_merge", True),
                                 ("allow_squash_merge", True), ("allow_merge_commit", False),
                                 ("allow_rebase_merge", False)) if repo.get(k) != want]
        add("warn" if bad else "ok", "repository settings",
            f"differ from setup-repo: {', '.join(bad)}" if bad else "as setup-repo sets them")
        if (repo.get("owner") or {}).get("type") == "Organization" and not CONFIG.get("agent_login"):
            add("fail", "agent_login", "the repository belongs to an organisation: set agent_login "
                                       "in .claude/pipeline/config.json")
        if login:
            try:
                perm = (gh.json(["api", f"repos/{{owner}}/{{repo}}/collaborators/{login}/permission"])
                        or {}).get("permission")
            except GhError:
                perm = None
            add("ok" if perm in ("admin", "maintain", "write") else "fail", "reviewer bot access",
                f"{perm}" if perm else "not a collaborator -- run setup-repo")

    state = gh.protection_state()
    add({"ok": "ok", "unreadable": "warn"}.get(state["state"], "fail"),
        f"branch protection on {DEFAULT_BRANCH}",
        state["state"] + (": " + "; ".join(state["problems"]) if state["problems"] else ""))

    try:
        sha = gh.head_sha(DEFAULT_BRANCH)
        jobs_seen = sum(1 for r in (gh.ci_rollup(sha) if sha else []) if r.get("__typename") == "CheckRun")
        add("ok", "CI readable", f"{jobs_seen} jobs at the head of {DEFAULT_BRANCH}, read through the Actions API")
    except GhError as e:
        add("fail", "CI readable", f"{str(e)[:120]} -- the token needs Actions and Commit statuses "
                                   "(docs/Pipeline.md § Setup, step 2)")

    try:
        wf = gh.json(["api", "repos/{owner}/{repo}/actions/permissions/workflow"]) or {}
        ok = wf.get("default_workflow_permissions") == "read" and not wf.get("can_approve_pull_request_reviews")
        add("ok" if ok else "fail", "Actions token", "read-only, cannot approve" if ok
            else f"{wf} -- a workflow could approve a PR; run setup-repo")
    except GhError as e:
        add("warn", "Actions token", f"cannot read: {str(e)[:120]}")

    have = labels_of({"labels": [{"name": n} for n in gh.labels()]})
    missing = sorted(set(LABELS) - have)
    add("warn" if missing else "ok", "labels",
        f"missing: {', '.join(missing)} -- the next tick creates them" if missing else "all present")

    jobs = workflow_jobs(root)
    absent = [c for c in REQUIRED_CHECKS if c not in jobs]
    add("fail" if absent else "ok", "required checks",
        f"no workflow job named {', '.join(absent)}: those checks never report and nothing merges"
        if absent else f"{', '.join(REQUIRED_CHECKS)} all exist as workflow jobs")

    settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    rules = settings.get("permissions", {}).get("allow", [])
    test = str(CONFIG["test_command"]).split()[0]
    add("ok" if any(test in r for r in rules) else "fail", "test command allowed",
        f"{test} is in .claude/settings.json" if any(test in r for r in rules)
        else f"no allow rule names {test}: unattended, every test run is refused")

    for problem in preflight(root, run=run, which=which):
        add("warn", "checkout", problem)
    wts = run(["git", "worktree", "list", "--porcelain"], cwd=root, capture_output=True, text=True,
              encoding="utf-8")
    count = sum(1 for l in (wts.stdout or "").splitlines() if l.startswith("worktree ")) - 1
    add("warn" if count > 10 else "ok", "worktrees",
        f"{count} besides the main checkout" + (" -- clean up (github_clean-branches skill)" if count > 10 else ""))
    for w in CONFIG_WARNINGS:
        add("warn", "config", w)
    return out


def make_lint_fn(gh: Gh, open_numbers: set[int]):
    cache: dict[int, str | None] = {}

    def state(n: int) -> str | None:
        if n in open_numbers:
            return "open"
        if n not in cache:
            cache[n] = gh.issue_state(n)
        if cache[n] == "unknown":
            return cache.pop(n)  # a failed lookup is asked again next time
        return cache[n]

    def lint(issue: dict) -> LintResult:
        return lint_body(issue["number"], issue.get("body", ""), parse_allowlist, state)
    return lint


def apply_ops(gh: Gh, plan: Plan, lint_fn) -> list[str]:
    """The plan's bookkeeping, in order. A write that failed or found the item
    moved skips the comments that would have explained it."""
    done = []
    failed: set[tuple] = set()
    for op in plan.ops:
        kind = op["op"]
        n = op.get("number")
        target = (op.get("kind", "issue") if kind != "set-status" else "issue", n)
        if kind == "comment" and target in failed:
            done.append(f"SKIPPED comment {n}: the write it explains did not happen")
            continue
        try:
            if kind == "set-status":
                gh.set_status(n, op["status"], op.get("expect", ANY))
            elif kind == "lint":
                issue = gh.issue(n)
                result = lint_fn(issue)
                gh.set_status(n, result.status, expect=None)
                gh.upsert_lint_note(n, lint_comment(n, result),
                                    clean=not (result.problems or result.warnings))
            elif kind == "lint-note":
                gh.upsert_lint_note(n, op["body"], clean=bool(op.get("clean")))
            elif kind == "add-label":
                gh.edit_labels(op["kind"], n, add=[op["label"]])
            elif kind == "remove-label":
                gh.edit_labels(op["kind"], n, remove=[op["label"]])
            elif kind == "comment":
                gh.comment(op["kind"], n, op["body"], as_bot=bool(op.get("as_bot")))
            elif kind == "post-status":
                gh.post_status(op["sha"], op["context"], op["description"])
            elif kind == "enable-automerge":
                gh.enable_automerge(n, op.get("head"))
            elif kind == "disable-automerge":
                gh.disable_automerge(n)
            elif kind == "rerun":
                gh.rerun(op["run"])
            elif kind == "create-issue":
                made = gh.create_issue(op["title"], op["body"], op["labels"])
                n = made or n
            elif kind == "close-issue":
                gh.close_issue(n, op.get("body"))
            done.append(f"{kind} {n if n is not None else ''}: "
                        f"{op.get('status') or op.get('label') or op.get('run') or ''} "
                        f"{op.get('why', '')}".replace("  ", " ").strip())
        except StaleState as e:
            failed.add(target)
            done.append(f"SKIPPED {kind} {n}: {e}")
        except GhError as e:
            failed.add(target)
            done.append(f"FAILED {kind} {n}: {e}")
    return done


def claim(gh: Gh, item: dict) -> str:
    """Take a dispatch item before its agent starts, so no other tick hands it out.

    Each claim re-reads the item first and refuses one that another tick,
    agent or person has taken since the survey (StaleState).
    """
    kind = item["kind"]
    if kind == "implement":
        n = item["issue"]
        labels = gh.labels_now("issue", n)
        if labels & {WORKING, HUMAN_HOLDS}:
            raise StaleState(f"#{n} is held ({', '.join(sorted(labels & {WORKING, HUMAN_HOLDS}))})")
        if item.get("resume"):
            if status_of(labels) != IN_PROGRESS:
                raise StaleState(f"#{n} is {status_of(labels)} now, not {IN_PROGRESS}")
            return f"resuming #{n} on {item.get('branch')}"
        gh.set_status(n, IN_PROGRESS, expect=READY)
        return f"claimed #{n} ({IN_PROGRESS})"
    if kind in ("review", "fix"):
        p = item["pr"]
        if WORKING in gh.labels_now("pr", p):
            raise StaleState(f"PR #{p} is already held")
        add = [WORKING]
        if kind == "review":
            add.append(REVIEWING)
            if item.get("head"):
                # Counted per head commit: decide() hands a PR to the owner
                # once reviews there keep ending without a verdict.
                gh.post_status(item["head"], REVIEW_STATUS, f"attempt {item.get('attempt', 1)}")
        elif item.get("round_label"):
            add.append(item["round_label"])
        gh.edit_labels("pr", p, add=add)
        return f"claimed PR #{p} ({', '.join(add)})"
    if kind == "triage" or (kind == "plan" and item.get("issue")):
        n = item["issue"]
        if WORKING in gh.labels_now("issue", n):
            raise StaleState(f"#{n} is already held")
        gh.edit_labels("issue", n, add=[WORKING])
        return f"claimed #{n} ({WORKING})"
    if kind == "plan":
        # Roadmap planning has no issue of its own, so it gets one: the claim
        # every other tick sees, closed by the planner when it is done.
        if gh.json(["issue", "list", "--label", PLANNING, "--state", "open", "--limit", "1",
                    "--json", "number"]):
            raise StaleState("a roadmap planner is already running")
        n = gh.create_issue("[pipeline] Planning the roadmap",
                            "**Pipeline:** the roadmap planner is running. It closes this issue "
                            "when it is done; if it does not, the next tick closes it after "
                            f"{CONFIG['limits']['stale_working_hours']} hours and plans again.",
                            [PLANNING, WORKING])
        item["tracking"] = n
        return f"opened #{n} ({PLANNING})" if n else "opened a planning issue (dry run)"
    return "nothing to claim"


# --- commands --------------------------------------------------------------------

def cmd_run(args) -> int:
    gh = Gh(dry_run=not args.apply)
    # Before the survey, so this tick's agents start from what is on GitHub.
    # The running pipeline.py is already loaded: an update to it counts from
    # the next tick.
    synced = sync_checkout() if args.apply else []
    now = datetime.now(timezone.utc)
    log_dir = pipeline_dir()
    issues = gh.open_issues()
    prs = gh.open_prs()
    # A file only the owner's machine has: an agent that can reach GitHub
    # cannot take it away, as it could the label.
    kill_file = log_dir is not None and (log_dir / "pause").exists()
    snap = {
        "now": now,
        "issues": issues,
        "prs": prs,
        "reviewer_login": gh.reviewer_login(),
        "remote_branches": remote_agent_branches(),
        "paused": gh.paused() or (f"the local kill file {log_dir / 'pause'}" if kill_file else False),
        "truncated": len(issues) >= ISSUE_LIMIT or len(prs) >= PR_LIMIT,
        "main": gh.main_health(),
        "owner_login": gh.owner_login(),
        "protection": gh.protection_state(),
        "require_protection": bool(CONFIG.get("require_protection", True)),
        "runs_today": runs_in(read_tick_log(log_dir, now - timedelta(hours=24))),
    }
    lint_fn = make_lint_fn(gh, {i["number"] for i in issues})
    plan = decide(snap, parse_allowlist, lint_fn)
    out = plan.to_json()
    out["setup_problems"] = out["setup_problems"] + synced + preflight() + CONFIG_WARNINGS
    if args.apply:
        # Paused or not: the status issue needs its label too.
        made = gh.ensure_labels()
        if made:
            out["labels_created"] = made
    if args.apply and plan.paused:
        out["ops_done"] = apply_ops(gh, plan, lint_fn)     # only turning auto-merge off
    if args.apply and not plan.paused:
        out["ops_done"] = apply_ops(gh, plan, lint_fn)
        out["claims"] = []
        for item in plan.dispatch:
            try:
                out["claims"].append(claim(gh, item))
            except StaleState as e:
                out["claims"].append(f"SKIPPED {item['kind']} {item.get('pr') or item.get('issue') or ''}: {e}")
                item["claim_failed"] = True
            except GhError as e:
                out["claims"].append(f"FAILED claim {item}: {e}")
                item["claim_failed"] = True
        out["dispatch"] = [d for d in plan.dispatch if not d.get("claim_failed")]
    for d in out["dispatch"]:
        d.pop("must", None)
    if args.apply:
        append_tick_log(log_dir, tick_entry(out, now))
        try:
            week = read_tick_log(log_dir, now - timedelta(days=7))
            out["status_issue"] = gh.upsert_status_issue(status_body(out, week, now))
        except GhError as e:
            out["status_issue"] = f"FAILED: {e}"
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_doctor(args) -> int:
    report = doctor_report(Gh())
    width = max(len(name) for _, name, _ in report)
    for level, name, detail in report:
        print(f"{level.upper():4}  {name.ljust(width)}  {detail}")
    fails = sum(1 for level, _, _ in report if level == "fail")
    print(f"\n{fails} failing, {sum(1 for l, _, _ in report if l == 'warn')} warnings")
    return 1 if fails else 0


CHECKS_EXIT = {"success": 0, "failure": 1, "pending": 8}   # the exit codes of `gh pr checks`


def pr_checks(gh: "Gh", number: int, sha: str | None = None) -> dict:
    """The required checks at a pull request's head (or at `sha`), read the
    way the tick reads them: `gh pr checks` needs a permission a fine-grained
    token cannot have (Gh.ci_rollup)."""
    if not sha:
        sha = (gh.json(["pr", "view", str(number), "--json", "headRefOid"]) or {}).get("headRefOid") or ""
    return {"sha": sha, **rollup_checks(gh.ci_rollup(sha) if sha else [], REQUIRED_CHECKS)}


def checks_lines(number: int, result: dict) -> list[str]:
    width = max((len(c) for c in REQUIRED_CHECKS), default=8)
    lines = [f"PR #{number} at {result['sha'][:10] or '?'}: {result['state']}"]
    for e in result["judged"]:
        where = (f"run {e['run']}" + (f" (attempt {e['attempt']})" if e["attempt"] else "")) if e["run"] else ""
        lines.append(f"  {e['name'].ljust(width)}  {e['outcome']:8} {where}".rstrip())
    for name in result["missing"]:
        lines.append(f"  {name.ljust(width)}  {'pending':8} not reported yet")
    for run in sorted({e["run"] for e in result["judged"] if e["outcome"] == "failure" and e["run"]}):
        lines.append(f"logs: gh run view {run} --log-failed")
    return lines


def cmd_checks(args) -> int:
    gh = Gh()
    deadline = time.monotonic() + args.timeout
    while True:
        result = pr_checks(gh, args.number, args.sha)
        if result["state"] != "pending" or not args.wait or time.monotonic() >= deadline:
            break
        time.sleep(min(args.interval, max(1.0, deadline - time.monotonic())))
    print("\n".join(checks_lines(args.number, result)))
    return CHECKS_EXIT[result["state"]]


def cmd_stats(args) -> int:
    gh = Gh()
    now = datetime.now(timezone.utc)
    merged = gh.json(["pr", "list", "--state", "merged", "--limit", "300", "--json",
                      "number,labels,createdAt,mergedAt"]) or []
    closed = gh.json(["issue", "list", "--state", "closed", "--label", AGENT_TASK, "--limit", "300",
                      "--json", "number,labels,createdAt,closedAt"]) or []
    ticks = read_tick_log(pipeline_dir(), now - timedelta(days=args.days))
    print(json.dumps(compute_stats(merged, closed, ticks, now, args.days), indent=2))
    return 0


def cmd_lint(args) -> int:
    gh = Gh(dry_run=not args.apply)
    issue = gh.issue(args.issue)
    labels = labels_of(issue)
    if AGENT_TASK not in labels or labels & {ASSET, HUMAN_DECISION}:
        print(json.dumps({"issue": args.issue, "skipped": "not an agent-task issue lint owns"}))
        return 0
    status = status_of(labels)
    result = make_lint_fn(gh, set())(issue)
    report = {"issue": args.issue, "current": status, "lint": result.status,
              "problems": result.problems, "warnings": result.warnings,
              "blockers_open": result.blockers_open}
    if status not in LINT_OWNED:
        report["skipped"] = f"status {status} is not lint's to change"
    elif WORKING in labels:
        report["skipped"] = f"{WORKING}: an agent is editing it; the tick lints it when it is done"
    elif args.apply and result.status != status:
        gh.ensure_labels()
        try:
            gh.set_status(args.issue, result.status, expect=status)
        except StaleState as e:
            report["skipped"] = str(e)
        else:
            report["applied"] = True
    if args.apply and status in LINT_OWNED and WORKING not in labels and "skipped" not in report:
        # On every edit, changed status or not: the note says what the lint sees now.
        gh.upsert_lint_note(args.issue, lint_comment(args.issue, result),
                            clean=not (result.problems or result.warnings))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_relint(args) -> int:
    gh = Gh(dry_run=not args.apply)
    issues = gh.open_issues()
    lint_fn = make_lint_fn(gh, {i["number"] for i in issues})
    changed = []
    for issue in issues:
        labels = labels_of(issue)
        if AGENT_TASK not in labels or labels & {ASSET, HUMAN_DECISION, IDEA} or WORKING in labels:
            continue
        status = status_of(labels)
        if status not in LINT_OWNED:
            continue
        result = lint_fn(issue)
        if result.status != status:
            changed.append({"issue": issue["number"], "from": status, "to": result.status})
            if args.apply:
                try:
                    gh.set_status(issue["number"], result.status, expect=status)
                except StaleState:
                    changed[-1]["skipped"] = "moved since the survey"
                    continue
                gh.upsert_lint_note(issue["number"], lint_comment(issue["number"], result),
                                    clean=not (result.problems or result.warnings))
    print(json.dumps({"changed": changed}, indent=2, ensure_ascii=False))
    return 0


def cmd_claim(args) -> int:
    """Claim by hand: `claim issue N [--interactive]`, `claim pr N [--round K]`."""
    gh = Gh()
    if args.kind == "issue":
        gh.ensure_labels()
        gh.set_status(args.number, IN_PROGRESS)
        if args.interactive:
            # The owner is on it: the tick neither resets it when it goes quiet
            # nor sends a fix pass to its pull request.
            gh.edit_labels("issue", args.number, add=[HUMAN_HOLDS])
        print(f"claimed #{args.number} ({IN_PROGRESS}{', ' + HUMAN_HOLDS if args.interactive else ''})")
        return 0
    add = [WORKING] + ([f"{FIX_ROUND}{args.round}"] if args.round else [REVIEWING])
    gh.edit_labels("pr", args.number, add=add)
    print(f"claimed PR #{args.number} ({', '.join(add)})")
    return 0


def cmd_release(args) -> int:
    """Drop a claim. `--round-label` also refunds a fix or conflict round whose
    agent never started (the tick passes it when an agent type is missing)."""
    remove = [WORKING]
    if args.kind == "pr":
        remove.append(REVIEWING)
        if args.round_label:
            remove.append(args.round_label)
    elif args.hold:
        # Only on request: triage, the planner and the tick release issues
        # too, and must never take the owner's hold away with their own.
        remove.append(HUMAN_HOLDS)
    Gh().edit_labels(args.kind, args.number, remove=remove)
    print(f"released {args.kind} {args.number} ({', '.join(remove)})")
    return 0


def cmd_set_status(args) -> int:
    gh = Gh()
    status = None if args.status == "none" else args.status
    if status and status not in STATUSES:
        print(f"unknown status {status}; one of: {', '.join(STATUSES)}, none", file=sys.stderr)
        return 2
    gh.ensure_labels()
    gh.set_status(args.issue, status)
    print(f"#{args.issue} -> {status}")
    return 0


def cmd_check_pr_body(args) -> int:
    body = sys.stdin.read()
    missing = check_pr_body(body)
    if missing:
        print("The PR description does not follow .github/pull_request_template.md:")
        for m in missing:
            print(f"  - {m}")
        print("Edit the description; this check re-runs on every edit.")
        return 1
    print("PR description has every required section.")
    return 0


def protection_payload(app_id: int = GITHUB_ACTIONS_APP_ID) -> dict:
    return {
        "required_status_checks": {
            "strict": False,
            "checks": [{"context": c, "app_id": app_id} for c in REQUIRED_CHECKS],
        },
        # Agents run as the owner's account, which is an admin. Without this,
        # every rule below is advisory for exactly the actor it exists to bind.
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 1,
            # Off on purpose: the reviewer pushes mechanical fixes and then
            # approves, and GitHub would refuse an approval of one's own push.
            "require_last_push_approval": False,
        },
        "restrictions": None,
        "required_linear_history": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_conversation_resolution": False,
    }


def _enabled(value) -> bool | None:
    """Protection fields come back as `{"enabled": x}` but are PUT as plain `x`."""
    return value.get("enabled") if isinstance(value, dict) else value


def protection_drift(actual: dict | None, wanted: dict) -> list[str]:
    """Where the branch protection GitHub reports differs from what setup-repo sets."""
    if not actual:
        return ["no branch protection"]
    drift = []
    for key in ("enforce_admins", "required_linear_history", "allow_force_pushes", "allow_deletions"):
        if _enabled(actual.get(key)) != wanted[key]:
            drift.append(f"{key} is {_enabled(actual.get(key))}, want {wanted[key]}")
    reviews_have = actual.get("required_pull_request_reviews") or {}
    for key, want in wanted["required_pull_request_reviews"].items():
        if reviews_have.get(key, False if isinstance(want, bool) else None) != want:
            drift.append(f"required_pull_request_reviews.{key} is {reviews_have.get(key)}, want {want}")
    checks_have = {(c.get("context"), c.get("app_id"))
                   for c in (actual.get("required_status_checks") or {}).get("checks") or []}
    checks_want = {(c["context"], c["app_id"]) for c in wanted["required_status_checks"]["checks"]}
    for context, app in sorted(checks_want - checks_have, key=str):
        drift.append(f"required check {context} (app {app}) missing")
    for context, app in sorted(checks_have - checks_want, key=str):
        drift.append(f"required check {context} (app {app}) not in required_checks")
    return drift


def actions_app_id(gh: Gh) -> int:
    """GitHub Actions' app id. 15368 on github.com; it differs on GHES and GHE.com."""
    try:
        return int((gh.json(["api", "apps/github-actions"]) or {}).get("id") or GITHUB_ACTIONS_APP_ID)
    except (GhError, TypeError, ValueError):
        return GITHUB_ACTIONS_APP_ID


def cmd_setup_repo(args) -> int:
    gh = Gh(dry_run=args.dry_run)
    report = []
    made = gh.ensure_labels(only_missing=False)
    report.append(f"labels created/updated: {len(made)}")

    settings = ["api", "-X", "PATCH", "repos/{owner}/{repo}",
                "-F", "allow_auto_merge=true", "-F", "delete_branch_on_merge=true",
                "-F", "allow_squash_merge=true", "-F", "allow_merge_commit=false",
                "-F", "allow_rebase_merge=false"]
    gh._run(settings, mutating=True)
    report.append("repo: auto-merge on, delete branch on merge on, squash only")

    # A workflow token that may approve pull requests is a second approver
    # that is not the author: code a PR runs in CI could approve that PR.
    try:
        gh._run(["api", "-X", "PUT", "repos/{owner}/{repo}/actions/permissions/workflow",
                 "-f", "default_workflow_permissions=read",
                 "-F", "can_approve_pull_request_reviews=false"], mutating=True)
        report.append("actions: workflow token read-only, may not approve pull requests")
    except GhError as e:
        report.append(f"actions: could NOT restrict the workflow token ({e}) -- an organisation "
                      "policy may set it; make sure it is read-only and cannot approve")
    # A fork's pull request runs its own ci.yml; the agents' token limits what
    # agents push, not what outsiders do. Hold every outsider's run for approval.
    try:
        gh._run(["api", "-X", "PUT", "repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval",
                 "-f", "approval_policy=all_external_contributors"], mutating=True)
        report.append("actions: workflow runs from outside contributors' forks wait for approval")
    except GhError as e:
        report.append(f"actions: could not require approval for fork workflow runs ({e}) -- "
                      "private repositories do not run fork workflows unless you allow it")

    app_id = actions_app_id(gh)
    payload = protection_payload(app_id)
    gh._run(["api", "-X", "PUT", f"repos/{{owner}}/{{repo}}/branches/{DEFAULT_BRANCH}/protection",
             "--input", "-"], input=json.dumps(payload), mutating=True)
    report.append(f"{DEFAULT_BRANCH} protected: checks {', '.join(REQUIRED_CHECKS)} "
                  f"(GitHub Actions, app {app_id}), 1 approval, stale approvals dismissed, "
                  "admins included, linear history")
    if not args.dry_run:
        try:
            actual = gh.json(["api", f"repos/{{owner}}/{{repo}}/branches/{DEFAULT_BRANCH}/protection"])
        except GhError as e:
            actual = None
            report.append(f"protection: could not read it back ({e})")
        for d in protection_drift(actual, payload):
            report.append(f"protection DRIFT: {d}")

    login = gh.reviewer_login()
    if not login:
        report.append(f"reviewer bot: NO TOKEN at {reviewer_token_path()} -- create it per "
                      "docs/Pipeline.md § Setup, then re-run")
    else:
        perm = None
        try:
            perm = (gh.json(["api", f"repos/{{owner}}/{{repo}}/collaborators/{login}/permission"])
                    or {}).get("permission")
        except GhError:
            pass
        if perm in ("admin", "maintain", "write"):
            report.append(f"reviewer bot: {login} has {perm} access")
        else:
            gh._run(["api", "-X", "PUT", f"repos/{{owner}}/{{repo}}/collaborators/{login}",
                     "-f", "permission=push"], mutating=True)
            report.append(f"reviewer bot: invited {login} with write access")
            if not args.dry_run:
                this_repo = (gh.json(["repo", "view", "--json", "nameWithOwner"]) or {}).get("nameWithOwner", "")
                invites = gh.json(["api", "user/repository_invitations"], as_reviewer=True) or []
                for inv in invites:
                    # Only this repository's: accepting every pending invitation
                    # would hand a token on this machine to repositories nobody
                    # meant it for.
                    name = (inv.get("repository") or {}).get("full_name", "")
                    if name.lower() != this_repo.lower():
                        report.append(f"reviewer bot: left invitation {inv['id']} to {name} alone")
                        continue
                    gh._run(["api", "-X", "PATCH", f"user/repository_invitations/{inv['id']}"],
                            as_reviewer=True, mutating=True)
                    report.append(f"reviewer bot: accepted invitation {inv['id']} to {name}")
    for line in report + gh.log:
        print(line)
    return 0


def cmd_config(args) -> int:
    if args.key == "reviewer_token_file":
        # Forward slashes: readable by Git Bash and by Windows alike.
        print(reviewer_token_path().as_posix())
        return 0
    value = CONFIG
    for part in args.key.split("."):
        if not isinstance(value, dict) or part not in value:
            print(f"no config key {args.key}", file=sys.stderr)
            return 2
        value = value[part]
    print(value if isinstance(value, str) else json.dumps(value))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(prog="pipeline.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("run"); s.add_argument("--apply", action="store_true"); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("lint"); s.add_argument("issue", type=int)
    s.add_argument("--apply", action="store_true"); s.set_defaults(fn=cmd_lint)
    s = sub.add_parser("relint"); s.add_argument("--apply", action="store_true"); s.set_defaults(fn=cmd_relint)
    s = sub.add_parser("claim"); s.add_argument("kind", choices=["issue", "pr"])
    s.add_argument("number", type=int); s.add_argument("--round", type=int)
    s.add_argument("--interactive", action="store_true"); s.set_defaults(fn=cmd_claim)
    s = sub.add_parser("release"); s.add_argument("kind", choices=["issue", "pr"])
    s.add_argument("number", type=int); s.add_argument("--round-label")
    s.add_argument("--hold", action="store_true", help="also hand an owner-held issue back")
    s.set_defaults(fn=cmd_release)
    s = sub.add_parser("set-status"); s.add_argument("issue", type=int); s.add_argument("status")
    s.set_defaults(fn=cmd_set_status)
    s = sub.add_parser("check-pr-body"); s.set_defaults(fn=cmd_check_pr_body)
    s = sub.add_parser("setup-repo"); s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_setup_repo)
    s = sub.add_parser("checks", help="the required checks at a PR's head: exit 0 green, 1 red, 8 pending")
    s.add_argument("number", type=int)
    s.add_argument("--sha", help="judge this commit instead of the PR's current head")
    s.add_argument("--wait", action="store_true", help="poll until no check is pending, or --timeout")
    s.add_argument("--timeout", type=float, default=540, help="seconds, default 540 (under the 10-minute Bash cap)")
    s.add_argument("--interval", type=float, default=30); s.set_defaults(fn=cmd_checks)
    s = sub.add_parser("doctor"); s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("stats"); s.add_argument("--days", type=int, default=30); s.set_defaults(fn=cmd_stats)
    s = sub.add_parser("config"); s.add_argument("key"); s.set_defaults(fn=cmd_config)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except GhError as e:
        print(f"pipeline: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
