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
mkdir -p "$BIN"
GH_LOG="$WORK/gh.log"
export GH_LOG
: > "$GH_LOG"

# Issue #228 in a real issue's shape: the template's checklist items, followed
# by prose that names OTHER files -- the exact thing the parser must not read
# as an entry.
cat > "$BIN/gh" <<'STUB'
#!/usr/bin/env bash
echo "$PWD :: $*" >> "$GH_LOG"
case "$1 $2" in
  "issue view")
    case "$3" in
      228) cat <<'BODY'
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
      ;;
      300) cat <<'BODY'
## Files in scope

Edit `src/sim/tick.gd` and `src/test/sim/test_tick.gd`.

## Non-goals
BODY
      ;;
      *) exit 1 ;;
    esac
    ;;
  *) exit 1 ;;
esac
STUB
chmod +x "$BIN/gh"
export PATH="$BIN:$PATH"

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

a="$(_scope_cache_path 12 https://github.com/o/one.git)"
b="$(_scope_cache_path 12 https://github.com/o/two.git)"
[ "$a" != "$b" ] && ok || bad "cache is keyed by repository" "$a = $b"

LIST="$WORK/list"
scope_fetch_allowlist 228 > "$LIST"
allowed() { scope_path_allowed "$1" "$LIST" && echo yes || echo no; }
expect_eq "no companions by default"      no  "$(allowed src/sim/town_market.gd.uid)"
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
        | bash "$HOOKS/allowlist_guard.sh" >/dev/null 2>&1
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
rm -f "$TMPDIR"/pipeline-scope-*
: > "$GH_LOG"
guard "$OTHER" "$REPO/CLAUDE.md" >/dev/null
fetched_in="$(grep 'issue view' "$GH_LOG" | head -1 | sed 's/ :: .*//')"
# Under Git Bash one directory has two POSIX spellings (/tmp/x and
# /c/Users/.../Temp/x); compare them in one canonical Windows form there.
canon() { local p; p="$(cd "$1" 2>/dev/null && pwd -P)"; if command -v cygpath >/dev/null 2>&1; then cygpath -m "$p"; else printf '%s' "$p"; fi; }
expect_eq "guard: issue is read from the file's checkout" "$(canon "$REPO_REAL")" "$(canon "$fetched_in")"

# --- result -------------------------------------------------------------------

echo
echo "hooks: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
