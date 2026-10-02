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
# It errs towards refusing where it can tell a tool let it down: a denied
# command costs a retry, a missed one costs the gate. It refuses when tr, sed,
# grep or awk is missing; when tr or sed leaves nothing of a command to read;
# and when the push check's awk fails, prints nothing, or prints a verdict it
# does not know. Without jq it falls back to matching the raw payload. A jq,
# cat or head that misbehaves, or a grep that errors, is not caught (#82).

set -u
set -f   # the command's words are split below; never expand them as globs

# Every check below reads text through these. One missing makes them match
# nothing, which would let every command through.
for tool in tr sed grep awk; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        {
            echo "BLOCKED by .claude/hooks/bash_guard.sh: a tool the guard needs is missing (tr, sed, grep or awk); the command is refused rather than let through unchecked."
            echo
            echo "Install $tool, or put it on PATH, then run the command again."
        } >&2
        exit 2
    fi
done

role="${1:-}"
payload="$(cat)"

if command -v jq >/dev/null 2>&1; then
    # One jq call, as this runs before every Bash command: the agent type on
    # the first line, then the command, which may span lines. The agent type
    # is kept to one line, or its tail would be read as part of the command.
    # Carriage returns (Windows' jq) are dropped by tr, which is linear: the
    # same in bash (${x//$'\r'/}) is quadratic in bash 3.2 and ran the hook
    # into its timeout, which skips the guard. A tr that prints nothing would
    # empty the command and pass it unread, so that refuses.
    raw="$(printf '%s' "$payload" \
           | jq -r '"\(.agent_type // "" | tostring | split("\n") | join(" "))\n\(.tool_input.command // "")"' \
             2>/dev/null)"
    fields="$(printf '%s' "$raw" | tr -d '\r')"
    if [ -n "$raw" ] && [ -z "$fields" ]; then
        {
            echo "BLOCKED by .claude/hooks/bash_guard.sh: the guard could not read the command (tr failed); the command is refused rather than let through unchecked."
            echo
            echo "Check that tr works, then run the command again."
        } >&2
        exit 2
    fi
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

# tr and sed ran but left nothing to read: every check below would match
# nothing and let the command through.
if [ -z "$norm" ]; then
    refuse "the guard could not read the command (tr or sed failed); the command is refused rather than let through unchecked." \
"Check that tr and sed work, then run the command again."
fi

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
#
# The rule: each `git push` in the text is read word by word up to the next
# `&&`, `||`, `;` or `|`. A forbidden flag refuses; other flags and redirects
# are skipped; the first other word is the remote, and every later one is a
# refspec, refused when it forces (+), deletes (:), or -- for a subagent --
# names a branch outside agent/.
#
# Read that way push by push, n pushes between two separators cost n times
# the words after them. So the same verdict is worked out in two linear
# passes: from the right, each word records what reading from it would meet
# up to the next separator; from the left, each `git push` looks that up.
# Whenever awk runs, the verdicts -- refused or not -- are the per-push loop's;
# only the cost changes. When one push breaks two rules, the rule a refusal
# names may be the other one. When awk fails, the push is refused.
push_refuse() {
    case "$1" in
        F) refuse "no force, delete or mirror pushes." \
"History on GitHub is shared state; a rewritten or deleted branch loses other
agents' and reviewers' work. Push new commits instead." ;;
        X) refuse "no forced (+) or deleting (:branch) refspecs." \
"Push new commits to your agent/ branch instead." ;;
        A) refuse "an agent pushes only to its agent/<N>- branch." \
"Branches outside agent/ are no agent's to write; the default branch takes
changes only through a reviewed pull request." ;;
        # E, nothing at all, and anything else awk might print: a verdict this
        # guard does not know is not a pass. (P never gets here.)
        E|*) refuse "the push check could not run (awk failed); the command is refused rather than let through unchecked." \
"The guard checks pushes with awk. Check that awk is installed and on PATH,
then run the command again." ;;
    esac
}
# One awk pass, run only when the text holds `git push`: bash 3.2's arrays
# get slower the further from their end they are written, which made the same
# two passes quadratic again. It prints the kind of the first refusal -- F a
# forbidden flag, X a + or : refspec, A a subagent's branch outside agent/ --
# or nothing. The word classes are the patterns the per-push loop used.
case "$norm" in
    *"git push"*)
        verdict="$(printf '%s\n' "$norm" | awk -v subagent="$subagent" '
            function kind(t) {
                if (t ~ /^(--force|--force=.*|--force-with-lease.*|--force-if-includes|-f|-.*f|--delete|-d|--mirror|--all|--prune)$/) return "F"
                if (t ~ /^-/ || index(t, ">") || index(t, "<")) return "S"
                if (t == "&&" || t == "||" || t == ";" || t == "|") return "B"
                return "W"
            }
            function bad(t,    d) {
                if (t ~ /^[+:]/) return "X"
                if (subagent != "1") return ""
                d = t
                if (index(d, ":")) d = substr(d, index(d, ":") + 1)
                if (substr(d, 1, 11) == "refs/heads/") d = substr(d, 12)
                return (substr(d, 1, 6) == "agent/") ? "" : "A"
            }
            {
                n = NF
                # For the words from i up to the next separator: fl[i] is F
                # when a forbidden flag is among them, bd[i] the kind of the
                # leftmost refused refspec among their words, nw[i] the index
                # of their first word. Index n+1 stands for "nothing more".
                fl[n + 1] = ""; bd[n + 1] = ""; nw[n + 1] = n + 1
                for (i = n; i >= 1; i--) {
                    k = kind($i)
                    if (k == "B") { fl[i] = ""; bd[i] = ""; nw[i] = n + 1 }
                    else if (k == "F") { fl[i] = "F"; bd[i] = bd[i + 1]; nw[i] = nw[i + 1] }
                    else if (k == "S") { fl[i] = fl[i + 1]; bd[i] = bd[i + 1]; nw[i] = nw[i + 1] }
                    else { fl[i] = fl[i + 1]; b = bad($i); bd[i] = (b != "") ? b : bd[i + 1]; nw[i] = i }
                }
                # Each `git push`: "git" ends word j and "push" starts word
                # j+1. What follows "push" in that word is read first, then
                # word j+2 onwards -- as the per-push loop read them.
                for (j = 1; j < n; j++) {
                    if ($j !~ /git$/ || substr($(j + 1), 1, 4) != "push") continue
                    rest = substr($(j + 1), 5); s = j + 2; remote_set = 0
                    if (rest != "") {
                        k = kind(rest)
                        if (k == "F") { print "F"; exit }
                        if (k == "B") continue
                        if (k == "W") remote_set = 1
                    }
                    if (fl[s] != "") { print "F"; exit }
                    if (remote_set) { if (bd[s] != "") { print bd[s]; exit } }
                    else if (nw[s] <= n && bd[nw[s] + 1] != "") { print bd[nw[s] + 1]; exit }
                }
                # Checked, nothing refused: said out loud, so that a silent
                # awk is not read as a pass.
                print "P"
            }')" || verdict=E    # awk failed or is missing: refuse, never pass unchecked
        # Only P passes. An empty verdict (a silent awk) refuses through
        # push_refuse's E|* arm.
        verdict="${verdict%$'\r'}"     # an awk on Windows may end its line with CR
        [ "$verdict" = P ] || push_refuse "$verdict" ;;
esac

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
