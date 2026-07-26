"""`Residency Submission ORS Profile v1` — the remote-submission adapter.

This is the translation layer the Episode Kernel Closure was extracted *for*.
It is deliberately not a second pipeline: every episode it produces goes through
the same `EpisodeKernelV1` that `trvs eval-one` and `trvs eval` go through, the
same `build_receipt_v1`, and the same `write_episode_bundle`. What this module
adds is a wire vocabulary and exactly one new fact — that the candidate arrived
as a *submission* rather than as the output of a process this machine launched.

What it is, and what it refuses to pretend to be
------------------------------------------------

`residency.repository.v1` is one-shot. It has no observation between steps
because it has no steps, and the kernel refuses `observe` / `step` / `reset` by
name. An ORS layer over it therefore cannot honestly advertise an interactive
sandbox, and this one does not: it exposes **exactly one tool**,
`submit_candidate`, and markets itself as a submission-and-reward adapter.

The three interactive routes still *exist* on the transport, and they relay the
kernel's own typed `KERNEL_OPERATION_UNSUPPORTED`. A 404 would say "this server
has no such endpoint", which is a claim about the transport; the truth is a
claim about the substrate, and the client deserves to be told which.

The trust boundary
------------------

The client supplies a finding and a patch. It supplies **nothing else**. Not the
reward, not the status or validity, not verifier outcomes or versions, not a
trace id or an episode id, not execution facts, not an exit code, not a timeout
flag, not a policy-violation list, and above all not a `runner.RunResult` — a
`RunResult` is the *server's* record of what it observed, and accepting one from
the wire would let a client narrate its own execution into a receipt.

`RemoteSubmissionV1` is therefore validated by **exact key set**, not by a
denylist. A denylist of twelve forbidden names answers "is this one of the
twelve things we thought of"; an exact key set answers "is this the document".
The named diagnostics for the forbidden fields are still produced, because
"unknown field `reward`" and "unknown field `rewrad`" deserve different help.

The adapter then constructs the `RunResult` itself, under a declared runner
profile that says what actually happened:

    traaviis.ors-submission.v1   filesystem: not_applicable
                                 network:    not_applicable
                                 termination: not_executed
                                 exit_code:   null

Nothing was executed here, so the sandbox posture is not weaker — it is an
inapplicable question, and `not_applicable` is the only honest answer. Claiming
`exited` + `0` would assert that a program ran and succeeded.

Identity
--------

An ORS episode and a local episode over the same task **honestly differ**, and
the ruling is explicit that they must not be forced to agree. The submission
trace carries its own version, `traaviis.submission-trace.v1`, so `trace-…`
differs by construction rather than by accident; the runner profile differs, so
`episode-…` differs too. Both are true statements about two different things
that happened. Forcing them to collide would require pretending a submission was
a process, which is the one thing this module exists not to do.

No rung is added to the identity ladder. An ORS session id is the kernel's own
`session-…` — randomness, not content — and the idempotency key is a *transport*
header that never enters a canonical byte string.

Durability
----------

`finished: true` is the strongest claim this adapter makes, so it is the last
thing it does. A `submit_candidate` call returns `finished: true` only after the
episode has been staged, **fully re-verified by replay**, fsynced, and atomically
renamed into place by `write_episode_bundle`. A client that reads
`finished: true` and then loses its connection can find the evidence on disk. A
staging or verification failure is reported as a failure, never as a finish.
"""

import hashlib
import os
import threading

from . import execfacts, identity, runner
from . import substrates as _substrates

__all__ = [
    "ORS_PROFILE_VERSION",
    "SUBMISSION_VERSION",
    "SUBMISSION_TRACE_VERSION",
    "ORS_RUNNER_PROFILE",
    "ORS_TOOL",
    "SUBMISSION_FIELDS",
    "CLIENT_FORBIDDEN_FIELDS",
    "OrsError",
    "validate_submission",
    "submission_trace",
    "trusted_run_result",
    "OrsSessionV1",
    "OrsAdapterV1",
    "open_adapter",
]

#: The profile this adapter implements. Named and versioned so a client can tell
#: what it is talking to without probing behaviour.
ORS_PROFILE_VERSION = "traaviis.ors-profile.v1"

#: The one document a client may send.
SUBMISSION_VERSION = "traaviis.remote-submission.v1"

#: The trace version for an episode whose candidate was *submitted* rather than
#: produced by a process this host launched. Distinct from
#: `runner.TRACE_VERSION` on purpose: `trace-…` should differ because the thing
#: it describes differs, not because a field happened to.
SUBMISSION_TRACE_VERSION = "traaviis.submission-trace.v1"

#: The declared adapter runner profile (see `execfacts`).
ORS_RUNNER_PROFILE = execfacts.ORS_RUNNER_PROFILE

#: The single tool. There is no second one, and there is no plan for a second
#: one under this substrate.
ORS_TOOL = "submit_candidate"

#: The exact key set of a `RemoteSubmissionV1`. Not a minimum — the exact set.
SUBMISSION_FIELDS = ("submission_version", "finding", "patch")

#: Fields a client is specifically forbidden to supply. This tuple is **not** the
#: validation rule (the exact key set above is); it exists so that a client that
#: sends one of these gets told *why* it is refused rather than a generic
#: "unknown field". Every one of them is something only the server can know.
CLIENT_FORBIDDEN_FIELDS = (
    "episode_id", "execution_facts", "exit_code", "policy_violations",
    "reward", "run_result", "status", "timed_out", "trace", "trace_id",
    "validity", "verification", "verifier_versions",
)

class OrsError(_substrates.AdmissionError):
    """A typed ORS refusal: `code` / `message` / `detail`, like every other."""


def _sha256_bytes(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_json(obj):
    return _sha256_bytes(identity.canonical_bytes(obj))


_EMPTY_DIGEST = _sha256_bytes(b"")


# --------------------------------------------------------------- submissions
def validate_submission(obj):
    """Validate a `RemoteSubmissionV1` by **exact key set**. Returns it normalized.

    Raises `OrsError` with a named code:

    * `ORS_SUBMISSION_MALFORMED`  — not a JSON object
    * `ORS_SUBMISSION_VERSION`    — wrong or missing `submission_version`
    * `ORS_SUBMISSION_FIELD`      — a key that is not one of the three, with the
      forbidden ones named as such
    * `ORS_SUBMISSION_TYPE`       — a key of the wrong type

    `patch` may be `null` (a finding-only submission is a real submission; the
    patch verifier scores its absence honestly). `finding` may be `null` for the
    same reason — an empty finding is scored, not rejected. What is *not*
    tolerated is a submission that carries a field the client may not own.
    """
    if not isinstance(obj, dict):
        raise OrsError("ORS_SUBMISSION_MALFORMED",
                       "a submission must be a JSON object, got %s"
                       % type(obj).__name__)
    version = obj.get("submission_version")
    if version != SUBMISSION_VERSION:
        raise OrsError(
            "ORS_SUBMISSION_VERSION",
            "submission_version must be %r, got %r" % (SUBMISSION_VERSION, version),
            {"expected": SUBMISSION_VERSION, "got": version})
    extra = sorted(set(obj) - set(SUBMISSION_FIELDS))
    if extra:
        forbidden = [k for k in extra if k in CLIENT_FORBIDDEN_FIELDS]
        if forbidden:
            raise OrsError(
                "ORS_SUBMISSION_FIELD",
                "a submission may not supply %s — the server determines %s from "
                "the episode it runs; a client that could set them could set its "
                "own reward" % (", ".join(forbidden),
                                "them" if len(forbidden) > 1 else "it"),
                {"forbidden": forbidden, "allowed": list(SUBMISSION_FIELDS)})
        raise OrsError(
            "ORS_SUBMISSION_FIELD",
            "unknown submission field(s) %s; a RemoteSubmissionV1 carries exactly %s"
            % (", ".join(extra), ", ".join(SUBMISSION_FIELDS)),
            {"unknown": extra, "allowed": list(SUBMISSION_FIELDS)})
    finding = obj.get("finding")
    if finding is not None and not isinstance(finding, dict):
        raise OrsError("ORS_SUBMISSION_TYPE",
                       "finding must be an object or null, got %s"
                       % type(finding).__name__,
                       {"field": "finding"})
    patch = obj.get("patch")
    if patch is not None and not isinstance(patch, str):
        raise OrsError("ORS_SUBMISSION_TYPE",
                       "patch must be a unified-diff string or null, got %s"
                       % type(patch).__name__,
                       {"field": "patch"})
    return {"submission_version": SUBMISSION_VERSION,
            "finding": finding, "patch": patch}


def submission_trace(submission):
    """The canonical `TraceV1` for a submitted candidate.

    Every digest here is over what actually exists. No command ran, so `command`
    is empty and `exit_code` is null; no file was written, so the four filesystem
    digests are over empty maps; no output was captured, so stdout/stderr digest
    the empty byte string. `result_file_digest` is over the canonical bytes of
    the finding the client sent — which is the one thing that *did* arrive, and
    the analogue of the result file a local agent would have written.

    The patch is not digested here. It binds into the receipt through
    `outputs.patch_id`, where a patch belongs; locally it only reaches the trace
    incidentally, via `files_created`.
    """
    finding = submission.get("finding")
    event = {
        "command": [],
        "cwd": ".",
        "environment_keys": [],
        "exit_code": None,
        "stdout_digest": _EMPTY_DIGEST,
        "stderr_digest": _EMPTY_DIGEST,
        "files_created_digest": _sha256_json({}),
        "files_modified_digest": _sha256_json({}),
        "files_deleted_digest": _sha256_json({}),
        "file_modes_changed_digest": _sha256_json({}),
        "result_file_digest": _sha256_bytes(
            identity.canonical_bytes(finding if finding is not None else {})),
        "policy_violations_digest": _sha256_json([]),
    }
    trace = {"trace_version": SUBMISSION_TRACE_VERSION, "events": [event]}
    trace["trace_id"] = identity.trace_id(trace)
    return trace


def trusted_run_result(submission):
    """Build the **server's** `RunResult` for a validated submission.

    The name is the point. This is not the client's account of a run; it is the
    server's record of what it received, in the shape the kernel already knows
    how to score. Every field is either derived from the submission or is a
    constant stating that nothing executed:

    ==========================  ==========================================
    `exit_code`                 `None` — no process, so no status to report
    `timed_out`                 `False` — nothing ran, so nothing timed out
    `output_truncated`          `False` — nothing was captured to truncate
    `policy_violations`         `[]` — no filesystem to escape from
    `files_*` / `workspace_after`  empty — the submission wrote nothing here
    ==========================  ==========================================

    `timed_out=False` with `exit_code=None` would be contradictory under the
    local profile (a null exit code *means* a timeout there). It is not
    contradictory here, because `execfacts` reads this pair through the
    non-executing branch and seals `termination: "not_executed"` instead of
    inferring a timeout — which is exactly why that branch exists.
    """
    finding = submission.get("finding")
    return runner.RunResult(
        exit_code=None,
        timed_out=False,
        output_truncated=False,
        stdout_truncated=False,
        stderr_truncated=False,
        stdout=b"",
        stderr=b"",
        result={"finding": finding} if finding is not None else {},
        patch_text=submission.get("patch"),
        files_created={},
        files_modified={},
        files_deleted={},
        file_modes_changed={},
        policy_violations=[],
        workspace_after={},
        trace=submission_trace(submission),
    )


# ------------------------------------------------------------------ sessions
class OrsSessionV1(object):
    """One remote session: a kernel session plus the transport's idempotency memo.

    The session id **is** the kernel's `session-…`. Minting a second id here
    would create a handle that maps to a handle, and the only thing it could add
    is a way for the two to disagree.

    `finished` and `output` together are what makes the idempotency rule
    decidable: a repeat of the *same* key replays the cached ToolOutput
    verbatim, and a *different* key after a finish is `ORS_SESSION_FINISHED`
    rather than a second episode wearing the first session's name.
    """

    __slots__ = ("session_id", "task_id", "idempotency_key", "output",
                 "finished")

    def __init__(self, session_id, task_id):
        self.session_id = session_id
        self.task_id = task_id
        self.idempotency_key = None
        self.output = None
        self.finished = False

    def describe(self):
        return {"session_id": self.session_id, "task_id": self.task_id,
                "finished": self.finished, "tool": ORS_TOOL}


class OrsAdapterV1(object):
    """The transport-independent ORS adapter over one admitted environment.

    Everything here is pure Python and returns plain dicts; the HTTP server in
    `ors_server` is a thin encoding of it. That split is not tidiness — it is
    what lets the battery drive the *whole* protocol without a socket, so O1–O30
    test the adapter's semantics rather than a client library's behaviour.

    One package, one kernel, one registry, one output root, per process.
    """

    def __init__(self, kernel, *, env_id, output, manifest=None,
                 substrate_profile=None):
        self._kernel = kernel
        self._env_id = env_id
        self._output = output
        self._manifest = manifest or {}
        self._substrate_profile = (substrate_profile
                                   or getattr(kernel, "substrate_profile", None))
        self._sessions = {}
        # Guards the ORS session table only. Never held across `finalize` or
        # across `write_episode_bundle` — the kernel already proved that a lock
        # spanning the scoring work serializes the server down to one episode at
        # a time, and this adapter must not reintroduce it one layer up.
        self._lock = threading.Lock()

    # --- description ------------------------------------------------------
    def describe(self):
        """What this server is. Advertises exactly one tool.

        The kernel's full operation map is reported too, unmodified, so a client
        can see that `observe` / `step` / `reset` are **false** rather than
        discovering it by calling them. A capability list that only ever lists
        capabilities cannot express a limit.
        """
        kernel_desc = self._kernel.describe()
        return {
            "ors_profile_version": ORS_PROFILE_VERSION,
            "kernel_version": kernel_desc.get("kernel_version"),
            "substrate_profile": self._substrate_profile,
            "env_id": self._env_id,
            "runner_profile": ORS_RUNNER_PROFILE,
            "submission_version": SUBMISSION_VERSION,
            "tools": [{
                "name": ORS_TOOL,
                "description": "submit one candidate finding + patch for a task "
                               "and receive its scored receipt",
                "input_schema": {
                    "type": "object",
                    "required": list(SUBMISSION_FIELDS),
                    "additionalProperties": False,
                    "properties": {
                        "submission_version": {"const": SUBMISSION_VERSION},
                        "finding": {"type": ["object", "null"]},
                        "patch": {"type": ["string", "null"]},
                    },
                },
            }],
            "operations": kernel_desc.get("operations", {}),
            "interactive": False,
            "tasks": self.list_tasks(),
        }

    def list_tasks(self):
        """The task ids, canonically ordered by `task_id`.

        Sorted, not manifest order. A remote client that pages this list must be
        able to page it again and get the same page, and the manifest's order is
        an artifact of how the package was written.
        """
        return sorted(self._kernel.list_tasks())

    def task(self, task_id):
        """The public description of one task, including its prompt.

        Contains only *static* facts: what the task declares. No session state,
        no open-session count, no reward so far — nothing a second call could
        find changed underneath it.
        """
        entry = self._kernel.entry(task_id)
        task = entry.task
        plan = task.get("verifier_plan") or {}
        return {
            "task_id": task_id,
            "env_id": self._env_id,
            "substrate_profile": task.get("substrate_profile"),
            "termination": dict(task.get("termination") or {}),
            "required_signals": sorted(plan.get("required") or []),
            "prompt": self.task_prompt(task_id),
            "submission_schema": {
                "submission_version": SUBMISSION_VERSION,
                "finding": {"summary": "<string>", "citations": ["<string>"]},
                "patch": "<unified diff string, or null>",
            },
        }

    def task_prompt(self, task_id):
        """The instruction text handed to a remote agent.

        Ruled content only: the instructions, the task id, the environment id,
        the required output schema, and the declared termination behaviour.
        Deliberately **no** mutable kernel state — no session count, no previous
        rewards, no "attempts remaining". A prompt that varies with server state
        is not reproducible, and two agents given "the same task" would not have
        been given the same task.
        """
        entry = self._kernel.entry(task_id)
        task = entry.task
        instructions = task.get("instructions") or {}
        objective = instructions.get("objective") or ""
        termination = (task.get("termination") or {}).get("mode") or "unknown"
        plan = task.get("verifier_plan") or {}
        required = sorted(plan.get("required") or [])
        lines = [
            "Task %s" % task_id,
            "Environment %s" % self._env_id,
            "",
            "Objective: %s" % objective,
            "",
            "Termination: %s. You get one submission for this session; there is "
            "no observation step and no retry." % termination,
            "",
            "Required signals: %s" % (", ".join(required) or "none"),
            "",
            "Submit exactly this document to the %s tool:" % ORS_TOOL,
            '  {"submission_version": "%s",' % SUBMISSION_VERSION,
            '   "finding": {"summary": "<your finding>", "citations": ["<path>"]},',
            '   "patch": "<unified diff over the subject tree, or null>"}',
            "",
            "The server scores the submission and returns the reward. Do not "
            "send a reward, a status, verifier outcomes, execution facts or any "
            "identifier — those are determined here, and a submission carrying "
            "one is refused.",
        ]
        return "\n".join(lines)

    # --- session lifecycle -------------------------------------------------
    def open_session(self, task_id):
        """Open a session for `task_id`. Returns the session description.

        The kernel session is opened *now*, not lazily at submission time, so
        the full per-task admission — declared ids recomputed, content bound to
        the snapshot, run policy checked, F4 config preflight — runs before the
        client is told it has a session. A client holding a session id has
        already been told something true about the task.
        """
        session_id = self._kernel.start(task_id)
        session = OrsSessionV1(session_id, task_id)
        with self._lock:
            self._sessions[session_id] = session
        return session.describe()

    def close_session(self, session_id):
        """Release a session. Idempotent, like the kernel's own `close`."""
        self._kernel.close(session_id)
        with self._lock:
            self._sessions.pop(session_id, None)
        return {"session_id": session_id, "closed": True}

    def _session(self, session_id):
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise OrsError("ORS_SESSION_UNKNOWN",
                           "no open session %r" % session_id,
                           {"session_id": session_id})
        return session

    # --- the one tool ------------------------------------------------------
    def call_tool(self, session_id, tool, arguments, idempotency_key=None):
        """Run `submit_candidate` for `session_id`. The whole protocol.

        The order is the guarantee:

        1. resolve the session, refuse an unknown tool by name
        2. apply the idempotency rule **before** doing any work
        3. validate the submission by exact key set
        4. build the server's trusted `RunResult`
        5. `kernel.finalize` — scored exactly once, linearizably
        6. `write_episode_bundle` — stage, replay-verify, fsync, atomic rename
        7. only then assemble a ToolOutput with `finished: true` and cache it

        Steps 5 and 6 are where `finished` is earned. Returning `finished: true`
        after step 5 would be the ordinary shape of this kind of server and
        would be a lie the moment the process died before step 6: the client
        would hold a reward whose evidence never reached disk.
        """
        if tool != ORS_TOOL:
            raise OrsError(
                "ORS_TOOL_UNKNOWN",
                "this server exposes exactly one tool, %r; %r is not it"
                % (ORS_TOOL, tool),
                {"tool": tool, "tools": [ORS_TOOL]})
        session = self._session(session_id)

        cached = self._check_idempotency(session, idempotency_key)
        if cached is not None:
            return cached

        submission = validate_submission(arguments)
        entry = self._kernel.entry(session.task_id)
        kernel_session = self._kernel.session(session_id)

        # A session the F4 config preflight ruled un-runnable is finalized with
        # `None`: its receipt is already determined and the kernel refuses a run
        # result for it. That path is reachable remotely and is a *scored*
        # outcome (`status: invalid`), so it returns a ToolOutput like any other
        # rather than an error — the configuration is what is invalid, not the
        # client's call.
        run_result = (trusted_run_result(submission)
                      if kernel_session.runnable else None)
        # The kernel's own typed refusals (a concurrent second finalize is
        # `KERNEL_SESSION_STATE`) propagate unwrapped: a remote client reads the
        # same vocabulary the local CLI prints, and rewrapping them here would
        # create a second name for one fact.
        evaluation_run = self._kernel.finalize(session_id, run_result)
        receipt = evaluation_run["receipt"]

        member = self._publish(evaluation_run, entry)
        output = self._tool_output(receipt, session.task_id, member)
        with self._lock:
            session.output = output
            session.finished = True
        return output

    def _check_idempotency(self, session, key):
        """Apply the transport idempotency rule. Returns a cached output or None.

        Three cases, and the middle one is the reason this is a *transport*
        concern rather than a payload field:

        * same key, already finished  → replay the cached ToolOutput verbatim.
          A client whose connection dropped retries with its key and gets the
          same answer, not a second episode.
        * different key, already finished → `ORS_SESSION_FINISHED`. A session
          has one episode in it. A new key means a new intent, and a new intent
          needs a new session.
        * different key, in flight → `ORS_SESSION_BUSY`, which is the honest
          answer while another call holds the kernel's finalize claim.
        """
        with self._lock:
            if session.finished:
                if key is not None and key == session.idempotency_key:
                    return session.output
                raise OrsError(
                    "ORS_SESSION_FINISHED",
                    "session %r has already submitted its candidate; a session "
                    "carries exactly one episode — open a new session to submit "
                    "again" % session.session_id,
                    {"session_id": session.session_id,
                     "task_id": session.task_id})
            if session.idempotency_key is not None \
                    and key != session.idempotency_key:
                raise OrsError(
                    "ORS_SESSION_BUSY",
                    "session %r is already submitting under a different "
                    "idempotency key" % session.session_id,
                    {"session_id": session.session_id})
            session.idempotency_key = key
        return None

    def _publish(self, evaluation_run, entry):
        """Stage → replay-verify → fsync → atomically publish. Returns the member.

        `write_episode_bundle` does all four; this method exists to turn its
        failures into typed ORS refusals and to keep the *absolute path* out of
        the response. A client is told the member name — `episode-…` — which is
        content-addressed and portable. The server's directory layout is the
        server's business, and an absolute path in a response is both a leak and
        a thing a client will eventually try to open.
        """
        from . import episode_bundle
        if evaluation_run.get("artifacts") is None:
            raise OrsError(
                "ORS_NO_EVIDENCE",
                "the episode produced no artifacts to persist, so there is "
                "nothing to publish and no finished result to report",
                {"task_id": entry.task_id})
        try:
            path = episode_bundle.write_episode_bundle(
                evaluation_run, task=entry.task, reward_spec=entry.reward_spec,
                snapshot=entry.snapshot, content=entry.content,
                dest_root=self._output,
                extra_verifiers=entry.extra_verifiers)
        except (episode_bundle.EpisodeBundleError, OSError) as exc:
            raise OrsError(
                "ORS_PUBLISH_FAILED",
                "the episode scored but its evidence could not be published, so "
                "it is not finished: %s: %s" % (type(exc).__name__, exc),
                {"task_id": entry.task_id,
                 "error": "%s: %s" % (type(exc).__name__, exc)})
        return os.path.basename(path)

    def _tool_output(self, receipt, task_id, member):
        """The ruled ToolOutput. No absolute paths, no unowned identities."""
        return {
            "blocks": [{
                "type": "text",
                "text": "episode %s — status %s, validity %s, reward %s"
                        % (receipt["episode_id"], receipt["status"],
                           receipt["validity"],
                           "null" if receipt["reward"] is None
                           else receipt["reward"]),
            }],
            "reward": receipt["reward"],
            "finished": True,
            "metadata": {
                "task_id": task_id,
                "episode_id": receipt["episode_id"],
                "status": receipt["status"],
                "validity": receipt["validity"],
                "evidence_member": member,
            },
        }

    # --- the three refusals ------------------------------------------------
    def unsupported(self, operation):
        """Relay the kernel's own `KERNEL_OPERATION_UNSUPPORTED`.

        `observe` / `step` / `reset` are routes on the transport that exist
        precisely so they can refuse *in the substrate's words*. A 404 would
        report a missing endpoint, which is false: the endpoint is here, and the
        substrate has no such operation.
        """
        return self._kernel._unsupported(operation)


# ------------------------------------------------------------------- startup
def open_adapter(package, split, output, *, engine=None, registry=None,
                 platform="unknown", toolchain=None, on_wiring_notes=None):
    """Perform the **complete** startup admission and return an `OrsAdapterV1`.

    This is the ruled order, and the whole point of it is that every step
    happens **before** a socket is bound:

    1. open the package through the §5a admission interface — every `task-`,
       every `rew-`, the subject identity and `env-…` re-derived from the bytes
       on disk
    2. resolve the named split; it must exist, be non-empty, and every member
       must resolve to a task in the closure
    3. bind the on-disk subject tree to its snapshot, once, for every task
    4. build one verifier registry
    5. build one `EpisodeKernelV1` over the whole admitted environment
    6. verify the output staging root is usable
    7. — and only after all of that may the caller bind and listen

    A server that binds first and admits on the first request advertises itself
    as ready while it is still capable of discovering that its package is
    unopenable. A client that connects to it has been told something false. The
    failure modes here are all knowable without a client, so they are all
    discovered without one, and the process exits non-zero having never claimed
    to be serving.

    Raises `SplitError` / `AdmissionError` / `KernelError` / `OrsError`.
    """
    from . import admission, evalsplit
    from . import kernel as _kernel

    manifest, artifacts = evalsplit.open_environment(package, engine=engine)
    task_ids = evalsplit.resolve_split(manifest, split)

    package = os.path.abspath(package)
    subject = manifest["subject"]
    snapshot = evalsplit._read_member(package, subject["snapshot"], "snapshot")
    subject_dir = os.path.join(package, subject["root"].replace("/", os.sep))
    content = admission.verify_subject_tree(snapshot, subject_dir)

    if registry is None:
        from . import wiring
        registry = wiring.default_registry(engine)
    extra_verifiers_for = evalsplit._verifiers_from(registry, on_wiring_notes)

    kernel = _kernel.environment_kernel(
        manifest, artifacts, snapshot, content,
        extra_verifiers_for=extra_verifiers_for,
        platform=platform, toolchain=toolchain,
        # The one substantive difference from `eval`: no process will be
        # launched, so the episodes this kernel scores are sealed under the
        # non-executing adapter profile. This is the single line that makes an
        # ORS episode honestly distinguishable from a local one.
        runner_profile=ORS_RUNNER_PROFILE)

    # A split is a *subset* of the package's tasks, and a server that admitted a
    # split but then served every task in the package would be serving tasks
    # nobody asked it to serve. Restricting the catalog here — rather than
    # filtering on the way out — means an unlisted task is `KERNEL_TASK_UNKNOWN`
    # at the kernel, not a check some later endpoint might forget.
    served = set(task_ids)
    kernel._entries = {k: v for k, v in kernel._entries.items() if k in served}

    _verify_output_root(output)

    return OrsAdapterV1(kernel, env_id=manifest["env_id"], output=output,
                        manifest=manifest,
                        substrate_profile=manifest.get("substrate_profile"))


def _verify_output_root(output):
    """Prove the episode output root is writable **before** the server binds.

    `EPISODE_OUTPUT` is mandatory for `--ors` because `finished: true` is a
    durability claim, and a durability claim over an unwritable directory is the
    exact failure this check exists to make impossible. Discovering it on the
    first submission would mean scoring a real episode — running a candidate's
    verifiers — and then having nowhere to put the proof.
    """
    if not output:
        raise OrsError(
            "ORS_OUTPUT_REQUIRED",
            "--ors requires an episode output directory: `finished: true` is a "
            "claim that the evidence is on disk, and it cannot be made without "
            "somewhere to put it")
    try:
        os.makedirs(output, exist_ok=True)
        probe = os.path.join(output, ".trvs-ors-probe")
        with open(probe, "wb") as fh:
            fh.write(b"")
        os.unlink(probe)
    except OSError as exc:
        raise OrsError(
            "ORS_OUTPUT_UNWRITABLE",
            "the episode output directory is not writable, so no submission "
            "could ever be published: %s" % exc,
            {"error": "%s: %s" % (type(exc).__name__, exc)})
    return os.path.abspath(output)
