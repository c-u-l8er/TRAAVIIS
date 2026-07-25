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
  no identity bindings                             → not_applicable
"""

import hashlib
import subprocess
import tempfile
from typing import Any, List, Mapping, Optional, Tuple

from . import identity, reward, toolchain
from .execfacts import UnsupportedPolicyError, validate_run_policy
from .forge_adapter import ForgeIdentityAdapterV1, ForgeUnavailable
from .paths import PathError, safe_join, safe_relposix
from .runner import RUNNER_PROFILE, _materialize, _normalize_command, _seal_env
from .vcontext import VerifierContextV1, VerifierResult

__all__ = [
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
            try:
                proc = subprocess.run(
                    _command_argv(cmd, executables),
                    cwd=cwd,
                    env=sealed_env,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                records.append({"command_id": cid, "exit_code": None,
                                "expected_exit_codes": list(expected),
                                "stdout_digest": None, "stderr_digest": None,
                                "error": type(exc).__name__})
                return _INFRA_ERROR, records
            records.append({
                "command_id": cid,
                "exit_code": proc.returncode,
                "expected_exit_codes": list(expected),
                "stdout_digest": _sha256_bytes(proc.stdout),
                "stderr_digest": _sha256_bytes(proc.stderr),
            })
            if proc.returncode not in expected:
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
