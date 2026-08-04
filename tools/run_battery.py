"""Run the whole battery and report one number.

    python3 tools/run_battery.py [--quiet] [-k SUBSTR] [--timeout SECONDS]

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

Four outcomes, not two
----------------------
A file can end badly in three different ways, and they are three different facts
about the tree. Reporting them as one word is how a handoff comes to say the
wrong thing:

  FAILED    an assertion said no. The test ran and disagreed.
  CRASHED   the file died of something that is not an assertion -- an import
            error, a `raise` of the wrong type, an IndexError on empty output.
            The tests after the exception never ran at all, so the count for
            that file is not just red, it is *short*.
  TIMED OUT the file never finished. That is not a crash: nothing said no and
            nothing raised. It is the one outcome where the battery has no idea
            what the answer would have been.

The earlier version had only CRASHED, and detected it as "exited non-zero having
printed no FAIL line". That misses the case this comment exists for: a file that
prints some FAIL lines and *then* dies. `test_cli.py` against a present-but-
incompatible engine does exactly that -- 3 FAIL lines, then an IndexError at the
fourth test, and the remaining 8 tests silently vanish from the total while the
battery reports a tidy "3 failed". So a crash is now also recognised structurally,
from the traceback Python itself writes to stderr when a process dies of an
uncaught exception. That signal cannot be confused with a test result line and no
summary dialect can hide it.

There was no timeout at all before, which meant a genuinely hung test file hung
the battery -- the documented authority on the tree's health would simply never
answer. The timeout is deliberately generous (see DEFAULT_TIMEOUT): it exists to
stop an infinite wait, not to convert a slow file into a fast failure. Slowness
is real and belongs in the report, so every file is timed and its wall clock is
printed; `test/test_cli.py` legitimately takes about eight minutes on a machine
with the Forge engine present, and a runner that did not say so is how it came to
be reported as a hang.

Exit status: 0 clean, 1 something failed, 2 a file crashed, 3 a file timed out.
The worst outcome present wins.
"""

import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TESTS = os.path.join(REPO, "test")

# Per-file wall-clock ceiling. Sized so that no honest file in this tree can hit
# it: the slowest (test_cli.py, which drives the real CLI over the real Forge
# engine) takes ~8 minutes, so this is ~4x headroom for a loaded machine. Raise
# it with --timeout rather than lowering it -- a timeout that fires on a merely
# slow file turns a real answer into a non-answer.
DEFAULT_TIMEOUT = 1800

_RESULT = re.compile(r"^(PASS|FAIL|SKIP)\b", re.M)
_TRACEBACK = re.compile(r"^Traceback \(most recent call last\):$", re.M)
# The last line of an uncaught-exception traceback: unindented, an exception
# name, optionally `: message`. `IndexError: list index out of range`, `Skip`,
# `SystemExit: 2` all match; a `  File "..."` frame or a result line does not.
_EXC_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(: .*)?$")

OK, FAILED, CRASHED, TIMEOUT = "ok", "failed", "crashed", "timeout"
_EXIT = {OK: 0, FAILED: 1, CRASHED: 2, TIMEOUT: 3}


def test_files(select=None):
    names = sorted(
        n for n in os.listdir(TESTS)
        if n.startswith("test_") and n.endswith(".py")
    )
    if select:
        names = [n for n in names if select in n]
    return names


def _died_of_an_exception(err):
    """True if stderr ends in the traceback of an uncaught exception.

    Python writes exactly this shape when a process dies of an exception that
    nothing caught, and it is always the *last* thing on stderr. Requiring it to
    be last is what keeps a test that legitimately prints a traceback (as part of
    its own captured output, mid-run) from being read as a crash: that one has
    the file's later output after it, this one has nothing after it.
    """
    if not _TRACEBACK.search(err):
        return False
    lines = [ln for ln in err.splitlines() if ln.strip()]
    return bool(lines) and bool(_EXC_LINE.match(lines[-1]))


def _kill_tree(proc):
    """Kill the file *and* anything it spawned.

    The test files in this tree shell out (test_cli.py runs the real `trvs` as a
    subprocess). Killing only the direct child would leave those grandchildren
    running and still holding the pipes, so the battery would go on waiting for
    output from a file it had already given up on. `start_new_session=True` gives
    the child its own process group; this signals the group.
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def run_one(name, env, timeout=DEFAULT_TIMEOUT):
    """Run one test file. Returns (tally, outcome, elapsed_seconds, output)."""
    started = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(TESTS, name)],
        cwd=REPO, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        # The reader threads are still draining; this returns what was produced
        # before the kill, which is exactly the evidence of where it stopped.
        # It is itself bounded: if a grandchild somehow survived the group kill
        # it would still hold the pipe open, and an unbounded wait here would
        # reintroduce the very hang this timeout exists to end.
        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            stdout = getattr(exc, "stdout", None) or ""
            stderr = (getattr(exc, "stderr", None) or "") + \
                "\n[run_battery: output truncated; a child survived the kill]\n"
    elapsed = time.monotonic() - started

    out = stdout + stderr
    tally = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for kind in _RESULT.findall(out):
        tally[kind] += 1

    if timed_out:
        outcome = TIMEOUT
    elif proc.returncode != 0 and (
            tally["FAIL"] == 0 or _died_of_an_exception(stderr)):
        # Either it never reported a failure at all (it broke before or between
        # the tests), or it reported some and then died -- both mean tests that
        # were supposed to run did not, so the file's count is short.
        outcome = CRASHED
    elif tally["FAIL"]:
        outcome = FAILED
    else:
        outcome = OK
    return tally, outcome, elapsed, out


def main(argv):
    quiet = "--quiet" in argv
    select = None
    if "-k" in argv:
        select = argv[argv.index("-k") + 1]
    timeout = DEFAULT_TIMEOUT
    if "--timeout" in argv:
        timeout = float(argv[argv.index("--timeout") + 1])

    env = dict(os.environ)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    names = test_files(select)
    total = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    crashes = []
    timeouts = []
    failures = []
    for name in names:
        tally, outcome, elapsed, out = run_one(name, env, timeout)
        for k in total:
            total[k] += tally[k]
        if outcome == CRASHED:
            crashes.append(name)
        elif outcome == TIMEOUT:
            timeouts.append(name)
        if tally["FAIL"]:
            failures.extend(
                line for line in out.splitlines() if line.startswith("FAIL")
            )
        if not quiet:
            note = {CRASHED: "  CRASHED", TIMEOUT: "  TIMED OUT"}.get(outcome, "")
            print("%-34s %4d passed  %2d skipped  %2d failed  %6.1fs%s" % (
                name, tally["PASS"], tally["SKIP"], tally["FAIL"], elapsed, note,
            ))
        if outcome in (CRASHED, TIMEOUT) and not quiet:
            print(out.rstrip()[-2000:])

    print("\n%d passed, %d skipped, %d failed  (%d files)" % (
        total["PASS"], total["SKIP"], total["FAIL"], len(names),
    ))
    for line in failures:
        print("  " + line)
    # A crashed or timed-out file did not run every test it contains, so the
    # totals above are a floor, not the tree's test count. Say so rather than
    # letting the number be read as complete.
    if crashes:
        print("  crashed (count is short): " + ", ".join(crashes))
    if timeouts:
        print("  timed out after %gs (count is short): %s"
              % (timeout, ", ".join(timeouts)))

    if timeouts:
        return _EXIT[TIMEOUT]
    if crashes:
        return _EXIT[CRASHED]
    return _EXIT[FAILED] if total["FAIL"] else _EXIT[OK]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
