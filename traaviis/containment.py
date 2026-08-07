"""Process containment — the boundary ends when candidate *execution* ends.

The eighth erasure route
========================
9D gave every spawned command its own session and killed the **group** on a
deadline or an output overflow. That is not a process-tree boundary, and the
ruling demonstrated the two ways through it::

    normal background child:   parent exit 0 -> descendant alive after run = true
    setsid  background child:  parent exit 0 -> descendant alive after run = true

Two distinct defects, and the first is the plainer one:

* **A successful exit cleaned nothing up.** The group kill fired only on timeout
  and on overflow. A candidate that spawns a worker and then exits 0 gets a
  clean run *and* leaves the worker running.
* **``setsid()`` escapes the group entirely.** Even an unconditional
  ``killpg`` is not enough: a descendant can put itself in a new session before
  the kill arrives, and a signal addressed to the old group never reaches it.

What survives can consume the host, mutate the workspace *while the evidence
scan is reading it*, and outlive the episode to attack the next one. It is the
same grade-erasure class as the FIFO: candidate-controlled work survives the
boundary and can still destroy the evaluator after the nominal command is done.

The mechanism
=============
A process group is a *cooperative* grouping — a process may leave it. A **cgroup
v2** membership is not: a process cannot remove itself from its cgroup, and
``cgroup.kill`` signals every member, transitively, regardless of session,
process group, parent, or reparenting to ``init``. ``cgroup.events`` then reports
``populated 0`` when the last one is gone, so "everything is dead" is something
the kernel *tells* us rather than something we infer from a ``/proc`` scan by
parent pid — which reparenting makes useless.

Three profiles, and the names are the guarantee:

``traaviis.cgroup-v2.v1``
    A per-run cgroup. The child is placed in it **before it can fork** (see
    `GUARD_SOURCE`), everything in it is killed after the direct child exits for
    *any* reason, and the kill is confirmed by waiting for ``populated 0``.
    ``remaining_processes`` is 0 or the run says so.

``traaviis.process-group.v1``
    Best-effort. A session + ``killpg``, which is what 9D had. It is honest
    about being defeatable by ``setsid``, and callers that require containment
    refuse it rather than treating it as the real thing.

``traaviis.uncontained.v1``
    Nothing. Reported, never claimed.

**Nothing here claims more than it delivers.** `Containment.facts()` returns the
profile that was actually achieved on this host and this run, not the one that
was asked for. A caller that needs real containment tests
``enforced and remaining_processes == 0``; the ruling's requirement that a host
which cannot provide the mechanism must not claim it is enforced by that field
existing and being false, rather than by a comment.
"""

import itertools
import os
import signal
import subprocess
import time

__all__ = [
    "CGROUP_KILL_OBSERVED_PROFILE", "CGROUP_V2_PROFILE", "LEGACY_CGROUP_PROFILE",
    "PROCESS_GROUP_PROFILE", "UNCONTAINED_PROFILE",
    "UNKNOWN", "CAPABILITY_AXES", "REQUIRED_CONTROLLERS",
    "ContainmentUnavailable", "preflight",
    "control_plane_reachable", "available_controllers",
    "CONTAINMENT_PROFILES", "DRAIN_SECONDS", "GUARD_SOURCE",
    "Containment", "open_containment", "cgroup_v2_available",
    "kill_process_group",
]

#: An **observed kill boundary**, and deliberately not called containment.
#:
#: The old name was ``traaviis.cgroup-v2.v1``, which sounds like the whole
#: thing. What this mechanism actually establishes is narrower and is exactly
#: what the name now says: every process still *in that cgroup* when the run
#: ends is killed, transitively, and the kernel confirms it. It does **not**
#: establish that a candidate could not leave — reproduced on the host that
#: shipped 9E, by writing a pid to the parent's ``cgroup.procs``.
#:
#: The corrected kernel-level statement, which is the one to reason from:
#:
#:     A delegatee cannot move processes outside its delegated subtree when the
#:     delegation boundary and permissions are configured correctly. *Within* a
#:     delegated subtree the delegatee may reorganize processes and create
#:     sub-cgroups as permitted.
CGROUP_KILL_OBSERVED_PROFILE = "traaviis.cgroup-kill-observed.v1"

#: The superseded name, kept so a stored value can still be read and so the
#: rename is legible rather than a silent substitution.
LEGACY_CGROUP_PROFILE = "traaviis.cgroup-v2.v1"

CGROUP_V2_PROFILE = CGROUP_KILL_OBSERVED_PROFILE
#: Best-effort: a session and ``killpg``. Defeated by ``setsid``.
PROCESS_GROUP_PROFILE = "traaviis.process-group.v1"
#: None at all.
UNCONTAINED_PROFILE = "traaviis.uncontained.v1"

#: Every profile this module can report, strongest first.
CONTAINMENT_PROFILES = (CGROUP_KILL_OBSERVED_PROFILE, PROCESS_GROUP_PROFILE,
                        UNCONTAINED_PROFILE)

#: The third truth value. A capability is `True` when it was **positively
#: tested**, `False` when it was tested and absent, and `UNKNOWN` when it could
#: not be tested at all.
#:
#: `UNKNOWN` exists because 9E's defect was not a wrong measurement -- it was
#: success inferred from an absent failure. Nothing was observed to escape, so
#: containment was reported. A missing observation now has its own value, and
#: `certifiable` treats it as disqualifying rather than as consent.
UNKNOWN = "unknown"

#: The axes `certifiable` is a conjunction over. Four separate questions that
#: one boolean used to answer at once.
CAPABILITY_AXES = ("kill_boundary_enforced", "control_plane_isolated",
                   "resource_controllers_enforced", "cleanup_closed")

#: The controllers a certified boundary must actually have. A kill boundary
#: ends a run; these bound it *while* it runs, which is the difference between
#: killing a fork bomb afterwards and the host surviving it.
REQUIRED_CONTROLLERS = frozenset({"memory", "pids"})

#: How long to wait for a killed cgroup to drain to ``populated 0``. Bounded for
#: the reason every wait in this package is bounded: an unbounded wait after a
#: kill reintroduces the hang the kill exists to end. A ``SIGKILL`` that has not
#: taken effect in this long means a task is stuck in uninterruptible I/O, which
#: is a fact about the host and is reported as a nonzero ``remaining_processes``
#: rather than waited out forever.
DRAIN_SECONDS = 30.0

_POLL_SECONDS = 0.005

#: The first drain poll. Doubles up to `_POLL_SECONDS`.
_DRAIN_POLL_MIN = 0.0002

#: How long to keep retrying the final `rmdir`. Short: the directory is already
#: empty by then, and anything still holding it is the host's business.
_RMDIR_SECONDS = 2.0

#: Bounds on the teardown walk. A candidate owns its own run cgroup, so it can
#: create cgroups *inside* it -- and a million of them would make teardown as
#: unbounded as the workspace enumeration 9E just bounded. Same lesson, one
#: layer down: the walk that cleans up is itself a resource.
_MAX_NESTED_CGROUPS = 4096
_MAX_NESTED_DEPTH = 32

#: Where a delegated cgroup subtree lives, if one does.
_CGROUP_ROOT = "/sys/fs/cgroup"


#: The bootstrap the child runs before becoming the real command.
#:
#: **Why a bootstrap at all.** The child has to be *inside* the cgroup before it
#: can fork, or the very first thing an adversarial command does — fork, then let
#: the parent be moved — puts a descendant outside the boundary. Writing the pid
#: from the parent after ``Popen`` returns loses that race by construction.
#:
#: **Why not ``preexec_fn``.** It runs between ``fork`` and ``exec`` in a process
#: that may hold locks taken by other threads — and this package's own
#: ``mcp_server`` handles each request on its own thread, so a verifier really
#: does execute in a multi-threaded process. That is the identical hazard
#: ``forge_adapter`` cites when it refuses ``multiprocessing``'s ``fork`` start
#: method, and the answer is the same: a fresh interpreter shares nothing.
#:
#: **Why ``-c`` and not ``-m``.** ``-m traaviis.spawnguard`` would need the
#: package on the child's path, and the agent's environment is a *sealed map* —
#: adding ``PYTHONPATH`` to it would change ``environment_keys``, which is inside
#: the canonical trace event, which would move every ``trace-…`` ever minted.
#: Inline source needs nothing but the interpreter.
#:
#: The status pipe is how an exec failure is told apart from the command's own
#: exit. Its write end is marked close-on-exec, so the parent sees **EOF** iff
#: the exec succeeded, and reads an ``errno`` iff it did not. That is exactly the
#: mechanism CPython's own ``subprocess`` uses, reproduced here because the
#: bootstrap sits between ``Popen`` and the real program.
GUARD_SOURCE = """\
import os, sys
procs, status_fd = sys.argv[1], int(sys.argv[2])
argv = sys.argv[3:]
try:
    if procs != "-":
        with open(procs, "w") as fh:
            fh.write(str(os.getpid()))
except OSError as exc:
    os.write(status_fd, b"C%d" % (exc.errno or 0))
    os._exit(125)
try:
    os.set_inheritable(status_fd, False)
    os.execv(argv[0], argv)
except OSError as exc:
    os.write(status_fd, b"X%d" % (exc.errno or 0))
    os._exit(127)
except Exception:
    os.write(status_fd, b"X0")
    os._exit(127)
"""


def kill_process_group(proc):
    """Kill `proc` and its process *group*. The best-effort boundary, in one place.

    **One implementation, deliberately.** This lived in `execlimits` as well
    until a non-vacuity probe in `test_evalone` deleted the `killpg` there,
    forced the weak profile, and watched the grandchild die anyway -- because the
    *other* copy, here, was still doing the group kill. A duplicated safety
    primitive is a probe that disables a boundary it is not measuring, and a law
    that then reports the boundary works.

    It is what 9D had and it is **not** process-tree containment: a descendant
    that calls `setsid()` leaves the group before any signal addressed to it
    arrives. `CGROUP_V2_PROFILE` is the boundary; this is the fallback, and the
    profile name is what says which one a run got.

    Degrades to killing the child on a platform with no ``killpg``, or when the
    group has already gone. A cleanup path that raises is worse than one that
    does less.
    """
    if hasattr(os, "killpg"):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _remove_nested(path):
    """Remove cgroups the candidate created *inside* its own run cgroup.

    A candidate owns its run cgroup — that is what lets the bootstrap put it
    there — so it can `mkdir` inside it and move into the child. Reproduced on
    this host: the nested directory survives teardown and **blocks removal of
    the run cgroup itself**, permanently, because `rmdir` refuses a cgroup with
    children. `cgroup.kill` is transitive so nothing *lives* there; what leaks
    is the kernel object, one per run, forever.

    Depth-first, because a cgroup can only be removed after its children.
    Bounded in both count and depth for the reason 9E bounded workspace
    enumeration: a million nested directories would make the cleanup walk the
    unbounded thing. Hitting a bound leaves the remainder for the sweep at the
    next `open_containment`, which is a slow leak rather than a hang.

    Returns how many were removed, so a caller can say whether the candidate
    did this at all.
    """
    removed = 0
    stack = [(path, 0)]
    order = []
    while stack:
        current, depth = stack.pop()
        if depth >= _MAX_NESTED_DEPTH or len(order) >= _MAX_NESTED_CGROUPS:
            break
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        order.append(entry.path)
                        stack.append((entry.path, depth + 1))
        except OSError:
            # A cgroup that vanished under us is not an error: the thing we were
            # about to remove is already gone.
            continue
    for child in reversed(order):
        try:
            os.rmdir(child)
            removed += 1
        except OSError:
            continue
    return removed


def control_plane_reachable(path):
    """Can the candidate edit the cgroup files that define its own containment?

    Three ways out of a per-run cgroup, all of them writes the candidate makes
    as its own uid, and any one of them defeats ``cgroup.kill`` on that cgroup:

    * write its pid to the **parent's** ``cgroup.procs`` -- migrate up;
    * create a **sibling** under the parent and migrate into it;
    * create a **nested** cgroup under its own and hide there, which also blocks
      removal of the run cgroup afterwards.

    The first is what was reproduced. All three reduce to one question -- is the
    parent directory writable by this uid -- because the candidate runs as the
    evaluator's own user and sees the host cgroup filesystem. There is no
    cgroup namespace, no mount namespace, and no uid separation between them.

    Answered by asking the kernel (``os.access``) rather than by reasoning about
    delegation, because the reasoning is exactly what 9E got wrong.
    """
    parent = os.path.dirname(path)
    try:
        if os.access(os.path.join(parent, "cgroup.procs"), os.W_OK):
            return True
        if os.access(parent, os.W_OK | os.X_OK):
            return True
        return bool(os.access(path, os.W_OK | os.X_OK))
    except OSError:
        return True


def available_controllers(path):
    """The resource controllers this cgroup actually has, as a sorted tuple.

    A cgroup only gets a controller if its **parent** delegated it through
    ``cgroup.subtree_control``, and cgroup v2's no-internal-process rule forbids
    enabling that on a cgroup which holds processes. Measured on the host that
    shipped 9E: the parent holds 61 of them, so the per-run cgroup gets nothing
    and ``pids.max`` / ``memory.max`` do not exist there at all.

    That is the tenth route in one sentence: the boundary can kill afterwards
    and cannot *bound* during. A fork bomb or a fast allocation reaches the host
    before any wall-clock supervisor does, and killing the cgroup after the host
    has OOMed preserves no episode.

    Reported rather than assumed, so a run can say which of the two it had.
    """
    try:
        with open(os.path.join(path, "cgroup.controllers"), encoding="ascii") as fh:
            return tuple(sorted(fh.read().split()))
    except OSError:
        return ()


def _own_cgroup_path():
    """This process's cgroup v2 directory, or ``None``.

    Read from ``/proc/self/cgroup``'s v2 line (``0::<path>``). A new cgroup is
    created *underneath* it, which is the only place an unprivileged process may
    create one: systemd delegates the user's own subtree, and delegation is what
    makes this work with no privilege at all.
    """
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split(":", 2)
                if len(parts) == 3 and parts[0] == "0":
                    return os.path.join(_CGROUP_ROOT, parts[2].lstrip("/"))
    except OSError:
        return None
    return None


def cgroup_v2_available():
    """Can this host actually give us a killable cgroup? Measured, not assumed.

    By *doing* it: create a directory, look for ``cgroup.kill``, remove it. A
    capability test that reads ``/proc/filesystems`` or checks a mount option
    would be a prediction, and this module's whole contract is that it reports
    what was achieved rather than what was expected.
    """
    base = _own_cgroup_path()
    if base is None or not os.path.isdir(base):
        return False
    probe = os.path.join(base, "traaviis-probe-%d" % os.getpid())
    try:
        os.mkdir(probe)
    except OSError:
        return False
    try:
        return os.path.exists(os.path.join(probe, "cgroup.kill"))
    finally:
        try:
            os.rmdir(probe)
        except OSError:
            pass


class Containment(object):
    """One run's containment. Open it, spawn into it, then empty it.

    Not a context manager by accident: `close` has to run after the direct child
    is reaped *and* report what it found, and a `__exit__` that swallowed that
    number would be the thing this class exists to prevent.
    """

    def __init__(self, profile, path=None):
        self.profile = profile
        self.path = path
        self.remaining = 0
        self.error = None
        self._closed = False
        #: Can a process running as the candidate reach the control files that
        #: define this containment? Measured at open time; see `facts`.
        self.control_plane_reachable = (
            control_plane_reachable(path) if path is not None else True)
        #: Which resource controllers this cgroup actually has. Empty means the
        #: boundary can kill but cannot *bound* -- see `available_controllers`.
        self.controllers = available_controllers(path) if path is not None else ()
        #: How many cgroups the candidate created inside its own, removed at
        #: teardown. Nonzero means the candidate touched the control plane.
        self.nested_removed = 0
        #: Did teardown leave nothing behind? `UNKNOWN` until `close` has run --
        #: which is why `run_bounded` closes before it reports, and why a report
        #: taken earlier would be claiming an outcome it has not reached yet.
        self.cleanup_closed = UNKNOWN

    # -- spawning -----------------------------------------------------------
    @property
    def procs_path(self):
        """The file a joining process writes its pid to, or ``"-"``."""
        if self.profile != CGROUP_V2_PROFILE or self.path is None:
            return "-"
        return os.path.join(self.path, "cgroup.procs")

    def wrap(self, argv, status_fd):
        """The argv that actually gets spawned, given the caller's `argv`.

        For the cgroup profile this is the bootstrap; for every other profile it
        is `argv` unchanged, because there is nothing to join and paying for an
        interpreter start to do nothing would be a cost with no guarantee behind
        it.
        """
        if self.profile != CGROUP_V2_PROFILE:
            return list(argv)
        import sys
        return [sys.executable, "-c", GUARD_SOURCE,
                self.procs_path, str(status_fd)] + list(argv)

    def wraps(self):
        """Does this profile insert a bootstrap between ``Popen`` and the command?"""
        return self.profile == CGROUP_V2_PROFILE

    # -- emptying -----------------------------------------------------------
    def _members(self):
        try:
            with open(os.path.join(self.path, "cgroup.procs"), encoding="ascii") as fh:
                return [int(line) for line in fh if line.strip()]
        except (OSError, ValueError):
            return []

    def _populated(self):
        """``cgroup.events``' ``populated`` flag, or ``None`` if unreadable.

        The kernel's own answer to "is anything still alive in here", which is
        why it is preferred over counting ``cgroup.procs``: it is transitive over
        descendant cgroups, so a process that created a *nested* cgroup to hide
        in is still counted.
        """
        try:
            with open(os.path.join(self.path, "cgroup.events"), encoding="ascii") as fh:
                for line in fh:
                    key, _, value = line.strip().partition(" ")
                    if key == "populated":
                        return value.strip() == "1"
        except OSError:
            return None
        return None

    def kill_all(self, proc=None):
        """Kill everything this run started, and **wait until nothing is left**.

        Called after the direct child is reaped, on *every* path — success,
        failure, timeout and overflow alike. 9D killed only on timeout and
        overflow, so a candidate that spawned a worker and exited 0 got a clean
        run and left the worker behind. A boundary that ends when the parent
        ends is not a boundary around the candidate's execution.

        Sets `remaining` to the number of processes still alive when the drain
        deadline expired — 0 on every normal run. A caller that needs to *claim*
        containment reads that number; it is never inferred.
        """
        if self.profile == CGROUP_V2_PROFILE and self.path is not None:
            try:
                with open(os.path.join(self.path, "cgroup.kill"), "w") as fh:
                    fh.write("1")
            except OSError as exc:
                self.error = "cgroup.kill: %s" % exc
            # An escalating poll, not a flat one. `SIGKILL` on a small tree is
            # done in well under a millisecond, and a flat 5 ms poll made every
            # single spawn in the package pay that 5 ms -- across a battery of
            # thousands of commands it is minutes of pure waiting. Starting
            # fine and backing off keeps the common case free while a genuinely
            # slow drain (a task stuck in uninterruptible I/O) still costs only
            # the bounded `DRAIN_SECONDS`.
            deadline = time.monotonic() + DRAIN_SECONDS
            wait = _DRAIN_POLL_MIN
            while True:
                populated = self._populated()
                if populated is False:
                    self.remaining = 0
                    return self.remaining
                if populated is None:
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(wait)
                wait = min(wait * 2, _POLL_SECONDS)
            self.remaining = len(self._members())
            return self.remaining

        # Best-effort. `killpg` reaches the session the child was given and
        # nothing that left it, which is the defect this profile is named for.
        if proc is not None:
            kill_process_group(proc)
        # `remaining` stays 0 and `enforced` is False. Reporting 0 here is not a
        # claim that nothing survived — it is the honest statement that this
        # profile cannot count, and `enforced: false` is what a reader must key
        # off. Guessing a number would be worse than admitting there is none.
        return self.remaining

    def close(self):
        """Remove the run's cgroup directory. Retried, because `rmdir` can race.

        The kernel can report ``populated 0`` a moment before it is willing to
        remove the directory, so a single `rmdir` immediately after the drain
        fails with ``EBUSY`` and leaves an **empty** cgroup behind. Measured:
        seven of them accumulated in one afternoon's work on this box. Each is
        cheap on its own and they never go away, which is the wrong shape for a
        thing an evaluator does thousands of times.

        Bounded, and it does not raise. A cleanup path that raises is worse than
        one that does less, and a survivor is already reported through
        `remaining` rather than through this.
        """
        if self._closed:
            return
        self._closed = True
        if self.profile == CGROUP_V2_PROFILE and self.path is not None:
            self.nested_removed = _remove_nested(self.path)
            deadline = time.monotonic() + _RMDIR_SECONDS
            wait = _DRAIN_POLL_MIN
            while True:
                try:
                    os.rmdir(self.path)
                    self.cleanup_closed = True
                    return
                except FileNotFoundError:
                    self.cleanup_closed = True
                    return
                except OSError:
                    if time.monotonic() >= deadline:
                        self.cleanup_closed = False
                        return
                    time.sleep(wait)
                    wait = min(wait * 2, _POLL_SECONDS)
        else:
            # Nothing was created, so nothing is left. True by construction
            # rather than by measurement, and the two are worth distinguishing:
            # this branch never had a cgroup to leak.
            self.cleanup_closed = True

    # -- reporting ----------------------------------------------------------
    def facts(self):
        """The capability vector — four axes, three truth values, one conjunction.

        9E reported one boolean, ``enforced``, and it answered four different
        questions at once. Worse, it answered them by *inference*: nothing was
        observed to escape, so containment was reported. The reproduced escape
        showed what that is worth — ``enforced: true, remaining_processes: 0``
        while a descendant it had lost was still alive.

        The axes are separated so that each is a claim somebody can check:

        ``kill_boundary_enforced``
            everything still *in this cgroup* at the end was killed, and the
            kernel confirmed it via ``populated 0``. True here.
        ``control_plane_isolated``
            the candidate could not reach the cgroup files that define its own
            containment. False on a host where the parent is writable by the
            candidate's uid, which is any host where they share a user.
        ``resource_controllers_enforced``
            ``memory`` and ``pids`` limits were actually applied. A kill
            boundary bounds nothing *during* the run; see `available_controllers`.
        ``cleanup_closed``
            no cgroup object survived the run, including any the candidate
            nested inside its own.

        ``certifiable`` is their **conjunction**, and `UNKNOWN` never satisfies
        it. That is the whole correction: *do not infer success from an absent
        failure*. ``uncertifiable_reasons`` names every axis that failed, so a
        refusal explains itself rather than leaving a caller to re-derive it.
        """
        kill = self.profile == CGROUP_KILL_OBSERVED_PROFILE and self.error is None
        reachable = self.control_plane_reachable
        isolated = UNKNOWN if reachable is UNKNOWN else (not reachable)
        controllers = set(self.controllers)
        enforced_controllers = bool(REQUIRED_CONTROLLERS <= controllers)
        cleanup = self.cleanup_closed

        axes = {
            "kill_boundary_enforced": kill,
            "control_plane_isolated": isolated,
            "resource_controllers_enforced": enforced_controllers,
            "cleanup_closed": cleanup,
        }
        reasons = []
        if kill is not True:
            reasons.append("kill_boundary_not_established")
        if isolated is not True:
            reasons.append("candidate_can_reach_parent_cgroup"
                           if isolated is False else "control_plane_unknown")
        for controller in sorted(REQUIRED_CONTROLLERS):
            if controller not in controllers:
                reasons.append("%s_controller_unavailable" % controller)
        if cleanup is not True:
            reasons.append("cleanup_not_closed"
                           if cleanup is False else "cleanup_unknown")

        facts = {
            "profile": self.profile,
            "remaining_processes_in_boundary": int(self.remaining),
            "controllers": sorted(controllers),
            "nested_cgroups_removed": int(self.nested_removed),
            "certifiable": all(axes[a] is True for a in CAPABILITY_AXES),
            "uncertifiable_reasons": reasons,
        }
        facts.update(axes)
        if self.error:
            facts["error"] = self.error
        return facts


def _sweep_abandoned(base, limit=64):
    """Remove empty cgroups left by processes that are gone.

    A run that is killed between `kill_all` and `close` -- an interrupted
    battery, a crashed evaluator -- leaves its directory behind. They are empty
    and harmless individually and they never disappear on their own, so a
    long-running evaluator accumulates them for as long as it runs.

    **Concurrency-safe by construction**, which is the only reason this is safe
    to do at all: a directory is touched only if its name carries a pid that no
    longer exists *and* the kernel reports it unpopulated. A live run's cgroup
    is named after a live pid, so a concurrent evaluation can never be swept out
    from under itself. `limit` keeps the sweep bounded -- this runs before every
    spawn, and an unbounded scan of a directory somebody else filled would be
    the enumeration problem 9E just closed, one layer down.
    """
    try:
        names = os.listdir(base)
    except OSError:
        return
    swept = 0
    for name in names:
        if swept >= limit:
            return
        if not name.startswith("traaviis-"):
            continue
        parts = name.rsplit("-", 2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        try:
            os.kill(pid, 0)
            continue                       # still running; not ours to remove
        except PermissionError:            # pragma: no cover -- alive, not ours
            continue
        except ProcessLookupError:
            pass
        except OSError:
            continue
        path = os.path.join(base, name)
        try:
            with open(os.path.join(path, "cgroup.events"), encoding="ascii") as fh:
                if "populated 1" in fh.read():
                    continue
            # Nested first. A run abandoned *while* a candidate had created
            # cgroups inside its own leaves a directory `rmdir` will refuse
            # forever, so a sweep that only tried the top level would walk past
            # exactly the leftovers that never go away on their own.
            _remove_nested(path)
            os.rmdir(path)
            swept += 1
        except OSError:
            continue


def open_containment(prefix="run"):
    """The strongest containment this host will give, opened and ready.

    Falls back rather than raising, because the *caller* decides whether a weak
    profile is acceptable — the runner refuses it, some batteries accept it, and
    a module that raised here would make that a decision it took on their behalf.
    """
    base = _own_cgroup_path()
    if base is not None and os.path.isdir(base):
        _sweep_abandoned(base)
        # Retried, and the retry is not defensiveness. `mcp_server` serves
        # concurrent sessions on threads, so two runs can open containment at
        # the same moment; a name collision would make `mkdir` fail and the
        # fallback below would **silently downgrade a contained run to
        # best-effort**. A silent downgrade of a safety boundary is worse than
        # no boundary, because the receipt would still say the run was watched.
        for _ in range(8):
            path = os.path.join(base, "traaviis-%s-%d-%d"
                                % (prefix, os.getpid(), next(_SEQUENCE)))
            try:
                os.mkdir(path)
            except FileExistsError:
                continue
            except OSError:
                break
            if os.path.exists(os.path.join(path, "cgroup.kill")):
                return Containment(CGROUP_V2_PROFILE, path)
            try:
                os.rmdir(path)
            except OSError:
                pass
            break
    if os.name == "posix":
        return Containment(PROCESS_GROUP_PROFILE)
    return Containment(UNCONTAINED_PROFILE)


#: A per-process sequence, so two concurrent runs cannot share a cgroup name.
#:
#: `itertools.count` rather than a counter this module increments, and the
#: difference is a real one under threads: `n += 1` on a shared value is a
#: read-modify-write and the interpreter may switch threads in the middle of it,
#: so two concurrent runs could draw the same number. `count.__next__` is a
#: single C call and cannot be interleaved. Not a timestamp and not randomness:
#: two runs starting in the same millisecond must still get two directories.
_SEQUENCE = itertools.count(1)


class ContainmentUnavailable(Exception):
    """A certified run was asked for and this host cannot provide the boundary.

    **Raised before the candidate is started, and that is the whole point.**
    9E ran the candidate and then marked the episode invalid when containment
    turned out to be missing — which punishes the candidate for the evaluator's
    host topology. A boundary that cannot be *established* is not misconduct; it
    is an unavailable evaluation.

    The line the 9F ruling draws:

        candidate migrates after a certified boundary was established
        candidate creates a forbidden nested cgroup
        candidate exceeds a deterministic controller limit
            -> invalid / reward 0

        the boundary could not be established at all
            -> no evaluation, no episode, exit 2

    `reasons` carries the failing capability axes, so the refusal says which
    guarantee was missing rather than only that one was.
    """

    def __init__(self, message, reasons=()):
        super().__init__(message)
        self.reasons = list(reasons)


def preflight(strict=True):
    """Open a containment, measure it, and tear it down. Returns its facts.

    A *dry run of the boundary* before any candidate exists. Every axis is
    established by doing the thing rather than by reading a config: the cgroup
    is created, its control plane probed, its controllers listed, and it is
    removed again. Nothing here can report a capability that was not exercised.

    `strict` decides whether an incomplete answer raises or is merely returned,
    because both callers are real: a certified command must refuse, and a
    best-effort command wants the same vector to *report*.
    """
    box = open_containment(prefix="preflight")
    try:
        box.kill_all()
    finally:
        box.close()
    facts = box.facts()
    if strict and not facts["certifiable"]:
        raise ContainmentUnavailable(
            "this host cannot establish a certified containment boundary: %s"
            % ", ".join(facts["uncertifiable_reasons"]),
            facts["uncertifiable_reasons"])
    return facts


def read_status(raw):
    """Decode the bootstrap's status pipe. ``None`` means the exec succeeded.

    ``b""`` (EOF) is success: the write end was close-on-exec, so the pipe
    closing with nothing in it *is* the report that the exec happened. A
    non-empty reading is one tag byte (``C`` for a containment failure, ``X`` for
    an exec failure) followed by a decimal errno.
    """
    if not raw:
        return None
    kind = "containment" if raw[:1] == b"C" else "exec"
    try:
        code = int(raw[1:] or b"0")
    except ValueError:
        code = 0
    return (kind, code)


def status_error(status):
    """The exception `run_bounded` should report for one status-pipe reading.

    An **exec** failure is reconstructed with its errno, deliberately:
    ``OSError(ENOENT, ...)`` *is* a `FileNotFoundError`, which is the exact type
    `subprocess.Popen` used to raise straight out of a spawn and the exact type
    `runner.run_agent` is contracted to re-raise. Inserting a bootstrap between
    ``Popen`` and the command must not change what a caller catches.

    A **containment** failure is deliberately errno-free. Its errno describes a
    write to ``cgroup.procs``, not the command, and constructing `OSError` with
    it could yield a `FileNotFoundError` for a run whose ``argv[0]`` was perfectly
    fine — an evaluator's infrastructure problem wearing a candidate's clothes.
    """
    kind, code = status
    if kind == "containment":
        detail = os.strerror(code) if code else "unknown error"
        return OSError("the run could not be placed in its cgroup (%s); it was "
                       "not started" % detail)
    return OSError(code, os.strerror(code) if code else "exec failed")
