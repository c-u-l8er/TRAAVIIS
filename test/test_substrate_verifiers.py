"""Battery for the substrate verifiers (traaviis.substrate_verifiers).

``tests`` runs the declared commands on two fresh materializations (baseline +
patched); ``identity`` re-lowers each bound source through a ForgeIdentityAdapter.
These pin the full state mapping GPT-5.6 ruled, using deterministic python argv
commands and the in-repo stub adapter (no TRVM engine needed).

Runs with pytest, or standalone: `python3 test/test_substrate_verifiers.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import reward as R  # noqa: E402
from traaviis import substrate_verifiers as SV  # noqa: E402
from traaviis.forge_adapter import (  # noqa: E402
    ForgeUnavailable, ForgeIdentityAdapterV1, LowerResult, StubForgeAdapter,
)
from traaviis.vcontext import VerifierContextV1  # noqa: E402

# A command that exits 0 iff src/mod.py is exactly "return 1".
_CHECK = [sys.executable, "-c",
          "import sys;sys.exit(0 if open('src/mod.py').read().strip()=="
          "'return 1' else 1)"]

BASELINE = {"src/mod.py": "return 1\n"}
PATCHED_OK = {"src/mod.py": "return 1\n"}      # still passes the check
PATCHED_BAD = {"src/mod.py": "return 2\n"}     # regresses the check
BROKEN_BASELINE = {"src/mod.py": "return 999\n"}  # baseline itself fails


def _tests_ctx(original, patched, plan=None):
    if plan is None:
        plan = {"commands": [{"argv": _CHECK, "cwd": "."}]}
    return VerifierContextV1(
        task={"test_plan": plan}, snapshot={},
        original_content=original, run={},
        patched_content=patched,
    )


# --- tests verifier ----------------------------------------------------------

def test_no_plan_is_not_applicable():
    ctx = VerifierContextV1(task={}, snapshot={}, original_content=BASELINE, run={})
    assert SV.tests_verifier(ctx).state == R.NOT_APPLICABLE


def test_malformed_plan_is_error():
    ctx = _tests_ctx(BASELINE, PATCHED_OK, plan={"commands": []})
    assert SV.tests_verifier(ctx).state == R.ERROR


def test_baseline_and_patched_pass_is_pass():
    ctx = _tests_ctx(BASELINE, PATCHED_OK)
    assert SV.tests_verifier(ctx).state == R.PASS


def test_patched_regression_is_fail():
    ctx = _tests_ctx(BASELINE, PATCHED_BAD)
    assert SV.tests_verifier(ctx).state == R.FAIL


def test_baseline_failure_is_error():
    # An inadmissible fixture (subject does not pass its own gate) is error.
    ctx = _tests_ctx(BROKEN_BASELINE, BROKEN_BASELINE)
    assert SV.tests_verifier(ctx).state == R.ERROR


def test_no_patched_tree_is_fail():
    ctx = _tests_ctx(BASELINE, None)
    assert SV.tests_verifier(ctx).state == R.FAIL


def test_unsupported_test_run_policy_is_error():
    # The test verifier shares the honest trusted-local runner; a test plan that
    # requests network:"disabled" would otherwise run unrestricted under a label
    # that claims isolation. It must be refused as an inadmissible fixture (error),
    # NOT silently run — the equivalent of the agent-policy preflight.
    plan = {"commands": [{"argv": _CHECK, "cwd": "."}],
            "run_policy": {"network": "disabled"}}
    ctx = _tests_ctx(BASELINE, PATCHED_OK, plan=plan)
    assert SV.tests_verifier(ctx).state == R.ERROR


def test_honest_test_run_policy_is_accepted():
    # The honest posture (network:"unrestricted") the runner actually delivers runs.
    plan = {"commands": [{"argv": _CHECK, "cwd": "."}],
            "run_policy": {"network": "unrestricted",
                           "runner_profile": "residency.trusted-local.v1"}}
    ctx = _tests_ctx(BASELINE, PATCHED_OK, plan=plan)
    assert SV.tests_verifier(ctx).state == R.PASS


# --- tests verifier, TestPlanV2 (logical toolchain) --------------------------

# A V2 command references its interpreter by the logical tool "python3" under a
# toolchain_profile instead of an absolute argv[0], so task identity is portable.
_V2_ARGS = ["-c", "import sys;sys.exit(0 if open('src/mod.py').read().strip()=="
            "'return 1' else 1)"]


def _v2_plan(tool="python3", profile="residency.python-host.v1", run_policy=None):
    plan = {
        "test_plan_version": SV.TEST_PLAN_V2,
        "toolchain_profile": profile,
        "commands": [{"tool": tool, "args": _V2_ARGS, "cwd": "."}],
    }
    if run_policy is not None:
        plan["run_policy"] = run_policy
    return plan


def test_v2_baseline_and_patched_pass_is_pass():
    ctx = _tests_ctx(BASELINE, PATCHED_OK, plan=_v2_plan())
    assert SV.tests_verifier(ctx).state == R.PASS


def test_v2_patched_regression_is_fail():
    ctx = _tests_ctx(BASELINE, PATCHED_BAD, plan=_v2_plan())
    assert SV.tests_verifier(ctx).state == R.FAIL


def test_v2_baseline_failure_is_error():
    ctx = _tests_ctx(BROKEN_BASELINE, BROKEN_BASELINE, plan=_v2_plan())
    assert SV.tests_verifier(ctx).state == R.ERROR


def test_v2_unknown_tool_is_error():
    # A tool the profile does not admit is an inadmissible fixture, not a verdict.
    ctx = _tests_ctx(BASELINE, PATCHED_OK, plan=_v2_plan(tool="rustc"))
    assert SV.tests_verifier(ctx).state == R.ERROR


def test_v2_unknown_profile_is_error():
    ctx = _tests_ctx(BASELINE, PATCHED_OK,
                     plan=_v2_plan(profile="no.such.profile.v1"))
    assert SV.tests_verifier(ctx).state == R.ERROR


def test_v2_plan_carries_no_host_path():
    # The logical plan is what enters task identity; the resolved interpreter
    # abspath must never appear in the authored plan (that was the V1 defect).
    from traaviis import identity
    raw = identity.canonical_bytes(_v2_plan()).decode("utf-8")
    assert "python3" in raw
    assert sys.executable not in raw


def test_v2_command_id_is_logical():
    # The V2 command_id hashes the logical tool + args, so it is identical on any
    # host regardless of where the interpreter is resolved.
    cmd = {"tool": "python3", "args": _V2_ARGS, "cwd": "."}
    assert SV._command_id(cmd) == SV._command_id(dict(cmd))


def test_v1_plan_has_no_logical_tools():
    assert SV.test_plan_tools({"commands": [{"argv": _CHECK, "cwd": "."}]}) == []


def test_v2_resolved_toolchain_seals_version_and_digest_not_path():
    # The resolved tool records only {version, executable_digest} — the host
    # abspath is used to run the command but is deliberately never sealed.
    from traaviis import toolchain
    facts, executables = toolchain.resolve_toolchain(
        "residency.python-host.v1", SV.test_plan_tools(_v2_plan()))
    assert facts["profile"] == "residency.python-host.v1"
    resolved = facts["resolved"]["python3"]
    assert set(resolved) == {"version", "executable_digest"}
    assert resolved["version"]
    assert resolved["executable_digest"].startswith("sha256:")
    assert os.path.isabs(executables["python3"])


# --- identity verifier -------------------------------------------------------

def _identity_ctx(patched, must_remain):
    return VerifierContextV1(
        task={"identity_policy": {"must_remain": must_remain}},
        snapshot={}, original_content={}, run={},
        patched_content=patched,
    )


_STUB = StubForgeAdapter()


def _binding(source, before_id):
    return {"path": "world.wrl", "profile": "forge.world.core.v1",
            "before_id": before_id}


def test_no_bindings_is_not_applicable():
    verifier = SV.make_identity_verifier(_STUB)
    ctx = _identity_ctx({"world.wrl": "src"}, {})
    assert verifier(ctx).state == R.NOT_APPLICABLE


def test_unchanged_source_is_pass():
    verifier = SV.make_identity_verifier(_STUB)
    src = "world unchanged"
    before = _STUB.lower_source(src).semantic_id
    ctx = _identity_ctx({"world.wrl": src}, {"w": _binding(src, before)})
    assert verifier(ctx).state == R.PASS


def test_moved_source_is_fail():
    verifier = SV.make_identity_verifier(_STUB)
    before = _STUB.lower_source("original").semantic_id
    ctx = _identity_ctx({"world.wrl": "MOVED"}, {"w": _binding("MOVED", before)})
    res = verifier(ctx)
    assert res.state == R.FAIL
    assert res.detail["moved"] == ["w"]


def test_no_patched_tree_is_error():
    verifier = SV.make_identity_verifier(_STUB)
    ctx = _identity_ctx(None, {"w": _binding("x", "sem-x")})
    assert verifier(ctx).state == R.ERROR


def test_missing_bound_source_is_error():
    verifier = SV.make_identity_verifier(_STUB)
    ctx = _identity_ctx({"other.wrl": "x"}, {"w": _binding("x", "sem-x")})
    assert verifier(ctx).state == R.ERROR


def test_compile_error_is_error_not_fail():
    verifier = SV.make_identity_verifier(_STUB)
    src = "world @@COMPILE_ERROR@@"
    ctx = _identity_ctx({"world.wrl": src}, {"w": _binding(src, "sem-anything")})
    # A lowering (compile) error cannot complete the comparison → error, not fail.
    assert verifier(ctx).state == R.ERROR


def test_forge_unavailable_is_error():
    class _DownAdapter(ForgeIdentityAdapterV1):
        version = "forge.identity-adapter.down.v1"

        def lower_source(self, source):
            raise ForgeUnavailable("engine down")

    verifier = SV.make_identity_verifier(_DownAdapter())
    ctx = _identity_ctx({"world.wrl": "x"}, {"w": _binding("x", "sem-x")})
    assert verifier(ctx).state == R.ERROR


def _main():
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures.append((name, exc))
            print(f"FAIL  {name}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
