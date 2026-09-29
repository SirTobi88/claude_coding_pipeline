#!/usr/bin/env bash
# One owner for the question "what may this branch write?"
#
# CONTRIBUTING-agents.md § *Blast radius is declared up front* makes the
# issue's **Files in scope** section an allowlist. Two places need to agree
# on what that section means, and when they disagree the disagreement is
# invisible until a correct PR is rejected or a violating one merges:
#
#   .claude/hooks/allowlist_guard.sh   before the edit happens
#   .github/workflows/pr-contract.yml  on every pull request
#   .claude/pipeline/pipeline.py       issue-lint, and scheduling overlap
#
# So all three use this file. Written for bash 3.2 -- macOS ships 3.2 and
# CI runs 5.x, and a bash-4 idiom here fails on exactly one of the two.
#
# Requires: gh (authenticated), jq.

# --- project setting -----------------------------------------------------------
#
# Generated companions. Some toolchains generate a sibling file that must be
# committed with its source -- Godot writes `x.gd.uid` beside `x.gd` and
# `x.png.import` beside `x.png`. An agent never chooses to write those, so a
# listed source file also covers `<file><suffix>` for each suffix here.
# Space-separated; empty means no companions. Set it for your stack, e.g.
#   Godot:  SCOPE_COMPANION_SUFFIXES="${SCOPE_COMPANION_SUFFIXES-.uid .import}"
SCOPE_COMPANION_SUFFIXES="${SCOPE_COMPANION_SUFFIXES-}"

# Issue number from a branch name, or empty when the branch is not an issue
# branch. `agent/<N>-<slug>` is the convention in CONTRIBUTING-agents.md
# § *Branches, commits, PRs*; anything else -- main, review-<N>, a scratch
# branch -- is deliberately unbound by an allowlist.
scope_issue_from_branch() {
    printf '%s' "$1" | sed -n 's|^agent/\([0-9][0-9]*\)-.*$|\1|p'
}

# Cache path for one issue's allowlist. Keyed by repository as well as issue
# number: the cache is machine-wide, and issue 12 of one checkout's origin is
# not issue 12 of another's.
_scope_cache_path() {
    # The readable part alone is lossy -- `tool-x` and `tool_x` both become
    # `tool_x` -- so a checksum of the exact identity goes with it.
    local repo sum
    repo="$(printf '%s' "${2:-}" | tr -c 'A-Za-z0-9' '_')"
    sum="$(printf '%s' "${2:-}" | cksum | cut -d' ' -f1)"
    printf '%s/pipeline-scope-%s-%s-%s.list' "${TMPDIR:-/tmp}" "${repo:-here}" "$sum" "$1"
}

# Echo one allowlisted path per line for issue <N>. Returns 2 if the issue
# could not be read (network, auth -- nothing wrong with the issue), 1 if it
# was read but names no paths (the issue is not ready).
#
# Cached for five minutes. Issues do change mid-flight -- an escalation is
# answered by widening the allowlist, and CONTRIBUTING-agents.md § *When you
# are blocked* makes that the expected repair -- so an unbounded cache would
# keep refusing an edit the human just authorised. Five minutes is short
# enough that the fix lands without a restart and long enough that a burst of
# edits costs one `gh` call.
#
# <repo>, optional, is the repository's identity for the cache key (its origin
# URL will do). gh reads the issue from the repository of the CURRENT
# directory, so a caller acting on another checkout runs this from there.
# SCOPE_NO_CACHE=1 reads the issue afresh -- the guard does, before it refuses.
scope_fetch_allowlist() {
    local issue="$1"
    local cache; cache="$(_scope_cache_path "$issue" "${2:-}")"

    if [ -f "$cache" ] && [ -z "${SCOPE_NO_CACHE:-}" ]; then
        local age
        age=$(( $(date +%s) - $(_scope_mtime "$cache") ))
        if [ "$age" -lt 300 ] && [ -s "$cache" ]; then
            cat "$cache"
            return 0
        fi
    fi

    local body
    body="$(gh issue view "$issue" --json body --jq .body 2>/dev/null)" || return 2
    [ -n "$body" ] || return 1

    # Written to a per-process file and moved into place, never truncated in
    # place: the cache is shared by every hook and every worktree on the
    # machine, and a reader landing between a truncate and a write would see
    # an empty allowlist. `mv` within one directory is atomic.
    local tmp="$cache.$$"
    printf '%s\n' "$body" | scope_parse_allowlist > "$tmp"
    if [ -s "$tmp" ]; then
        mv -f "$tmp" "$cache"
        cat "$cache"
        return 0
    fi
    rm -f "$tmp"
    return 1
}

# Read an issue body on stdin; echo one allowlisted path per line.
#
# The grammar -- .claude/pipeline/pipeline.py's lint reads the same one, and a
# test holds the two together:
#
#   - The section is the FIRST `## Files in scope` heading, in any letter case
#     and with anything after it (`## Files in scope (allowlist)`), up to the
#     next `## ` heading. A `###` heading is content.
#   - HTML comments and fenced code blocks are not part of it: a commented-out
#     example or a pasted snippet names no file this issue may write.
#   - An entry is a TOP-LEVEL list item -- `-`, `*`, `+` or `1.` / `1)` at the
#     list's least indentation, with an optional `[ ]` checkbox. A nested item
#     is a note about the one above it.
#   - Only the LEADING token of an item is a path: its first backticked token
#     when it starts with one (spaces allowed inside), otherwise its first word.
#     The trailing annotation is free prose that routinely names OTHER files:
#     "#12 also lists it", "that file belongs to #12". Sweeping every backticked
#     token would allowlist a file the issue says in so many words is not this
#     issue's (a real bug in the project this came from).
#   - A path contains a `/` or a `.`; a leading `./` or `/` is dropped.
#
# A section with no list items at all falls back to every backticked
# path-like token in it. That is over-permissive, and lint warns about it, but
# it is older issues' only shape and an empty allowlist would fail them.
scope_parse_allowlist() {
    local section paths
    section="$(awk '
        { sub(/\r$/, "") }
        incomment {
            i = index($0, "-->")
            if (!i) next
            $0 = substr($0, i + 3); incomment = 0
        }
        {
            while ((i = index($0, "<!--")) > 0) {
                rest = substr($0, i + 4); j = index(rest, "-->")
                if (j) { $0 = substr($0, 1, i - 1) substr(rest, j + 3) }
                else   { $0 = substr($0, 1, i - 1); incomment = 1; break }
            }
        }
        /^[ \t]*(```|~~~)/ { fence = !fence; next }
        fence { next }
        /^##[ \t]/ {
            if (f) exit
            h = tolower($0)
            if (!done && h ~ /^##[ \t]+files in scope/) { f = 1; done = 1 }
            next
        }
        f
    ')"

    paths="$(printf '%s\n' "$section" | _scope_items | _scope_leading_path)"
    [ -n "$paths" ] || paths="$(printf '%s\n' "$section" | grep -o '`[^`]*`' | tr -d '`' | _scope_paths_only)"
    printf '%s\n' "$paths" | grep -v '^$' | sort -u
}

# The section on stdin; echo the text of each top-level list item -- those at
# the list's least indentation -- without its marker and checkbox.
_scope_items() {
    awk '
        { gsub(/\t/, "    ") }
        match($0, /^ *([-*+]|[0-9]+[.)])[ \t]+/) {
            len = RLENGTH
            s = $0; sub(/^ */, "", s); ind = length($0) - length(s)
            n++; indent[n] = ind; text[n] = substr($0, len + 1)
            if (n == 1 || ind < min) min = ind
        }
        END {
            for (i = 1; i <= n; i++) {
                if (indent[i] != min) continue
                t = text[i]; sub(/^\[[ xX]\][ \t]+/, "", t); print t
            }
        }
    '
}

# One list item's text on each line; echo its leading path, if it has one.
_scope_leading_path() {
    awk '
        substr($0, 1, 1) == "`" {
            rest = substr($0, 2); j = index(rest, "`")
            if (!j) next
            p = substr(rest, 1, j - 1)
            gsub(/^[ \t]+|[ \t]+$/, "", p)
        }
        substr($0, 1, 1) != "`" {
            split($0, w, /[ \t]+/); p = w[1]
            sub(/[,;:]+$/, "", p)
            # Markdown bold around a bare path, not a `**` glob.
            if (p ~ /^\*\*[^\/]/ && p ~ /\*\*$/ && length(p) > 4) p = substr(p, 3, length(p) - 4)
        }
        p ~ /[\/.]/ { sub(/^\.\//, "", p); sub(/^\//, "", p); print p }
    '
}

# Keep the tokens that look like a path: a slash or a dot, no whitespace.
_scope_paths_only() {
    grep -E '^[^[:space:]]*[/.][^[:space:]]*$' | sed 's|^\./||; s|^/||'
}

# File mtime in epoch seconds, or 0 when it cannot be read. BSD stat and GNU
# stat disagree on the flag, and this runs on both. GNU is tried first because
# the BSD spelling is not an error there: `stat -f` is GNU's filesystem mode,
# so `stat -f %m f` prints a block of filesystem status for `f` before failing
# on the operand `%m` -- and that block would land in the arithmetic.
_scope_mtime() {
    local t
    t="$(stat -c %Y "$1" 2>/dev/null)" || t="$(stat -f %m "$1" 2>/dev/null)" || t=0
    case "$t" in ''|*[!0-9]*) t=0 ;; esac
    printf '%s\n' "$t"
}

# Is <path> (repo-relative) covered by the allowlist in <list-file>?
# Exit 0 = allowed, 1 = not.
scope_path_allowed() {
    local path="$1" list="$2" entry

    # A generated companion (SCOPE_COMPANION_SUFFIXES, top of this file) is
    # covered by its source. Without this, every change to a file the
    # toolchain shadows would trip the guard on a file the author never chose
    # to write -- in the project this came from, every script and every asset
    # PR failed that way until the rule existed.
    local companion="" suffix
    for suffix in $SCOPE_COMPANION_SUFFIXES; do
        case "$path" in
            *"$suffix") companion="${path%"$suffix"}"; break ;;
        esac
    done

    while IFS= read -r entry; do
        [ -n "$entry" ] || continue
        _scope_entry_covers "$entry" "$path" && return 0
        [ -n "$companion" ] && _scope_entry_covers "$entry" "$companion" && return 0
    done < "$list"

    return 1
}

# Does one allowlist entry cover one path?
_scope_entry_covers() {
    local entry="$1" path="$2"

    [ "$entry" = "$path" ] && return 0

    # A trailing slash, or an entry with no dot in its last segment, is a
    # directory: it covers everything beneath it. Issues write both forms.
    case "$entry" in
        */) case "$path" in "$entry"*) return 0 ;; esac ;;
        *[*?[]*)                                              # glob entry
            # `**` also stands for no directory at all, as in .gitignore:
            # `docs/**/*.md` covers `docs/x.md`, `**/x.gd` covers a root `x.gd`.
            case "$entry" in
                '**/'*) _scope_entry_covers "${entry#\*\*/}" "$path" && return 0 ;;
            esac
            case "$entry" in
                */'**'/*) _scope_entry_covers "${entry%%/\*\*/*}/${entry#*/\*\*/}" "$path" && return 0 ;;
            esac
            # `case` lets `*` cross `/`, so `ui/*.gd` would also grant
            # `ui/deep/nested.gd`. Hold a glob to its own depth unless it
            # says `**`, which is the spelling that means "and below".
            case "$path" in $entry) ;; *) return 1 ;; esac
            case "$entry" in *'**'*) return 0 ;; esac
            [ "$(_scope_depth "$entry")" = "$(_scope_depth "$path")" ] && return 0
            ;;
        *)
            case "${entry##*/}" in
                *.*) : ;;                                     # has an extension: a file
                *)   case "$path" in "$entry"/*) return 0 ;; esac ;;
            esac
            ;;
    esac

    return 1
}

# Number of `/` in a path.
_scope_depth() {
    printf '%s' "$1" | tr -cd '/' | wc -c | tr -d ' '
}
