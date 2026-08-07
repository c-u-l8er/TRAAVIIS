"""Substrate verifiers: ``tests`` (controlled test-command run) and ``identity``
(Forge re-lower). Both take a ``VerifierContextV1`` and return a ``VerifierResult``.

Per the GPT-5.6 closure ruling these are the two verifiers that touch substrate,
so they live apart from the pure ``traaviis.verifiers``.

**tests** (``TestPlanV1`` / ``TestPlanV2`` in ``task.test_plan``): run the declared
commands on two fresh materializations of the sealed subject — a clean baseline copy
and a clean copy with the candidate patch applied — under a separate
``VerifierRunPolicyV1``.

  baseline meets expectation & patched meets it       → pass
  baseline meets expectation & patched does not       → fail (candidate)
  baseline does not meet its expectation              → error (fixture inadmissible)
  toolchain resolution / timeout / runner failure     → error
  no patched tree (patch absent or did not apply)     → fail
  test plan absent                                    → not_applicable

The asymmetry is deliberate: the baseline judges the *fixture* and the patched run
judges the *candidate*, so a baseline that misbehaves is never a verdict against an
agent that has not been consulted yet.

A ``TestPlanV1`` command carries a raw ``argv`` (whose absolute interpreter token
made ``task-…`` host-specific) and must exit 0 in both phases. A ``TestPlanV2``
command differs in two ways.

*Tool resolution.* It carries a **logical** ``tool`` reference + ``args`` under a
``toolchain_profile``: the logical reference is all that enters task identity (so
``task-…`` is host-independent), and the resolver (``traaviis.toolchain``) maps the
tool to the concrete host executable at run time, recording its version + binary
digest in ``execution_facts.toolchain`` — attested, not identity-bearing.

*Per-phase expectations.* It may declare ``baseline`` / ``patched``
``allowed_exit_codes``, defaulting to ``[0]``::

    {"tool": "python", "args": ["-m", "pytest", "test_bug.py"],
     "baseline": {"allowed_exit_codes": [1]},
     "patched":  {"allowed_exit_codes": [0]}}

This is what makes a *bug reproduction* expressible. Under the V1 rule a command
that failed on the sealed subject made the fixture inadmissible, so a task could
not require a test that fails before the fix and passes after — the single most
common shape of a real repair task. Declaring the expectation per phase says
plainly what each phase is for, and a plan that declares nothing keeps the V1
meaning exactly.

The ``tests ≤ 0.40`` reward cap keys off ``fail`` only; ``error`` never scores.

**identity** (``task.identity_policy.must_remain``): re-lower each explicitly-bound
source in the patched tree through a ``ForgeIdentityAdapterV1`` and compare to the
sealed ``before_id``.

  every bound source lowers equal to its before_id → pass
  any successfully-lowered source moved            → fail
  source missing / lowering error / Forge down     → error  (a WRL compile error is
                                                     error, not fail — the identity
                                                     comparison could not complete)
  lowering outran its deadline, worker killed      → error  (``FORGE_LOWER_TIMEOUT``)
  no identity bindings                             → not_applicable

The lowering runs in a killable worker process, not in the evaluating one — see
``VERIFIER_ISOLATION_POLICY`` below and ``forge_adapter._lower_in_worker``. The
last row is what that buys: a candidate-authored WRL source that hangs Forge used
to hang the whole evaluation, which erased the episode without raising anything
for ``evalone.resolve_signal`` to classify. It is now an ``error`` like any other
substrate unavailability, sealed under a stable code.
"""

import hashlib
import tempfile
from typing import Any, List, Mapping, Optional, Tuple

from . import execlimits, identity, reward, toolchain
from .execfacts import UnsupportedPolicyError, validate_run_policy
from .forge_adapter import (
    ERROR_CODE_FORGE_SOURCE_TOO_LARGE, ERROR_CODE_FORGE_TIMEOUT,
    ForgeIdentityAdapterV1, ForgeSourceTooLarge, ForgeTimeout, ForgeUnavailable,
)
from .paths import PathError, safe_join, safe_relposix
from .runner import RUNNER_PROFILE, _materialize, _normalize_command, _seal_env
from .vcontext import VerifierContextV1, VerifierResult

__all__ = [
    "VERIFIER_ISOLATION_POLICY",
    "TESTS_VERIFIER_VERSION",
    "TESTS_IMPL_VERSION",
    "TEST_PLAN_V2",
    "test_plan_tools",
    "tests_verifier",
    "make_identity_verifier",
    "run_command_set",
    "command_set_passed",
    "BASELINE",
    "PATCHED",
]

# --- Which verifiers get a process boundary, and which are trusted without one -
#
# `evalone.resolve_signal` contains a verifier that **stops** — a raise, or a
# return the result contract refuses, becomes `error` and the episode survives.
# It cannot contain a verifier that **never stops**. An infinite loop, a native
# deadlock, an `os._exit`, a segfault, or an allocation the host cannot satisfy
# reaches no `except` clause: the evaluating process does not come back, nothing
# is persisted, and a failing grade is erased exactly as thoroughly as an
# uncaught exception erased it. Containment against *not returning* is not a
# handler; it is a process you are willing to kill.
#
# So the question "is this verifier trusted in-process?" has to have a stated
# answer for every verifier, including ones this repository did not write. It
# does, below, and the map is the answer — not a comment that can drift from it.
# Four rows:
#
#   builtin_pure   `traaviis.verifiers` — citations, patch, finding_completeness.
#                  Trusted in-process. They are this repository's own code,
#                  they walk already-bounded structures, and they call nothing.
#                  Their inputs are candidate-controlled, which is why the
#                  *exception* seam covers them; their control flow is not.
#   tests          Bounded process group. This row used to read
#                  `subprocess_timeout`, and the ruling that opened 9D was right
#                  that it did not say enough: `subprocess.run(timeout=…)` is a
#                  *clock* deadline and nothing else. It kills the direct child,
#                  so a grandchild that inherited stdout survives it and keeps
#                  the collection waiting; and it buffers all output and applies
#                  `max_output_bytes` afterwards, so a command emitting
#                  indefinitely exhausts the host before any bound is reached.
#                  A deadline that can be outlived by a grandchild and a cap
#                  that arrives after the memory is gone are not containment.
#                  Every command now runs through `execlimits.run_bounded`:
#                  session leader, streaming caps, group kill on deadline or
#                  overflow, bounded reap.
#   identity       Bounded worker group. It used to call
#                  `adapter.lower_source(patched[rel])` in this process, on a WRL
#                  source the candidate wrote, with no timeout anywhere on the
#                  path. That asymmetry against `tests` was the hole; see
#                  `forge_adapter._lower_in_worker`. The worker's request and
#                  response are now bounded too — an internally generated
#                  envelope around a candidate-authored payload is not a trusted
#                  internal parse.
#   external       **Required.** A verifier this repository did not write, reaching
#                  `evalone` through `extra_verifiers` or a future plugin seam, is
#                  NOT trusted in-process and must provide its own killable
#                  boundary. This is a *precondition on the caller*, stated here
#                  rather than left open, because `extra_verifiers` is a plain
#                  mapping of callables and nothing in this package can enforce it
#                  — `resolve_signal` cannot time out a call it is inside of. A
#                  caller who ignores this row gets exactly the failure mode
#                  routes 5 and 6 were opened to close, and now knows it.
#
# The batteries' own injected doubles are `external` by this rule and are trusted
# anyway, which is not an exception to it: a battery is not an adversary, and it
# is the same reason `StubForgeAdapter` may lower in-process.
VERIFIER_ISOLATION_POLICY = {
    "builtin_pure": "in_process_trusted",
    "tests": "bounded_process_group",
    "identity": "bounded_worker_group",
    "external": "worker_process_required",
}

# Contract version — the signal the reward asks for (sealed as ``contract``).
TESTS_VERIFIER_VERSION = "residency.tests.v1"
# Implementation version — the code that answered (sealed as ``implementation``
# via ``.version``). Named independently of the contract.
TESTS_IMPL_VERSION = "traaviis.tests-impl.v1"

# The ``TestPlanV2`` discriminator. A plan whose ``test_plan_version`` equals this
# uses logical ``tool`` + ``args`` commands under a ``toolchain_profile``; any other
# value (including absent) is the legacy ``TestPlanV1`` (raw ``argv``) shape.
TEST_PLAN_V2 = "traaviis.test-plan.v2"

# _run outcomes for one command set over one materialization.
_ALL_PASS = "all_pass"
_SOME_FAIL = "some_fail"
_INFRA_ERROR = "infra_error"


def command_set_passed(state) -> bool:
    """Did every command in the set exit as its phase expected?

    The public predicate over `run_command_set`'s first return value. Callers
    asking "did this pass?" should use this rather than comparing against the
    underscored state constants, which are an implementation detail: an infra
    error and a genuine failure are both "not passed" here, and callers that
    need to tell them apart should read the per-command records instead.
    """
    return state == _ALL_PASS


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _is_v2(plan: Mapping[str, Any]) -> bool:
    return plan.get("test_plan_version") == TEST_PLAN_V2


def _command_id(cmd: Mapping[str, Any]) -> str:
    """A host-independent id for one test command.

    A ``TestPlanV2`` command is identified by its logical ``{tool, args, cwd}`` — no
    host path is ever involved, so the id is machine-independent by construction. A
    legacy ``TestPlanV1`` command is identified by its normalized ``argv`` (absolute
    tokens reduced to basename, as in the canonical trace) + ``cwd``, so it too
    identifies *which* command ran without leaking the interpreter's host path.
    """
    if "tool" in cmd:
        canon = identity.canonical_bytes({
            "tool": cmd["tool"],
            "args": list(cmd.get("args", [])),
            "cwd": cmd.get("cwd", "."),
        })
    else:
        canon = identity.canonical_bytes({
            "argv": _normalize_command(cmd["argv"]),
            "cwd": cmd.get("cwd", "."),
        })
    return "cmd-" + hashlib.sha256(canon).hexdigest()


#: The phases a command is run in, and the expectation key each reads.
BASELINE, PATCHED = "baseline", "patched"
#: A command that declares no expectation for a phase must exit 0 in it -- the
#: TestPlanV1 rule, kept as the default so an undeclared plan means what it did.
_DEFAULT_EXPECTED = (0,)


def _validate_expectation(cmd: Mapping[str, Any], phase: str) -> None:
    spec = cmd.get(phase)
    if spec is None:
        return
    if not isinstance(spec, Mapping):
        raise ValueError("command %s expectation must be an object" % phase)
    codes = spec.get("allowed_exit_codes")
    if codes is None:
        return
    if not isinstance(codes, list) or not codes:
        raise ValueError("%s.allowed_exit_codes must be a non-empty list" % phase)
    for code in codes:
        # bool is an int subclass; `true` is not an exit code.
        if isinstance(code, bool) or not isinstance(code, int):
            raise ValueError("%s.allowed_exit_codes must contain integers" % phase)


def _expected_exit_codes(cmd: Mapping[str, Any], phase: str) -> Tuple[int, ...]:
    """The exit codes `cmd` is permitted to return in `phase`."""
    spec = cmd.get(phase)
    if isinstance(spec, Mapping) and isinstance(spec.get("allowed_exit_codes"), list):
        return tuple(spec["allowed_exit_codes"])
    return _DEFAULT_EXPECTED


def _validate_command_v2(cmd: Mapping[str, Any]) -> None:
    tool = cmd.get("tool")
    if not isinstance(tool, str) or not tool:
        raise ValueError("command tool must be a non-empty string")
    args = cmd.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("command args must be a string list")
    _validate_expectation(cmd, BASELINE)
    _validate_expectation(cmd, PATCHED)


def _validate_command_v1(cmd: Mapping[str, Any]) -> None:
    argv = cmd.get("argv")
    if not isinstance(argv, list) or not argv \
            or not all(isinstance(a, str) for a in argv):
        raise ValueError("command argv must be a non-empty string list")


def _validate_test_plan(plan: Mapping[str, Any]) -> list:
    commands = plan.get("commands")
    if not isinstance(commands, list) or not commands:
        raise ValueError("test_plan.commands must be a non-empty list")
    v2 = _is_v2(plan)
    if v2 and not isinstance(plan.get("toolchain_profile"), str):
        raise ValueError("TestPlanV2 requires a toolchain_profile string")
    for cmd in commands:
        if not isinstance(cmd, Mapping):
            raise ValueError("each command must be an object")
        if v2:
            _validate_command_v2(cmd)
        else:
            _validate_command_v1(cmd)
        safe_relposix(cmd.get("cwd", "."), allow_empty=True)  # raises on unsafe cwd
    return commands


def test_plan_tools(plan: Mapping[str, Any]) -> List[str]:
    """The sorted set of logical tool names a ``TestPlanV2`` references (else ``[]``).

    The one seam both the ``tests`` verifier and ``evalone`` use to learn which
    tools a plan needs resolved — the verifier to build argv, ``evalone`` to seal the
    resolved toolchain into ``execution_facts`` — so the two resolve exactly the same
    set. A legacy ``TestPlanV1`` references no logical tools.
    """
    if not isinstance(plan, Mapping) or not _is_v2(plan):
        return []
    tools = {c["tool"] for c in plan.get("commands", [])
             if isinstance(c, Mapping) and isinstance(c.get("tool"), str)}
    return sorted(tools)


def _command_argv(cmd: Mapping[str, Any], executables: Mapping[str, str]) -> List[str]:
    """The concrete argv for one command — V2 resolves the logical tool, V1 is raw."""
    if "tool" in cmd:
        return [executables[cmd["tool"]]] + list(cmd.get("args", []))
    return list(cmd["argv"])


def run_command_set(
    plan: Mapping[str, Any], content: Mapping[str, str],
    *, executables: Optional[Mapping[str, str]] = None,
    phase: str = PATCHED,
) -> Tuple[str, List[dict]]:
    """Materialize ``content`` and run every command in order under the plan policy.

    Returns ``(state, records)`` where ``state`` is ``_ALL_PASS`` (every command
    exited as ``phase`` expects), ``_SOME_FAIL`` (some command did not), or
    ``_INFRA_ERROR`` (timeout / spawn failure), and ``records`` is one canonical
    evidence entry per command reached —
    ``{command_id, exit_code, expected_exit_codes, stdout_digest, stderr_digest}``
    (an infra failure adds a final entry with ``exit_code`` null and an ``error``
    tag). ``shell`` is always false. The digests let ``verify-episode`` re-attest
    the run without re-reading the (possibly large) captured output bytes.

    ``phase`` selects which expectation each command is judged against — a
    ``TestPlanV2`` command may declare ``baseline``/``patched``
    ``allowed_exit_codes``; anything undeclared (and every ``TestPlanV1`` command)
    must exit 0. Recording ``expected_exit_codes`` next to the observed one makes
    each entry self-describing: the evidence states the rule it was judged by, so
    a reader need not re-derive the plan to see why a run passed.

    For a ``TestPlanV2`` plan ``executables`` maps each logical tool to its
    resolved host path (built by the caller via ``traaviis.toolchain``); a
    ``TestPlanV1`` plan ignores it.
    """
    commands = _validate_test_plan(plan)
    executables = dict(executables or {})
    run_policy = plan.get("run_policy", {})
    sealed_env = _seal_env(run_policy)
    # A TestPlanV1 command has no phase expectations at all; judging it by the V2
    # default keeps the legacy rule (exit 0) exactly.
    v2 = _is_v2(plan)

    root = tempfile.mkdtemp(prefix="traaviis-test-")
    records: List[dict] = []
    try:
        _materialize(content, root)
        all_pass = True
        for cmd in commands:
            cwd_rel = cmd.get("cwd", ".")
            cid = _command_id(cmd)
            expected = _expected_exit_codes(cmd, phase) if v2 else _DEFAULT_EXPECTED
            try:
                cwd = safe_join(root, cwd_rel) if cwd_rel not in (".", "") else root
            except PathError:
                records.append({"command_id": cid, "exit_code": None,
                                "expected_exit_codes": list(expected),
                                "stdout_digest": None, "stderr_digest": None,
                                "error": "unsafe_cwd"})
                return _INFRA_ERROR, records
            timeout = cmd.get("timeout_seconds")
            # `execlimits.run_bounded`, not `subprocess.run`. The row above
            # claiming this verifier is `subprocess_timeout`-contained was true
            # about the *clock* and false about the *process tree* and the
            # *memory*: `subprocess.run` buffers all output and applies a cap
            # afterwards, so a command emitting indefinitely exhausts the host
            # before any bound is reached, and its `timeout=` kills the direct
            # child only, so a grandchild holding stdout keeps the collection
            # waiting after the deadline that was supposed to end it. A
            # deadline is not a bounded process-tree-and-evidence boundary, and
            # the row now describes one.
            proc = execlimits.run_bounded(
                _command_argv(cmd, executables),
                cwd=cwd,
                env=sealed_env,
                timeout=timeout,
                max_stdout_bytes=execlimits.MAX_STDOUT_BYTES,
                max_stderr_bytes=execlimits.MAX_STDERR_BYTES,
            )
            if proc["spawn_error"] is not None:
                records.append({"command_id": cid, "exit_code": None,
                                "expected_exit_codes": list(expected),
                                "stdout_digest": None, "stderr_digest": None,
                                "error": proc["spawn_error"]})
                return _INFRA_ERROR, records
            if proc["timed_out"]:
                records.append({"command_id": cid, "exit_code": None,
                                "expected_exit_codes": list(expected),
                                "stdout_digest": None, "stderr_digest": None,
                                "error": "TimeoutExpired"})
                return _INFRA_ERROR, records
            record = {
                "command_id": cid,
                "exit_code": proc["exit_code"],
                "expected_exit_codes": list(expected),
                "stdout_digest": _sha256_bytes(proc["stdout"]),
                "stderr_digest": _sha256_bytes(proc["stderr"]),
            }
            if (proc["stdout_truncated"] or proc["stderr_truncated"]) \
                    and phase == BASELINE:
                # **Baseline overflow is an inadmissible fixture, not a verdict**
                # (9E attribution). The baseline judges the *fixture*: a command
                # that floods its output before the candidate has touched
                # anything says the sealed subject is not the world the task
                # describes. Marking the candidate down for it would score
                # somebody for a task nobody could have passed.
                records.append({"command_id": cid, "exit_code": None,
                                "expected_exit_codes": list(expected),
                                "stdout_digest": None, "stderr_digest": None,
                                "error": "OutputOverflow",
                                "execution_limits_version":
                                    execlimits.EXECUTION_LIMITS_VERSION})
                return _INFRA_ERROR, records
            if proc["stdout_truncated"] or proc["stderr_truncated"]:
                # Conditional, for the same reason `runner` makes the trace's
                # `execution_limits_version` conditional: a command that stayed
                # inside the profile records byte-identical evidence and its
                # `episode-…` does not move. A command that went past it is
                # recording a digest over *truncated* bytes, and evidence that
                # does not say so is a false record — the digest would look like
                # a complete capture forever after.
                record["stdout_truncated"] = bool(proc["stdout_truncated"])
                record["stderr_truncated"] = bool(proc["stderr_truncated"])
                record["execution_limits_version"] = \
                    execlimits.EXECUTION_LIMITS_VERSION
            records.append(record)
            if proc["exit_code"] not in expected:
                # Includes the patched-phase overflow: `run_bounded` reports no
                # exit code for a run that emitted more than the profile keeps,
                # and `None` is not in any `allowed_exit_codes`, so the command
                # did not behave as the plan declares. That is a `tests` **fail**
                # (9E attribution) -- the candidate's own command, its own bytes,
                # a declared bound -- and not a substrate error.
                all_pass = False
        return (_ALL_PASS if all_pass else _SOME_FAIL), records
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def tests_verifier(context: VerifierContextV1) -> VerifierResult:
    """The ``tests`` verifier over a ``VerifierContextV1`` (state mapping above)."""
    plan = context.task.get("test_plan")
    if not isinstance(plan, Mapping):
        return VerifierResult(reward.NOT_APPLICABLE)
    try:
        _validate_test_plan(plan)
    except ValueError as exc:
        # A malformed test plan is an invalid fixture, not agent evidence.
        return VerifierResult(reward.ERROR, {"reason": f"bad test_plan: {exc}"})
    # The test verifier runs its commands on the SAME honest trusted-local runner as
    # the agent; a test run_policy that requests an isolation the runner cannot keep
    # (e.g. network:"disabled") would otherwise run unrestricted under a label that
    # claims otherwise. Refuse it here — an inadmissible fixture is an error, not a
    # verdict against the agent.
    try:
        validate_run_policy(plan.get("run_policy", {}), RUNNER_PROFILE)
    except UnsupportedPolicyError as exc:
        return VerifierResult(reward.ERROR, {"reason": f"unsupported test run_policy: {exc}"})

    # TestPlanV2: resolve every logical tool to its host executable before running.
    # A tool that cannot be resolved (unknown profile / not admitted / absent) is an
    # inadmissible fixture — error, never a verdict against the agent. The identical
    # resolution runs in ``evalone`` to seal the toolchain into ``execution_facts``.
    executables: Optional[dict] = None
    if _is_v2(plan):
        try:
            _facts, executables = toolchain.resolve_toolchain(
                plan["toolchain_profile"], test_plan_tools(plan))
        except toolchain.ToolchainError as exc:
            return VerifierResult(reward.ERROR,
                                  {"reason": f"toolchain resolution failed: {exc}"})

    # The baseline judges the *fixture*, the patched run judges the *candidate*, so
    # the two mismatches are not the same kind of event. A baseline that does not
    # behave as the plan declares means the sealed subject is not the world the task
    # describes — that is an inadmissible fixture (error), never a verdict against
    # an agent who has not been consulted yet. A patched run that does not meet its
    # expectation is exactly a verdict against the candidate (fail).
    baseline, base_rec = run_command_set(
        plan, context.original_content, executables=executables, phase=BASELINE)
    if baseline == _INFRA_ERROR:
        return VerifierResult(reward.ERROR,
                              {"reason": "baseline run failed", "baseline": base_rec})
    if baseline == _SOME_FAIL:
        return VerifierResult(
            reward.ERROR,
            {"reason": "baseline did not meet its declared expectation",
             "baseline": base_rec})

    if context.patched_content is None:
        # No applicable patched tree → the candidate failed the acceptance contract.
        return VerifierResult(reward.FAIL,
                              {"reason": "no patched tree", "baseline": base_rec})

    patched, patched_rec = run_command_set(
        plan, context.patched_content, executables=executables, phase=PATCHED)
    detail = {"baseline": base_rec, "patched": patched_rec}
    if patched == _INFRA_ERROR:
        return VerifierResult(reward.ERROR, {"reason": "patched run failed", **detail})
    if patched == _ALL_PASS:
        return VerifierResult(reward.PASS, detail)
    return VerifierResult(
        reward.FAIL,
        {"reason": "patched did not meet its declared expectation", **detail})


# The wired implementation version (sealed as ``verifier_versions.tests.implementation``).
tests_verifier.version = TESTS_IMPL_VERSION


def make_identity_verifier(adapter: ForgeIdentityAdapterV1):
    """Build an ``identity`` verifier bound to a ``ForgeIdentityAdapterV1``."""

    def identity_verifier(context: VerifierContextV1) -> VerifierResult:
        policy = context.task.get("identity_policy")
        must_remain = policy.get("must_remain") if isinstance(policy, Mapping) else None
        if not isinstance(must_remain, Mapping) or not must_remain:
            return VerifierResult(reward.NOT_APPLICABLE)

        patched = context.patched_content
        if patched is None:
            return VerifierResult(reward.ERROR, {"reason": "no patched tree to re-lower"})

        moved = []
        bindings = {}
        for label, binding in must_remain.items():
            if not isinstance(binding, Mapping):
                return VerifierResult(reward.ERROR, {"reason": f"bad binding {label!r}"})
            path = binding.get("path")
            before_id = binding.get("before_id")
            if not isinstance(path, str) or not isinstance(before_id, str):
                return VerifierResult(reward.ERROR, {"reason": f"bad binding {label!r}"})
            try:
                rel = safe_relposix(path)
            except PathError as exc:
                return VerifierResult(reward.ERROR, {"reason": f"unsafe path: {exc}"})
            if rel not in patched:
                return VerifierResult(reward.ERROR,
                                      {"reason": f"bound source missing: {rel}"})
            try:
                lowered = adapter.lower_source(patched[rel])
            except ForgeTimeout:
                # Caught BEFORE `ForgeUnavailable`, which it subclasses. Sealed as
                # a stable CODE and not as `str(exc)`: this detail enters
                # `verification_evidence[identity]` and therefore `episode-…`, and
                # a timeout message is the one kind of message that is guaranteed
                # to be about the host — a deadline, a pid, a duration. The `path`
                # is the task's own declared string, already inside `task-…`, so
                # naming which binding hung adds no host bytes.
                #
                # No committed id can move onto this branch. Before the worker
                # existed a hang did not produce this detail, or any other: it
                # produced no episode at all, because the process never returned.
                return VerifierResult(
                    reward.ERROR,
                    {"reason": "forge lowering timed out",
                     "error_code": ERROR_CODE_FORGE_TIMEOUT,
                     "path": path})
            except ForgeSourceTooLarge:
                # **fail, not error** (9E attribution). The bound is a declared
                # number of bytes: the same source is over it on every host, so
                # this is a fact about what the candidate wrote and not about
                # this machine. Under 9D it was a `ForgeUnavailable` and came out
                # as `error` -- which nulls the reward, so a candidate could
                # unscore its identity signal by writing a large enough file.
                # The message is *not* sealed, only the stable code: a size in
                # the detail would be a number about these bytes rather than
                # about the rule, and `path` is already inside `task-`.
                return VerifierResult(
                    reward.FAIL,
                    {"reason": "patched source exceeds the identity source-size "
                               "profile",
                     "error_code": ERROR_CODE_FORGE_SOURCE_TOO_LARGE,
                     "path": path})
            except ForgeUnavailable as exc:
                return VerifierResult(reward.ERROR, {"reason": f"forge down: {exc}"})
            if not lowered.ok:
                return VerifierResult(reward.ERROR,
                                      {"reason": f"lower error: {lowered.error}"})
            # The re-lowered id of the bound source in the patched tree — equal to
            # before_id means the identity held; different means it moved.
            bindings[label] = {"path": path, "before_id": before_id,
                               "after_id": lowered.semantic_id}
            if lowered.semantic_id != before_id:
                moved.append(label)

        if moved:
            return VerifierResult(reward.FAIL,
                                  {"moved": sorted(moved), "bindings": bindings})
        return VerifierResult(reward.PASS, {"bindings": bindings})

    # The identity verifier's implementation version is the adapter's version — the
    # exact lowering seam that produced each re-lowered id (sealed as
    # ``verifier_versions.identity.implementation``).
    identity_verifier.version = getattr(adapter, "version", None)
    return identity_verifier
