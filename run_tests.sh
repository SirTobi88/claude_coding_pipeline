#!/usr/bin/env bash
# The project's single test entry point.
#
#   exit 0    -> all tests passed
#   non-zero  -> a test failed, or the runner itself errored
#
# EDIT THIS FILE when you adopt the pipeline: make it run your project's whole
# suite. Three rules make it usable by agents and by CI alike:
#
#   1. It finds its tools itself (PATH, then well-known install locations);
#      nobody should have to export a variable to run it.
#   2. Its first line says what produced the result (tool and version), and its
#      last line is a summary -- that is what agents quote in a PR.
#   3. Its exit code is the result. Nothing else is.
#
# This template repository contains no product code, so its suite is the
# pipeline's own tests.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

py=""
for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
        py="$candidate"; break
    fi
done
[ -n "$py" ] || { echo "No working Python >= 3.9 on PATH." >&2; exit 1; }

echo "Runner: $("$py" --version 2>&1), $(bash --version | head -1)"
bash .claude/hooks/test/run_tests.sh
"$py" -m unittest discover -s .claude/pipeline/tests
echo "Summary: all suites passed"
