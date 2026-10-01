#!/usr/bin/env bash
# PreToolUse guard on Edit|Write|MultiEdit|NotebookEdit: refuse a write outside
# the issue's allowlist.
#
# CONTRIBUTING-agents.md § *Blast radius is declared up front* calls widening
# the allowlist yourself "the single most common way an agent-authored branch
# becomes unmergeable", and CI can only ever notice it afterwards -- by which
# point the branch is already wrong and the review spends its time on
# bookkeeping instead of on the code. This refuses the write instead.
#
# Scope is taken from the branch name, so this binds exactly the branches the
# contract binds: `agent/<N>-<slug>` and nothing else. Work on main, in a
# review worktree, or on a scratch branch is untouched -- with one exception:
# a session working in an agent worktree may not write into ANOTHER worktree
# of the same repository (the main checkout included), where the branch would
# not bind it.
#
# It sees the file-editing tools only. A write through the shell -- `sed -i`,
# `cat > f`, a script -- never reaches it; parsing arbitrary shell for its
# write targets is not a problem with a reliable answer. So this catches the
# accident, not the workaround; the `allowlist` job in pr-contract.yml catches both.
#
# FAILS OPEN when it cannot tell, deliberately: when gh or jq is missing, or
# the issue cannot be read (network, auth), the edit proceeds. This runs on
# every single edit; a guard that bricks the session when the network blinks
# would be turned off within a day, and .github/workflows/pr-contract.yml runs
# the same check on the pull request as the backstop. It FAILS CLOSED when it
# can tell: an issue that was read and names no parseable path allows nothing,
# because CI is certain to reject whatever the session writes.

set -u

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/issue_scope.sh
. "$HOOK_DIR/lib/issue_scope.sh"

allow() { exit 0; }

command -v gh  >/dev/null 2>&1 || allow
command -v jq  >/dev/null 2>&1 || allow

payload="$(cat)"
# Edit, Write and MultiEdit name `file_path`; NotebookEdit names `notebook_path`.
# `jq | tr` inline rather than scope_jq: a function in a pipeline is one more
# fork, and on Windows every fork on this path is felt on every edit.
file_path="$(printf '%s' "$payload" \
             | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null \
             | tr -d '\r')"
cwd="$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null | tr -d '\r')"
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

# Find the file's checkout. Everything after this -- which repository, which
# branch, which issue -- is read off the FILE, not off the session's cwd: a
# session in the main checkout can write by absolute path into
# `.claude/worktrees/agent-<N>-.../`, and that write is bound by issue <N>'s
# allowlist, not by whatever the main checkout has checked out.
#
# The file usually does not exist yet, and neither may its directory -- a new
# test file under a new folder is routine. So start from the NEAREST EXISTING
# ancestor and keep the part that is not there yet as the tail. Resolving only
# the immediate parent, and falling back to the raw path when it was missing,
# once waved every write into a new directory through.
case "$file_path" in /*) ;; *) file_path="$cwd/$file_path" ;; esac
_dir="$(dirname "$file_path")"
_tail="$(basename "$file_path")"
while [ ! -d "$_dir" ]; do
    _tail="$(basename "$_dir")/$_tail"
    _next="$(dirname "$_dir")"
    [ "$_next" != "$_dir" ] || allow
    _dir="$_next"
done

# One git call, three lines -- this runs on every edit: the checkout's root,
# the directory's repo-relative prefix (an empty line at the top level), and
# the branch.
#
# The repo-relative path is git's spelling. `--show-prefix` answers in the
# repository's own spelling -- its letter case, its symlinks resolved -- however
# the path arrived. Comparing the typed path against the root as text failed
# open twice: on macOS a worktree under /tmp is /private/tmp to git, and on
# Windows and macOS `e:\repo\SRC` is `E:/repo/src` to git; both read as
# "outside the repository", which the guard allows.
nl='
'
parsed="$(git -C "$_dir" rev-parse --show-toplevel --show-prefix --abbrev-ref HEAD 2>/dev/null)" || allow
root="${parsed%%"$nl"*}"
prefix="${parsed#*"$nl"}"
branch="${prefix#*"$nl"}"
prefix="${prefix%%"$nl"*}"
[ -n "$root" ] || allow
rel="$prefix$_tail"

issue="$(scope_issue_from_branch "$branch")"

# Writing from an agent's worktree into another worktree of the same
# repository: the main checkout, or another agent's. The other branch does not
# bind the write, the change never reaches this agent's PR, and in the main
# checkout it changes what every later tick runs. No legitimate job does it.
cwd_root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || true)"
case "$cwd_root" in
    */.claude/worktrees/*)
        if [ "$cwd_root" != "$root" ] \
           && [ "$(git -C "$cwd_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" \
                = "$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" ]; then
            {
                echo "BLOCKED: this session works in $cwd_root,"
                echo "but the write goes to another checkout of the repository:"
                echo
                echo "  $root/$rel"
                echo
                echo "Write inside your own worktree. A path copied from an issue or from an"
                echo "earlier Read may point at the main checkout; make it relative to yours."
            } >&2
            exit 2
        fi ;;
esac

[ -n "$issue" ] || allow

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
fetch() {
    ( cd "$root" && scope_fetch_allowlist "$issue" "$origin" ) > "$list" 2>/dev/null
}
rc=0; fetch || rc=$?
[ "$rc" -eq 2 ] && allow          # could not read the issue: CI is the backstop

if [ "$rc" -eq 0 ] && [ -s "$list" ] && [ "$rel_escapes" = 0 ] && scope_path_allowed "$rel" "$list"; then
    allow
fi
# Before refusing, read the issue again past the five-minute cache: the
# answer to an escalation is a widened allowlist, and it must take effect now.
rc=0; SCOPE_NO_CACHE=1 fetch || rc=$?
[ "$rc" -eq 2 ] && allow
if [ "$rc" -eq 0 ] && [ -s "$list" ] && [ "$rel_escapes" = 0 ] && scope_path_allowed "$rel" "$list"; then
    allow
fi

# Exit 2 is the blocking code: the tool call does not run and this message is
# fed back to the agent. Say what the contract says, not just "denied" -- the
# correct next action is an escalation, and an agent that reads only "blocked"
# will try to find a way around it.
{
    if [ "$rc" -ne 0 ] || [ ! -s "$list" ]; then
        echo "BLOCKED: issue #$issue's Files in scope names no path this parser can read,"
        echo "so nothing may be written on branch $branch."
    else
        echo "BLOCKED by the Files-in-scope allowlist."
    fi
    echo
    echo "  branch: $branch  (issue #$issue)"
    echo "  write:  $rel"
    echo
    echo "Per CONTRIBUTING-agents.md § 'When you are blocked', a file outside the"
    echo "allowlist is escalated, never solved. Comment on issue #$issue saying which"
    echo "file you need and why, then run"
    echo
    echo "  .claude/bin/pipeline set-status $issue status:escalated"
    echo
    echo "and stop -- in a fix pass, release the PR first (.claude/bin/pipeline release pr <P>)."
    echo "Do not widen the list yourself."
    if [ -s "$list" ]; then
        echo
        echo "Issue #$issue allows:"
        sed 's/^/  - /' "$list"
    fi
} >&2

exit 2
