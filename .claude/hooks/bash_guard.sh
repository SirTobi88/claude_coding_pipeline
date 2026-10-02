#!/usr/bin/env bash
# PreToolUse guard on Bash: the few commands that would let an agent act
# outside its role.
#
#   bash_guard.sh          .claude/settings.json, every session and subagent
#   bash_guard.sh <role>   an agent's own frontmatter hooks: implementer,
#                          triage or planner
#
# What it refuses:
#
#   - Acting as the reviewer bot (.claude/bin/gh-reviewer, or its token) from
#     any subagent but github-pr-reviewer, from any role, and from the
#     scheduled tick's own session (PIPELINE_TICK set). The bot's approval is
#     the only one branch protection counts (docs/Pipeline.md § Merging), so an
#     implementer holding it could approve its own pull request.
#   - Changing branch protection, rulesets, Actions permissions or
#     collaborators, from any session, and `pipeline setup-repo` -- the one way
#     those change, run by the owner in a terminal.
#   - Rewriting or deleting remote history: force, delete and mirror pushes,
#     `+` refspecs, `:branch` deletions. A subagent pushes only to agent/
#     branches.
#   - A subagent applying `human-decision`: that label is the owner's key to the
#     pipeline's control paths (docs/Pipeline.md § What stays with the owner).
#   - For an implementer: editing, creating or closing issues -- widening its
#     own Files in scope is an escalation, never a fix
#     (CONTRIBUTING-agents.md § When you are blocked) -- writing through
#     `gh api`, and approving or merging.
#   - For triage and the planner: pushing, approving or merging. They repair
#     issues; they never write the implementation.
#
# A GUARD RAIL, NOT A BOUNDARY. It matches command text, and docs/LESSONS.md
# § Merging records how a text match ends: heredocs, eval, quoting and a
# different binary each get past it. It stops the accident and the casual
# attempt, and tells the agent what to do instead. What actually binds is in
# docs/Pipeline.md § What binds an agent.
#
# Without jq it falls back to matching the raw payload, which errs towards
# refusing: a denied command costs a retry, a missed one costs the gate.

set -u
set -f   # the command's words are split below; never expand them as globs

role="${1:-}"
payload="$(cat)"

if command -v jq >/dev/null 2>&1; then
    # One jq call, as this runs before every Bash command: the agent type on
    # the first line, then the command, which may span lines. The agent type
    # is kept to one line, or its tail would be read as part of the command.
    fields="$(printf '%s' "$payload" \
              | jq -r '"\(.agent_type // "" | tostring | split("\n") | join(" "))\n\(.tool_input.command // "")"' \
                2>/dev/null | tr -d '\r')"
    agent="${fields%%"
"*}"
    cmd="${fields#"$agent"}"
    cmd="${cmd#"
"}"
else
    cmd="$payload"
    agent="$(printf '%s' "$payload" | sed -n 's/.*"agent_type"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
fi
[ -n "$cmd" ] || exit 0

# A newline ends a command as `;` does, so a call on a later line is checked
# like one on the first. Two exceptions: a backslash-newline continues the
# line, and a heredoc body is treated as a mention: its lines are joined without
# a separator, so no anchored check sees them as a command start (`bash <<EOF`
# runs its body, and that gets past this, as quoting does). Pure bash, and only
# for a command that spans lines: this runs before every Bash call. The pieces
# are joined once at the end -- appending to one string is quadratic, and a long
# heredoc would run the hook into its timeout, which skips the guard.
nl='
'
lines="$cmd"
case "$cmd" in
    *"$nl"*)
        # <<EOF, <<-EOF, <<'END-X', <<"EOF", <<\EOF; not a <<< here-string.
        heredoc_start='(^|[^<])<<(-?)[[:space:]]*\\?['"'"'"]?([^[:space:];&|<>()'"'"'"\\]+)'
        parts=() delim="" strip_tabs="" cont=""
        while IFS= read -r line || [ -n "$line" ]; do
            if [ -n "$delim" ]; then
                end="$line"
                [ -n "$strip_tabs" ] && end="${end#"${end%%[!	]*}"}"
                [ "$end" = "$delim" ] && delim=""
                parts+=(" $line")
                continue
            fi
            if [ -n "$cont" ] || [ ${#parts[@]} -eq 0 ]; then
                parts+=(" $line")
            else
                parts+=(" ; $line")
            fi
            cont=""
            case "$line" in
                *\\) last=$((${#parts[@]} - 1)); parts[last]="${parts[last]%\\}"; cont=1 ;;
            esac
            if [[ $line =~ $heredoc_start ]]; then
                delim="${BASH_REMATCH[3]}" strip_tabs="${BASH_REMATCH[2]}"
            fi
        done <<< "$cmd"
        saved_ifs="$IFS"; IFS=; lines="${parts[*]}"; IFS="$saved_ifs"
        ;;
esac

# One spelling to match against: whitespace runs collapsed, gh's -R/--repo
# option dropped wherever it sits (`gh -R o/r issue edit`), and git's -C <dir>
# too (`git -C .claude/worktrees/review-5 push` is a `git push`).
norm="$(printf '%s' "$lines" | tr -s ' \t\n' '   ' \
        | sed -e 's/ -R[ =][^ ]*//g' -e 's/ --repo[ =][^ ]*//g' -e 's/git -C [^ ]*/git/g')"

refuse() {
    {
        echo "BLOCKED by .claude/hooks/bash_guard.sh: $1"
        echo
        echo "  command: $(printf '%s' "$cmd" | head -c 300)"
        echo
        printf '%s\n' "$2"
    } >&2
    exit 2
}

# A subagent, or a role given on the command line, is never the owner.
subagent=0
if [ -n "$role" ] || [ -n "$agent" ]; then subagent=1; fi

# --- the reviewer bot ------------------------------------------------------------

# The token file's name, from config.json without starting Python on every
# Bash call. The default name is matched too, in case config.json moved it.
case "${BASH_SOURCE[0]}" in */*) here="${BASH_SOURCE[0]%/*}" ;; *) here=. ;; esac
token_name="$(sed -n 's/.*"reviewer_token_file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
              "$here/../pipeline/config.json" 2>/dev/null)"
token_name="${token_name%%"
"*}"   # the first match
token_name="${token_name##*/}"

# Who may act as the bot: the reviewer subagent, and a main session that is
# not the scheduled tick -- the owner running the review skill by hand.
bot_allowed=0
if [ -z "$role" ]; then
    if [ "$agent" = "github-pr-reviewer" ]; then
        bot_allowed=1
    elif [ -z "$agent" ] && [ -z "${PIPELINE_TICK:-}" ]; then
        bot_allowed=1
    fi
fi

uses_bot=0
# The wrapper where it is called -- at the start of a command, after a
# separator, inside $( ), or behind an interpreter -- not wherever it is
# named: a grep of the docs for it is not a use. The token is different: any
# command that names it or its file reads it. (The `case` spares every other
# command the grep.)
case "$norm" in
    *gh-reviewer*)
        if printf '%s' "$norm" \
             | grep -qE '(^|[;&|(`]|\$\() *((bash|sh|exec|env|command|xargs|nohup|time)( +-[^ ]+)*( +[A-Za-z_]+=[^ ]*)* +)*([^ ;&|()`]*/)?gh-reviewer( |$)'; then
            uses_bot=1
        fi ;;
esac
case "$cmd" in
    *PIPELINE_REVIEWER_TOKEN*|*reviewer-token*) uses_bot=1 ;;
esac
if [ -n "$token_name" ]; then
    case "$cmd" in *"$token_name"*) uses_bot=1 ;; esac
fi
if [ "$uses_bot" = 1 ] && [ "$bot_allowed" = 0 ]; then
    refuse "only the reviewer agent acts as the reviewer bot." \
"The bot's approval is the only one that can merge anything, so nobody else
may use .claude/bin/gh-reviewer or its token (docs/Pipeline.md § Merging).
If this job needs a review, finish your own work; the pipeline dispatches the
reviewer."
fi

# --- the rules everyone is judged by -----------------------------------------------

# Whether a `gh api` call writes: an explicit -X/--method that is not GET, or
# fields or a body without one. Quoted and glued spellings count
# (`-X "DELETE"`, `-XPUT`, `--method=patch`).
api_writes() {
    local m
    m="$(printf '%s' "$norm" | grep -oE -- "(-X|--method)(=| )?['\"]?[A-Za-z]+" | head -1 \
         | sed -E "s/^(-X|--method)(=| )?['\"]?//" | tr '[:lower:]' '[:upper:]')"
    if [ -n "$m" ]; then [ "$m" != GET ]; return; fi
    case "$norm" in
        *" -f "*|*" -F "*|*" -f="*|*" -F="*|*"--field"*|*"--raw-field"*|*"--input"*) return 0 ;;
    esac
    return 1
}

case "$norm" in
    *"gh api"*)
        case "$norm" in
            */protection*|*/rulesets*|*/actions/permissions*|*/collaborators*)
                if api_writes; then
                    refuse "branch protection, rulesets, Actions permissions and collaborators are not changed from a Claude session." \
"They are the rules every agent is judged by. \`.claude/bin/pipeline setup-repo\`
sets them, run by the owner in a terminal (docs/Pipeline.md § Setup)."
                fi ;;
            *mutation*BranchProtection*|*mutation*Ruleset*)
                refuse "branch protection and rulesets are not changed from a Claude session." \
"\`.claude/bin/pipeline setup-repo\` sets them, run by the owner in a terminal." ;;
        esac ;;
esac

# Where it is run, not wherever it is named: a heredoc or a grep that only
# mentions it is not a call.
case "$norm" in
    *setup-repo*)
        if printf '%s' "$norm" \
             | grep -qE '(^|[;&|(]) *((python3?|py) )?([^ ;&|]*/)?pipeline(\.py)? setup-repo'; then
            refuse "setup-repo is run by the owner in a terminal, not from a Claude session." \
"It sets branch protection and needs the owner's own login, which no agent
holds (docs/Pipeline.md § Setup, step 4)."
        fi ;;
esac

# Pushes: never rewrite or delete remote history; a subagent pushes only to an
# agent/ branch. Every push in the command is checked, not only the first: an
# allowed push must not carry a forbidden one behind a separator.
rest="$norm"
while :; do
    case "$rest" in
        *"git push"*) ;;
        *) break ;;
    esac
    after="${rest#*git push}"
    rest="$after"
    remote=""
    # Only this push's own words: the next push is checked in its own round,
    # and reading every later word each round made the cost quadratic.
    seg="${after%%git push*}"
    for tok in $seg; do
        case "$tok" in
            --force|--force=*|--force-with-lease*|--force-if-includes|-f|-*f|--delete|-d|--mirror|--all|--prune)
                refuse "no force, delete or mirror pushes." \
"History on GitHub is shared state; a rewritten or deleted branch loses other
agents' and reviewers' work. Push new commits instead." ;;
            -*|*">"*|*"<"*) continue ;;
            "&&"|"||"|";"|"|") break ;;
        esac
        if [ -z "$remote" ]; then remote="$tok"; continue; fi
        case "$tok" in
            +*|:*) refuse "no forced (+) or deleting (:branch) refspecs." \
"Push new commits to your agent/ branch instead." ;;
        esac
        dest="${tok#*:}"
        dest="${dest#refs/heads/}"
        if [ "$subagent" = 1 ]; then
            case "$dest" in
                agent/*) ;;
                *) refuse "an agent pushes only to its agent/<N>- branch." \
"Branches outside agent/ are no agent's to write; the default branch takes
changes only through a reviewed pull request." ;;
            esac
        fi
    done
done

labels_something=0
case "$norm" in
    *add-label*|*"issue create"*|*"pr create"*|*"gh label"*|*"gh api"*) labels_something=1 ;;
esac
if [ "$subagent" = 1 ] && [ "$labels_something" = 1 ]; then
    case "$norm" in
        *human-decision*)
            refuse "only the owner applies human-decision." \
"The label is what lets a change touch the pipeline's own files
(docs/Pipeline.md § What stays with the owner). If a decision is needed,
ask it as one precise question and set status:needs-human." ;;
    esac
fi

# --- roles ---------------------------------------------------------------------------

case "$role" in
    implementer)
        case "$norm" in
            *"gh issue edit"*|*"gh issue create"*|*"gh issue close"*|*"gh issue reopen"*|\
            *"gh issue delete"*|*"gh issue transfer"*|*"gh issue pin"*|*"gh issue lock"*|*"gh label"*)
                refuse "an implementer reads its issue and comments on it; it does not change issues." \
"If the work needs a file outside Files in scope, or the issue is wrong, that is
an escalation (CONTRIBUTING-agents.md § When you are blocked): comment on the
issue with the precise question, then run
  .claude/bin/pipeline set-status <N> status:escalated
and stop. Triage repairs the issue." ;;
            *"gh pr merge"*|*"gh pr review"*)
                refuse "an implementer never approves or merges." \
"You authored this branch; the reviewer bot judges it and GitHub merges it." ;;
        esac
        case "$norm" in
            *"gh api"*)
                if api_writes; then
                    refuse "an implementer only reads through gh api." \
"Use gh pr create / gh pr edit / gh pr comment / gh issue comment for the
writes your job needs."
                fi ;;
        esac ;;
    triage|planner)
        case "$norm" in
            *"git push"*|*"gh pr merge"*|*"gh pr review"*)
                refuse "$role repairs issues; it never pushes code, approves or merges." \
"File the work as an issue (github-issue-create, autonomous mode) instead." ;;
        esac ;;
esac

exit 0
