#!/usr/bin/env bash
# The `allowlist` required check (.github/workflows/pr-contract.yml), as a
# function so .claude/hooks/test/run_tests.sh can run it against a stub gh.
#
# The workflow runs it from `pull_request_target` with the DEFAULT branch
# checked out and no line of the pull request's code executed. So a pull
# request cannot loosen the rule that judges it: not this file, not the parser
# it sources, and not the job's own steps -- under `pull_request` all three
# came from the pull request.
#
# What it enforces, in order:
#
#   1. A fork's head is never an agent branch: `agent/<N>-` on a fork names no
#      claimed issue, it only borrows the name.
#   2. The pull request is bound to at most one issue -- the `agent/<N>-` in
#      its branch, read off the branch and never off the body the author
#      writes (the body must still close N and nothing else), or else the one
#      issue its body closes.
#   3. A pull request from the agents' account (AGENT_LOGIN) is bound, and to an
#      open agent-task issue that is in flight and has no other open pull
#      request -- it cannot borrow a closed, unclaimed or owner-held issue's
#      allowlist. Work comes from an issue (CLAUDE.md, rule one).
#   4. Every changed path, including the old path of a rename, is inside the
#      bound issue's Files in scope.
#   5. A pipeline control path (config.json `control_paths`) changes only under
#      an issue labelled `human-decision` -- the owner's work, never an agent's
#      (docs/Pipeline.md § What stays with the owner). An unbound pull request
#      from anyone else -- a dependency bot, a collaborator, a fork -- has no
#      allowlist, but may not touch a control path at all.
#
# Environment: PR, BRANCH (head ref), AUTHOR (pull request author's login),
# AGENT_LOGIN (the account the agents push as), HEAD_REPO (owner/name of the
# head repository; defaults to GITHUB_REPOSITORY), CONTROL_PATHS_FILE (one
# control path per line; must exist), GITHUB_REPOSITORY.
# Requires: gh (authenticated), jq, and lib/issue_scope.sh sourced first.
# Written for bash 3.2, like issue_scope.sh. Every gh call is checked before
# its output is piped, so the result never depends on the caller's pipefail,
# and every jq result goes through `scope_jq`, which drops the CR that jq on
# Windows ends lines with.

# Statuses an issue may carry while a pull request for it is legitimately open.
PR_ALLOWLIST_IN_FLIGHT="status:in-progress status:in-review status:escalated status:needs-human"

_pra_err() { echo "::error::$*"; }

_pra_lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

pr_allowlist_check() {
    local raw body closes branch_issue issue="" others meta state labels status s
    local cross=0 agent=0 list changed violations=0 f human=0 rc=0

    if [ -z "${CONTROL_PATHS_FILE:-}" ] || [ ! -f "$CONTROL_PATHS_FILE" ]; then
        _pra_err "No control-path list (CONTROL_PATHS_FILE) -- refusing to judge without it."
        return 1
    fi

    raw="$(gh pr view "$PR" --json body,closingIssuesReferences 2>/dev/null)" || {
        _pra_err "Could not read PR #$PR."
        return 1
    }
    body="$(printf '%s' "$raw" | scope_jq -r '.body // ""')"
    # What closes on merge: GitHub's own reading of the body (every spelling,
    # `owner/repo#N` and issue URLs included), plus the plain keywords as whole
    # words -- "prefixes #12" must not read as "fixes #12" -- in case GitHub has
    # not linked them yet.
    closes="$( { printf '%s' "$raw" | scope_jq -r '.closingIssuesReferences[]?.number'
                 printf '%s' "$body" \
                   | grep -oiE '(^|[^[:alnum:]_])(clos(e|es|ed)|fix(es|ed)?|resolv(e|es|ed)):?[[:space:]]+#[0-9]+' \
                   | grep -oE '#[0-9]+' | tr -d '#'
               } | grep -v '^$' | sort -u || true)"

    if [ -n "${HEAD_REPO:-}" ] && [ "$(_pra_lower "$HEAD_REPO")" != "$(_pra_lower "$GITHUB_REPOSITORY")" ]; then
        cross=1
    fi
    if [ "$cross" = 0 ] && [ -n "$AGENT_LOGIN" ] && [ "$(_pra_lower "$AUTHOR")" = "$(_pra_lower "$AGENT_LOGIN")" ]; then
        agent=1
    fi
    branch_issue="$(scope_issue_from_branch "$BRANCH")"

    # --- 1. and 2. what the pull request is bound to ---------------------------------
    if [ -n "$branch_issue" ] && [ "$cross" = 1 ]; then
        _pra_err "Branch '$BRANCH' comes from $HEAD_REPO. agent/<N>- branches live in this repository; a fork's is not one."
        return 1
    fi
    if [ -n "$branch_issue" ]; then
        if ! printf '%s\n' "$closes" | grep -qx "$branch_issue"; then
            _pra_err "Branch '$BRANCH' is issue #$branch_issue, but the PR body does not say 'Closes #$branch_issue'."
            return 1
        fi
        others="$(printf '%s\n' "$closes" | grep -vx "$branch_issue" | grep -v '^$' || true)"
        if [ -n "$others" ]; then
            _pra_err "Branch '$BRANCH' is issue #$branch_issue, but the PR also closes: $(echo $others)."
            echo "One issue per PR -- and a second issue would be a second allowlist this job does not check."
            return 1
        fi
        issue="$branch_issue"
    else
        if [ "$(printf '%s\n' "$closes" | grep -c .)" -gt 1 ]; then
            _pra_err "The PR closes more than one issue: $(echo $closes). One issue per PR."
            return 1
        fi
        issue="$(printf '%s\n' "$closes" | grep -v '^$' | head -1 || true)"
    fi

    # The changed files, needed from here on whatever the binding. Every path a
    # file had counts, not only where it ended up: a rename lists only its NEW
    # path in `--name-only`, so moving an out-of-scope file into an allowed
    # directory would slip through. The files API carries `previous_filename`.
    raw="$(gh api --paginate "repos/$GITHUB_REPOSITORY/pulls/$PR/files?per_page=100" 2>/dev/null)" || {
        _pra_err "Could not read the diff for PR #$PR."
        return 1
    }
    changed="$(mktemp)"
    printf '%s' "$raw" | scope_jq -r '.[] | .filename, (.previous_filename // empty)' > "$changed"
    if [ ! -s "$changed" ]; then
        _pra_err "PR #$PR reports no changed files -- refusing to pass on an empty diff."
        return 1
    fi

    if [ -z "$issue" ]; then
        if [ "$agent" = 1 ]; then
            _pra_err "PR #$PR is by the agents' account ($AUTHOR) and bound to no issue."
            echo "Work comes from an issue: push it on an agent/<N>- branch, or say 'Closes #<N>' in the body."
            return 1
        fi
        echo "Not bound to an issue and not by the agents' account ($AUTHOR): no allowlist applies,"
        echo "but a pipeline control path may not change."
        while IFS= read -r f; do
            [ -n "$f" ] || continue
            if scope_path_allowed "$f" "$CONTROL_PATHS_FILE"; then
                echo "  ->  $f   PIPELINE CONTROL PATH"
                violations=$((violations + 1))
            fi
        done < "$changed"
        if [ "$violations" -gt 0 ]; then
            _pra_err "$violations pipeline control path(s) changed by an unbound pull request."
            echo "Bind it to an issue labelled human-decision ('Closes #<N>' in the body) -- the owner's call"
            echo "(docs/Pipeline.md § What stays with the owner)."
            return 1
        fi
        echo "No control path touched."
        return 0
    fi

    # --- 3. the issue is one this pull request may work ---------------------------------
    meta="$(gh issue view "$issue" --json state,labels 2>/dev/null)" || {
        _pra_err "Could not read issue #$issue from GitHub (network or auth). Re-run the job."
        return 1
    }
    state="$(printf '%s' "$meta" | scope_jq -r '.state // ""')"
    labels=" $(printf '%s' "$meta" | scope_jq -r '[.labels[]?.name] | join(" ")') "
    if [ "$state" != "OPEN" ]; then
        _pra_err "Issue #$issue is ${state:-unreadable}, not open: a closed issue's allowlist binds nothing."
        return 1
    fi
    case "$labels" in *" human-decision "*) human=1 ;; esac
    if [ "$agent" = 1 ] || [ -n "$branch_issue" ]; then
        case "$labels" in
            *" agent-task "*) ;;
            *) _pra_err "Issue #$issue is not an agent-task issue, so this PR cannot work it."
               return 1 ;;
        esac
        status=""
        for s in $PR_ALLOWLIST_IN_FLIGHT; do
            case "$labels" in *" $s "*) status="$s" ;; esac
        done
        if [ -z "$status" ]; then
            _pra_err "Issue #$issue is not in flight (none of: $PR_ALLOWLIST_IN_FLIGHT)."
            echo "A pull request works the issue the pipeline claimed for it; #$issue is claimed by nobody."
            return 1
        fi
        raw="$(gh pr list --state open --limit 300 --json number,headRefName,isCrossRepository,closingIssuesReferences 2>/dev/null)" || {
            _pra_err "Could not list open pull requests. Re-run the job."
            return 1
        }
        others="$(printf '%s' "$raw" | scope_jq -r --arg p "agent/$issue-" --argjson n "$issue" --argjson me "$PR" \
                    '.[] | select(.number != $me and ((.isCrossRepository // false) | not)
                                  and ((.headRefName | startswith($p))
                                       or ([.closingIssuesReferences[]?.number] | index($n))))
                         | .number')"
        if [ -n "$others" ]; then
            _pra_err "Issue #$issue already has open PR(s) #$(echo $others | sed 's/ /, #/g'). One PR per issue."
            return 1
        fi
    fi

    # --- 4. and 5. every changed path ----------------------------------------------------
    echo "Checking against issue #$issue"
    list="$(mktemp)"
    scope_fetch_allowlist "$issue" > "$list" || rc=$?
    if [ "$rc" -eq 2 ]; then
        _pra_err "Could not read issue #$issue from GitHub (network or auth). Re-run the job."
        return 1
    elif [ "$rc" -ne 0 ]; then
        _pra_err "Issue #$issue has no parseable 'Files in scope' section."
        return 1
    fi
    echo "Allowed:"; sed 's/^/  - /' "$list"

    echo "Changed:"
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        if ! scope_path_allowed "$f" "$list"; then
            echo "  ->  $f   OUTSIDE THE ALLOWLIST"
            violations=$((violations + 1))
        elif [ "$human" = 0 ] && scope_path_allowed "$f" "$CONTROL_PATHS_FILE"; then
            echo "  ->  $f   PIPELINE CONTROL PATH (issue #$issue is not labelled human-decision)"
            violations=$((violations + 1))
        else
            echo "  ok  $f"
        fi
    done < "$changed"

    if [ "$violations" -gt 0 ]; then
        _pra_err "$violations file(s) outside what issue #$issue allows."
        echo "Revert the out-of-scope change, or escalate on the issue if the work"
        echo "genuinely needs the file (CONTRIBUTING-agents.md § 'When you are blocked')."
        echo "A pipeline control path is the owner's to change: the issue needs the"
        echo "human-decision label (docs/Pipeline.md § What stays with the owner)."
        echo "Widening an issue's allowlist does not re-trigger this job; editing the PR"
        echo "description or pushing does."
        return 1
    fi
    echo "All changed files are inside the allowlist."
    return 0
}
