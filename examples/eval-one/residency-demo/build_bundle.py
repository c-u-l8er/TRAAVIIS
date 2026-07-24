"""Regenerate the content-addressed config of the residency-demo task bundle.

Seals ``subject/`` into ``snapshot.json`` and writes ``reward.json`` + ``task.json``
with their recomputed ``reward_id`` / ``task_id`` so ``trvs eval-one`` admits the
bundle without an id mismatch. Run after editing the subject or the specs:

    python3 examples/eval-one/residency-demo/build_bundle.py

The agent under evaluation is the deterministic in-repo stub (test/fixtures/
stub_agent.py). Pass it at run time so the bundle stays machine-independent:

    trvs eval-one examples/eval-one/residency-demo \
        --agent python3 $PWD/test/fixtures/stub_agent.py --platform linux-x86_64
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, REPO)

from traaviis import identity, snapshot as S  # noqa: E402


def _write(name, obj):
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def main():
    snap = S.build_snapshot(os.path.join(HERE, "subject"))
    _write("snapshot.json", snap)

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

    task = {
        "task_spec_version": "traaviis.task.v1",
        "substrate_profile": "residency.repository.v1",
        "subject": {"snapshot_id": snap["snapshot_id"]},
        "instructions": {"objective": "cite the spec and patch src/mod.py"},
        "reward_id": reward_spec["reward_id"],
        # tests + identity are scored-but-not-required, so their not_applicable in
        # this self-contained demo (no test_plan, Forge re-lower unpublished) is a
        # valid partial-reward episode, never an invalid config.
        "verifier_plan": {
            "required": ["citations", "patch", "finding_completeness"],
            "not_applicable": ["native", "oracle", "tests", "identity"],
        },
        "termination": {"mode": "one_shot"},
        "agent_run_policy": {
            "policy_version": "traaviis.agent-run-policy.v1",
            "command_mode": "argv",
            "shell": False,
            # The runner is trusted-local, not a sandbox: it observes writes and does
            # not isolate the network. "unrestricted" is the honest, only-supported
            # posture (a "disabled" request would be rejected at preflight).
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

    # The operational bundle manifest (eval-bundle.v1): names each member with a
    # safe, in-bundle relative path. Default names are used here, made explicit.
    _write("bundle.json", {
        "eval_bundle_version": "traaviis.eval-bundle.v1",
        "task": "task.json",
        "reward": "reward.json",
        "snapshot": "snapshot.json",
        "subject": "subject",
        "agent": "agent.json",
    })

    print("sealed subject   ", snap["snapshot_id"])
    print("reward           ", reward_spec["reward_id"])
    print("task             ", task["task_id"])


if __name__ == "__main__":
    main()
