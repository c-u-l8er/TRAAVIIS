"""Laws for the 9E closure — process containment, enumeration, and attribution.

The eighth erasure route
========================
9D gave every command its own session and killed the **group** on a deadline or
an output overflow. The ruling showed that is not a process-tree boundary::

    normal background child:  parent exit 0 -> descendant alive after run = true
    setsid  background child: parent exit 0 -> descendant alive after run = true

Two defects. A **successful exit cleaned nothing up** — the group kill fired only
on timeout and overflow, so a candidate could spawn a worker, exit 0, and receive
a clean run. And **`setsid()` escapes the group entirely** — a descendant can
leave the group before any signal addressed to it arrives, so even an
unconditional `killpg` is not enough.

What survives can consume the host, mutate the workspace *while the evidence
scan is reading it*, and outlive the episode to attack the next one. Same
grade-erasure class as the FIFO, reached after the nominal command is over.

The closure is a per-run **cgroup v2**: a process cannot remove itself from its
cgroup, `cgroup.kill` is transitive over sessions, process groups and
reparenting, and `cgroup.events`' `populated` flag is the *kernel* answering
"is anything still alive in here" — rather than a `/proc` scan by parent pid,
which reparenting makes useless.

The seventeen laws the ruling requires
======================================
    Y1   normal background child is killed after parent exits 0
    Y2   setsid descendant is killed
    Y3   double-fork daemon is killed
    Y4   escaped child cannot mutate workspace during scan
    Y5   zero descendants before evidence collection
    Y6   one million empty-directory shape is refused early
    Y7   directory count boundary exact
    Y8   total-entry boundary exact
    Y9   path-byte boundary exact
    Y10  regular-file-to-FIFO swap cannot block
    Y11  regular-file-to-symlink swap cannot escape
    Y12  candidate-created symlink produces invalid/0
    Y13  agent stdout overflow produces invalid/0
    Y14  baseline test overflow produces error
    Y15  patched test overflow produces fail
    Y16  patched Forge source over byte limit produces identity fail
    Y17  Forge wall-clock timeout remains error/null

Y21 closes the loop the other way: the boundary must not leak the thing it is
made of. Y18 states the attribution rule the last five are instances of. Y19
pins that
the containment report is a *measurement* rather than a claim, and Y20 pins the
other half of the ruling's choice: a host with no mechanism records the fact and
the episode is invalid, so an uncontained evaluation is never mistaken for a
contained one.

**On a host with no delegated cgroup v2, Y1-Y5, Y12 and Y13 skip rather than
pass.** A law about what a boundary prevents passes trivially where there is no
boundary, and a green file would then read as evidence that the eighth route is
closed. A skip is the honest word -- "this tree cannot test this" -- and the
escalation belongs to the packet gate, which forbids skips: such a host is told
it cannot certify the packet, and the skip names what went unverified.

Run directly:      python3 test/test_containment.py
Run under pytest:  pytest test/test_containment.py
"""
import ast
import inspect
import os
import shutil
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import containment as C  # noqa: E402
from traaviis import evalone as EV  # noqa: E402
from traaviis import execlimits as E  # noqa: E402
from traaviis import forge_adapter as FA  # noqa: E402
from traaviis import reward as R  # noqa: E402
from traaviis import runner as RUN  # noqa: E402
from traaviis import substrate_verifiers as SV  # noqa: E402
from traaviis.vcontext import VerifierContextV1  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = os.path.join(HERE, "fixtures", "stub_agent.py")

CONTENT = {"spec/one.md": "alpha\nbeta\n", "src/mod.py": "return 1\n"}

#: Generous. These laws prove *termination and death*, not speed.
TERMINATES_WITHIN = 120.0


class Skip(Exception):
    pass


#: Measured once, by actually creating a cgroup and looking for `cgroup.kill`.
CONTAINABLE = C.cgroup_v2_available()

_NO_MECHANISM = (
    "this host has no delegated cgroup v2, so there is no process-tree "
    "boundary here to test")


def _needs_containment():
    """Skip -- never pass -- when the host cannot build the boundary at all.

    The distinction this file had to get right, and the one the packet's G6 gate
    exists to catch. A law about what a boundary prevents will pass trivially on
    a machine that has no boundary: Y1-Y3 would "prove" descendants die because
    nothing was ever contained, and the battery would read as evidence that the
    eighth route is closed.

    A *skip* is the honest word for it -- "this tree cannot test this" -- and it
    is deliberately not a failure, because the host being unable to contain
    anything is not a defect in the code under test. What it *is* is a reason
    this host cannot certify a packet, and that escalation belongs to the gate:
    G6 forbids skips with the engine present, so a run on such a host is
    REJECTED with the skip naming exactly what went unverified. That is a better
    place for the decision than here.

    Y20 covers the other half on every host: when there is no mechanism, the run
    must *say so* and the episode must be invalid.
    """
    if not CONTAINABLE:
        raise Skip(_NO_MECHANISM)


def _policy(**over):
    p = {
        "policy_version": "traaviis.agent-run-policy.v1",
        "command_mode": "argv",
        "shell": False,
        "network": "disabled",
        "timeout_seconds": 60,
        "max_output_bytes": 4194304,
        "environment": {"TRAAVIIS_STUB_MODE": "ok"},
        "writable_paths": ["."],
        "result_path": "result.json",
        "patch_path": "candidate.patch",
    }
    p.update(over)
    return p


def _run(mode, **over):
    over.setdefault("environment", {"TRAAVIIS_STUB_MODE": mode})
    return RUN.run_agent([sys.executable, STUB], CONTENT, _policy(**over))


def _alive(pid):
    """Does this pid exist? The OS's answer, not an inference from a return."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:                              # pragma: no cover
        return True
    return True


def _wait_gone(pid, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def _reap(pid):
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def _escape_probe(script, label):
    """Run `script` in a scratch cwd; return (run, escaped pid).

    The pid is written by the fixture to a file *outside* the workspace the
    runner deletes, because the whole question is what is still alive after the
    run is over.
    """
    root = tempfile.mkdtemp(prefix="trvs-9e-%s-" % label)
    pidfile = os.path.join(root, "escape.pid")
    run = E.run_bounded([sys.executable, "-c", script % {"pid": pidfile}],
                        cwd=root, timeout=TERMINATES_WITHIN)
    with open(pidfile, encoding="ascii") as fh:
        pid = int(fh.read().strip())
    return run, pid, root


_BACKGROUND = (
    "import subprocess, sys\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
    "open(%(pid)r, 'w').write(str(p.pid))\n")

_SETSID = (
    "import subprocess, sys\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'],\n"
    "                     start_new_session=True)\n"
    "open(%(pid)r, 'w').write(str(p.pid))\n")

_DAEMON = (
    "import os, time\n"
    "r, w = os.pipe()\n"
    "if os.fork() == 0:\n"
    "    os.setsid()\n"
    "    if os.fork() == 0:\n"
    "        os.write(w, str(os.getpid()).encode()); os.close(w)\n"
    "        time.sleep(600)\n"
    "    os._exit(0)\n"
    "os.close(w)\n"
    "pid = os.read(r, 32).decode()\n"
    "open(%(pid)r, 'w').write(pid)\n"
    "os.wait()\n")


# ============================== Y1-Y5: the process boundary ends with execution

def _escape_law(script, label, why):
    _needs_containment()
    run, pid, root = _escape_probe(script, label)
    try:
        assert run["exit_code"] == 0, (
            "the fixture must exit cleanly -- a run that failed proves nothing "
            "about what a *successful* run leaves behind (%s)" % run["exit_code"])
        assert run["process_containment"]["kill_boundary_enforced"] is True, \
            run["process_containment"]
        assert run["surviving_processes"] == 0, run["process_containment"]
        gone = _wait_gone(pid)
        if not gone:
            _reap(pid)
        assert gone, "%s (pid %d)" % (why, pid)
    finally:
        _reap(pid)
        shutil.rmtree(root, ignore_errors=True)


def test_y1_a_normal_background_child_is_killed_after_the_parent_exits_zero():
    """The plainer half of the eighth route: nothing was cleaned up on success.

    9D killed the process group on a timeout and on an output overflow, and on
    nothing else — so a candidate that spawns a worker and returns 0 got a clean
    run *and* left the worker running. No adversarial trick is needed for this
    one; it is what any program that daemonizes does by accident.
    """
    _escape_law(_BACKGROUND, "bgchild",
                "a background child outlived a successful run")


def test_y2_a_setsid_descendant_is_killed():
    """The half that defeats `killpg` however unconditionally it is called.

    A process may leave its process group. `setsid()` puts the descendant in a
    new session, and a signal addressed to the old group never reaches it — so
    "kill the group after every exit" would have closed Y1 and left this open.
    A process cannot remove *itself* from its cgroup, which is why `setsid` no
    longer helps and why the mechanism changed rather than the timing. It is
    **not** the stronger claim 9E made: a candidate that can write the parent's
    `cgroup.procs` leaves the subtree and the kill never reaches it. Y22 is that
    exploit; this law is about the descendant that stays.
    """
    _escape_law(_SETSID, "setsid", "a setsid descendant outlived the run")


def test_y3_a_double_fork_daemon_is_killed():
    """Reparented to init, unreachable from the original pid, still killed.

    The strongest of the three: after the middle process exits there is no
    parent-child path from anything the evaluator knows to the survivor, so a
    `/proc` walk by parent pid finds nothing. The ruling says so explicitly, and
    it is why `cgroup.events` is read instead — the kernel tracks membership,
    and membership survives reparenting.
    """
    _escape_law(_DAEMON, "daemon", "a double-forked daemon outlived the run")


def test_y4_an_escaped_child_cannot_mutate_the_workspace_during_the_scan():
    """The consequence that makes the escape an *evidence* defect, not just a leak.

    A survivor rewriting the workspace means the digests describe a tree that
    existed at no single moment — and worse, one that kept changing after the
    episode was declared over. The trace would be a record of something that
    never happened.

    Measured on the bytes: after the run returns, the file is read twice with a
    gap. A live mutator rewrites it every millisecond, so two equal readings
    separated by half a second are the statement that nothing is writing any
    more.
    """
    _needs_containment()
    root = tempfile.mkdtemp(prefix="trvs-9e-mutate-")
    pidfile = os.path.join(root, "escape.pid")
    target = os.path.join(root, "target.txt")
    script = (
        "import subprocess, sys\n"
        "open(%r, 'w').write('original')\n"
        "p = subprocess.Popen([sys.executable, '-c',\n"
        "  \"import time\\n\"\n"
        "  \"i = 0\\n\"\n"
        "  \"while True:\\n\"\n"
        "  \"    i += 1\\n\"\n"
        "  \"    open(%r, 'w').write('mutated %%d' %% i)\\n\"\n"
        "  \"    time.sleep(0.001)\\n\"])\n"
        "open(%r, 'w').write(str(p.pid))\n"
        "import time; time.sleep(0.3)\n" % (target, target, pidfile))
    pid = None
    try:
        run = E.run_bounded([sys.executable, "-c", script], cwd=root,
                            timeout=TERMINATES_WITHIN)
        with open(pidfile, encoding="ascii") as fh:
            pid = int(fh.read().strip())
        assert run["surviving_processes"] == 0, run["process_containment"]
        assert _wait_gone(pid), "the mutator outlived the run (pid %d)" % pid

        with open(target, "rb") as fh:
            first = fh.read()
        time.sleep(0.5)
        with open(target, "rb") as fh:
            second = fh.read()
        assert first == second, (
            "the workspace changed after the run returned: %r then %r"
            % (first[:40], second[:40]))
    finally:
        if pid is not None:
            _reap(pid)
        shutil.rmtree(root, ignore_errors=True)


def test_y5_zero_descendants_before_evidence_collection():
    """Containment is emptied *before* the scan, not merely at some point.

    Two halves, and the second is why the first is not enough. Behaviourally,
    every escape mode reports `surviving_processes == 0`. Structurally, the
    order is read off `run_agent`'s parse tree: the `run_bounded` call — which
    contains the kill — comes before the post-run `_scan`. An implementation
    that scanned first and killed afterwards would satisfy every count in this
    file and still read a workspace that was being written.
    """
    _needs_containment()
    for mode in ("bgchild", "setsid", "daemon"):
        result = _run(mode)
        assert result["surviving_processes"] == 0, (mode, result["process_containment"])
        assert result["process_containment"]["kill_boundary_enforced"] is True, mode

    # `ast.walk` yields breadth-first, not in source order, so the line numbers
    # are collected and *then* sorted. Reading order off the walk is how a
    # structural claim quietly becomes a claim about tree shape.
    tree = ast.parse(inspect.getsource(RUN.run_agent))
    lines = sorted((n.lineno, ast.unparse(n.func))
                   for n in ast.walk(tree) if isinstance(n, ast.Call))
    spawn = [ln for ln, name in lines if name == "execlimits.run_bounded"]
    scans = [ln for ln, name in lines if name == "_scan"]
    assert spawn, "run_agent no longer spawns"
    assert len(scans) >= 2, "run_agent no longer scans before and after"
    assert scans[0] < spawn[0], "the baseline scan does not precede the run"
    assert scans[-1] > spawn[0], (
        "the post-run scan does not follow the contained run")

    # ...and the kill really is on `run_bounded`'s **unconditional** path, not
    # inside the timeout branch where 9D had it. Depth is measured from the
    # function body: a `kill_all` nested inside any `If`/`Try` handler would be
    # conditional, and that is the defect, not a style.
    bounded = ast.parse(inspect.getsource(E.run_bounded)).body[0]
    top_level_kill = False
    for statement in bounded.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and \
                    ast.unparse(node.func).endswith("kill_all"):
                if isinstance(statement, (ast.If, ast.Try, ast.While, ast.For)):
                    continue
                top_level_kill = True
    assert top_level_kill, (
        "every `kill_all` in run_bounded is inside a branch; a clean exit would "
        "leave the containment full, which is the eighth erasure route")


# ==================== Y6-Y9: enumeration is bounded, not only file *reads*

def _tree(build, **kw):
    root = tempfile.mkdtemp(prefix="trvs-9e-scan-")
    try:
        build(root)
        started = time.monotonic()
        try:
            E.scan_tree(root, **kw)
            return None, time.monotonic() - started
        except E.ResourceLimitError as exc:
            return exc, time.monotonic() - started
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_y6_the_million_empty_directory_shape_is_refused_early():
    """A tree of empty directories crosses no *file* bound and still costs.

    9D bounded regular files and nothing else. The ruling's example is exact: a
    candidate creates an enormous tree of empty directories, no file-count limit
    is crossed, and evidence collection still consumes unbounded time and
    memory — because the cost of collection is per *entry*, and enumeration was
    not bounded at all.

    "Early" is the claim, so it is measured rather than asserted. The fixture
    builds far more directories than the bound admits and the refusal must
    arrive in a small fraction of the time it took to *create* them: a scan that
    walked the whole tree before refusing would take at least as long as the
    build did. A million is not created here — creating it would take longer than
    the property is worth — and the shape is identical, which is the point of
    bounding the count rather than the size.
    """
    def build(root):
        for i in range(4000):
            os.mkdir(os.path.join(root, "d%05d" % i))

    started = time.monotonic()
    scratch = tempfile.mkdtemp(prefix="trvs-9e-build-")
    try:
        build(scratch)
    finally:
        build_seconds = time.monotonic() - started
        shutil.rmtree(scratch, ignore_errors=True)

    exc, elapsed = _tree(build, max_directories=100)
    assert exc is not None, "4000 directories passed a bound of 100"
    assert exc.reason == "too_many_directories", exc.reason
    assert elapsed < max(build_seconds, 1.0), (
        "the scan took %.3fs to refuse a tree that took %.3fs to build, so it "
        "walked the whole thing first" % (elapsed, build_seconds))


def test_y7_the_directory_count_boundary_is_exact():
    """At the bound it is accepted; one over, refused. No off-by-one either way.

    An exact boundary matters in both directions. A bound that refused *at* the
    limit would deny an honest workspace that fits; one that admitted one over
    would be a bound nobody could rely on.
    """
    def build(count):
        def go(root):
            for i in range(count):
                os.mkdir(os.path.join(root, "d%04d" % i))
        return go

    exc, _ = _tree(build(5), max_directories=5)
    assert exc is None, "5 directories were refused by a bound of 5: %s" % exc
    exc, _ = _tree(build(6), max_directories=5)
    assert exc is not None and exc.reason == "too_many_directories"
    assert exc.detail["max_directories"] == 5


def test_y8_the_total_entry_boundary_is_exact():
    """Every entry counts, of every type — that is what "entries" means.

    Directories and files are counted into the same budget, so a candidate
    cannot get under the entry bound by choosing a type that some other bound
    happens not to cover. The mixed fixture is the point: 3 directories plus 3
    files is 6 entries, not two separate threes.
    """
    def build(dirs, files):
        def go(root):
            for i in range(dirs):
                os.mkdir(os.path.join(root, "d%d" % i))
            for i in range(files):
                with open(os.path.join(root, "f%d" % i), "wb"):
                    pass
        return go

    exc, _ = _tree(build(3, 3), max_entries=6, max_directories=1000)
    assert exc is None, "6 entries were refused by a bound of 6: %s" % exc
    exc, _ = _tree(build(3, 4), max_entries=6, max_directories=1000)
    assert exc is not None and exc.reason == "too_many_entries", exc
    assert exc.detail["max_entries"] == 6


def test_y9_the_path_byte_boundaries_are_exact():
    """One name, and the sum of all names. Both bounded, both measured in bytes.

    Path *names* are a resource: a workspace can hold few files and still carry
    megabytes of names, and every one of them is copied into the evidence map,
    hashed, and sealed. Bytes rather than characters, because that is what the
    memory costs and what the canonical form encodes.
    """
    def build(name):
        def go(root):
            with open(os.path.join(root, name), "wb"):
                pass
        return go

    exc, _ = _tree(build("a" * 200), max_single_path_bytes=200)
    assert exc is None, "a 200-byte name was refused by a 200-byte bound"
    exc, _ = _tree(build("a" * 201), max_single_path_bytes=200)
    assert exc is not None and exc.reason == "path_too_long", exc
    assert exc.detail["max_bytes"] == 200

    def many(root):
        for i in range(10):
            with open(os.path.join(root, "n%03d" % i), "wb"):
                pass

    exc, _ = _tree(many, max_path_bytes=40)
    assert exc is None, "10 names of 4 bytes were refused by a 40-byte total"
    exc, _ = _tree(many, max_path_bytes=39)
    assert exc is not None and exc.reason == "workspace_paths_too_large", exc


# ============================ Y10-Y12: the check/use race, and symlinks

def _open_in(root, name, subject=None):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        got, _st = E._open_regular_at(fd, name, subject or name)
        os.close(got)
    finally:
        os.close(fd)


def test_y10_a_regular_file_to_fifo_swap_cannot_block():
    """The post-swap state is what the open sees, and the open does not block.

    9D did ``lstat -> verify regular -> open``, three operations on a *name*. A
    surviving candidate process could replace that name with a FIFO in between,
    and the FIFO route came back through the door marked time-of-check /
    time-of-use.

    The race is not simulated here, because the *property* does not need a race
    to state: whatever the name refers to at the moment of the open is what the
    open must survive. `O_NONBLOCK` means reaching a FIFO returns instead of
    blocking, and the `fstat` is on the descriptor — so the swap's only possible
    effect is that the open sees a FIFO, and that is exactly what is exercised.

    A deadline is asserted as well as a refusal, because "refused" and "refused
    without waiting for a writer that will never come" are different claims and
    only the second one closes the route.
    """
    root = tempfile.mkdtemp(prefix="trvs-9e-swap-")
    try:
        os.mkfifo(os.path.join(root, "swapped"))
        started = time.monotonic()
        try:
            _open_in(root, "swapped")
        except E.ResourceLimitError as exc:
            assert exc.reason == "irregular_file", exc.reason
            assert exc.detail["kind"] == "fifo"
        else:
            raise AssertionError("a FIFO was opened as a regular file")
        assert time.monotonic() - started < 5.0, "the open blocked on the FIFO"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_y11_a_regular_file_to_symlink_swap_cannot_escape():
    """`O_NOFOLLOW` means the swap cannot redirect a read out of the workspace.

    The other half of the same race, and the more dangerous one: a symlink
    swapped in for a checked regular file does not merely refuse to be evidence,
    it points the reader at a file the workspace does not contain. The kernel
    refuses to traverse the final component, so the target is never opened —
    which is why this is a property of the flag rather than of a check.

    The target here is a real file *outside* the scanned root, so a law that
    passed by accident (because the target did not exist) would be visible.
    """
    outside = tempfile.mkdtemp(prefix="trvs-9e-outside-")
    root = tempfile.mkdtemp(prefix="trvs-9e-swap2-")
    try:
        secret = os.path.join(outside, "secret.txt")
        with open(secret, "wb") as fh:
            fh.write(b"not in the workspace")
        os.symlink(secret, os.path.join(root, "swapped"))
        try:
            _open_in(root, "swapped")
        except E.ResourceLimitError as exc:
            assert exc.reason == "symlink", exc.reason
        else:
            raise AssertionError("a symlink was followed out of the workspace")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_y12_a_candidate_created_symlink_produces_invalid_and_zero():
    """Refused, named, and scored — not skipped.

    9D skipped symlinks and recorded the deviation. The ruling overturned it,
    and the argument is what makes the verdict follow: snapshot materialization
    cannot express a symlink, the workspace begins from that sealed
    materialization, so any symlink present afterwards was created by the
    candidate. Omitting it leaves the trace describing an incomplete filesystem
    state, and incomplete in the direction that pays the candidate.

    The whole chain is asserted, because a refusal that did not reach the score
    would be a message rather than a consequence: the violation is sealed into
    `policy_violations`, which `evalone` reads as tampered, which
    `reward.score` turns into 0 and `validity: invalid`.
    """
    _needs_containment()
    result = _run("symlink")
    assert any(v.endswith(":symlink:link.txt")
               for v in result["resource_violations"]), \
        result["resource_violations"]
    assert set(result["resource_violations"]) <= set(result["policy_violations"])
    assert result["policy_violations"], "the refusal did not reach the score"
    scored = R.score({"citations": R.PASS}, _REWARD, ["citations"],
                     tampered=True)
    assert scored["reward"] == 0.0 and scored["validity"] == "invalid", scored


# ============================= Y13-Y18: the attribution table

#: A one-signal reward spec, enough to ask what a state is worth.
_REWARD = {
    "reward_spec_version": "traaviis.reward.v1",
    "substrate_profile": "residency.repository.v1",
    "signals": {"citations": {"verifier": "residency.citations.v1",
                              "weight": 1.0}},
    "aggregation": "terminal",
}


def test_y13_agent_output_overflow_produces_invalid_and_zero():
    """A deterministic byte cap on the candidate's own bytes is evidence.

    §10a used to rule that exceeding `max_output_bytes` made the affected
    verifier `error` — which means `reward = None`, an *unscored* episode. That
    is the erasure shape: a candidate that prints enough unscores itself. The
    cap is a declared number, identical on every host, so crossing it says
    something about the submission and not about the machine.

    Three things, and the third is the one that had to change in `evalone`: the
    violation is recorded, it is sealed into `policy_violations`, and the
    substrate-failure rule no longer fires on truncation — checked on the parse
    tree, because a comment saying so is not a branch.
    """
    _needs_containment()
    result = _run("spew", max_output_bytes=1 << 20)
    assert result["stderr_truncated"] is True
    assert any(v.endswith(":stderr_too_large")
               for v in result["resource_violations"]), \
        result["resource_violations"]
    assert set(result["resource_violations"]) <= set(result["policy_violations"])

    finish = ast.parse(inspect.getsource(EV._finish_episode))
    run_error = [n for n in ast.walk(finish)
                 if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "run_error"
                         for t in n.targets)]
    assert run_error, "evalone no longer computes run_error"
    for node in run_error:
        assert "output_truncated" not in ast.unparse(node), (
            "a truncated capture still routes to substrate error: %s"
            % ast.unparse(node))


def test_y14_a_baseline_test_overflow_is_an_error():
    """The baseline judges the *fixture*, so its overflow is an invalid fixture.

    A command that floods its output before the candidate has touched anything
    says the sealed subject is not the world the task describes. Scoring the
    candidate for it would mark somebody down for a task nobody could pass —
    which is the same rule the baseline already follows for an unexpected exit.
    """
    plan = {
        "test_plan_version": SV.TEST_PLAN_V2,
        "toolchain_profile": "cpython-3.11",
        "commands": [{"tool": "python", "args": ["-c", _SPEW_SOURCE],
                      "timeout_seconds": 60}],
    }
    state, records = SV.run_command_set(
        plan, {"x.txt": "x"}, executables={"python": sys.executable},
        phase=SV.BASELINE)
    assert not SV.command_set_passed(state), state
    assert records[-1]["error"] == "OutputOverflow", records[-1]
    assert records[-1]["execution_limits_version"] == E.EXECUTION_LIMITS_VERSION
    assert state == SV._INFRA_ERROR, state


def test_y15_a_patched_test_overflow_is_a_fail():
    """The patched run judges the *candidate*, so its overflow is a failure.

    Same bytes, same bound, opposite verdict — and the difference is attribution,
    which is the whole content of the ruling's table. The patched command is the
    candidate's; the baseline command is the fixture's.
    """
    plan = {
        "test_plan_version": SV.TEST_PLAN_V2,
        "toolchain_profile": "cpython-3.11",
        "commands": [{"tool": "python", "args": ["-c", _SPEW_SOURCE],
                      "timeout_seconds": 60}],
    }
    state, records = SV.run_command_set(
        plan, {"x.txt": "x"}, executables={"python": sys.executable},
        phase=SV.PATCHED)
    assert state == SV._SOME_FAIL, state
    assert not SV.command_set_passed(state)
    assert records[-1].get("error") is None, \
        "a patched overflow must be a verdict, not an infrastructure error"
    assert records[-1]["stdout_truncated"] is True
    assert records[-1]["execution_limits_version"] == E.EXECUTION_LIMITS_VERSION


_SPEW_SOURCE = ("import sys\n"
                "buf = 'x' * 65536\n"
                "while True:\n"
                "    sys.stdout.write(buf)\n")


class _OversizedAdapter(FA.ForgeIdentityAdapterV1):
    """An adapter that refuses every source for being over the size profile."""

    version = "forge.identity-adapter.oversized-double.v1"

    def lower_source(self, source):
        raise FA.ForgeSourceTooLarge("over the profile")


class _HangingAdapter(FA.ForgeIdentityAdapterV1):
    """An adapter whose lowering never answered and was killed."""

    version = "forge.identity-adapter.hanging-double.v1"

    def lower_source(self, source):
        raise FA.ForgeTimeout("killed on its deadline")


def _identity_context():
    task = {"identity_policy": {"must_remain": {
        "core": {"path": "core.wrl", "before_id": "sem-" + "0" * 64}}}}
    return VerifierContextV1(
        task=task, snapshot={}, original_content={"core.wrl": "x"},
        run={}, patched_content={"core.wrl": "x" * 16})


def test_y16_a_patched_source_over_the_byte_limit_is_an_identity_fail():
    """A declared byte bound is evidence about the submission, not the host.

    9D made `ForgeSourceTooLarge` a `ForgeUnavailable` so the existing handlers
    would absorb it, and flagged what that cost: `error` nulls the reward, so a
    candidate could unscore its identity signal by writing a large enough WRL
    file. The ruling drew the line at attribution, and the same source is over
    the bound on every host — so it is a `fail`.

    The base class is asserted too. Left as a `ForgeUnavailable` subclass the
    old handler would keep catching it first and keep producing `error`,
    whatever the verdict below said.
    """
    assert not issubclass(FA.ForgeSourceTooLarge, FA.ForgeUnavailable)

    verifier = SV.make_identity_verifier(_OversizedAdapter())
    result = verifier(_identity_context())
    assert result.state == R.FAIL, result.state
    assert result.detail["error_code"] == FA.ERROR_CODE_FORGE_SOURCE_TOO_LARGE
    # A *code*, never a size: a byte count is a fact about these bytes rather
    # than about the rule, and this detail is sealed into `episode-…`.
    assert "path" in result.detail
    assert not any(isinstance(v, int) for v in result.detail.values())


def test_y17_a_forge_wall_clock_timeout_remains_error_and_null():
    """The other side of the line, and it must not move.

    A timeout is host-sensitive: the same source on a slower machine takes
    longer, so turning it into a numerical failure would score a submission
    differently on two hosts. The ruling is explicit that it stays `error` until
    Forge has a deterministic fuel or reduction bound — a *reduction* count is a
    fact about the computation, a *second* is a fact about the computer.

    `error` means `reward = None` rather than 0, which is not a good outcome for
    the candidate either: the episode stays invalid, wins nothing, and remains
    in split-level coverage. Asserted here so `null` is never read as a pass.
    """
    verifier = SV.make_identity_verifier(_HangingAdapter())
    result = verifier(_identity_context())
    assert result.state == R.ERROR, result.state
    assert result.detail["error_code"] == FA.ERROR_CODE_FORGE_TIMEOUT
    scored = R.score({"identity": R.ERROR}, _IDENTITY_REWARD, ["identity"])
    assert scored["reward"] is None, scored
    # ...and `null` is not a soft landing: the episode is invalid either way.
    assert scored["validity"] == "invalid" and scored["status"] == "error", scored


_IDENTITY_REWARD = {
    "reward_spec_version": "traaviis.reward.v1",
    "substrate_profile": "residency.repository.v1",
    "signals": {"identity": {"verifier": "residency.identity.v1",
                             "weight": 1.0}},
    "aggregation": "terminal",
}


def test_y18_the_attribution_rule_is_one_rule_and_not_a_list_of_cases():
    """The five laws above are instances; this states what they instantiate.

        deterministic candidate-byte limit
            -> evidence against the candidate  (invalid / 0, or fail)
        host-runtime availability or wall-clock limit
            -> no trustworthy candidate verdict  (error / null)

    Written as an executable table rather than as prose, so that a future
    refusal has to be classified rather than left to inherit whichever handler
    it happens to land in — which is exactly how an oversized source came to be
    `error` in the first place.

    The `error` row is not a soft landing and the last assertion says so: an
    error episode is invalid, wins nothing, and stays in coverage. `null` must
    never read as a good result.
    """
    byte_limits = ["stdout_too_large", "stderr_too_large", "result_too_large",
                   "patch_too_large", "workspace_file_too_large",
                   "workspace_too_large", "too_many_files", "too_many_entries",
                   "too_many_directories", "workspace_too_deep",
                   "path_too_long", "workspace_paths_too_large",
                   "irregular_file", "symlink", "processes_escaped"]
    for reason in byte_limits:
        assert reason in E.REASONS, reason
    # Every one of them is recorded as a *policy violation*, which is the single
    # mechanism that produces invalid/0. There is no second path to that verdict.
    prefix = RUN.RESOURCE_VIOLATION_PREFIX
    assert prefix.startswith("/"), \
        "a relative path could forge one of these entries"

    tampered = R.score({"citations": R.PASS}, _REWARD, ["citations"],
                       tampered=True)
    errored = R.score({"identity": R.ERROR}, _IDENTITY_REWARD, ["identity"])
    failed = R.score({"identity": R.FAIL}, _IDENTITY_REWARD, ["identity"])
    assert (tampered["reward"], tampered["validity"]) == (0.0, "invalid"), tampered
    assert (errored["reward"], errored["validity"]) == (None, "invalid"), errored
    assert failed["reward"] == 0.0, failed
    # The two zeroes are reached by different routes and mean different things:
    # a byte-limit violation invalidates the whole episode, a `fail` is an
    # ordinary bad answer inside a valid one. Collapsing them would lose the
    # attribution this table exists to keep.
    assert tampered["validity"] != failed["validity"]


def test_y19_the_containment_report_is_what_was_achieved_not_what_was_asked():
    """`enforced` is a measurement, and the profile name is the guarantee.

    The ruling's requirement -- a host that cannot provide the mechanism must not
    claim process-tree containment -- is enforced by this field being computed
    from what happened rather than from what was attempted. `killpg` plus a
    session is a real mitigation and is *not* containment, so it reports
    `enforced: false` and a different profile name; the two are never collapsed.

    On a host with the mechanism this also asserts the strong case, so the file
    does not consist entirely of laws about the weak one.
    """
    assert C.CONTAINMENT_PROFILES == (C.CGROUP_KILL_OBSERVED_PROFILE,
                                      C.PROCESS_GROUP_PROFILE,
                                      C.UNCONTAINED_PROFILE)
    # The name says what was measured. "cgroup-v2" sounded like the whole
    # mechanism; what it delivers is an observed kill of whatever stayed.
    assert C.CGROUP_KILL_OBSERVED_PROFILE == "traaviis.cgroup-kill-observed.v1"
    assert C.LEGACY_CGROUP_PROFILE == "traaviis.cgroup-v2.v1"
    weak = C.Containment(C.PROCESS_GROUP_PROFILE)
    assert weak.facts()["kill_boundary_enforced"] is False, weak.facts()
    assert weak.facts()["certifiable"] is False, weak.facts()
    none = C.Containment(C.UNCONTAINED_PROFILE)
    assert none.facts()["kill_boundary_enforced"] is False, none.facts()
    assert none.facts()["certifiable"] is False, none.facts()

    if not CONTAINABLE:
        raise Skip(_NO_MECHANISM)
    box = C.open_containment(prefix="law")
    try:
        assert box.profile == C.CGROUP_KILL_OBSERVED_PROFILE, box.profile
        box.kill_all()
        box.close()
        facts = box.facts()
        assert facts["kill_boundary_enforced"] is True, facts
        assert facts["remaining_processes_in_boundary"] == 0, facts
    finally:
        box.kill_all()
        box.close()


def test_y20_a_missing_boundary_refuses_before_the_candidate_runs():
    """A host that cannot contain must **refuse**, not run and then punish.

    This law used to assert the opposite, and the 9F ruling corrected it. 9E
    recorded `/resource-limit.v1:containment_unavailable` as a policy violation,
    which ran the candidate and then marked the episode invalid — charging a
    submission for the evaluator host's topology. The submission did nothing.

    The corrected line:

        the boundary could not be established at all
            -> no evaluation, no episode, exit 2

        candidate misbehaves inside a boundary that *was* established
            -> invalid / reward 0

    Three things are checked. `containment.preflight(strict=True)` raises before
    anything is spawned. `run_agent` no longer records the missing boundary as a
    violation — read off the parse tree, since the absence of a string is
    exactly what a comment could restore. And the capability vector still
    travels on the result, so nothing is hidden; it is simply not charged.
    """
    facts = C.preflight(strict=False)
    assert set(C.CAPABILITY_AXES) <= set(facts), facts
    assert "certifiable" in facts and "uncertifiable_reasons" in facts

    if facts["certifiable"]:
        raise Skip("this host certifies; the refusal path needs one that cannot")
    try:
        C.preflight(strict=True)
    except C.ContainmentUnavailable as exc:
        assert exc.reasons, "a refusal must say which capability was missing"
        assert set(exc.reasons) == set(facts["uncertifiable_reasons"]), exc.reasons
    else:
        raise AssertionError(
            "strict preflight accepted a host whose own report says it cannot "
            "certify: %s" % facts)

    recorded = set()
    for node in ast.walk(ast.parse(inspect.getsource(RUN.run_agent))):
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("append"):
            recorded.add(ast.unparse(node))
    joined = " ".join(sorted(recorded))
    assert "containment_unavailable" not in joined, (
        "run_agent still charges the candidate for the host's topology: %s"
        % joined)
    # ...while the candidate-attributable one stays, because a process that
    # escaped a boundary the evaluator *did* establish is evidence.
    assert "processes_escaped" in joined, joined
    assert "RESOURCE_VIOLATION_PREFIX" in joined, joined


def test_y21_containment_leaves_no_cgroup_behind_and_sweeps_abandoned_ones():
    """The boundary must not leak the thing it is made of.

    Two defects, one found by looking rather than by a failing law.

    `rmdir` immediately after the drain can fail with `EBUSY`: the kernel
    reports `populated 0` a moment before it will remove the directory. A single
    attempt therefore leaves an **empty** cgroup behind. Measured on this box:
    seven of them accumulated in an afternoon. Each is cheap and none of them
    ever goes away, which is the wrong shape for something an evaluator does
    thousands of times — the resource-exhaustion class 9E exists to close, one
    layer beneath the runs.

    And a run killed between `kill_all` and `close` — an interrupted battery, a
    crashed evaluator — leaves its directory regardless of how patient `rmdir`
    is. So `open_containment` sweeps directories whose name carries a pid that
    no longer exists and which the kernel reports unpopulated.

    **The sweep's safety is the interesting assertion.** A live run's cgroup is
    named after a live pid, so it can never be swept out from under a concurrent
    evaluation. That is checked directly: a containment belonging to *this*
    process survives a sweep, and a directory named after a pid that is gone
    does not.
    """
    _needs_containment()
    base = C._own_cgroup_path()
    assert base and os.path.isdir(base)

    def mine():
        return {n for n in os.listdir(base) if n.startswith("traaviis-")}

    before = mine()
    box = C.open_containment(prefix="leaklaw")
    assert box.profile == C.CGROUP_KILL_OBSERVED_PROFILE
    created = os.path.basename(box.path)
    box.kill_all()
    box.close()
    assert created not in mine(), \
        "a closed containment left its cgroup behind"
    assert mine() <= before | {created} - {created}, \
        "closing one containment disturbed another"

    # A live run is never swept: this containment is named after *this* pid.
    live = C.open_containment(prefix="livelaw")
    try:
        C._sweep_abandoned(base)
        assert os.path.isdir(live.path), \
            "the sweep removed a cgroup belonging to a running process"
    finally:
        live.kill_all()
        live.close()

    # ...and an abandoned one is. A pid that cannot exist stands in for a
    # crashed evaluator, which is the case the sweep is for and the one that
    # cannot be produced by crashing this battery on purpose.
    dead = os.path.join(base, "traaviis-abandoned-%d-1" % _IMPOSSIBLE_PID)
    os.mkdir(dead)
    try:
        C._sweep_abandoned(base)
        assert not os.path.isdir(dead), \
            "an empty cgroup from a dead process was not swept"
    finally:
        if os.path.isdir(dead):
            os.rmdir(dead)


#: A pid no process can have. `/proc/sys/kernel/pid_max` is the ceiling, so one
#: above it is unallocatable by construction -- which is what makes the sweep's
#: "this process is gone" branch testable without killing something real.
def _impossible_pid():
    try:
        with open("/proc/sys/kernel/pid_max", encoding="ascii") as fh:
            return int(fh.read().strip()) + 1
    except (OSError, ValueError):        # pragma: no cover -- non-Linux
        return 2 ** 31 - 1


_IMPOSSIBLE_PID = _impossible_pid()


# ================= Y22-Y23: the ninth route, executable rather than inferred

_MIGRATE_AND_DAEMONIZE = (
    "import os, sys, time\n"
    "def note(s):\n"
    "    open(%(report)r, 'a').write(s + '\\n')\n"
    "def mycg():\n"
    "    for line in open('/proc/self/cgroup'):\n"
    "        p = line.rstrip('\\n').split(':', 2)\n"
    "        if len(p) == 3 and p[0] == '0':\n"
    "            return '/sys/fs/cgroup' + p[2]\n"
    "    return None\n"
    "cur = mycg()\n"
    "note('cgroup=' + str(cur))\n"
    "escaped = False\n"
    "if cur:\n"
    "    parent = os.path.dirname(cur)\n"
    "    try:\n"
    "        fd = os.open(parent + '/cgroup.procs', os.O_WRONLY)\n"
    "        os.write(fd, str(os.getpid()).encode()); os.close(fd)\n"
    "        note('MIGRATED-PARENT'); escaped = True\n"
    "    except OSError as e:\n"
    "        note('parent refused: %%s' %% e)\n"
    "    if not escaped:\n"
    "        try:\n"
    "            sib = parent + '/traaviis-law-sibling'\n"
    "            os.mkdir(sib)\n"
    "            fd = os.open(sib + '/cgroup.procs', os.O_WRONLY)\n"
    "            os.write(fd, str(os.getpid()).encode()); os.close(fd)\n"
    "            note('MIGRATED-SIBLING'); escaped = True\n"
    "        except OSError as e:\n"
    "            note('sibling refused: %%s' %% e)\n"
    "note('escaped=' + str(escaped))\n"
    "r, w = os.pipe()\n"
    "if os.fork() == 0:\n"
    "    os.setsid()\n"
    "    if os.fork() == 0:\n"
    "        os.write(w, str(os.getpid()).encode()); os.close(w)\n"
    "        time.sleep(600)\n"
    "    os._exit(0)\n"
    "os.close(w)\n"
    "open(%(pid)r, 'w').write(os.read(r, 32).decode())\n"
    "os.wait()\n")


def _migration_probe():
    """Run a candidate that tries to leave its cgroup, then daemonizes.

    Returns `(run, escaped-pid, report text, scratch root)`. The caller owns the
    pid and the directory.
    """
    root = tempfile.mkdtemp(prefix="trvs-9f-migrate-")
    pidfile = os.path.join(root, "escape.pid")
    report = os.path.join(root, "report.txt")
    script = _MIGRATE_AND_DAEMONIZE % {"report": report, "pid": pidfile}
    run = E.run_bounded([sys.executable, "-c", script], cwd=root,
                        timeout=TERMINATES_WITHIN)
    time.sleep(0.5)
    with open(pidfile, encoding="ascii") as fh:
        pid = int(fh.read().strip())
    with open(report, encoding="utf-8") as fh:
        text = fh.read()
    return run, pid, text, root


def test_y22_a_candidate_cannot_both_escape_the_cgroup_and_be_reported_contained():
    """The ninth route, as an executable law instead of an inference.

    9E claimed *"a cgroup v2 membership cannot be left"*. That is **false**, and
    this law is what proves it either way rather than arguing about the kernel's
    delegation model. The candidate reads `/proc/self/cgroup`, writes its pid to
    the parent's `cgroup.procs` (or makes a sibling and moves into it),
    double-forks a daemon, and exits 0.

    The acceptance rule is the ruling's, and it is a disjunction — any of these
    is fine:

        migration denied
        or the descendant stays inside the run boundary
        or the descendant is killed

    and exactly one outcome is forbidden:

        the run returns successfully while the descendant survives

    **On this host the escape succeeds**, so what the law enforces here is the
    honesty half: a run that could be escaped must not report `certifiable`.
    That is not a softer test — it is the same forbidden outcome, stated as the
    conjunction it actually is. 9E failed it: `enforced: true`,
    `remaining_processes: 0`, descendant alive.
    """
    _needs_containment()
    run, pid, report, root = _migration_probe()
    try:
        alive = _alive(pid)
        facts = run["process_containment"]
        escaped = "MIGRATED-PARENT" in report or "MIGRATED-SIBLING" in report

        # The forbidden outcome, in full.
        assert not (run["exit_code"] == 0 and alive and facts.get("certifiable")), (
            "a candidate escaped its cgroup, survived the run, and the run "
            "reported itself certifiably contained.\nreport:\n%s\nfacts: %s"
            % (report, facts))

        # And the two halves are consistent with each other: if the control
        # plane was reachable the escape must have worked, and if it was not,
        # the descendant must be dead. Either direction failing means the probe
        # and the measurement disagree, and one of them is lying.
        #
        # The key is asserted present before it is read. This law previously
        # said `facts.get("control_plane_reachable")` and kept passing after
        # that field was replaced by `control_plane_isolated` -- `get` returned
        # `None`, the law took the "isolated" branch, and it went red claiming
        # the candidate had escaped an isolated containment. A missing
        # measurement read as a measurement is the same defect as inferring
        # success from an absent failure, one level up in the test.
        assert "control_plane_isolated" in facts, facts
        if facts["control_plane_isolated"] is not True:
            assert escaped, (
                "the containment reports its control plane reachable, but the "
                "candidate could not migrate. The measurement is pessimistic "
                "and should be tightened:\n%s" % report)
            assert not facts.get("certifiable"), facts
        else:
            assert not escaped, (
                "the candidate migrated out of a containment that reports its "
                "control plane unreachable:\n%s" % report)
            assert not alive, "the descendant outlived a certifiable run"
    finally:
        _reap(pid)
        sib = os.path.join(os.path.dirname(
            C.open_containment(prefix="tmp").path or ""), "traaviis-law-sibling")
        if os.path.isdir(sib):
            try:
                os.rmdir(sib)
            except OSError:
                pass
        shutil.rmtree(root, ignore_errors=True)


def test_y23_the_boundary_reports_whether_it_can_bound_and_not_only_kill():
    """The tenth route: a kill boundary is not a resource boundary.

    A per-run cgroup with no controllers can kill everything afterwards and can
    bound nothing during. A fork bomb or a fast allocation reaches the host
    before any wall-clock supervisor does, and killing the cgroup after the host
    has OOMed preserves no episode — the grade is erased by the machine dying
    rather than by anything the evaluator classified.

    A controller only exists in a cgroup if its parent delegated it through
    `cgroup.subtree_control`, and cgroup v2's no-internal-process rule forbids
    enabling that on a cgroup holding processes. Measured on this host: the
    parent holds dozens, so the per-run cgroup gets none.

    So this law does not require controllers — it requires the run to **say**
    whether it had them, which is what stops "contained" from being read as
    "bounded". Applying them is 9F items 5-7 and needs a topology this host does
    not currently provide.
    """
    _needs_containment()
    box = C.open_containment(prefix="ctl")
    try:
        facts = box.facts()
        assert "controllers" in facts, facts
        assert isinstance(facts["controllers"], list), facts
        assert facts["controllers"] == sorted(facts["controllers"])
        # Whatever is reported must actually be there: a claimed controller with
        # no control file is the same class of overclaim as `enforced` was.
        for controller in facts["controllers"]:
            probe = {"pids": "pids.max", "memory": "memory.max"}.get(controller)
            if probe:
                assert os.path.exists(os.path.join(box.path, probe)), \
                    "%s is claimed but %s does not exist" % (controller, probe)
    finally:
        box.kill_all()
        box.close()


def test_y24_a_candidate_created_nested_cgroup_does_not_leak_the_run_cgroup():
    """The candidate owns its run cgroup, so it can build inside it.

    That is not incidental: putting the candidate *in* the cgroup is what the
    bootstrap does, and a cgroup a process can join is a cgroup that process can
    `mkdir` under. Reproduced: the candidate creates `a/b/c`, moves into the
    leaf, and exits. `cgroup.kill` is transitive so nothing survives — but
    `rmdir` refuses a cgroup with children, so the **run cgroup leaks
    permanently**, one kernel object per run, forever.

    Teardown is therefore depth-first and bounded: a million nested directories
    would make the cleanup walk the unbounded thing, which is the enumeration
    lesson of 9E one layer down. Hitting the bound leaves the rest to the sweep,
    which is a slow leak rather than a hang.

    The count is reported, and that matters beyond hygiene: a nonzero
    `nested_cgroups_removed` is evidence the candidate **touched the control
    plane that defines its own containment**, which is the ninth route's
    territory even when it did not manage to leave.
    """
    _needs_containment()
    base = C._own_cgroup_path()
    before = {n for n in os.listdir(base) if n.startswith("traaviis-")}

    root = tempfile.mkdtemp(prefix="trvs-9f-nested-")
    script = ("import os\n"
              "cur = [l.split(':', 2)[2].strip() for l in open('/proc/self/cgroup')\n"
              "       if l.startswith('0::')][0]\n"
              "p = '/sys/fs/cgroup' + cur\n"
              "os.makedirs(p + '/a/b/c')\n"
              "open(p + '/a/b/c/cgroup.procs', 'w').write(str(os.getpid()))\n")
    try:
        run = E.run_bounded([sys.executable, "-c", script], cwd=root,
                            timeout=TERMINATES_WITHIN)
        facts = run["process_containment"]
        if run["exit_code"] != 0:
            raise Skip("this host does not let a candidate nest cgroups")

        assert facts["nested_cgroups_removed"] >= 3, (
            "the candidate created three nested cgroups and teardown removed "
            "%s" % facts["nested_cgroups_removed"])
        assert run["surviving_processes"] == 0, facts

        after = {n for n in os.listdir(base) if n.startswith("traaviis-")}
        assert after <= before, (
            "the run cgroup leaked: %s" % sorted(after - before))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = skipped = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print("PASS %s" % t.__name__)
        except Skip as s:
            skipped += 1
            print("SKIP %s (%s)" % (t.__name__, s))
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (t.__name__, e))
    print("\n%d passed, %d skipped, %d failed" % (passed, skipped, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
