"""Regenerate the content-addressed config of the real Residency spec-vs-impl task.

Unlike ``residency-demo`` (three pure signals), this bundle exercises ALL FIVE
Residency signals for real — citations, patch, finding_completeness, **tests**
(a controlled acceptance run) and **identity** (a Forge re-lower of the frozen
world). With the deterministic ``residency_agent.py`` in ``ok`` mode every signal
passes and the episode scores a full ``reward = 1.0``.

Two config values are computed against THIS environment and baked in, so the task
is self-consistent locally (exactly like ``residency-demo`` bakes its ids):

  * ``identity_policy.must_remain.world.before_id`` — the real ``sem-…`` the frozen
    world lowers to through the published ``forge_api.lower_source`` boundary. If
    the engine is unavailable the identity signal cannot be sealed; run with
    ``TRVS_FORGE_DIR`` pointing at ``TRVM/forge``.
  * ``test_plan.commands[].argv[0]`` — this interpreter's absolute path. The
    trusted-local runner exposes no ``PATH`` (R1: the toolchain profile owns it),
    so the acceptance command must be an absolute argv. task_id therefore depends
    on the interpreter path and is regenerated per environment.

    python3 examples/eval-one/residency-forge/build_bundle.py

Run it with the in-repo stub agent:

    trvs eval-one examples/eval-one/residency-forge \
        --agent python3 $PWD/test/fixtures/residency_agent.py --platform linux-x86_64
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, REPO)

from traaviis import identity, snapshot as S  # noqa: E402
from traaviis.forge_adapter import real_adapter, ForgeUnavailable  # noqa: E402


def _write(name, obj):
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def main():
    snap = S.build_snapshot(os.path.join(HERE, "subject"))
    _write("snapshot.json", snap)

    # Seal the frozen world's real SemanticArtifactID through the public boundary.
    with open(os.path.join(HERE, "subject", "world", "frozen.wrl"),
              "r", encoding="utf-8") as fh:
        world_src = fh.read()
    try:
        lowered = real_adapter().lower_source(world_src)
    except ForgeUnavailable as exc:
        sys.stderr.write("Forge unavailable, cannot seal identity: %s\n"
                         "Set TRVS_FORGE_DIR to TRVM/forge and retry.\n" % exc)
        raise SystemExit(2)
    if not lowered.ok:
        sys.stderr.write("frozen world did not lower: %s\n" % lowered.error)
        raise SystemExit(2)
    before_id = lowered.semantic_id

    reward_spec = {
        "reward_spec_version": "traaviis.reward.v1",
        "substrate_profile": "residency.repository.v1",
        "signals": {
            "citations":            {"verifier": "residency.citations.v1", "weight": 0.25},
            "patch":                {"verifier": "residency.patch.v1",     "weight": 0.20},
            "tests":                {"verifier": "residency.tests.v1",     "weight": 0.30},
            "identity":             {"verifier": "residency.identity.v1",  "weight": 0.15},
            "finding_completeness": {"verifier": "residency.finding.v1",   "weight": 0.10},
        },
        "caps": [
            {"when": {"signal": "patch", "state": "fail"}, "reward_max": 0.25},
            {"when": {"signal": "citations", "state": "fail"}, "reward_max": 0.25},
            {"when": {"signal": "tests", "state": "fail"}, "reward_max": 0.40},
        ],
        "aggregation": "terminal",
    }
    reward_spec["reward_id"] = identity.reward_id(reward_spec)
    _write("reward.json", reward_spec)

    # The acceptance gate: the module returns a small, in-contract integer. It must
    # pass on the sealed baseline AND on the candidate-patched tree; the `ok` agent
    # patches `return 1` -> `return 2`, both in-contract, so the tests signal passes.
    check = ("import sys;sys.exit(0 if open('src/mod.py').read().strip() in "
             "('return 1','return 2') else 1)")

    task = {
        "task_spec_version": "traaviis.task.v1",
        "substrate_profile": "residency.repository.v1",
        "subject": {"snapshot_id": snap["snapshot_id"]},
        "instructions": {
            "objective": "cite spec/residency.md, patch src/mod.py within the "
                         "return contract, and keep world/frozen.wrl's identity",
        },
        "reward_id": reward_spec["reward_id"],
        "verifier_plan": {
            "required": ["citations", "patch", "finding_completeness",
                         "tests", "identity"],
            "not_applicable": ["native", "oracle"],
        },
        # The tests signal: a controlled acceptance run on baseline + patched trees.
        "test_plan": {
            "commands": [
                {"argv": [sys.executable, "-c", check], "cwd": ".",
                 "timeout_seconds": 30},
            ],
            "baseline": "must_pass",
            "run_policy": {
                "runner_profile": "residency.trusted-local.v1",
                "network": "unrestricted",
            },
        },
        # The identity signal: the frozen world must keep its sealed sem id.
        "identity_policy": {
            "must_remain": {
                "world": {
                    "path": "world/frozen.wrl",
                    "profile": "forge.world.core.v1",
                    "before_id": before_id,
                },
            },
        },
        "termination": {"mode": "one_shot"},
        "agent_run_policy": {
            "policy_version": "traaviis.agent-run-policy.v1",
            "command_mode": "argv",
            "shell": False,
            "network": "unrestricted",
            "timeout_seconds": 30,
            "max_output_bytes": 4194304,
            "environment": {"TRAAVIIS_STUB_MODE": "ok"},
            "writable_paths": ["."],
            "result_path": "result.json",
            "patch_path": "candidate.patch",
        },
    }
    task["task_id"] = identity.task_id(task)
    _write("task.json", task)

    _write("bundle.json", {
        "eval_bundle_version": "traaviis.eval-bundle.v1",
        "task": "task.json",
        "reward": "reward.json",
        "snapshot": "snapshot.json",
        "subject": "subject",
        "agent": "agent.json",
    })

    print("sealed subject   ", snap["snapshot_id"])
    print("frozen world id  ", before_id)
    print("reward           ", reward_spec["reward_id"])
    print("task             ", task["task_id"])


if __name__ == "__main__":
    main()
