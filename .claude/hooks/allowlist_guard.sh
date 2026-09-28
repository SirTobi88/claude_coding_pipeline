#!/usr/bin/env bash
# PreToolUse guard on Edit|Write: refuse a write outside the issue's allowlist.
#
# CONTRIBUTING-agents.md § *Blast radius is declared up front* calls widening
# the allowlist yourself "the single most common way an agent-authored branch
# becomes unmergeable", and CI can only ever notice it afterwards -- by which
# point the branch is already wrong and the review spends its time on
# bookkeeping instead of on the code. This refuses the write instead.
#
# Scope is taken from the branch name, so this binds exactly the branches the
# contract binds: `agent/<N>-<slug>` and nothing else. Work on main, in a
# review worktree, or on a scratch branch is untouched.
#
# It sees the Edit and Write tools only. A write through the shell -- `sed -i`,
# `cat > f`, a script -- never reaches it; parsing arbitrary shell for its
# write targets is not a problem with a reliable answer. So this catches the
# accident, not the workaround; the `allowlist` job in pr-contract.yml catches both.
#
# FAILS OPEN, deliberately. If gh is unauthenticated, the issue cannot be
# read, or its Files in scope section is unparseable, the edit proceeds. This
# runs on every single edit; a guard that bricks the session when the network
# blinks would be turned off within a day, and .github/workflows/pr-contract.yml runs
# the same check on the pull request as the backstop.

set -u

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/issue_scope.sh
. "$HOOK_DIR/lib/issue_scope.sh"

allow() { exit 0; }

command -v gh  >/dev/null 2>&1 || allow
command -v jq  >/dev/null 2>&1 || allow

payload="$(cat)"
file_path="$(printf '%s' "$payload" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
cwd="$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null)"
[ -n "$file_path" ] || allow
[ -n "$cwd" ] || cwd="$PWD"

# On Windows the paths arrive in Windows spelling -- `E:\repo\x.py` from Claude
# Code, `C:/Users/...` from any native tool. Neither starts with `/`, so the
# relative-path branch below took every one of them, resolved it to nonsense,
# and BLOCKED every write on an agent branch, in scope or not. cygpath exists
# only under Git Bash / MSYS / Cygwin; elsewhere the path passes through as is.
to_posix() {
    case "$1" in
        [A-Za-z]:[\\/]*|*\\*)
            if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; return; fi ;;
    esac
    printf '%s' "$1"
}
file_path="$(to_posix "$file_path")"
cwd="$(to_posix "$cwd")"

# Resolve the file to a physical path first. Everything after this --
# which repository, which branch, which issue -- is read off the FILE, not off
# the session's cwd: a session in the main checkout can write by absolute path
# into `.claude/worktrees/agent-<N>-.../`, and that write is bound by issue
# <N>'s allowlist, not by whatever the main checkout has checked out.
#
# Physical on both sides, because git always answers with symlinks resolved
# while a path arrives as the agent spelled it: on macOS a worktree under /tmp
# is reported as /private/tmp, a textual prefix test decides the file is
# outside the repo, and that fails OPEN -- the one direction a guard must not
# fail by accident.
#
# The file usually does not exist yet, and neither may its directory -- a new
# test file under a new folder is routine. So resolve the NEAREST EXISTING
# ancestor and re-append the part that is not there yet. Resolving only the
# immediate parent, and falling back to the raw path when it is missing, took
# every write into a new directory back to the unresolved spelling, where the
# prefix test below missed and waved it through.
case "$file_path" in /*) ;; *) file_path="$cwd/$file_path" ;; esac
_dir="$(dirname "$file_path")"
_tail="$(basename "$file_path")"
while [ ! -d "$_dir" ]; do
    _tail="$(basename "$_dir")/$_tail"
    _next="$(dirname "$_dir")"
    [ "$_next" != "$_dir" ] || allow
    _dir="$_next"
done
_dir="$(cd "$_dir" 2>/dev/null && pwd -P)" || allow
abs="${_dir%/}/$_tail"

root="$(git -C "$_dir" rev-parse --show-toplevel 2>/dev/null)" || allow
[ -n "$root" ] || allow
root="$(cd "$root" 2>/dev/null && pwd -P)" || allow

branch="$(git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null)" || allow
issue="$(scope_issue_from_branch "$branch")"
[ -n "$issue" ] || allow

# Repo-relative, or out of the repo entirely (a scratchpad file, an absolute
# path elsewhere) in which case the allowlist has nothing to say about it.
case "$abs" in
    "$root"/*) rel="${abs#"$root"/}" ;;
    *)         allow ;;
esac

# A `..` can only survive into `rel` through the unresolved tail, and there it
# would let a directory entry (`src/`) cover a path that climbs back
# out of it. No legitimate write is spelled that way, so treat it as outside.
case "/$rel/" in */../*) rel_escapes=1 ;; *) rel_escapes=0 ;; esac

# A gitignored path can never reach the pull request's diff, so the CI job
# never judges it, and refusing it here would send the agent to escalate over
# a file nobody will ever see -- a build cache, a scratch file.
# `check-ignore` skips tracked files, so a tracked file under an ignore
# pattern is still guarded; forcing an ignored file in with `git add -f` is
# the CI job's to catch.
if [ "$rel_escapes" = 0 ] && git -C "$root" check-ignore -q -- "$rel" 2>/dev/null; then
    allow
fi

list="$(mktemp)" || allow
trap 'rm -f "$list"' EXIT
# From the file's own checkout: gh reads the issue from the repository of the
# current directory, and that is the repository whose allowlist binds.
origin="$(git -C "$root" remote get-url origin 2>/dev/null || echo "$root")"
( cd "$root" && scope_fetch_allowlist "$issue" "$origin" ) > "$list" 2>/dev/null || allow
[ -s "$list" ] || allow

[ "$rel_escapes" = 0 ] && scope_path_allowed "$rel" "$list" && allow

# Exit 2 is the blocking code: the tool call does not run and this message is
# fed back to the agent. Say what the contract says, not just "denied" -- the
# correct next action is an escalation, and an agent that reads only "blocked"
# will try to find a way around it.
{
    echo "BLOCKED by the Files-in-scope allowlist."
    echo
    echo "  branch: $branch  (issue #$issue)"
    echo "  write:  $rel"
    echo
    echo "That path is not in issue #${issue}'s Files in scope. Per"
    echo "CONTRIBUTING-agents.md § 'When you are blocked', needing a file outside"
    echo "the allowlist is escalated, never solved: comment on issue #$issue saying"
    echo "which file you need and why, and stop. Do not widen the list yourself."
    echo
    echo "Issue #$issue allows:"
    sed 's/^/  - /' "$list"
} >&2

exit 2
