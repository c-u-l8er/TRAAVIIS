"""Substrate verifiers: ``tests`` (controlled test-command run) and ``identity``
(Forge re-lower). Both take a ``VerifierContextV1`` and return a ``VerifierResult``.

Per the GPT-5.6 closure ruling these are the two verifiers that touch substrate,
so they live apart from the pure ``traaviis.verifiers``.

**tests** (``TestPlanV1`` in ``task.test_plan``): run the declared commands on two
fresh materializations of the sealed subject — a clean baseline copy and a clean
copy with the candidate patch applied — under a separate ``VerifierRunPolicyV1``.

  baseline-all-0 & patched-all-0                      → pass
  baseline-all-0 & any-patched-nonzero                → fail (candidate regressed)
  baseline-nonzero                                    → error (fixture inadmissible)
  toolchain/timeout/runner failure                    → error
  no patched tree (patch absent or did not apply)     → fail
  test plan absent                                    → not_applicable

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

import subprocess
import tempfile
from typing import Any, Mapping, Tuple

from . import reward
from .forge_adapter import ForgeIdentityAdapterV1, ForgeUnavailable
from .paths import PathError, safe_join, safe_relposix
from .runner import _materialize, _seal_env
from .vcontext import VerifierContextV1, VerifierResult

__all__ = [
    "TESTS_VERIFIER_VERSION",
    "tests_verifier",
    "make_identity_verifier",
    "run_command_set",
]

TESTS_VERIFIER_VERSION = "residency.tests.v1"

# _run outcomes for one command set over one materialization.
_ALL_PASS = "all_pass"
_SOME_FAIL = "some_fail"
_INFRA_ERROR = "infra_error"


def _validate_test_plan(plan: Mapping[str, Any]) -> list:
    commands = plan.get("commands")
    if not isinstance(commands, list) or not commands:
        raise ValueError("test_plan.commands must be a non-empty list")
    for cmd in commands:
        if not isinstance(cmd, Mapping):
            raise ValueError("each command must be an object")
        argv = cmd.get("argv")
        if not isinstance(argv, list) or not argv \
                or not all(isinstance(a, str) for a in argv):
            raise ValueError("command argv must be a non-empty string list")
        safe_relposix(cmd.get("cwd", "."), allow_empty=True)  # raises on unsafe cwd
    return commands


def run_command_set(
    plan: Mapping[str, Any], content: Mapping[str, str],
) -> str:
    """Materialize ``content`` and run every command in order under the plan policy.

    Returns ``_ALL_PASS`` (all exit 0), ``_SOME_FAIL`` (some nonzero), or
    ``_INFRA_ERROR`` (timeout / spawn failure). ``shell`` is always false.
    """
    commands = _validate_test_plan(plan)
    run_policy = plan.get("run_policy", {})
    sealed_env = _seal_env(run_policy)

    root = tempfile.mkdtemp(prefix="traaviis-test-")
    try:
        _materialize(content, root)
        all_pass = True
        for cmd in commands:
            cwd_rel = cmd.get("cwd", ".")
            try:
                cwd = safe_join(root, cwd_rel) if cwd_rel not in (".", "") else root
            except PathError:
                return _INFRA_ERROR
            timeout = cmd.get("timeout_seconds")
            try:
                proc = subprocess.run(
                    list(cmd["argv"]),
                    cwd=cwd,
                    env=sealed_env,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                )
            except (subprocess.TimeoutExpired, OSError):
                return _INFRA_ERROR
            if proc.returncode != 0:
                all_pass = False
        return _ALL_PASS if all_pass else _SOME_FAIL
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

    baseline = run_command_set(plan, context.original_content)
    if baseline == _INFRA_ERROR:
        return VerifierResult(reward.ERROR, {"reason": "baseline run failed"})
    if baseline == _SOME_FAIL:
        # Fixture is inadmissible: the sealed subject does not pass its own gate.
        return VerifierResult(reward.ERROR, {"reason": "baseline tests fail"})

    if context.patched_content is None:
        # No applicable patched tree → the candidate failed the acceptance contract.
        return VerifierResult(reward.FAIL, {"reason": "no patched tree"})

    patched = run_command_set(plan, context.patched_content)
    if patched == _INFRA_ERROR:
        return VerifierResult(reward.ERROR, {"reason": "patched run failed"})
    if patched == _ALL_PASS:
        return VerifierResult(reward.PASS)
    return VerifierResult(reward.FAIL, {"reason": "patched tests regressed"})


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
            if lowered.semantic_id != before_id:
                moved.append(label)

        if moved:
            return VerifierResult(reward.FAIL, {"moved": sorted(moved)})
        return VerifierResult(reward.PASS)

    return identity_verifier
