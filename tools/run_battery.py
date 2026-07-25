"""Run the whole battery and report one number.

    python3 tools/run_battery.py [--quiet] [-k SUBSTR]

Why this exists. Every `test/test_*.py` is a self-running script with its own
`main()`, and the battery was "run" by invoking them one at a time and adding the
results up by hand. That is how a handoff memo came to open with "340 green" and
then claim a clean extraction reproduced "335/335": nobody was wrong on purpose,
the total was just never computed by anything that could be re-run. A count that
only exists in prose is not evidence.

It is worse than tedious, because the files do not even agree on how to report.
Two dialects are in the tree:

    12 passed, 0 skipped, 0 failed        (test_cli.py and relatives)
    18/18 passed                          (test_admission.py and relatives)

A totaller that parsed summary lines would have to know both, and would silently
undercount the day someone writes a third. So this does not parse summaries at
all. It counts the per-test result lines -- `PASS name`, `FAIL name`, `SKIP name`
-- which every file in the tree already emits identically. That signal is
structural: a test cannot run without printing one, and a file cannot invent a
new summary format that hides them.

Exit status is 1 if anything failed, 2 if a test file crashed outright (which is
distinct from a failing assertion and must not be reported as a mere failure),
else 0.
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TESTS = os.path.join(REPO, "test")

_RESULT = re.compile(r"^(PASS|FAIL|SKIP)\b", re.M)


def test_files(select=None):
    names = sorted(
        n for n in os.listdir(TESTS)
        if n.startswith("test_") and n.endswith(".py")
    )
    if select:
        names = [n for n in names if select in n]
    return names


def run_one(name, env):
    proc = subprocess.run(
        [sys.executable, os.path.join(TESTS, name)],
        cwd=REPO, env=env, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    tally = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for kind in _RESULT.findall(out):
        tally[kind] += 1
    # A file that exits non-zero having printed no FAIL line did not fail a
    # test -- it broke before or between them (import error, syntax error, a
    # raise that is not an AssertionError). Counting that as "0 failed" is how a
    # red battery reads green, so it gets its own outcome.
    crashed = proc.returncode != 0 and tally["FAIL"] == 0
    return tally, crashed, out


def main(argv):
    quiet = "--quiet" in argv
    select = None
    if "-k" in argv:
        select = argv[argv.index("-k") + 1]

    env = dict(os.environ)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    total = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    crashes = []
    failures = []
    for name in test_files(select):
        tally, crashed, out = run_one(name, env)
        for k in total:
            total[k] += tally[k]
        if crashed:
            crashes.append(name)
        if tally["FAIL"]:
            failures.extend(
                line for line in out.splitlines() if line.startswith("FAIL")
            )
        if not quiet:
            print("%-34s %3d passed  %2d skipped  %2d failed%s" % (
                name, tally["PASS"], tally["SKIP"], tally["FAIL"],
                "  CRASHED" if crashed else "",
            ))
        if crashed and not quiet:
            print(out.rstrip()[-2000:])

    print("\n%d passed, %d skipped, %d failed  (%d files)" % (
        total["PASS"], total["SKIP"], total["FAIL"], len(test_files(select)),
    ))
    for line in failures:
        print("  " + line)
    if crashes:
        print("  crashed: " + ", ".join(crashes))
        return 2
    return 1 if total["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
