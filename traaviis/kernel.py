"""`EpisodeKernelV1` — the substrate-neutral episode kernel.

Everything that runs an episode in TRAAVIIS has, until now, been one function:
`evalone.evaluate`. That function admits a subject, preflights a configuration,
launches a local subprocess, verifies the result and seals a receipt — five
different responsibilities, one of which (the subprocess) is the only part that
is specific to "an agent that is a command on this machine".

This module extracts the other four into a kernel, and it does so **before** any
transport exists. The order matters. A kernel extracted after an HTTP server is
a description of whatever the server happened to need; a kernel extracted before
one is a statement about what an episode *is*, which the server then has to
translate into. The ruled build order is: freeze the kernel, then write
`trvs serve --ors` as a translation layer over it.

The interface
-------------

::

    EpisodeKernelV1 {
        list_tasks()
        start(task_id)
        observe(session_id)
        step(session_id, action)
        reset(session_id)
        finalize(session_id, run_result)
        close(session_id)
    }

`start` returns a **session id**: an ephemeral process handle. It is emphatically
not an artifact id. It is minted from randomness, not from content, so two starts
of the identical task produce different session ids; it never enters a receipt, a
trace, an episode bundle or any canonical byte string. The identity ladder (RFC
§1) is closed and this slice does not add a rung to it — a session is a thing the
process is doing, and when the process ends it is simply gone.

What Residency v1 supports, and what it refuses
-----------------------------------------------

`residency.repository.v1` is a **one-shot** substrate. An episode is: admit a
frozen repository, run one agent once, score what came back. There is no
observation between steps because there are no steps. So `ResidencyKernelV1`
supports `list_tasks` / `start` / `finalize` / `close` and refuses `observe`,
`step` and `reset` by name, with a typed `KERNEL_OPERATION_UNSUPPORTED`.

It would have been easy — and much friendlier to a future generic client — to
make those three succeed as no-ops: `observe` returns the initial content,
`step` ignores the action, `reset` does nothing. That is exactly the failure this
refusal exists to prevent. A client that steps a Residency episode and gets back
`ok` has been told its actions were applied. They were not. An interactive
protocol whose operations silently do nothing is worse than one that has none,
because the second cannot be believed by mistake.

The adapter
-----------

The existing local command runner becomes an adapter over the kernel::

    session_id = kernel.start(task_id)
    run_result = runner.run_agent(agent_command, session.content, session.policy)
    evaluation_run = kernel.finalize(session_id, run_result)
    kernel.close(session_id)

`run_episode` is that, exactly. Note what the kernel does *not* take: the agent
command. The kernel knows what the agent must be given (the admitted content and
the run policy) and what it must produce (a `RunResult`); it does not know or
care how the thing that produces one is invoked. That is the whole substance of
"substrate-neutral", and it is why an ORS transport can be a translation layer
rather than a second pipeline.

One consequence is worth stating plainly, because it constrains `serve --ors`
before it is written. The local adapter reads `session.content` and
`session.policy` *in process*, through `kernel.session(...)`. A remote client
cannot: the only ruled way to read a session across a transport is `observe`,
and Residency refuses it. So a Residency episode is not remotely steppable, and
an ORS layer over this kernel can honestly expose `start` + `finalize` and
nothing else. That is a real limit of the substrate, surfaced by the extraction
rather than hidden by it.

Process model
-------------

One kernel = one admitted environment. Many ephemeral sessions may be open
against it at once, over one shared verifier registry and one shared engine seam.
The kernel's lock is held only for the dict operations that register, look up,
transition and release a session — **never** across a session's lifetime, never
across scoring, and never across a subprocess. A kernel that locked for the
duration of a run would serialize a server down to one episode at a time and
would deadlock the moment a session outlived a request.

But a lock that is *only* short is not enough, and the gap is worth naming
because it is the kind that hides. Finalization is a **one-shot** transition,
and reading `state == "started"` in one statement and writing `"finalized"` in a
later one is not one. Two callers can both pass the check before either writes,
and the second is not refused — it re-runs the verifier plan, which for a
Residency task may mean executing a candidate's test suite a second time. Both
callers get the *same receipt*, which is exactly why nobody notices: the
identity is unmoved and the work is doubled. Sequentially it cannot happen;
under a server accepting concurrent requests it is reachable from outside.

So the lifecycle carries an explicit in-flight state and the claim is atomic::

    started ──claim (under the lock)──▶ finalizing ──▶ finalized
                                                   └──▶ finalize_failed

`finalize` takes the lock only to move `started → finalizing`, does the whole
expensive part outside it, then takes it again to record the outcome. A caller
arriving mid-flight loses the claim and is refused by name. `close` refuses a
`finalizing` session (`KERNEL_SESSION_BUSY`) — removing it from the table would
not stop the work, only make it unattributable. A failed finalization is
terminal rather than retryable, because verifiers and test commands have
external effects and "run it again" is not a safe default; the honest recovery
is a new session over the same task.
"""

import threading
import uuid

from . import admission as _admission
from . import runner
from . import substrates as _substrates
# The kernel phases live in `evalone` because that is where the receipt builder
# lives and a receipt must be built by exactly one piece of code (`build_receipt_v1`
# is shared with replay). The kernel owns the *lifecycle*; `evalone` owns the
# *episode*. `evalone.evaluate` imports this module lazily to close the cycle.
from .evalone import _admit_episode, _finish_episode, _invalid_config_run

__all__ = [
    "KERNEL_VERSION",
    "OPERATIONS",
    "INTERACTIVE_OPERATIONS",
    "SESSION_PREFIX",
    "SESSION_STARTED",
    "SESSION_FINALIZING",
    "SESSION_FINALIZED",
    "SESSION_FINALIZE_FAILED",
    "SESSION_STATES",
    "SESSION_CLOSEABLE",
    "KernelError",
    "TaskEntryV1",
    "SessionV1",
    "EpisodeKernelV1",
    "ResidencyKernelV1",
    "local_kernel",
    "environment_kernel",
    "run_episode",
]

KERNEL_VERSION = "traaviis.episode-kernel.v1"

#: The frozen operation set. A kernel implements a subset of these and refuses
#: the rest by name; nothing outside this tuple is a kernel operation.
OPERATIONS = ("list_tasks", "start", "observe", "step", "reset",
              "finalize", "close")

#: The three operations that only mean something for a substrate with a
#: *stepped* episode. Residency v1 has none, and refuses all three.
INTERACTIVE_OPERATIONS = ("observe", "step", "reset")

#: Session ids carry this prefix so that a session id is visibly *not* an
#: artifact id at a glance. Every rung of the identity ladder is
#: `<name>-<64 hex>` derived from canonical bytes; a session is `session-` plus
#: randomness and derives from nothing.
SESSION_PREFIX = "session-"

#: A session's lifecycle::
#:
#:     started → finalizing → finalized
#:                          → finalize_failed
#:
#: `started`, `finalized` and `finalize_failed` may be closed; `finalizing` may
#: not, because another caller is inside the scoring work at that moment.
#: **Closed is not a state** — a closed session is forgotten, not retained, so
#: "closed" is spelled by the session's absence from the table.
#:
#: `finalizing` is what makes finalization *linearizable*. Without it two
#: callers can both read `started` before either writes `finalized`, and the
#: second one is not refused — it re-runs the verifiers. The receipt would be
#: identical (that is what makes it hard to notice), but the verifier plan may
#: execute a test suite, so "the same answer twice" is not the same thing as
#: "the work happened once".
SESSION_STARTED = "started"
SESSION_FINALIZING = "finalizing"
SESSION_FINALIZED = "finalized"
SESSION_FINALIZE_FAILED = "finalize_failed"

#: Every state a live session can be in, in lifecycle order.
SESSION_STATES = (SESSION_STARTED, SESSION_FINALIZING, SESSION_FINALIZED,
                  SESSION_FINALIZE_FAILED)

#: The states from which a session may be closed. `finalizing` is excluded.
SESSION_CLOSEABLE = (SESSION_STARTED, SESSION_FINALIZED,
                     SESSION_FINALIZE_FAILED)


class KernelError(_substrates.AdmissionError, _admission.AdmissionError):
    """A typed kernel refusal.

    It inherits from **both** admission error types on purpose. The newer typed
    one (`substrates.AdmissionError`, which carries `code` / `message` /
    `detail`) is the shape every refusal in this codebase now takes; the older
    plain one (`admission.AdmissionError`) is what `evalsplit._run_one` and the
    CLI already catch to turn a bad task into a recorded failed episode rather
    than an abandoned split. Inheriting from one and not the other would have
    quietly changed which failures abandon a run — so it inherits from both, and
    every existing `except` clause keeps meaning what it meant.
    """


class TaskEntryV1(object):
    """One task a kernel can start: the complete admitted input for an episode.

    This is a *catalog* record, not an artifact. It holds references to the
    already-read task / reward / snapshot documents and the admitted content
    map, plus the verifier implementations wired for this task. Nothing here is
    hashed and nothing here is minted.
    """

    __slots__ = ("task_id", "task", "reward_spec", "snapshot", "content",
                 "extra_verifiers")

    def __init__(self, task_id, task, reward_spec, snapshot, content,
                 extra_verifiers=None):
        self.task_id = task_id
        self.task = task
        self.reward_spec = reward_spec
        self.snapshot = snapshot
        self.content = content
        self.extra_verifiers = dict(extra_verifiers or {})

    def __repr__(self):
        return "TaskEntryV1(%s)" % self.task_id


class SessionV1(object):
    """One open episode session. An ephemeral process handle, nothing more.

    A session exists between `start` and `close`. It holds the admitted plan —
    which is to say, everything that was proven true before the agent was
    allowed to run — and the two things an adapter needs in order to run one:
    `content` and `policy`.

    `runnable` is the F4 config preflight's answer. A task whose *required*
    signal has no verifier that can resolve it is an invalid configuration, and
    the agent must not run at all; the session is still a real session, its
    receipt is already determined, and `finalize(session_id, None)` returns it.
    Refusing to open the session instead would have turned a scored outcome
    (`status = invalid`) into an exception, which is a different claim.
    """

    __slots__ = ("session_id", "task_id", "kernel_version", "substrate_profile",
                 "state", "result", "_plan")

    def __init__(self, session_id, task_id, plan, *, kernel_version,
                 substrate_profile):
        self.session_id = session_id
        self.task_id = task_id
        self.kernel_version = kernel_version
        self.substrate_profile = substrate_profile
        self.state = SESSION_STARTED
        #: The `EvaluationRunV1` this session produced, once it has one. Held so
        #: a caller that reaches the session after a successful `finalize` reads
        #: the result rather than being tempted to compute it a second time.
        #: `None` in every other state.
        self.result = None
        self._plan = plan

    @property
    def runnable(self):
        """True when the agent should be run; False for an invalid config (F4)."""
        return not self._plan["unresolved"]

    @property
    def content(self):
        """The admitted subject content the agent runs over."""
        return self._plan["content"]

    @property
    def policy(self):
        """The task's `AgentRunPolicyV1`, already proven honest for this runner."""
        return self._plan["policy"]

    def describe(self):
        """A transport-safe description. No content, no bytes, no identities
        the session does not own."""
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "kernel_version": self.kernel_version,
            "substrate_profile": self.substrate_profile,
            "state": self.state,
            "runnable": self.runnable,
        }

    def __repr__(self):
        return "SessionV1(%s, %s, %s)" % (
            self.session_id, self.task_id, self.state)


class EpisodeKernelV1(object):
    """The frozen kernel interface. Every operation refuses unless declared.

    A subclass states `supported_operations` and overrides exactly those. Every
    other operation in `OPERATIONS` raises `KERNEL_OPERATION_UNSUPPORTED` from
    the base class, which is what makes "this substrate cannot do that" a
    property of the declaration rather than of whichever method somebody
    remembered to leave unimplemented.
    """

    kernel_version = KERNEL_VERSION
    substrate_profile = None
    supported_operations = ()

    def supports(self, operation):
        """Whether this kernel implements `operation`."""
        return operation in self.supported_operations

    def describe(self):
        """What this kernel is and what it can do. Not identity-bearing."""
        return {
            "kernel_version": self.kernel_version,
            "substrate_profile": self.substrate_profile,
            "operations": {op: self.supports(op) for op in OPERATIONS},
        }

    def _unsupported(self, operation):
        raise KernelError(
            "KERNEL_OPERATION_UNSUPPORTED",
            "substrate %r has no %s operation; this kernel supports %s"
            % (self.substrate_profile, operation,
               ", ".join(self.supported_operations) or "nothing"),
            {"operation": operation,
             "substrate_profile": self.substrate_profile,
             "supported": list(self.supported_operations)})

    # --- the seven ruled operations ---------------------------------------
    def list_tasks(self):
        self._unsupported("list_tasks")

    def start(self, task_id):
        self._unsupported("start")

    def observe(self, session_id):
        self._unsupported("observe")

    def step(self, session_id, action):
        self._unsupported("step")

    def reset(self, session_id):
        self._unsupported("reset")

    def finalize(self, session_id, run_result):
        self._unsupported("finalize")

    def close(self, session_id):
        self._unsupported("close")


class ResidencyKernelV1(EpisodeKernelV1):
    """The `residency.repository.v1` kernel: one-shot, no observation, no steps.

    Constructed over an **already admitted** environment — a task catalog whose
    subject tree has been bound to its snapshot once, and whose verifier
    implementations come from one registry. Every session it opens re-proves the
    per-task admission laws (declared ids, cross-binding, run policy) before it
    will report itself runnable, so a catalog entry is a convenience, never a
    shortcut past a check.
    """

    substrate_profile = "residency.repository.v1"
    supported_operations = ("list_tasks", "start", "finalize", "close")

    def __init__(self, entries, *, platform="unknown", toolchain=None,
                 runner_profile=runner.RUNNER_PROFILE):
        self._entries = {e.task_id: e for e in entries}
        self._platform = platform
        self._toolchain = toolchain
        self._runner_profile = runner_profile
        self._sessions = {}
        # Guards the session table only. Never held across `_admit_episode`,
        # `_finish_episode` or a subprocess — see the module docstring.
        self._lock = threading.Lock()

    # --- catalog ----------------------------------------------------------
    def list_tasks(self):
        """The task ids this kernel can start, sorted.

        A listing, not a proof. For an environment kernel the ids were already
        re-derived from the packaged bytes by `open_package`; for the one-task
        local kernel they are the task's declared id. Either way `start` runs
        the full admission and is where a wrong id is caught.
        """
        return sorted(self._entries)

    def entry(self, task_id):
        """The catalog record for `task_id`. Raises `KERNEL_TASK_UNKNOWN`."""
        try:
            return self._entries[task_id]
        except KeyError:
            raise KernelError(
                "KERNEL_TASK_UNKNOWN",
                "this kernel has no task %r (has: %s)"
                % (task_id, ", ".join(self.list_tasks()) or "none"),
                {"task_id": task_id, "known": self.list_tasks()})

    def describe(self):
        out = EpisodeKernelV1.describe(self)
        out["tasks"] = self.list_tasks()
        out["open_sessions"] = self.open_sessions()
        return out

    # --- sessions ---------------------------------------------------------
    def open_sessions(self):
        """The ids of the sessions currently open, sorted."""
        with self._lock:
            return sorted(self._sessions)

    def session(self, session_id):
        """The live `SessionV1` for `session_id`.

        A kernel-local accessor, deliberately **not** one of the seven ruled
        operations: it hands back the in-process session object, which only an
        in-process adapter can use. The transport-visible way to read a session
        is `observe`, and this substrate refuses it.
        """
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KernelError(
                "KERNEL_SESSION_UNKNOWN",
                "no open session %r" % session_id,
                {"session_id": session_id})
        return session

    def start(self, task_id):
        """Admit `task_id` and open a session. Returns the ephemeral session id.

        Everything that can be checked without running anything is checked here:
        declared reward / task / snapshot ids are recomputed, the content is
        proven to be the sealed subject, the task is cross-bound to *these*
        verified artifacts, the run policy is refused if the runner cannot
        honestly keep it, and the F4 configuration preflight decides whether the
        agent may run at all. A failure raises before a session exists.
        """
        entry = self.entry(task_id)
        plan = _admit_episode(
            entry.task, entry.content, entry.reward_spec,
            snapshot=entry.snapshot, extra_verifiers=entry.extra_verifiers,
            runner_profile=self._runner_profile)
        session_id = SESSION_PREFIX + uuid.uuid4().hex
        session = SessionV1(
            session_id, task_id, plan,
            kernel_version=self.kernel_version,
            substrate_profile=self.substrate_profile)
        with self._lock:
            self._sessions[session_id] = session
        return session_id

    def _claim_finalize(self, session_id, run_result):
        """Atomically take the session's one finalization, or refuse.

        The whole transition — look the session up, check it is still
        `started`, check the run result matches what the preflight decided, and
        mark it `finalizing` — happens under the table lock, so exactly one
        caller can ever leave this method holding the claim. Everything
        expensive stays outside.

        The two run-result refusals deliberately leave the session `started`:
        passing the wrong argument is a caller error that ran nothing, so it
        must not consume the session's single chance to be scored. Only
        entering the scoring work does that.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KernelError(
                    "KERNEL_SESSION_UNKNOWN",
                    "no open session %r" % session_id,
                    {"session_id": session_id})
            if session.state != SESSION_STARTED:
                raise KernelError(
                    "KERNEL_SESSION_STATE",
                    "session %r is %s and cannot be finalized%s"
                    % (session_id, session.state,
                       " while another caller is finalizing it"
                       if session.state == SESSION_FINALIZING else " again"),
                    {"session_id": session_id, "state": session.state})
            if session.runnable and run_result is None:
                raise KernelError(
                    "KERNEL_RUN_RESULT_MISSING",
                    "session %r is runnable; finalize needs the RunResult the "
                    "agent produced" % session_id,
                    {"session_id": session_id, "task_id": session.task_id})
            if not session.runnable and run_result is not None:
                raise KernelError(
                    "KERNEL_RUN_RESULT_UNEXPECTED",
                    "session %r has an invalid task configuration and was not "
                    "runnable; no agent should have run" % session_id,
                    {"session_id": session_id, "task_id": session.task_id})
            session.state = SESSION_FINALIZING
            return session

    def _release_finalize(self, session, state, result=None):
        """Record the outcome of a claimed finalization, under the lock."""
        with self._lock:
            session.state = state
            session.result = result

    def finalize(self, session_id, run_result):
        """Score the session **exactly once** and return its `EvaluationRunV1`.

        `run_result` is the `runner.RunResult` the adapter produced, or `None`
        for a session the config preflight already ruled invalid. Supplying the
        wrong one of those is refused rather than reconciled: a run result for a
        session that was never allowed to run means the caller ran an agent it
        was told not to, and scoring it anyway would seal evidence the receipt's
        own preflight says does not exist.

        **One shot, and the shot is claimed atomically.** Scoring is not a pure
        read: the verifier plan may execute a candidate's test suite. Two
        callers that both observed `started` would both run it. So the state
        transition is serialized under the table lock while the work itself —
        verification, scoring, evidence — runs outside it, and a second caller
        arriving mid-flight is refused with `KERNEL_SESSION_STATE` rather than
        quietly handed a second execution whose receipt happens to match.

        A finalization that *raises* is terminal: the session becomes
        `finalize_failed` and cannot be finalized again. Verifiers and test
        commands have external effects, so a silent retry is not generally safe
        — the honest recovery is a new session over the same task.
        """
        session = self._claim_finalize(session_id, run_result)
        try:
            plan = session._plan
            if not session.runnable:
                run = _invalid_config_run(plan)
            else:
                run = _finish_episode(
                    plan, run_result, platform=self._platform,
                    toolchain=self._toolchain,
                    runner_profile=self._runner_profile)
        except BaseException:
            self._release_finalize(session, SESSION_FINALIZE_FAILED)
            raise
        self._release_finalize(session, SESSION_FINALIZED, run)
        return run

    def close(self, session_id):
        """Release the session. Returns True if one was open.

        Idempotent for an id that names nothing: closing is releasing a process
        handle, not freeing a resource that can be double-freed, and an
        adapter's `finally: close()` must not raise over an already-released id
        and mask the real error on its way out.

        The one refusal is `KERNEL_SESSION_BUSY`, for a session another caller
        is actively finalizing. Removing it from the table there would not stop
        the work — it would only make the result unattributable, and let a third
        caller `start` past a verifier run still in flight.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if session.state == SESSION_FINALIZING:
                raise KernelError(
                    "KERNEL_SESSION_BUSY",
                    "session %r is being finalized and cannot be closed"
                    % session_id,
                    {"session_id": session_id, "state": session.state})
            del self._sessions[session_id]
            return True


def local_kernel(task, content, reward_spec, *, snapshot, extra_verifiers=None,
                 platform="unknown", toolchain=None,
                 runner_profile=runner.RUNNER_PROFILE):
    """A one-task Residency kernel over an already-admitted subject.

    This is the `eval-one` shape: a caller holding one task, its reward, its
    snapshot and the bound content, with no package around them. The catalog key
    is the task's declared `task_id`; when a task declares none (which
    `admission.verify_declared_id` permits) it is derived, because a catalog
    needs a key even when the document did not supply one. `start` recomputes and
    reconciles it either way.
    """
    from . import identity

    task_id = task.get("task_id") if hasattr(task, "get") else None
    if not isinstance(task_id, str) or not task_id:
        try:
            task_id = identity.task_id(task)
        except Exception as exc:  # pragma: no cover - malformed task document
            raise KernelError(
                "KERNEL_TASK_UNIDENTIFIED",
                "the task declares no task_id and one cannot be derived: %s" % exc,
                {"error": "%s: %s" % (type(exc).__name__, exc)})
    entry = TaskEntryV1(task_id, task, reward_spec, snapshot, content,
                        extra_verifiers)
    return ResidencyKernelV1([entry], platform=platform, toolchain=toolchain,
                             runner_profile=runner_profile)


def environment_kernel(manifest, artifacts, snapshot, content, *,
                       extra_verifiers_for=None, platform="unknown",
                       toolchain=None, runner_profile=runner.RUNNER_PROFILE):
    """A Residency kernel over one reopened package — one kernel, many sessions.

    Takes what `evalsplit.open_environment` already produced rather than
    reopening a package a second way: the manifest, the re-derived artifacts, the
    snapshot, and the subject content bound to it **once** for every task in the
    package. `extra_verifiers_for(task)` supplies the implementations; it is the
    same seam `eval` uses, so the split and the kernel cannot wire differently.

    The substrate is checked by name. `trvm.world.v1` packs and reopens, but its
    episode semantics are unbuilt, and a kernel that accepted it would have to
    invent them.
    """
    profile = manifest.get("substrate_profile")
    if profile != ResidencyKernelV1.substrate_profile:
        raise KernelError(
            "KERNEL_SUBSTRATE_UNSUPPORTED",
            "no episode kernel for substrate %r; %r is the only substrate with "
            "frozen episode semantics" % (profile,
                                          ResidencyKernelV1.substrate_profile),
            {"substrate_profile": profile})

    tasks = artifacts.get("tasks") or {}
    rewards = artifacts.get("rewards") or {}
    entries = []
    for task_id in sorted(tasks):
        task = tasks[task_id]
        reward_spec = rewards.get(task.get("reward_id"))
        if reward_spec is None:
            raise KernelError(
                "KERNEL_REWARD_UNRESOLVED",
                "task %s references reward %r, which is not in this package"
                % (task_id, task.get("reward_id")),
                {"task_id": task_id, "reward_id": task.get("reward_id")})
        extra = extra_verifiers_for(task) if extra_verifiers_for else {}
        entries.append(TaskEntryV1(task_id, task, reward_spec, snapshot,
                                   content, extra or {}))
    return ResidencyKernelV1(entries, platform=platform, toolchain=toolchain,
                             runner_profile=runner_profile)


def run_episode(kernel, task_id, agent_command):
    """The local-command adapter: `start` → `runner.run_agent` → `finalize`.

    The whole of the ruled adapter, and the only place in TRAAVIIS where the
    kernel and the subprocess boundary meet. Returns the complete
    `EvaluationRunV1`.

    An invalid-config session is not run: the kernel already knows its receipt,
    and `finalize(session_id, None)` returns it. That branch is the reason
    `runnable` is a property of the session rather than something the adapter
    infers, and the reason `finalize` refuses a `RunResult` for a session that
    was not runnable.
    """
    session_id = kernel.start(task_id)
    try:
        session = kernel.session(session_id)
        run_result = None
        if session.runnable:
            run_result = runner.run_agent(
                agent_command, session.content, session.policy)
        return kernel.finalize(session_id, run_result)
    finally:
        kernel.close(session_id)
