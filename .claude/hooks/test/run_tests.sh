#!/usr/bin/env bash
# Tests for the allowlist enforcement: lib/issue_scope.sh and
# allowlist_guard.sh.
#
#   bash .claude/hooks/test/run_tests.sh
#
# Exits 0 when every case passes. Runs offline: `gh` is a stub on PATH that
# answers from fixed fixtures, so a case never depends on what is open on
# GitHub today. Runs under bash 3.2 (macOS) and 5.x (CI's ubuntu runner) --
# the hooks run on both, and each has already broken on exactly one of them.
#
# Every case is one row: what the hook is given, and the exit code it must
# return. Many are FAIL-OPEN regressions -- a write an earlier version of the
# guard waved through. When one of those starts passing again, the guard has
# a hole.

set -u

# The hooks read these from the environment. The suite sets each one where a
# case needs it and must see none of the caller's: an implementer inside a tick
# runs this with PIPELINE_TICK=1, and a case that assumes the owner's session
# failed there in the first live run.
unset PIPELINE_TICK SCOPE_NO_CACHE SCOPE_COMPANION_SUFFIXES CONTROL_PATHS_FILE \
      PIPELINE_REVIEWER_TOKEN PIPELINE_REVIEWER_TOKEN_FILE PIPELINE_REVIEWER_LOGIN \
      PR_ALLOWLIST_IN_FLIGHT AGENT_LOGIN GITHUB_REPOSITORY HEAD_REPO

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS="$(cd "$HERE/.." && pwd)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# The allowlist cache lives under $TMPDIR and is shared machine-wide. Point it
# at this run's own directory so a real cached issue cannot leak in, and so
# concurrent runs cannot see each other's fixtures.
export TMPDIR="$WORK/tmp"
mkdir -p "$TMPDIR"

pass=0; fail=0
ok()  { pass=$((pass + 1)); }
bad() { fail=$((fail + 1)); printf 'FAIL  %s\n' "$1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/      /'; }

expect_eq() {  # <name> <expected> <actual>
    if [ "$2" = "$3" ]; then ok; else bad "$1" "expected: $2
actual:   $3"; fi
}

# --- stub gh ----------------------------------------------------------------

BIN="$WORK/bin"
FX="$WORK/fixtures"
mkdir -p "$BIN" "$FX"
GH_LOG="$WORK/gh.log"
export GH_LOG GH_FIXTURES="$FX"
: > "$GH_LOG"

# The stub answers every call from a file under $GH_FIXTURES, so a case sets up
# GitHub's state by writing files:
#
#   issue-<N>.body.md    `gh issue view N --json body --jq .body`
#   issue-<N>.meta.json  `gh issue view N --json state,labels`
#   pr-<N>.json          `gh pr view N --json body`
#   prs.json             `gh pr list ...`
#   files-<N>.json       `gh api --paginate repos/.../pulls/N/files...`
#
# A missing file is a failed call.
cat > "$BIN/gh" <<'STUB'
#!/usr/bin/env bash
echo "$PWD :: $*" >> "$GH_LOG"
fx="$GH_FIXTURES"
case "$1 $2" in
  "issue view")
    case " $* " in
      *" state,labels "*) f="$fx/issue-$3.meta.json" ;;
      *)                  f="$fx/issue-$3.body.md" ;;
    esac ;;
  "pr view")        f="$fx/pr-$3.json" ;;
  "pr list")        f="$fx/prs.json" ;;
  "api --paginate") f="$fx/files-$(printf '%s' "$3" | sed -n 's|.*/pulls/\([0-9]*\)/files.*|\1|p').json" ;;
  *) exit 1 ;;
esac
[ -f "$f" ] || exit 1
cat "$f"
STUB
chmod +x "$BIN/gh"
export PATH="$BIN:$PATH"

# Issue #228 in a real issue's shape: the template's checklist items, followed
# by prose that names OTHER files -- the exact thing the parser must not read
# as an entry.
cat > "$FX/issue-228.body.md" <<'BODY'
## Context

Touch `Design/Systems/Simulation.md` only by reading it.

## Files in scope

- [ ] `src/sim/town_market.gd` (modify -- stub landed in #229)
- [ ] `src/test/sim/` (new directory)
- [ ] `src/scripts/ui/*.gd` (modify)
- [ ] `Documentation/**/*.md` (modify)
- [ ] src/sim/plain.gd (modify -- written without backticks)
- [ ] see the note below

`src/scripts/main.gd` belongs to #225 -- do **not** touch `CLAUDE.md`.

## Definition of done

- [ ] `./run_tests.sh` exits 0
BODY
cat > "$FX/issue-300.body.md" <<'BODY'
## Files in scope

Edit `src/sim/tick.gd` and `src/test/sim/test_tick.gd`.

## Non-goals
BODY

command -v jq >/dev/null 2>&1 || { echo "jq is required to run these tests"; exit 1; }

# --- lib/issue_scope.sh -----------------------------------------------------

# shellcheck source=../lib/issue_scope.sh
. "$HOOKS/lib/issue_scope.sh"

expect_eq "branch -> issue"        228 "$(scope_issue_from_branch agent/228-market-screen)"
expect_eq "non-agent branch"       ""  "$(scope_issue_from_branch review-228)"
expect_eq "chore branch"           ""  "$(scope_issue_from_branch chore/agent/228-x)"

expect_eq "checklist items only, prose ignored" \
"Documentation/**/*.md
src/scripts/ui/*.gd
src/sim/plain.gd
src/sim/town_market.gd
src/test/sim/" \
    "$(scope_fetch_allowlist 228)"

expect_eq "no checklist -> prose fallback" \
"src/sim/tick.gd
src/test/sim/test_tick.gd" \
    "$(scope_fetch_allowlist 300)"

scope_fetch_allowlist 999 >/dev/null 2>&1
expect_eq "unreadable issue returns 2 (fetch failure)" 2 "$?"

: > "$GH_LOG"
scope_fetch_allowlist 228 >/dev/null
expect_eq "second fetch is served from cache" 0 "$(grep -c 'issue view' "$GH_LOG")"

m="$(_scope_mtime "$HOOKS/allowlist_guard.sh")"
case "$m" in ''|*[!0-9]*) bad "mtime is numeric" "got: $m" ;; *) ok ;; esac
expect_eq "mtime of a missing file is 0" 0 "$(_scope_mtime "$WORK/nope")"

# The grammar (issue_scope.sh, scope_parse_allowlist). One body, every shape an
# issue has been written in; only the entries marked ENTRY may come out.
parse() { printf '%s\n' "$1" | scope_parse_allowlist | tr '\n' ' ' | sed 's/ $//'; }
expect_eq "parser: grammar" "assets/ui files/icon.png docs/**/*.md src/after_sub.py src/bold.py src/c.py src/plain.gd" "$(parse '## Files In Scope (allowlist)

<!-- an example, not an entry:
- [ ] `src/example.gd`
-->
- [ ] `src/c.py` (modify) -- not `src/prose_named.py`, that is #12s
  - `src/nested_note.py` belongs to #12
+ `assets/ui files/icon.png` (create)
1. `docs/**/*.md` (modify)
2) **src/bold.py** (modify)
* src/plain.gd, (modify)

```python
## not a heading
- `src/in_fence.py`
```

### A sub-heading is content
- `src/after_sub.py`

## Non-goals

## Files in scope

- `src/second_section.py`')"
expect_eq "parser: CRLF body" "src/a.py src/b.py" "$(printf '## Files in scope\r\n\r\n- `src/a.py`\r\n- src/b.py\r\n' | scope_parse_allowlist | tr '\n' ' ' | sed 's/ $//')"
expect_eq "parser: a list nested one level everywhere" "src/a.py src/b.py" "$(parse '## Files in scope

  - `src/a.py`
  - `src/b.py`')"

a="$(_scope_cache_path 12 https://github.com/o/one.git)"
b="$(_scope_cache_path 12 https://github.com/o/two.git)"
[ "$a" != "$b" ] && ok || bad "cache is keyed by repository" "$a = $b"
a="$(_scope_cache_path 12 https://github.com/o/tool-x.git)"
b="$(_scope_cache_path 12 https://github.com/o/tool_x.git)"
[ "$a" != "$b" ] && ok || bad "cache key survives punctuation" "$a = $b"

LIST="$WORK/list"
scope_fetch_allowlist 228 > "$LIST"
allowed() { scope_path_allowed "$1" "$LIST" && echo yes || echo no; }
# Empty means none. Set here, not read from issue_scope.sh, where a project
# sets its own (docs/ADOPTING.md § C).
SCOPE_COMPANION_SUFFIXES=""
expect_eq "no companions when none are set" no "$(allowed src/sim/town_market.gd.uid)"
# The companion cases below use the Godot setting from issue_scope.sh's header.
SCOPE_COMPANION_SUFFIXES=".uid .import"
expect_eq "exact file"                    yes "$(allowed src/sim/town_market.gd)"
expect_eq ".gd.uid companion"             yes "$(allowed src/sim/town_market.gd.uid)"
expect_eq "dir entry covers below"        yes "$(allowed src/test/sim/deep/test_x.gd)"
expect_eq "glob at its own depth"         yes "$(allowed src/scripts/ui/market_screen.gd)"
expect_eq "glob companion"                yes "$(allowed src/scripts/ui/market_screen.gd.uid)"
expect_eq "glob does not cross /"         no  "$(allowed src/scripts/ui/deep/nested.gd)"
expect_eq "** does cross /"               yes "$(allowed Documentation/Technical/Deep/x.md)"
expect_eq "prose-named file not allowed"  no  "$(allowed src/scripts/main.gd)"
expect_eq "'do not touch' not allowed"    no  "$(allowed CLAUDE.md)"
expect_eq "Context section not allowed"   no  "$(allowed Design/Systems/Simulation.md)"
expect_eq "sibling prefix not allowed"    no  "$(allowed src/sim/town_market.gd.bak)"
expect_eq ".import companion"             yes "$(allowed src/sim/town_market.gd.import)"
expect_eq ".import of an unlisted file"   no  "$(allowed src/assets/ui/icon.png.import)"
covers() { _scope_entry_covers "$1" "$2" && echo yes || echo no; }
expect_eq "** matches no directory too"   yes "$(covers 'docs/**/*.md' docs/x.md)"
expect_eq "** matches several"            yes "$(covers 'docs/**/*.md' docs/a/b/x.md)"
expect_eq "leading **/ matches the root"  yes "$(covers '**/x.gd' x.gd)"
expect_eq "two ** collapse together"      yes "$(covers 'a/**/b/**/*.md' a/b/x.md)"
expect_eq "** stays inside its prefix"    no  "$(covers 'docs/**/*.md' other/x.md)"
expect_eq "[ab] is a glob"                yes "$(covers 'src/[ab].py' src/a.py)"
expect_eq "[ab] matches nothing else"     no  "$(covers 'src/[ab].py' src/c.py)"
expect_eq "a path with a space"           yes "$(covers 'assets/ui files/icon.png' 'assets/ui files/icon.png')"

# --- allowlist_guard.sh ------------------------------------------------------

# A repo reached through a symlink, so every path the guard is handed is
# spelled differently from what git reports -- the macOS /tmp ->
# /private/tmp case, and the one that twice made the guard fail open.
REPO_REAL="$WORK/real/repo"
mkdir -p "$REPO_REAL/src/sim" "$REPO_REAL/src/scripts/ui"
git -C "$REPO_REAL" init -q
git -C "$REPO_REAL" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
git -C "$REPO_REAL" checkout -q -b agent/228-probe
# Git Bash's `ln -s` silently makes a COPY unless Windows symlinks are enabled,
# and a copy is a second repository that never sees the branch changes below.
# Where there are no symlinks there is no symlink-spelling hazard to test, so
# those cases run against the real path instead.
ln -s "$WORK/real" "$WORK/link" 2>/dev/null
if [ -L "$WORK/link" ]; then
    REPO="$WORK/link/repo"
else
    rm -rf "$WORK/link"
    REPO="$REPO_REAL"
fi

guard() {  # <cwd> <file_path> -> exit code
    jq -n --arg c "$1" --arg f "$2" '{cwd: $c, tool_input: {file_path: $f}}' \
        | "$BASH" "$HOOKS/allowlist_guard.sh" >/dev/null 2>&1
    echo $?
}
expect_eq "guard: in scope"                         0 "$(guard "$REPO" "$REPO/src/sim/town_market.gd")"
expect_eq "guard: out of scope, existing dir"       2 "$(guard "$REPO" "$REPO/src/sim/other.gd")"
expect_eq "guard: out of scope, NEW dir"            2 "$(guard "$REPO" "$REPO/src/brand/new/x.gd")"
expect_eq "guard: out of scope, repo root"          2 "$(guard "$REPO" "$REPO/CLAUDE.md")"
expect_eq "guard: in scope, NEW dir under dir entry" 0 "$(guard "$REPO" "$REPO/src/test/sim/new/test_y.gd")"
expect_eq "guard: relative path"                    2 "$(guard "$REPO" "CLAUDE.md")"
expect_eq "guard: .. escaping a dir entry"          2 "$(guard "$REPO" "$REPO/src/test/sim/new/../../../CLAUDE.md")"
expect_eq "guard: outside the repo"                 0 "$(guard "$REPO" "$WORK/elsewhere/x.txt")"
# Windows spelling, as Claude Code hands it over on Windows. Only where cygpath
# exists -- elsewhere there is no such spelling to receive.
if command -v cygpath >/dev/null 2>&1; then
    expect_eq "guard: Windows path, in scope"  0 "$(guard "$(cygpath -w "$REPO")" "$(cygpath -w "$REPO/src/sim/town_market.gd")")"
    expect_eq "guard: Windows path, out of scope" 2 "$(guard "$(cygpath -w "$REPO")" "$(cygpath -w "$REPO/CLAUDE.md")")"
    expect_eq "guard: mixed C:/ path, in scope" 0 "$(guard "$(cygpath -m "$REPO")" "$(cygpath -m "$REPO/src/sim/town_market.gd")")"
fi
printf 'scratch/\n' > "$REPO_REAL/.gitignore"
expect_eq "guard: gitignored path is not the diff's" 0 "$(guard "$REPO" "$REPO/scratch/notes.txt")"
expect_eq "guard: ignore file itself still guarded"  2 "$(guard "$REPO" "$REPO/.gitignore")"
rm -f "$REPO_REAL/.gitignore"
git -C "$REPO_REAL" checkout -q -b review-228
expect_eq "guard: non-agent branch is unbound"      0 "$(guard "$REPO" "$REPO/CLAUDE.md")"
git -C "$REPO_REAL" checkout -q agent/228-probe

# The branch that binds is the one holding the FILE, not the session's cwd: a
# session in an unbound checkout writing by absolute path into an agent
# worktree is bound by that worktree's issue, and the reverse is not.
OTHER="$WORK/other"
git init -q "$OTHER"
git -C "$OTHER" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
git -C "$OTHER" checkout -q -b main-ish
expect_eq "guard: cwd unbound, file in agent worktree" 2 "$(guard "$OTHER" "$REPO/CLAUDE.md")"
expect_eq "guard: cwd in agent worktree, file elsewhere" 0 "$(guard "$REPO" "$OTHER/CLAUDE.md")"
# NotebookEdit names its file `notebook_path`.
nb="$(jq -n --arg c "$REPO" --arg f "$REPO/CLAUDE.ipynb" '{cwd: $c, tool_input: {notebook_path: $f}}' \
      | "$BASH" "$HOOKS/allowlist_guard.sh" >/dev/null 2>&1; echo $?)"
expect_eq "guard: NotebookEdit out of scope"            2 "$nb"

# Letter case differs from git's (Windows, macOS): the path is still judged,
# not taken for "outside the repository" and allowed.
if [ -e "$(printf '%s' "$REPO_REAL" | tr '[:lower:]' '[:upper:]')" ]; then
    UP="$(printf '%s' "$REPO_REAL" | tr '[:lower:]' '[:upper:]')"
    expect_eq "guard: other case, out of scope"          2 "$(guard "$UP" "$UP/CLAUDE.md")"
    # The directories are spelled differently; the new file's own name is
    # what gets created, so it must match the allowlist as typed.
    expect_eq "guard: other case dirs, in scope"         0 "$(guard "$UP" "$UP/SRC/SIM/town_market.gd")"
    expect_eq "guard: a new file named in other case"    2 "$(guard "$UP" "$UP/SRC/SIM/TOWN_MARKET.GD")"
fi

# Issue read, but no parseable path: nothing may be written (CI would refuse
# all of it). Before, the guard failed open here.
printf '## Files in scope\n\nsee the design doc\n' > "$FX/issue-301.body.md"
git -C "$REPO_REAL" checkout -q -b agent/301-empty
expect_eq "guard: issue with no parseable path"       2 "$(guard "$REPO" "$REPO/src/sim/town_market.gd")"
git -C "$REPO_REAL" checkout -q agent/228-probe

# Widened since the cache was filled: the guard reads again before refusing.
guard "$REPO" "$REPO/src/sim/town_market.gd" >/dev/null     # fills the cache
cp "$FX/issue-228.body.md" "$WORK/issue-228.orig"
printf '\n## Files in scope\n\n- [ ] `docs/widened.md`\n' > "$WORK/extra"
awk 'NR==FNR{extra=extra $0 "\n"; next} /^## Files in scope/{print; getline; print; printf "%s", substr(extra, index(extra, "- ")); next} 1' \
    "$WORK/extra" "$WORK/issue-228.orig" > "$FX/issue-228.body.md"
expect_eq "guard: a just-widened allowlist counts"     0 "$(guard "$REPO" "$REPO/docs/widened.md")"
cp "$WORK/issue-228.orig" "$FX/issue-228.body.md"
rm -f "$TMPDIR"/pipeline-scope-*

# From one worktree into another of the same repository -- the main checkout
# included -- is refused, whatever the target branch is.
WT="$REPO_REAL/.claude/worktrees/agent-228-wt"
git -C "$REPO_REAL" worktree add -q -b agent/228-wt "$WT" 2>/dev/null
expect_eq "guard: worktree writes into its own tree"   0 "$(guard "$WT" "$WT/src/sim/town_market.gd")"
expect_eq "guard: worktree writes into the main tree"  2 "$(guard "$WT" "$REPO_REAL/src/sim/town_market.gd")"
git -C "$REPO_REAL" checkout -q -b main-ish-2
expect_eq "guard: ...even an unbound main tree"        2 "$(guard "$WT" "$REPO_REAL/CLAUDE.md")"
expect_eq "guard: the main tree may write anywhere"    0 "$(guard "$REPO_REAL" "$REPO_REAL/CLAUDE.md")"
git -C "$REPO_REAL" checkout -q agent/228-probe
git -C "$REPO_REAL" worktree remove --force "$WT" 2>/dev/null

rm -f "$TMPDIR"/pipeline-scope-*
: > "$GH_LOG"
guard "$OTHER" "$REPO/CLAUDE.md" >/dev/null
fetched_in="$(grep 'issue view' "$GH_LOG" | head -1 | sed 's/ :: .*//')"
# Under Git Bash one directory has two POSIX spellings (/tmp/x and
# /c/Users/.../Temp/x); compare them in one canonical Windows form there.
canon() { local p; p="$(cd "$1" 2>/dev/null && pwd -P)"; if command -v cygpath >/dev/null 2>&1; then cygpath -m "$p"; else printf '%s' "$p"; fi; }
expect_eq "guard: issue is read from the file's checkout" "$(canon "$REPO_REAL")" "$(canon "$fetched_in")"

# --- bash_guard.sh -------------------------------------------------------------

bguard() {  # <command> <agent_type, or "" for the main session> [role] -> exit code
    jq -n --arg c "$1" --arg a "$2" \
        'if $a == "" then {tool_input: {command: $c}} else {tool_input: {command: $c}, agent_type: $a} end' \
        | "$BASH" "$HOOKS/bash_guard.sh" ${3:-} >/dev/null 2>&1
    echo $?
}
R=".claude/bin/gh-reviewer pr review 5 --approve"
expect_eq "bash guard: the reviewer agent acts as the bot"      0 "$(bguard "$R" github-pr-reviewer)"
expect_eq "bash guard: the main session may (owner, review skill)" 0 "$(bguard "$R" "")"
expect_eq "bash guard: an implementer may not"                  2 "$(bguard "$R" github-issue-resolver)"
expect_eq "bash guard: any other subagent may not"              2 "$(bguard "$R" general-purpose)"
expect_eq "bash guard: a role never may, whatever agent_type"   2 "$(bguard "$R" github-pr-reviewer implementer)"
expect_eq "bash guard: reading the token file"                  2 "$(bguard "cat ~/.config/claude-pipeline/my-project/reviewer-token" github-issue-resolver)"
expect_eq "bash guard: the token through the environment"       2 "$(bguard 'GH_TOKEN=$PIPELINE_REVIEWER_TOKEN gh pr review 5 --approve' github-triage)"
expect_eq "bash guard: protection DELETE, even the main session" 2 "$(bguard "gh api -X DELETE repos/o/r/branches/main/protection" "")"
expect_eq "bash guard: protection written with -f"              2 "$(bguard "gh api repos/o/r/branches/main/protection/enforce_admins -f x=y" "")"
expect_eq "bash guard: protection --method=delete"              2 "$(bguard "gh api repos/o/r/branches/main/protection --method=delete" "")"
expect_eq "bash guard: ruleset PUT"                             2 "$(bguard "gh api -XPUT repos/o/r/rulesets/7 --input r.json" "")"
expect_eq "bash guard: reading protection is fine"              0 "$(bguard "gh api repos/o/r/branches/main/protection" "")"
expect_eq "bash guard: implementer edits an issue"              2 "$(bguard "gh issue edit 5 --body-file x.md" github-issue-resolver implementer)"
expect_eq "bash guard: implementer creates an issue"            2 "$(bguard "gh issue create --title x" github-issue-resolver implementer)"
expect_eq "bash guard: implementer comments on its issue"       0 "$(bguard "gh issue comment 5 --body 'blocked on x'" github-issue-resolver implementer)"
expect_eq "bash guard: implementer escalates"                   0 "$(bguard ".claude/bin/pipeline set-status 5 status:escalated" github-issue-resolver implementer)"
expect_eq "bash guard: implementer reads reviews via api"       0 "$(bguard "gh api repos/o/r/pulls/5/reviews" github-issue-resolver implementer)"
expect_eq "bash guard: implementer GET with -F is still a read" 0 "$(bguard "gh api -X GET repos/o/r/pulls/5/comments -F per_page=100" github-issue-resolver implementer)"
expect_eq "bash guard: implementer writes via api"              2 "$(bguard "gh api repos/o/r/issues/5 -X PATCH -f body=x" github-issue-resolver implementer)"
expect_eq "bash guard: implementer merges"                      2 "$(bguard "gh pr merge 5 --squash" github-issue-resolver implementer)"
expect_eq "bash guard: implementer pushes its branch"           0 "$(bguard "git push -u origin agent/5-x" github-issue-resolver implementer)"
expect_eq "bash guard: triage edits an issue"                   0 "$(bguard "gh issue edit 5 --body-file .pipeline-tmp/issue-5.md" github-triage triage)"
expect_eq "bash guard: triage pushes"                           2 "$(bguard "git push origin agent/5-x" github-triage triage)"
expect_eq "bash guard: planner merges"                          2 "$(bguard "gh pr merge 5" github-planner planner)"
expect_eq "bash guard: an ordinary command"                     0 "$(bguard "git status" github-issue-resolver implementer)"
expect_eq "bash guard: the scheduled tick may not act as the bot" 2 "$( export PIPELINE_TICK=1; bguard "$R" "" )"
expect_eq "bash guard: ...but its reviewer subagent may"        0 "$( export PIPELINE_TICK=1; bguard "$R" github-pr-reviewer )"
expect_eq "bash guard: quoted method"                           2 "$(bguard "gh api repos/o/r/branches/main/protection -X \"DELETE\"" "")"
expect_eq "bash guard: method after a double space"             2 "$(bguard "gh api --method  PATCH repos/o/r/issues/5" github-issue-resolver implementer)"
expect_eq "bash guard: protection through GraphQL"              2 "$(bguard "gh api graphql -f query='mutation { deleteBranchProtectionRule(input: {}) { clientMutationId } }'" "")"
expect_eq "bash guard: setup-repo from a session"               2 "$(bguard ".claude/bin/pipeline setup-repo" "")"
expect_eq "bash guard: setup-repo through python"               2 "$(bguard "python3 .claude/pipeline/pipeline.py setup-repo --dry-run" "")"
expect_eq "bash guard: setup-repo after &&"                     2 "$(bguard "cd x && .claude/bin/pipeline setup-repo" "")"
expect_eq "bash guard: naming setup-repo is not running it"     0 "$(bguard "grep -n 'pipeline setup-repo' docs/Pipeline.md" "")"
expect_eq "bash guard: implementer edits via -R"                2 "$(bguard "gh -R o/r issue edit 5 --add-label x" github-issue-resolver implementer)"
expect_eq "bash guard: implementer edits via --repo later"      2 "$(bguard "gh issue --repo o/r edit 5" github-issue-resolver implementer)"
expect_eq "bash guard: force push, flag last"                   2 "$(bguard "git push origin agent/5-x --force" github-issue-resolver implementer)"
expect_eq "bash guard: force push, -f"                          2 "$(bguard "git push -f origin agent/5-x" "")"
expect_eq "bash guard: force-with-lease, main session"          2 "$(bguard "git push --force-with-lease origin agent/5-x" "")"
expect_eq "bash guard: + refspec"                               2 "$(bguard "git push origin +agent/5-x" github-issue-resolver implementer)"
expect_eq "bash guard: deleting refspec"                        2 "$(bguard "git push origin :agent/5-x" "")"
expect_eq "bash guard: --delete"                                2 "$(bguard "git push origin --delete agent/5-x" "")"
expect_eq "bash guard: subagent pushes onto main"               2 "$(bguard "git push origin agent/5-x:main" github-issue-resolver implementer)"
expect_eq "bash guard: subagent pushes a non-agent branch"      2 "$(bguard "git push origin chore/x" github-issue-resolver)"
expect_eq "bash guard: reviewer pushes HEAD to the PR branch"   0 "$(bguard "git push origin HEAD:agent/5-x" github-pr-reviewer)"
expect_eq "bash guard: a push piped through tail"               0 "$(bguard "git push -u origin agent/5-x 2>&1 | tail -3" github-issue-resolver implementer)"
expect_eq "bash guard: the owner pushes a feature branch"       0 "$(bguard "git push -u origin chore/welle-0" "")"
expect_eq "bash guard: triage applies human-decision"           2 "$(bguard "gh issue edit 5 --add-label human-decision" github-triage triage)"
expect_eq "bash guard: planner files a human-decision issue"    2 "$(bguard "gh issue create --title q --label human-decision" github-planner planner)"
expect_eq "bash guard: planner lists human-decision issues"     0 "$(bguard "gh issue list --label human-decision --state open" github-planner planner)"
expect_eq "bash guard: the owner applies human-decision"        0 "$(bguard "gh issue edit 5 --add-label human-decision" "")"
expect_eq "bash guard: the bot through bash"                    2 "$(bguard "bash .claude/bin/gh-reviewer pr review 5 --approve" github-issue-resolver implementer)"
expect_eq "bash guard: the bot after &&"                        2 "$(bguard "cd x && .claude/bin/gh-reviewer api user" github-issue-resolver)"
expect_eq "bash guard: the bot inside \$( )"                    2 "$(bguard 'echo "$(.claude/bin/gh-reviewer api user)"' github-triage triage)"
expect_eq "bash guard: the bot through env"                     2 "$(bguard "env FOO=1 .claude/bin/gh-reviewer pr merge 5" github-issue-resolver)"
expect_eq "bash guard: naming the wrapper is not using it"      0 "$(bguard "grep -n gh-reviewer docs/Pipeline.md" github-issue-resolver implementer)"
expect_eq "bash guard: git -C force push"                       2 "$(bguard "git -C .claude/worktrees/review-5 push --force origin HEAD:agent/5-x" github-pr-reviewer)"
expect_eq "bash guard: git -C push onto main"                   2 "$(bguard "git -C .claude/worktrees/review-5 push origin HEAD:main" github-pr-reviewer)"
expect_eq "bash guard: reviewer pushes from its worktree"       0 "$(bguard "git -C .claude/worktrees/review-5 push origin HEAD:agent/5-x" github-pr-reviewer)"
expect_eq "bash guard: triage may not push via git -C"          2 "$(bguard "git -C x push origin agent/5-x" github-triage triage)"
# A newline separates commands, as `;` does (#36); a heredoc body is a mention.
expect_eq "bash guard: gh-reviewer on a later line"             2 "$(bguard "cd x
.claude/bin/gh-reviewer api user" github-issue-resolver implementer)"
expect_eq "bash guard: setup-repo on a later line"              2 "$(bguard "cd x
.claude/bin/pipeline setup-repo" "")"
expect_eq "bash guard: a push, then another command"            0 "$(bguard "git push origin agent/1-x
ls" github-issue-resolver implementer)"
expect_eq "bash guard: a heredoc that names gh-reviewer"        0 "$(bguard "cat > f <<EOF
.claude/bin/gh-reviewer api user
EOF" github-issue-resolver implementer)"
expect_eq "bash guard: a call after a heredoc ends"             2 "$(bguard "cat > f <<'EOF'
text
EOF
.claude/bin/gh-reviewer api user" github-issue-resolver implementer)"
expect_eq "bash guard: a <<- heredoc ends at a tabbed delimiter" 2 "$(bguard "cat > f <<-EOF
	text
	EOF
.claude/bin/gh-reviewer api user" github-issue-resolver implementer)"
expect_eq "bash guard: a <<\\EOF heredoc is a mention"           0 "$(bguard "cat > f <<\\EOF
.claude/bin/gh-reviewer api user
EOF" github-issue-resolver implementer)"
expect_eq "bash guard: a quoted delimiter with a dash ends"     2 "$(bguard "cat > f <<'END-X'
text
END-X
.claude/bin/gh-reviewer api user" github-issue-resolver implementer)"
long_body="$(i=0; while [ $i -lt 20000 ]; do echo "line $i of a long heredoc"; i=$((i + 1)); done)"
long_start=$SECONDS
expect_eq "bash guard: a call after a 20000-line heredoc"       2 "$(bguard "cat > f <<EOF
$long_body
EOF
.claude/bin/gh-reviewer api user" github-issue-resolver implementer)"
expect_eq "bash guard: a 20000-line heredoc well inside the hook timeout" yes \
    "$([ $((SECONDS - long_start)) -lt 5 ] && echo yes || echo no)"
expect_eq "bash guard: a here-string is not a heredoc"          2 "$(bguard "cat <<<EOF
.claude/bin/gh-reviewer api user" github-issue-resolver implementer)"
expect_eq "bash guard: a backslash continues the push"          0 "$(bguard "git push origin \\
agent/1-x" github-issue-resolver implementer)"
expect_eq "bash guard: a continued push onto main"              2 "$(bguard "git push origin \\
main" github-issue-resolver implementer)"
expect_eq "bash guard: a second push to main, on a later line"  2 "$(bguard "git push origin agent/1-x
git push origin main" github-issue-resolver implementer)"
expect_eq "bash guard: a second push, forced, after ;"          2 "$(bguard "git push origin agent/1-x; git push --force origin agent/1-x" github-issue-resolver implementer)"
expect_eq "bash guard: the owner's second push deletes main"    2 "$(bguard "git push origin agent/1-x && git push --delete origin main" "")"
expect_eq "bash guard: two pushes to agent/ branches"           0 "$(bguard "git push origin agent/1-x && git push origin agent/2-y" github-issue-resolver implementer)"
expect_eq "bash guard: a push to main on a later line"          2 "$(bguard "ls
git push origin main" github-issue-resolver implementer)"

# --- lib/pr_allowlist.sh: the `allowlist` required check ------------------------

# shellcheck source=../lib/pr_allowlist.sh
. "$HOOKS/lib/pr_allowlist.sh"
export GITHUB_REPOSITORY=o/r AGENT_LOGIN=owner
printf '.github/\n.claude/\nrun_tests.sh\n' > "$WORK/control_paths"
export CONTROL_PATHS_FILE="$WORK/control_paths"

meta() {  # <issue> <state> [label...]
    local n="$1" st="$2"; shift 2
    if [ $# -eq 0 ]; then set -- ""; fi
    printf '%s\n' "$@" | jq -R . | jq -s --arg s "$st" '{state: $s, labels: map(select(. != "") | {name: .})}' \
        > "$FX/issue-$n.meta.json"
}
prbody() {  # <pr> <body> [issue GitHub links as closing...]
    local n="$1" b="$2"; shift 2
    printf '%s\n' "$@" | jq -R 'select(. != "") | {number: tonumber}' | jq -s --arg b "$b" \
        '{body: $b, closingIssuesReferences: .}' > "$FX/pr-$n.json"
}
files() {  # <pr> <path>... ; "old->new" is a rename
    local n="$1"; shift
    printf '%s\n' "$@" \
        | jq -R 'split("->") | if length == 2 then {filename: .[1], previous_filename: .[0]} else {filename: .[0]} end' \
        | jq -s . > "$FX/files-$n.json"
}
openprs() {  # "<number> <head ref> [fork] [closes-issue]"...
    printf '%s\n' "$@" | jq -R 'split(" ") | {number: (.[0] | tonumber), headRefName: .[1],
        isCrossRepository: (.[2] == "fork"),
        closingIssuesReferences: (if (.[3] // "") != "" then [{number: (.[3] | tonumber)}] else [] end)}' \
        | jq -s . > "$FX/prs.json"
}
check() {  # <pr> <branch> <author> [head repo] -> exit code
    ( PR="$1" BRANCH="$2" AUTHOR="$3" HEAD_REPO="${4:-o/r}" pr_allowlist_check >/dev/null 2>&1 ); echo $?
}

meta 228 OPEN agent-task status:in-progress
prbody 501 "Adds the market.

Closes #228"
openprs "501 agent/228-market"
files 501 src/sim/town_market.gd src/test/sim/test_market.gd
expect_eq "allowlist: in scope"                               0 "$(check 501 agent/228-market owner)"
files 501 src/sim/town_market.gd src/sim/other.gd
expect_eq "allowlist: a file out of scope"                    1 "$(check 501 agent/228-market owner)"
files 501 "src/sim/secret.gd->src/sim/plain.gd"
expect_eq "allowlist: renamed from out of scope"              1 "$(check 501 agent/228-market owner)"
echo '[]' > "$FX/files-501.json"
expect_eq "allowlist: an empty diff"                          1 "$(check 501 agent/228-market owner)"
files 501 src/sim/town_market.gd
prbody 501 "Adds the market."
expect_eq "allowlist: body does not close the branch's issue" 1 "$(check 501 agent/228-market owner)"
prbody 501 "Closes #228, fixes #229"
expect_eq "allowlist: body closes a second issue"             1 "$(check 501 agent/228-market owner)"
prbody 501 "Prefixes #229 differently. Closes #228"
expect_eq "allowlist: 'prefixes #N' is not a closing keyword" 0 "$(check 501 agent/228-market owner)"
prbody 501 "Closes #228"
meta 228 CLOSED agent-task status:in-review
expect_eq "allowlist: a closed issue's scope is not borrowed" 1 "$(check 501 agent/228-market owner)"
meta 228 OPEN agent-task status:ready
expect_eq "allowlist: an issue nobody claimed"                1 "$(check 501 agent/228-market owner)"
meta 228 OPEN status:in-progress
expect_eq "allowlist: not an agent-task issue"                1 "$(check 501 agent/228-market owner)"
meta 228 OPEN agent-task status:escalated
expect_eq "allowlist: an escalated issue is still in flight"  0 "$(check 501 agent/228-market owner)"
openprs "501 agent/228-market" "502 agent/228-again"
expect_eq "allowlist: a second open PR for the issue"         1 "$(check 501 agent/228-market owner)"
openprs "501 agent/228-market" "502 chore/other - 228"
expect_eq "allowlist: a second PR that closes the issue"      1 "$(check 501 agent/228-market owner)"
openprs "501 agent/228-market" "900 agent/228-market fork"
expect_eq "allowlist: a fork's same-named branch does not count" 0 "$(check 501 agent/228-market owner)"
expect_eq "allowlist: a fork's agent/<N>- head is refused"   1 "$(check 900 agent/228-market mallory evil/r)"
openprs "501 agent/228-market"
prbody 501 "Closes #228" 228 229
expect_eq "allowlist: GitHub links a second closing issue"    1 "$(check 501 agent/228-market owner)"
prbody 501 "Closes #228"
rm -f "$FX/prs.json"
expect_eq "allowlist: open PRs cannot be listed"              1 "$(check 501 agent/228-market owner)"
openprs "501 agent/228-market"
( CONTROL_PATHS_FILE="$WORK/no-such-file"; PR=501 BRANCH=agent/228-market AUTHOR=owner pr_allowlist_check >/dev/null 2>&1 )
expect_eq "allowlist: no control-path list"                   1 "$?"

# Bound through the body, not the branch.
meta 228 OPEN agent-task status:in-progress
prbody 505 "Closes #228"
files 505 src/sim/town_market.gd
openprs "505 chore/market"
expect_eq "allowlist: agents' account, body-bound, in flight" 0 "$(check 505 chore/market owner)"
meta 228 OPEN agent-task status:ready
expect_eq "allowlist: agents' account, body-bound, unclaimed" 1 "$(check 505 chore/market owner)"
meta 228 OPEN status:ready human-decision
expect_eq "allowlist: agents' account borrows an owner issue" 1 "$(check 505 chore/market owner)"
expect_eq "allowlist: a collaborator may close an owner issue" 0 "$(check 505 chore/market alice)"
meta 228 OPEN agent-task status:in-progress
openprs "501 agent/228-market"

prbody 503 "Tidy the README."
files 503 README.md
expect_eq "allowlist: unbound, by the agents' account"        1 "$(check 503 chore/tidy owner)"
expect_eq "allowlist: unbound, login differs only in case"    1 "$(check 503 chore/tidy Owner)"
expect_eq "allowlist: unbound, by someone else"               0 "$(check 503 dependabot/npm/x 'dependabot[bot]')"
files 503 README.md .github/workflows/ci.yml
expect_eq "allowlist: unbound, someone else, a control path"  1 "$(check 503 dependabot/actions/x 'dependabot[bot]')"
expect_eq "allowlist: unbound fork PR, a control path"        1 "$(check 503 patch-1 mallory evil/r)"

cat > "$FX/issue-240.body.md" <<'BODY'
## Files in scope

- [ ] `.github/workflows/ci.yml` (modify)
- [ ] `src/app.py` (modify)
BODY
meta 240 OPEN agent-task status:in-review
prbody 504 "Closes #240"
openprs "504 agent/240-ci"
files 504 .github/workflows/ci.yml src/app.py
expect_eq "allowlist: control path without human-decision"  1 "$(check 504 agent/240-ci owner)"
meta 240 OPEN agent-task status:in-review human-decision
expect_eq "allowlist: control path with human-decision"     0 "$(check 504 agent/240-ci owner)"
meta 240 OPEN agent-task status:in-review
files 504 src/app.py
expect_eq "allowlist: no control path touched"              0 "$(check 504 agent/240-ci owner)"
rm -f "$FX/issue-240.meta.json"
expect_eq "allowlist: the issue cannot be read"             1 "$(check 504 agent/240-ci owner)"

# --- result -------------------------------------------------------------------

echo
echo "hooks: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
