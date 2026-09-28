#!/usr/bin/env python3
"""The coding pipeline's deterministic half.

Agents make judgement calls; this file makes every decision that does not need
one: which issue is ready, which pull request needs a review, a fix or nothing,
and which labels say so. docs/Pipeline.md is the contract; this is its
executable form. Everything project-specific lives in config.json beside it.

    pipeline.py run [--apply]           one tick: bookkeeping + the dispatch list (JSON)
    pipeline.py lint ISSUE [--apply]    readiness of one agent-task issue
    pipeline.py relint [--apply]        lint every open issue whose status lint owns
    pipeline.py claim issue|pr N [--round K]
    pipeline.py release issue|pr N      drop pipeline:working when an agent finishes
    pipeline.py set-status ISSUE STATUS set one status label (or "none"), dropping the rest
    pipeline.py check-pr-body           PR description shape, body on stdin (CI)
    pipeline.py setup-repo [--dry-run]  labels, repo settings, branch protection, bot access
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
    "limits": {
        "max_parallel_implement": 3,
        "max_parallel_review": 2,
        "max_fix_rounds": 2,
        "stale_in_progress_hours": 6,
        "stale_working_hours": 4,
    },
}


def load_config(path: Path = HERE / "config.json") -> dict:
    """config.json over the defaults; a missing file means all defaults."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    limits = {**cfg["limits"], **user.get("limits", {})}
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
SPEC_DEFECT = "spec-defect"
TRIAGED = "triaged"
FIX_ROUND = "fix-round-"

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
    SPEC_DEFECT: ("e99695", "Review handed this back because the issue was underspecified"),
    TRIAGED: ("d4c5f9", "Triage has answered this once; a second escalation goes to a human"),
    FIX_ROUND + "1": ("f9d0c4", "One fix pass has been dispatched for this pull request"),
    FIX_ROUND + "2": ("f9d0c4", "Two fix passes have been dispatched; the next failure goes to a human"),
    AGENT_TASK: ("0052cc", "A single seam, sized for one coding agent and one branch"),
    ASSET: ("fef2c0", "Art or audio deliverable; never auto-assigned to an agent"),
    HUMAN_DECISION: ("b60205", "Needs a human judgement call; never handed to an agent"),
}

# --- limits ------------------------------------------------------------------

_L = CONFIG["limits"]
MAX_FIX_ROUNDS = int(_L["max_fix_rounds"])
MAX_PARALLEL_IMPLEMENT = int(_L["max_parallel_implement"])
MAX_PARALLEL_REVIEW = int(_L["max_parallel_review"])
MAX_COMMENT_ONLY_REVIEWS = 2
STALE_IN_PROGRESS = timedelta(hours=float(_L["stale_in_progress_hours"]))
STALE_WORKING = timedelta(hours=float(_L["stale_working_hours"]))

# Required checks, pinned to the GitHub Actions app so that a commit status
# posted by hand under the same name cannot satisfy them. 15368 is GitHub
# Actions' app id on github.com.
REQUIRED_CHECKS = tuple(CONFIG["required_checks"])
DEFAULT_BRANCH = CONFIG["default_branch"]
GITHUB_ACTIONS_APP_ID = 15368

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
    for s in STATUSES:
        if s in labels:
            return s
    return None


def fix_rounds(labels: set[str]) -> int:
    rounds = [int(l[len(FIX_ROUND):]) for l in labels
              if l.startswith(FIX_ROUND) and l[len(FIX_ROUND):].isdigit()]
    return max(rounds, default=0)


def branch_issue(branch: str) -> int | None:
    m = AGENT_BRANCH.match(branch or "")
    return int(m.group(1)) if m else None


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def split_sections(body: str) -> dict[str, str]:
    """Level-2 headings to their text, keyed lower-case. `###` is content."""
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in strip_comments(body or "").splitlines():
        m = re.match(r"^##\s+(?!#)(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


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

def bash_path() -> str | None:
    # On Windows, PATH can put WSL's System32\bash.exe ahead of Git Bash. The
    # parser needs a bash that sees this checkout's paths, so prefer Git's.
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe",
                          r"C:\Program Files\Git\usr\bin\bash.exe"):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


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
    return [p for p in proc.stdout.splitlines() if p.strip()]


def _is_dir_entry(entry: str) -> bool:
    return entry.endswith("/") or "." not in entry.rstrip("/").rsplit("/", 1)[-1]


def paths_overlap(a: str, b: str) -> bool:
    """Could two allowlist entries name the same file? Conservative on globs."""
    a, b = a.strip().lstrip("./"), b.strip().lstrip("./")
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        if any(c in x for c in "*?"):
            prefix = re.split(r"[*?]", x, maxsplit=1)[0]
            if y.startswith(prefix) or prefix.startswith(y.rstrip("/") + "/"):
                return True
        elif _is_dir_entry(x) and y.startswith(x.rstrip("/") + "/"):
            return True
    return False


def scopes_overlap(xs: list[str], ys: list[str]) -> bool:
    return any(paths_overlap(x, y) for x in xs for y in ys)


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


def lint_body(number: int, body: str, allowlist_fn, state_fn) -> LintResult:
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
            if not allowlist_fn(body):
                r.problems.append("`## Files in scope` names no parseable path")
        except RuntimeError as e:
            r.warnings.append(f"allowlist not checked: {e}")

    for sec in ("Interface", "Context"):
        text = find_section(sections, sec) or ""
        if re.search(r"\bL\d+(\s*[–-]\s*\d+)?\b", text):
            r.warnings.append(f"`## {sec}` anchors on line numbers, which rot; "
                              "prefer symbol names")

    for n in blocked_by_numbers(find_section(sections, "Blocked by"), number):
        if state_fn(n) == "open":
            r.blockers_open.append(n)
    return r


def lint_comment(number: int, result: LintResult) -> str:
    lines = ["<!-- issue-lint -->", f"**issue-lint: #{number} is not ready to hand out.**", ""]
    lines += [f"- {p}" for p in result.problems]
    if result.warnings:
        lines += ["", "Also worth fixing:"] + [f"- {w}" for w in result.warnings]
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
    setup_problems: list[str] = field(default_factory=list)
    ops: list[dict] = field(default_factory=list)        # bookkeeping, applied in order
    dispatch: list[dict] = field(default_factory=list)   # agents to spawn
    waiting: list[str] = field(default_factory=list)
    in_flight: list[str] = field(default_factory=list)
    awaiting_human: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {k: getattr(self, k) for k in (
            "paused", "setup_problems", "ops", "dispatch",
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


def decide(snap: dict, allowlist_fn, lint_fn) -> Plan:
    """One tick's plan from a snapshot. Pure: no GitHub, no clock but snap['now']."""
    plan = Plan()
    now: datetime = snap["now"]
    issues: list[dict] = snap["issues"]
    prs: list[dict] = snap["prs"]
    reviewer = snap.get("reviewer_login")
    remote_branches: set[str] = snap.get("remote_branches", set())

    if any(PAUSE in labels_of(i) for i in issues):
        plan.paused = True
        return plan
    if not reviewer:
        plan.setup_problems.append(
            f"reviewer bot login unknown: no token at {CONFIG['reviewer_token_file']} "
            "and no PIPELINE_REVIEWER_LOGIN -- reviews and auto-merge are skipped")

    def stale(item: dict, age: timedelta) -> bool:
        t = parse_time(item.get("updatedAt"))
        return t is None or now - t > age

    # ---- pull requests ----
    open_pr_by_issue: dict[int, dict] = {}
    reviews_planned = 0
    for pr in sorted(prs, key=lambda p: p["number"]):
        n = pr["number"]
        tag = f"PR #{n}"
        issue_n = branch_issue(pr.get("headRefName", "")) or next(iter(pr.get("closingIssues") or []), None)
        if issue_n:
            open_pr_by_issue[issue_n] = pr
        labels = labels_of(pr)
        if pr.get("isDraft"):
            plan.waiting.append(f"{tag}: draft")
            continue
        if NEEDS_HUMAN in labels:
            plan.awaiting_human.append(f"{tag}: {NEEDS_HUMAN}")
            continue
        if WORKING in labels:
            if not stale(pr, STALE_WORKING):
                plan.in_flight.append(f"{tag}: an agent holds it")
                continue
            plan.ops.append({"op": "remove-label", "kind": "pr", "number": n, "label": WORKING,
                             "why": "stale pipeline:working"})

        rounds = fix_rounds(labels)
        base = {"pr": n, "issue": issue_n, "branch": pr.get("headRefName")}

        def fix(reason: str, counts: bool = True):
            if counts and rounds >= MAX_FIX_ROUNDS:
                plan.ops.append({"op": "add-label", "kind": "pr", "number": n, "label": NEEDS_HUMAN,
                                 "why": f"{reason} after {rounds} fix rounds"})
                plan.ops.append({"op": "comment", "kind": "pr", "number": n, "body":
                                 f"**Pipeline:** {reason} again after {rounds} fix rounds. "
                                 f"Handing this to a human (`{NEEDS_HUMAN}`)."})
                plan.awaiting_human.append(f"{tag}: fix rounds exhausted ({reason})")
                return
            plan.dispatch.append({**base, "kind": "fix", "agent": "github-issue-resolver",
                                  "reason": reason,
                                  "round": rounds + 1 if counts else rounds})

        if pr.get("mergeable") == "CONFLICTING":
            fix("conflict", counts=False)
            continue
        checks = pr.get("checks", "pending")
        if checks == "pending":
            plan.waiting.append(f"{tag}: CI running")
            continue
        if checks == "failure":
            fix("ci-failed")
            continue

        verdict, comment_only = latest_verdict(pr.get("reviews", []), reviewer, pr.get("headRefOid", ""))
        if not reviewer:
            plan.waiting.append(f"{tag}: CI green, no reviewer identity")
            continue
        if verdict == "APPROVED":
            if pr.get("autoMerge"):
                plan.waiting.append(f"{tag}: approved, auto-merge pending")
            else:
                plan.ops.append({"op": "enable-automerge", "number": n,
                                 "why": "approved at head without auto-merge"})
            continue
        if verdict == "CHANGES_REQUESTED":
            fix("review")
            continue
        if comment_only >= MAX_COMMENT_ONLY_REVIEWS:
            plan.ops.append({"op": "add-label", "kind": "pr", "number": n, "label": NEEDS_HUMAN,
                             "why": "reviewer left comments but no verdict, twice"})
            plan.awaiting_human.append(f"{tag}: no verdict after {comment_only} reviews")
            continue
        if reviews_planned >= MAX_PARALLEL_REVIEW:
            plan.deferred.append(f"{tag}: review cap ({MAX_PARALLEL_REVIEW}) reached")
            continue
        reviews_planned += 1
        plan.dispatch.append({**base, "kind": "review", "agent": "github-pr-reviewer",
                              "reason": "CI green, no verdict at head"})

    # ---- issues ----
    planner_planned = False
    active_statuses = {READY, BLOCKED, NEEDS_SPEC, IN_PROGRESS, IN_REVIEW, ESCALATED}
    active = 0
    in_progress_scopes: list[list[str]] = []
    candidates: list[tuple[dict, list[str]]] = []
    idle_open = any(IDLE in labels_of(i) for i in issues)
    ideas_open = False

    for issue in sorted(issues, key=lambda i: i["number"]):
        n = issue["number"]
        tag = f"#{n}"
        labels = labels_of(issue)

        if IDEA in labels:
            if NEEDS_HUMAN in labels:
                plan.awaiting_human.append(f"{tag}: idea waiting on an answer")
                continue
            ideas_open = True
            if WORKING in labels and not stale(issue, STALE_WORKING):
                plan.in_flight.append(f"{tag}: planner holds this idea")
            elif not planner_planned:
                planner_planned = True
                plan.dispatch.append({"kind": "plan", "agent": "github-planner", "mode": "idea",
                                      "issue": n, "reason": "idea waiting"})
            continue
        if AGENT_TASK not in labels:
            continue
        if labels & {HUMAN_DECISION, ASSET}:
            plan.awaiting_human.append(f"{tag}: {', '.join(sorted(labels & {HUMAN_DECISION, ASSET}))}")
            continue

        status = status_of(labels)
        if status in active_statuses:
            active += 1
        pr = open_pr_by_issue.get(n)

        if status == NEEDS_HUMAN:
            plan.awaiting_human.append(f"{tag}: {NEEDS_HUMAN}")
        elif status in (ESCALATED, NEEDS_SPEC):
            if status == NEEDS_SPEC and pr:
                plan.ops.append({"op": "set-status", "number": n, "status": IN_REVIEW,
                                 "why": "a PR is open for it"})
            elif WORKING in labels and not stale(issue, STALE_WORKING):
                plan.in_flight.append(f"{tag}: triage holds it")
            else:
                if WORKING in labels:
                    plan.ops.append({"op": "remove-label", "kind": "issue", "number": n,
                                     "label": WORKING, "why": "stale pipeline:working"})
                if TRIAGED in labels:
                    plan.ops.append({"op": "set-status", "number": n, "status": NEEDS_HUMAN,
                                     "why": f"{status} again after triage"})
                    plan.ops.append({"op": "comment", "kind": "issue", "number": n, "body":
                                     f"**Pipeline:** `{status}` a second time after triage. "
                                     f"Handing this to a human (`{NEEDS_HUMAN}`)."})
                    plan.awaiting_human.append(f"{tag}: {status} twice")
                else:
                    plan.dispatch.append({"kind": "triage", "agent": "github-triage", "issue": n,
                                          "reason": status})
        elif status == IN_PROGRESS:
            if pr:
                plan.ops.append({"op": "set-status", "number": n, "status": IN_REVIEW,
                                 "why": f"PR #{pr['number']} is open"})
            elif stale(issue, STALE_IN_PROGRESS) and not any(
                    b.startswith(f"agent/{n}-") for b in remote_branches):
                plan.ops.append({"op": "set-status", "number": n, "status": None,
                                 "why": "in progress for hours with no branch and no PR"})
                plan.ops.append({"op": "lint", "number": n})
            else:
                in_progress_scopes.append(_safe_scope(allowlist_fn, issue.get("body", "")))
                plan.in_flight.append(f"{tag}: implementer working")
        elif status == IN_REVIEW:
            if not pr:
                plan.ops.append({"op": "set-status", "number": n, "status": ESCALATED,
                                 "why": "its PR was closed without merging"})
                plan.ops.append({"op": "comment", "kind": "issue", "number": n, "body":
                                 "**Pipeline:** the pull request for this issue was closed "
                                 "without merging while the issue stayed open. Triage decides "
                                 "whether to re-run it, amend it, or close it."})
        elif status in LINT_OWNED:
            if pr:
                plan.ops.append({"op": "set-status", "number": n, "status": IN_REVIEW,
                                 "why": f"PR #{pr['number']} is open"})
                continue
            result: LintResult = lint_fn(issue)
            desired = result.status
            if desired != status:
                plan.ops.append({"op": "set-status", "number": n, "status": desired,
                                 "why": "; ".join(result.problems) or
                                        (f"blocked by #{', #'.join(map(str, result.blockers_open))}"
                                         if result.blockers_open else "lint passed")})
                if desired == NEEDS_SPEC:
                    plan.ops.append({"op": "comment", "kind": "issue", "number": n,
                                     "body": lint_comment(n, result)})
                if desired in active_statuses and status not in active_statuses:
                    active += 1
            if desired == READY:
                candidates.append((issue, _safe_scope(allowlist_fn, issue.get("body", ""))))
            elif desired == BLOCKED:
                plan.waiting.append(f"{tag}: blocked by #{', #'.join(map(str, result.blockers_open))}")

    slots = MAX_PARALLEL_IMPLEMENT - len(in_progress_scopes)
    taken = list(in_progress_scopes)
    for issue, scope in candidates:
        tag = f"#{issue['number']}"
        if slots <= 0:
            plan.deferred.append(f"{tag}: implementer cap ({MAX_PARALLEL_IMPLEMENT}) reached")
            continue
        clash = next((s for s in taken if scopes_overlap(scope, s)), None)
        if clash is not None:
            plan.deferred.append(f"{tag}: shares a file in Files in scope with work in flight")
            continue
        taken.append(scope)
        slots -= 1
        plan.dispatch.append({"kind": "implement", "agent": "github-issue-resolver",
                              "issue": issue["number"], "title": issue.get("title", ""),
                              "reason": READY})

    if (not plan.dispatch and active == 0 and not prs and not idle_open
            and not ideas_open and not planner_planned):
        plan.dispatch.append({"kind": "plan", "agent": "github-planner", "mode": "roadmap",
                              "reason": "queue empty"})
    return plan


def _safe_scope(allowlist_fn, body: str) -> list[str]:
    try:
        return allowlist_fn(body)
    except RuntimeError:
        return ["*"]  # unknown scope: treat as overlapping everything


# --- GitHub ------------------------------------------------------------------

class GhError(RuntimeError):
    pass


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
        return self.json(["issue", "list", "--state", "open", "--limit", "300",
                          "--json", "number,title,labels,body,updatedAt"]) or []

    def issue(self, n: int) -> dict:
        return self.json(["issue", "view", str(n), "--json", "number,title,labels,body,updatedAt,state"])

    def issue_state(self, n: int) -> str | None:
        try:
            data = self.json(["api", f"repos/{{owner}}/{{repo}}/issues/{n}"])
            return (data or {}).get("state")
        except GhError:
            return None

    def open_prs(self) -> list[dict]:
        raw = self.json(["pr", "list", "--state", "open", "--limit", "100", "--json",
                         "number,title,headRefName,headRefOid,isDraft,labels,mergeable,"
                         "updatedAt,statusCheckRollup,autoMergeRequest,closingIssuesReferences"]) or []
        prs = []
        for p in raw:
            # One page of 100: `--paginate` would print one JSON array per page,
            # back to back, which is not a JSON document.
            reviews = self.json(["api", f"repos/{{owner}}/{{repo}}/pulls/{p['number']}/reviews"
                                        "?per_page=100"]) or []
            prs.append({
                "number": p["number"], "title": p.get("title", ""),
                "headRefName": p.get("headRefName", ""), "headRefOid": p.get("headRefOid", ""),
                "isDraft": p.get("isDraft", False), "labels": p.get("labels", []),
                "mergeable": p.get("mergeable"), "updatedAt": p.get("updatedAt"),
                "checks": rollup_state(p.get("statusCheckRollup") or []),
                "autoMerge": p.get("autoMergeRequest") is not None,
                "closingIssues": [c["number"] for c in p.get("closingIssuesReferences") or []],
                "reviews": [{"login": (r.get("user") or {}).get("login"), "state": r.get("state"),
                             "commit": r.get("commit_id"), "submitted_at": r.get("submitted_at")}
                            for r in reviews],
            })
        return prs

    def reviewer_login(self) -> str | None:
        env = os.environ.get("PIPELINE_REVIEWER_LOGIN", "").strip()
        if env:
            return env
        if not reviewer_token():
            return None
        try:
            return (self.json(["api", "user"], as_reviewer=True) or {}).get("login")
        except GhError:
            return None

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

    def set_status(self, n: int, status: str | None, current: set[str]) -> None:
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

    def comment(self, kind: str, n: int, body: str) -> None:
        self._run([kind if kind == "pr" else "issue", "comment", str(n), "--body-file", "-"],
                  input=body, mutating=True)

    def enable_automerge(self, n: int) -> None:
        self._run(["pr", "merge", str(n), "--auto", "--squash", "--delete-branch"],
                  as_reviewer=True, mutating=True)


def rollup_state(rollup: list[dict]) -> str:
    """pending | success | failure over a PR head's check runs and statuses."""
    if not rollup:
        return "pending"
    state = "success"
    for c in rollup:
        if c.get("__typename") == "StatusContext" or "context" in c and "status" not in c:
            s = (c.get("state") or "").upper()
            if s in ("PENDING", "EXPECTED", ""):
                state = "pending" if state == "success" else state
            elif s != "SUCCESS":
                return "failure"
        else:
            if (c.get("status") or "").upper() != "COMPLETED":
                state = "pending" if state == "success" else state
            elif (c.get("conclusion") or "").upper() not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
                return "failure"
    return state


def remote_agent_branches() -> set[str]:
    proc = subprocess.run(["git", "ls-remote", "--heads", "origin", "agent/*"],
                          capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT)
    out = set()
    for line in proc.stdout.splitlines():
        parts = line.split("refs/heads/", 1)
        if len(parts) == 2:
            out.add(parts[1].strip())
    return out


def make_lint_fn(gh: Gh, open_numbers: set[int]):
    cache: dict[int, str | None] = {}

    def state(n: int) -> str | None:
        if n in open_numbers:
            return "open"
        if n not in cache:
            cache[n] = gh.issue_state(n)
        return cache[n]

    def lint(issue: dict) -> LintResult:
        return lint_body(issue["number"], issue.get("body", ""), parse_allowlist, state)
    return lint


def apply_ops(gh: Gh, plan: Plan, issues_by_number: dict[int, dict], lint_fn) -> list[str]:
    done = []
    current = {n: labels_of(i) for n, i in issues_by_number.items()}
    for op in plan.ops:
        kind = op["op"]
        n = op.get("number")
        try:
            if kind == "set-status":
                gh.set_status(n, op["status"], current.get(n, set()))
                cur = current.setdefault(n, set())
                cur.difference_update(STATUSES)
                if op["status"]:
                    cur.add(op["status"])
            elif kind == "lint":
                issue = gh.issue(n)
                result = lint_fn(issue)
                gh.set_status(n, result.status, current.get(n, set()))
                if result.status == NEEDS_SPEC:
                    gh.comment("issue", n, lint_comment(n, result))
            elif kind == "add-label":
                gh.edit_labels(op["kind"], n, add=[op["label"]])
            elif kind == "remove-label":
                gh.edit_labels(op["kind"], n, remove=[op["label"]])
            elif kind == "comment":
                gh.comment(op["kind"], n, op["body"])
            elif kind == "enable-automerge":
                gh.enable_automerge(n)
            done.append(f"{kind} {n}: {op.get('status') or op.get('label') or ''} {op.get('why', '')}".strip())
        except GhError as e:
            done.append(f"FAILED {kind} {n}: {e}")
    return done


def claim(gh: Gh, item: dict, issues_by_number: dict[int, dict]) -> str:
    kind = item["kind"]
    if kind == "implement":
        n = item["issue"]
        gh.set_status(n, IN_PROGRESS, labels_of(issues_by_number.get(n, {})))
        return f"claimed #{n} ({IN_PROGRESS})"
    if kind in ("review", "fix"):
        add = [WORKING]
        if kind == "fix" and item.get("round", 0) > 0:
            add.append(f"{FIX_ROUND}{item['round']}")
        gh.edit_labels("pr", item["pr"], add=add)
        return f"claimed PR #{item['pr']} ({', '.join(add)})"
    if kind in ("triage",) or (kind == "plan" and item.get("issue")):
        gh.edit_labels("issue", item["issue"], add=[WORKING])
        return f"claimed #{item['issue']} ({WORKING})"
    return "nothing to claim"


# --- commands --------------------------------------------------------------------

def cmd_run(args) -> int:
    gh = Gh(dry_run=not args.apply)
    issues = gh.open_issues()
    snap = {
        "now": datetime.now(timezone.utc),
        "issues": issues,
        "prs": gh.open_prs(),
        "reviewer_login": gh.reviewer_login(),
        "remote_branches": remote_agent_branches(),
    }
    open_numbers = {i["number"] for i in issues}
    lint_fn = make_lint_fn(gh, open_numbers)
    plan = decide(snap, parse_allowlist, lint_fn)
    out = plan.to_json()
    if args.apply and not plan.paused:
        made = gh.ensure_labels()
        if made:
            out["labels_created"] = made
        by_number = {i["number"]: i for i in issues}
        out["ops_done"] = apply_ops(gh, plan, by_number, lint_fn)
        out["claims"] = []
        for item in plan.dispatch:
            try:
                out["claims"].append(claim(gh, item, by_number))
            except GhError as e:
                out["claims"].append(f"FAILED claim {item}: {e}")
                item["claim_failed"] = True
        out["dispatch"] = [d for d in plan.dispatch if not d.get("claim_failed")]
    print(json.dumps(out, indent=2, ensure_ascii=False))
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
    elif args.apply and result.status != status:
        gh.ensure_labels()
        gh.set_status(args.issue, result.status, labels)
        if result.status == NEEDS_SPEC:
            gh.comment("issue", args.issue, lint_comment(args.issue, result))
        report["applied"] = True
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_relint(args) -> int:
    gh = Gh(dry_run=not args.apply)
    issues = gh.open_issues()
    lint_fn = make_lint_fn(gh, {i["number"] for i in issues})
    changed = []
    for issue in issues:
        labels = labels_of(issue)
        if AGENT_TASK not in labels or labels & {ASSET, HUMAN_DECISION, IDEA}:
            continue
        status = status_of(labels)
        if status not in LINT_OWNED:
            continue
        result = lint_fn(issue)
        if result.status != status:
            changed.append({"issue": issue["number"], "from": status, "to": result.status})
            if args.apply:
                gh.set_status(issue["number"], result.status, labels)
                if result.status == NEEDS_SPEC:
                    gh.comment("issue", issue["number"], lint_comment(issue["number"], result))
    print(json.dumps({"changed": changed}, indent=2, ensure_ascii=False))
    return 0


def cmd_claim(args) -> int:
    gh = Gh()
    item = {"kind": "implement" if args.kind == "issue" else "review",
            "issue": args.number, "pr": args.number, "round": args.round or 0}
    if args.kind == "pr" and args.round:
        item["kind"] = "fix"
    issues = {args.number: gh.issue(args.number)} if args.kind == "issue" else {}
    print(claim(gh, item, issues))
    return 0


def cmd_release(args) -> int:
    Gh().edit_labels(args.kind, args.number, remove=[WORKING])
    print(f"released {args.kind} {args.number}")
    return 0


def cmd_set_status(args) -> int:
    gh = Gh()
    status = None if args.status == "none" else args.status
    if status and status not in STATUSES:
        print(f"unknown status {status}; one of: {', '.join(STATUSES)}, none", file=sys.stderr)
        return 2
    gh.ensure_labels()
    gh.set_status(args.issue, status, labels_of(gh.issue(args.issue)))
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


def protection_payload() -> dict:
    return {
        "required_status_checks": {
            "strict": False,
            "checks": [{"context": c, "app_id": GITHUB_ACTIONS_APP_ID} for c in REQUIRED_CHECKS],
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

    gh._run(["api", "-X", "PUT", f"repos/{{owner}}/{{repo}}/branches/{DEFAULT_BRANCH}/protection",
             "--input", "-"], input=json.dumps(protection_payload()), mutating=True)
    report.append(f"{DEFAULT_BRANCH} protected: checks {', '.join(REQUIRED_CHECKS)} "
                  "(GitHub Actions only), 1 approval, stale approvals dismissed, admins included, "
                  "linear history")

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
                invites = gh.json(["api", "user/repository_invitations"], as_reviewer=True) or []
                for inv in invites:
                    gh._run(["api", "-X", "PATCH", f"user/repository_invitations/{inv['id']}"],
                            as_reviewer=True, mutating=True)
                    report.append(f"reviewer bot: accepted invitation {inv['id']}")
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
    s.set_defaults(fn=cmd_claim)
    s = sub.add_parser("release"); s.add_argument("kind", choices=["issue", "pr"])
    s.add_argument("number", type=int); s.set_defaults(fn=cmd_release)
    s = sub.add_parser("set-status"); s.add_argument("issue", type=int); s.add_argument("status")
    s.set_defaults(fn=cmd_set_status)
    s = sub.add_parser("check-pr-body"); s.set_defaults(fn=cmd_check_pr_body)
    s = sub.add_parser("setup-repo"); s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_setup_repo)
    s = sub.add_parser("config"); s.add_argument("key"); s.set_defaults(fn=cmd_config)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except GhError as e:
        print(f"pipeline: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
