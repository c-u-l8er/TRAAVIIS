"""End-to-end battery for the eval-one orchestrator (traaviis.evalone, RFC §10).

Drives the full one-shot pipeline against the deterministic stub agent: snapshot
-> controlled run -> trace -> finding/patch -> verifiers -> reward -> episode
receipt. Pins the happy path (valid full-reward episode), a bad-patch fail with
the §7 floor, a tampered (policy-violation) invalid episode, a required-but-
deferred verifier producing invalid-config, and episode-identity stability vs.
movement.

Runs with pytest, or standalone: `python3 test/test_evalone.py`.
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import admission as ADM  # noqa: E402
from traaviis import evalone as E  # noqa: E402
from traaviis import identity as I  # noqa: E402
from traaviis import reward as R  # noqa: E402
from traaviis import snapshot as S  # noqa: E402
from traaviis import substrate_verifiers as SV  # noqa: E402
from traaviis.vcontext import VerifierResult  # noqa: E402

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
    "caps": [  # F1: explicit trigger state, renamed from "floors"
        {"when": {"signal": "patch", "state": "fail"}, "reward_max": 0.25},
        {"when": {"signal": "citations", "state": "fail"}, "reward_max": 0.25},
        {"when": {"signal": "tests", "state": "fail"}, "reward_max": 0.40},
    ],
    "aggregation": "terminal",
}

VER_VERSIONS = {"citations": "1", "patch": "1", "tests": "1",
                "identity": "1", "finding_completeness": "1"}

# A structured toolchain in the shape execution_facts.v1 seals (E1). Changing the
# resolved python version moves episode- (see test_toolchain_change_moves_episode).
TOOLCHAIN = {"profile": "cpython-3.11",
             "resolved": {"python": {"version": "3.11.4"}}}


def _content_hash(text):
    data = text.encode("utf-8").replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _snapshot():
    # A real sealed subject so admission binds content<->snapshot exactly.
    snap = {"snapshot_version": S.SNAPSHOT_VERSION,
            "files": {k: _content_hash(v) for k, v in CONTENT.items()},
            "exclusions": [], "binary_paths": [], "file_modes": {},
            "base_revision": None, "visible_config": {}}
    snap["snapshot_id"] = I.snapshot_id(snap)
    return snap


def _task(required):
    return {
        "task_spec_version": "traaviis.task.v1",
        "substrate_profile": "residency.repository.v1",
        "subject": {"snapshot_id": _snapshot()["snapshot_id"]},
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


def _pass_tests(context):
    return VerifierResult(R.PASS)  # injected substrate verifier stand-in


def _pass_identity(context):
    return VerifierResult(R.PASS)


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
        toolchain=TOOLCHAIN,
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
                   toolchain={"profile": "cpython-3.11",
                              "resolved": {"python": {"version": "3.11.9"}}})
    assert a["episode_id"] != b["episode_id"]


def test_nonzero_exit_is_error_and_null_reward():
    # A non-allowed exit code is substrate failure: error / invalid / null reward,
    # never a false fail (exit-code semantics ruling).
    r = _eval("nonzero", ["citations", "patch", "tests", "identity"])
    assert r["status"] == R.STATUS_ERROR
    assert r["reward"] is None
    assert r["validity"] == R.INVALID


def test_malformed_json_result_never_crashes():
    # A result.json that parses to a JSON list (not an object) must not crash the
    # pipeline (blocker 7): it yields an empty finding scored as fail.
    r = _eval("listresult", ["citations", "patch"])
    assert r["status"] == R.STATUS_OK
    assert r["verification"]["citations"] == R.FAIL
    assert r["episode_id"].startswith("episode-")


def test_patched_tests_regression_hits_040_cap():
    # Wire the REAL tests verifier: baseline passes the check, the ok patch changes
    # "return 1" -> "return 2" so the patched tree regresses -> tests fail -> 0.40.
    check = [sys.executable, "-c",
             "import sys;sys.exit(0 if open('src/mod.py').read().strip()=="
             "'return 1' else 1)"]
    task = _task(["citations", "patch", "tests", "identity"])
    task["test_plan"] = {"commands": [{"argv": check, "cwd": "."}]}
    r = E.eval_one(
        task, CONTENT, [sys.executable, STUB], REWARD_SPEC,
        snapshot=_snapshot(), verifier_versions=VER_VERSIONS,
        extra_verifiers={"tests": SV.tests_verifier, "identity": _pass_identity},
        platform="linux-x86_64", toolchain=TOOLCHAIN,
    )
    assert r["verification"]["tests"] == R.FAIL
    assert r["status"] == R.STATUS_OK
    assert abs(r["reward"] - 0.40) < 1e-9  # tests fail cap


def test_false_declared_snapshot_id_is_rejected():
    snap = _snapshot()
    snap["snapshot_id"] = "snap-fixture"  # a lie
    try:
        E.eval_one(_task(["patch"]), CONTENT, [sys.executable, STUB], REWARD_SPEC,
                   snapshot=snap, verifier_versions=VER_VERSIONS,
                   extra_verifiers=ALL_PASS)
    except ADM.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a false snapshot_id")


def test_content_snapshot_mismatch_is_rejected():
    tampered = dict(CONTENT, **{"src/mod.py": "return 42\n"})
    try:
        E.eval_one(_task(["patch"]), tampered, [sys.executable, STUB], REWARD_SPEC,
                   snapshot=_snapshot(), verifier_versions=VER_VERSIONS,
                   extra_verifiers=ALL_PASS)
    except ADM.AdmissionError:
        return
    raise AssertionError("expected AdmissionError when content != sealed subject")


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
