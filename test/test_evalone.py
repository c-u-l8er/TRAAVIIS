"""End-to-end battery for the eval-one orchestrator (traaviis.evalone, RFC §10).

Drives the full one-shot pipeline against the deterministic stub agent: snapshot
-> controlled run -> trace -> finding/patch -> verifiers -> reward -> episode
receipt. Pins the happy path (valid full-reward episode), a bad-patch fail with
the §7 floor, a tampered (policy-violation) invalid episode, a required-but-
deferred verifier producing invalid-config, and episode-identity stability vs.
movement.

Runs with pytest, or standalone: `python3 test/test_evalone.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import evalone as E  # noqa: E402
from traaviis import identity as I  # noqa: E402
from traaviis import reward as R  # noqa: E402
from traaviis import snapshot as S  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = os.path.join(HERE, "fixtures", "stub_agent.py")

# The frozen subject the stub was written against.
CONTENT = {"spec/one.md": "alpha\nbeta\n", "src/mod.py": "return 1\n"}

REWARD_SPEC = {
    "reward_spec_version": "traaviis.reward.v1",
    "substrate_profile": "residency.repository.v1",
    "signals": {
        "citations":            {"verifier": "residency.citations.v1", "weight": 0.25},
        "patch":                {"verifier": "residency.patch.v1",     "weight": 0.20},
        "tests":                {"verifier": "residency.tests.v1",     "weight": 0.30},
        "identity":             {"verifier": "residency.identity.v1",  "weight": 0.15},
        "finding_completeness": {"verifier": "residency.finding.v1",   "weight": 0.10},
    },
    "floors": [
        {"when": "patch", "reward_max": 0.25},
        {"when": "citations", "reward_max": 0.25},
        {"when": "tests", "reward_max": 0.40},
    ],
    "aggregation": "terminal",
}

VER_VERSIONS = {"citations": "1", "patch": "1", "tests": "1",
                "identity": "1", "finding_completeness": "1"}


def _snapshot():
    return {"snapshot_version": S.SNAPSHOT_VERSION,
            "files": {k: "sha256:x" for k in CONTENT},
            "exclusions": [], "file_modes": {},
            "base_revision": None, "visible_config": {},
            "snapshot_id": "snap-fixture"}


def _task(required):
    return {
        "task_spec_version": "traaviis.task.v1",
        "substrate_profile": "residency.repository.v1",
        "subject": {"snapshot_id": "snap-fixture"},
        "instructions": {"objective": "demo"},
        "reward_id": I.reward_id(REWARD_SPEC),
        "verifier_plan": {"required": required,
                          "not_applicable": ["native", "oracle"]},
        "termination": {"mode": "one_shot"},
        "agent_run_policy": {
            "policy_version": "traaviis.agent-run-policy.v1",
            "command_mode": "argv", "shell": False, "network": "disabled",
            "timeout_seconds": 30, "max_output_bytes": 4194304,
            "environment": {"TRAAVIIS_STUB_MODE": "ok",
                            "PATH": os.environ.get("PATH", "")},
            "writable_paths": ["."],
            "result_path": "result.json", "patch_path": "candidate.patch",
        },
    }


def _pass_tests(run, task, content):
    return R.PASS  # injected substrate verifier stand-in


def _pass_identity(run, task, content):
    return R.PASS


ALL_PASS = {"tests": _pass_tests, "identity": _pass_identity}


def _eval(mode, required, extra=ALL_PASS, env=None):
    task = _task(required)
    if env is not None:
        task["agent_run_policy"]["environment"] = env
    else:
        task["agent_run_policy"]["environment"]["TRAAVIIS_STUB_MODE"] = mode
    return E.eval_one(
        task, CONTENT, [sys.executable, STUB], REWARD_SPEC,
        snapshot=_snapshot(), verifier_versions=VER_VERSIONS,
        extra_verifiers=extra, platform="linux-x86_64",
        toolchain={"python": "3.11.4"},
    )


def test_happy_path_full_reward_valid_episode():
    r = _eval("ok", ["citations", "patch", "tests", "identity"])
    assert r["status"] == R.STATUS_OK
    assert r["validity"] == R.VALID
    assert abs(r["reward"] - 1.0) < 1e-9
    assert r["episode_id"].startswith("episode-")
    assert r["verification"]["citations"] == R.PASS
    assert r["verification"]["native"] == R.NOT_APPLICABLE
    assert r["outputs"]["finding_id"].startswith("finding-")
    assert r["outputs"]["patch_id"].startswith("patch-")


def test_bad_patch_fails_patch_and_hits_floor():
    r = _eval("badpatch", ["citations", "patch", "tests", "identity"])
    assert r["verification"]["patch"] == R.FAIL
    assert r["status"] == R.STATUS_OK
    assert abs(r["reward"] - 0.25) < 1e-9  # §7 patch floor


def test_policy_violation_is_invalid_episode():
    r = _eval("ok", ["citations", "patch"], env={
        "TRAAVIIS_STUB_MODE": "ok", "PATH": os.environ.get("PATH", "")})
    # constrain writable to src/ so root outputs violate
    # (re-run with a tighter policy)
    task = _task(["citations", "patch"])
    task["agent_run_policy"]["writable_paths"] = ["src/"]
    r = E.eval_one(task, CONTENT, [sys.executable, STUB], REWARD_SPEC,
                   snapshot=_snapshot(), verifier_versions=VER_VERSIONS,
                   extra_verifiers=ALL_PASS, platform="linux-x86_64")
    assert r["status"] == R.STATUS_INVALID
    assert r["validity"] == R.INVALID
    assert r["reward"] == 0.0


def test_required_deferred_verifier_is_invalid_config():
    # Require identity but do NOT inject it -> defaults not_applicable -> invalid.
    r = _eval("ok", ["citations", "patch", "identity"], extra={})
    assert r["status"] == R.STATUS_INVALID
    assert r["reward"] is None


def test_episode_identity_stable_across_reruns():
    a = _eval("ok", ["citations", "patch", "tests", "identity"])
    b = _eval("ok", ["citations", "patch", "tests", "identity"])
    assert a["episode_id"] == b["episode_id"]


def test_toolchain_change_moves_episode():
    a = _eval("ok", ["citations", "patch", "tests", "identity"])
    task = _task(["citations", "patch", "tests", "identity"])
    b = E.eval_one(task, CONTENT, [sys.executable, STUB], REWARD_SPEC,
                   snapshot=_snapshot(), verifier_versions=VER_VERSIONS,
                   extra_verifiers=ALL_PASS, platform="linux-x86_64",
                   toolchain={"python": "3.11.9"})
    assert a["episode_id"] != b["episode_id"]


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
