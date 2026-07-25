"""Battery for the frozen REAL Residency task (examples/eval-one/residency-forge).

Unlike ``residency-demo`` (three pure signals), this bundle exercises ALL FIVE
Residency signals for real -- citations, patch, finding_completeness, **tests**
(a controlled acceptance run) and **identity** (a Forge re-lower of the frozen
world). It pins the full 1.0 episode plus every negative case GPT-5.6 required:

  positive     all five pass          -> status ok / valid / reward 1.0
  wrongcite    citation quote mismatch -> citations fail, capped 0.25
  testfail     patched acceptance fail -> tests fail, capped 0.40
  identityfail world sem-id moves      -> identity fail (no cap)  reward 0.85
  identitybreak world re-lower errors  -> identity error -> status error, null
  badpatch     diff will not apply      -> patch fail, capped 0.25 (pure trio)
  rerun        identical inputs         -> byte-identical episode id
  no-forge     identity unwired         -> invalid config BEFORE the agent runs
  version      different Forge version  -> different episode id

The five-signal cases fold through the real TRVM engine and the acceptance
runner; when the engine cannot be located (no TRVM checkout / no
``TRVS_FORGE_DIR``) they SKIP rather than fail -- they assert the Residency
contract, not the presence of the engine on a given machine. The three
structural cases (badpatch trio, no-forge invalid-config, version sensitivity)
are engine-free and always run.

Run directly:      python3 test/test_real_residency.py
Run under pytest:  pytest test/test_real_residency.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import admission  # noqa: E402
from traaviis import evalone  # noqa: E402
from traaviis import identity as I  # noqa: E402
from traaviis import reward as R  # noqa: E402
from traaviis.forge_adapter import (  # noqa: E402
    ForgeIdentityAdapterV1, LowerResult,
)
from traaviis.substrate_verifiers import (  # noqa: E402
    make_identity_verifier, tests_verifier,
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUNDLE = os.path.join(REPO, "examples", "eval-one", "residency-forge")
FIXTURE = os.path.join(HERE, "fixtures", "residency_agent.py")
AGENT = [sys.executable, FIXTURE]  # absolute exe -> no PATH needed under sealed env


class Skip(Exception):
    pass


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# --- engine gating (five-signal cases only) --------------------------------

def _engine_available():
    argv = [sys.executable, "-m", "traaviis.cli", "doctor"]
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
    return not (p.returncode == 2 and "could not locate" in p.stderr)


def _require_engine():
    if not _engine_available():
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")


# --- CLI subprocess driver -------------------------------------------------

def _run_cli(bundle):
    """Run ``trvs eval-one <bundle> --agent <fixture> --json`` and return
    (exit_code, receipt_dict). The agent mode is baked into the bundle's
    ``agent_run_policy.environment`` (never the process env), so the sealed run
    is fully reproducible."""
    argv = [sys.executable, "-m", "traaviis.cli", "eval-one", bundle,
            "--agent", *AGENT, "--platform", "linux-x86_64", "--json"]
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
    receipt = json.loads(p.stdout) if p.stdout.strip() else None
    return p.returncode, receipt


def _bundle_with_mode(tmp, mode):
    """Copy the frozen bundle, set the stub agent's TRAAVIIS_STUB_MODE inside the
    sealed ``agent_run_policy.environment``, and re-seal ``task_id`` so the ONLY
    thing under test is the mode (not a stale id)."""
    dst = os.path.join(tmp, "residency-forge")
    shutil.copytree(BUNDLE, dst)
    task_p = os.path.join(dst, "task.json")
    task = _load(task_p)
    task["agent_run_policy"]["environment"]["TRAAVIIS_STUB_MODE"] = mode
    task.pop("task_id", None)
    task["task_id"] = I.task_id(task)
    _dump(task_p, task)
    return dst


# --- in-process eval_one driver (engine-free structural cases) -------------

def _load_bundle_parts():
    task = _load(os.path.join(BUNDLE, "task.json"))
    reward_spec = _load(os.path.join(BUNDLE, "reward.json"))
    snapshot = _load(os.path.join(BUNDLE, "snapshot.json"))
    content = admission.verify_subject_tree(snapshot, os.path.join(BUNDLE, "subject"))
    return task, reward_spec, snapshot, content


# ===========================================================================
# Five-signal cases (skip without the engine)
# ===========================================================================

def test_positive_all_five_pass_reward_one():
    _require_engine()
    code, r = _run_cli(BUNDLE)
    assert code == 0, r
    assert r["status"] == "ok" and r["validity"] == "valid"
    assert abs(r["reward"] - 1.0) < 1e-9, r["reward"]
    v = r["verification"]
    for sig in ("citations", "patch", "tests", "identity", "finding_completeness"):
        assert v[sig] == "pass", (sig, v)
    assert v["native"] == "not_applicable" and v["oracle"] == "not_applicable"
    impl = r["verifier_versions"]["identity"]["implementation"]
    assert impl.startswith("forge.identity.v1@api-")
    assert "@lower-" in impl and "@engine-" in impl


def test_wrong_citation_fails_and_caps():
    _require_engine()
    tmp = tempfile.mkdtemp(prefix="traaviis-res-")
    try:
        dst = _bundle_with_mode(tmp, "wrongcite")
        code, r = _run_cli(dst)
        assert r["verification"]["citations"] == "fail", r["verification"]
        # patch/tests/identity/finding still pass; the citations-fail cap pins 0.25.
        assert r["status"] == "ok" and r["validity"] == "valid"
        assert abs(r["reward"] - 0.25) < 1e-9, r["reward"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_patched_test_failure_caps_at_040():
    _require_engine()
    tmp = tempfile.mkdtemp(prefix="traaviis-res-")
    try:
        dst = _bundle_with_mode(tmp, "testfail")
        code, r = _run_cli(dst)
        v = r["verification"]
        assert v["tests"] == "fail", v
        assert v["patch"] == "pass", v  # the diff applied; the acceptance run rejected
        assert r["status"] == "ok" and r["validity"] == "valid"
        assert abs(r["reward"] - 0.40) < 1e-9, r["reward"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_identity_change_fails_identity():
    _require_engine()
    tmp = tempfile.mkdtemp(prefix="traaviis-res-")
    try:
        dst = _bundle_with_mode(tmp, "identityfail")
        code, r = _run_cli(dst)
        v = r["verification"]
        assert v["identity"] == "fail", v  # a VALID but different world moved the id
        # the other four still pass; no identity cap -> reward = 1.0 - 0.15
        assert r["status"] == "ok" and r["validity"] == "valid"
        assert abs(r["reward"] - 0.85) < 1e-9, r["reward"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_broken_world_is_identity_error_null():
    _require_engine()
    tmp = tempfile.mkdtemp(prefix="traaviis-res-")
    try:
        dst = _bundle_with_mode(tmp, "identitybreak")
        code, r = _run_cli(dst)
        assert r["verification"]["identity"] == "error", r["verification"]
        # a REQUIRED signal in error -> episode status error, reward withheld.
        assert r["status"] == "error", r
        assert r["reward"] is None
        assert code == cli_exit("error")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_identical_rerun_same_episode_id():
    _require_engine()
    _, a = _run_cli(BUNDLE)
    _, b = _run_cli(BUNDLE)
    assert a["episode_id"] == b["episode_id"], (a["episode_id"], b["episode_id"])
    assert a["reward"] == b["reward"] == 1.0


# ===========================================================================
# Structural cases (engine-free -- always run)
# ===========================================================================

def test_badpatch_fails_patch_and_caps_025():
    # A diff whose context does not match never applies. To read this as the pure
    # patch-fail cap (not an identity/tests error over a missing patched tree), it
    # is scored under a THREE-signal reward (citations/patch/finding) with the
    # substrate signals dropped -- exactly the residency-demo posture.
    tmp = tempfile.mkdtemp(prefix="traaviis-res-")
    try:
        dst = _bundle_with_mode(tmp, "badpatch")
        # Collapse to a pure trio: drop the substrate plan + policies, reweight,
        # keep only the patch-fail cap, then re-seal reward_id + task references.
        reward_p = os.path.join(dst, "reward.json")
        rw = _load(reward_p)
        rw["signals"] = {
            "citations": {"verifier": "residency.citations.v1", "weight": 0.45},
            "patch": {"verifier": "residency.patch.v1", "weight": 0.35},
            "finding_completeness": {"verifier": "residency.finding.v1", "weight": 0.20},
        }
        rw["caps"] = [{"when": {"signal": "patch", "state": "fail"}, "reward_max": 0.25}]
        rw.pop("reward_id", None)
        rw["reward_id"] = I.reward_id(rw)
        _dump(reward_p, rw)

        task_p = os.path.join(dst, "task.json")
        task = _load(task_p)
        task["reward_id"] = rw["reward_id"]
        task["verifier_plan"]["required"] = ["citations", "patch", "finding_completeness"]
        task["verifier_plan"]["not_applicable"] = ["tests", "identity", "native", "oracle"]
        task.pop("test_plan", None)
        task.pop("identity_policy", None)
        task.pop("task_id", None)
        task["task_id"] = I.task_id(task)
        _dump(task_p, task)

        # bundle.json still references reward.json/task.json by name -- unchanged.
        code, r = _run_cli(dst)
        v = r["verification"]
        assert v["patch"] == "fail", v
        assert v["citations"] == "pass" and v["finding_completeness"] == "pass", v
        assert v["tests"] == "not_applicable" and v["identity"] == "not_applicable", v
        assert r["status"] == "ok" and r["validity"] == "valid"
        assert abs(r["reward"] - 0.25) < 1e-9, r["reward"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_forge_unavailable_is_invalid_config():
    # When Forge cannot be located the CLI leaves ``identity`` UNWIRED. A required
    # signal with no verifier can never resolve -> the task config is invalid and
    # the agent is refused a run (F4): status invalid, reward null, no trace.
    task, reward_spec, snapshot, content = _load_bundle_parts()
    # Emulate the unwired posture: provide every substrate verifier EXCEPT identity.
    receipt = evalone.eval_one(
        task, content, AGENT, reward_spec,
        snapshot=snapshot,
        extra_verifiers={"tests": tests_verifier},  # identity deliberately absent
        platform="linux-x86_64",
    )
    assert receipt["status"] == R.STATUS_INVALID, receipt["status"]
    assert receipt["validity"] == R.INVALID
    assert receipt["reward"] is None
    assert receipt["trace_id"] is None  # refused BEFORE the agent ran
    assert receipt["verification"]["identity"] == R.NOT_APPLICABLE


def test_different_forge_version_different_episode_id():
    # The identity verifier's implementation version embeds the engine version.
    # Two Forge builds that agree on the world's sem-id but differ in version must
    # seal DIFFERENT verifier_versions.identity -> different episode ids, even
    # though every signal and output is byte-identical.
    task, reward_spec, snapshot, content = _load_bundle_parts()
    before_id = task["identity_policy"]["must_remain"]["world"]["before_id"]

    class _PinnedAdapter(ForgeIdentityAdapterV1):
        # The ok-mode patch never touches the world, so the only source ever
        # lowered is the unchanged frozen world -> return its sealed id.
        def __init__(self, ver):
            self.version = ver

        def lower_source(self, source):
            return LowerResult(semantic_id=before_id, ok=True)

    def _episode(ver):
        return evalone.eval_one(
            task, content, AGENT, reward_spec,
            snapshot=snapshot,
            extra_verifiers={
                "tests": tests_verifier,
                "identity": make_identity_verifier(_PinnedAdapter(ver)),
            },
            platform="linux-x86_64",
        )

    a = _episode("forge.identity.v1@trvm-0.7.0-alpha.5")
    b = _episode("forge.identity.v1@trvm-0.9.0-beta.1")
    assert a["status"] == "ok" and b["status"] == "ok", (a["status"], b["status"])
    assert a["verification"]["identity"] == "pass"
    assert a["episode_id"] != b["episode_id"], "engine version must move the id"
    # And a same-version pair is byte-identical (the version is the ONLY mover).
    c = _episode("forge.identity.v1@trvm-0.7.0-alpha.5")
    assert a["episode_id"] == c["episode_id"]


# --- exit-code helper (mirrors cli._EVAL_EXIT without importing the module) -

def cli_exit(status):
    from traaviis import cli
    return cli._EVAL_EXIT[status]


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
