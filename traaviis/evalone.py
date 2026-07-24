"""`trvs eval-one` orchestrator — the one-shot Residency evaluation pipeline.

Wires the frozen ten steps of ``RFC_EVIDENCE_RESIDENCY.md`` §10 into a single
deterministic episode: snapshot the subject → run the agent under policy →
capture the trace → read the finding + patch → run the verifiers → score the
reward → emit the ``episode-…`` receipt. Every identity is content-addressed via
``traaviis.identity``; scoring is the pure engine in ``traaviis.reward``.

Slice status. The three **substrate-independent** verifiers (``citations``,
``patch``, ``finding_completeness``) are wired live from ``traaviis.verifiers``.
The two **substrate** verifiers are injectable and default to deferred:

  ``tests``     needs the controlled test-command run — pass a callable
                ``(patched_content, task) -> state`` via ``extra_verifiers``.
  ``identity``  needs the Forge re-lower (TRVM) to check that ``must_remain``
                ``sem-…`` domains did not move — pass a callable
                ``(run, task) -> state``.

Any signal not resolved by a live or injected verifier defaults to
``not_applicable``; if the task marks it *required*, the reward engine turns that
into an invalid task configuration (§6a) — which is the correct, honest result
until those two verifiers are ratified and wired.

Under-frozen edges flagged for GPT-5.6:

  E1  ``execution_facts`` canonical schema. Built here as
      ``{exit_code, platform, timed_out, output_truncated, toolchain}`` — these
      enter ``episode-…`` (see ``identity._EPISODE_IDENTITY_KEYS``). The exact
      key set + ``platform`` normalization + resolved-toolchain shape need
      ratifying.
  E2  A ``policy_violation`` (write outside ``writable_paths``) is treated as a
      tampered/invalid episode (``reward = 0``, ``validity = invalid``). Whether a
      sandbox-escape is ``invalid`` vs ``error`` is a policy call.
"""

from typing import Any, Callable, Dict, List, Mapping, Optional

from . import identity, reward, runner, verifiers

__all__ = ["eval_one", "EPISODE_VERSION"]

EPISODE_VERSION = "traaviis.episode.v1"

Verifier = Callable[..., str]


def _finding_artifact(result: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    raw = (result or {}).get("finding") or {}
    summary = raw.get("summary", "")
    citations = raw.get("citations", [])
    finding = {
        "finding_version": "residency.finding.v1",
        "claims": [{"statement": summary, "citations": citations}],
    }
    finding["finding_id"] = identity.finding_id(finding)
    return finding


def _patch_artifact(patch_text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(patch_text, str) or not patch_text.strip():
        return None
    patch = {"patch_version": "residency.patch.v1", "diff": patch_text}
    patch["patch_id"] = identity.patch_id(patch)
    return patch


def eval_one(
    task: Mapping[str, Any],
    content: Mapping[str, str],
    agent_command,
    reward_spec: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    verifier_versions: Mapping[str, str],
    extra_verifiers: Optional[Mapping[str, Verifier]] = None,
    platform: str = "unknown",
    toolchain: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one Residency episode and return the ``episode-…`` receipt dict.

    ``content`` is the normalized (LF) text map of the frozen subject; ``snapshot``
    is its sealed ``SnapshotV1``. ``agent_command`` is an argv vector. The task's
    ``agent_run_policy`` governs the controlled run.
    """
    extra_verifiers = dict(extra_verifiers or {})
    plan = task.get("verifier_plan", {})
    required = list(plan.get("required", []))
    declared_na = list(plan.get("not_applicable", []))
    policy = task.get("agent_run_policy", {})

    # 2-4: controlled run → trace + outputs.
    run = runner.run_agent(agent_command, content, policy)
    finding = _finding_artifact(run["result"])
    patch = _patch_artifact(run["patch_text"])

    # Build the total verification map over every declared signal.
    signal_ids = set(reward_spec.get("signals", {})) | set(required) \
        | set(declared_na) | {"native", "oracle"}

    def resolve(sig: str) -> str:
        if sig in ("native", "oracle"):
            return reward.NOT_APPLICABLE
        if sig == "citations":
            return verifiers.verify_citations(finding, content)
        if sig == "finding_completeness":
            return verifiers.verify_finding_completeness(finding)
        if sig == "patch":
            return reward.FAIL if patch is None else \
                verifiers.verify_patch(patch, content)
        if sig in extra_verifiers:
            return extra_verifiers[sig](run, task, content)  # tests/identity
        return reward.NOT_APPLICABLE

    verification = {sig: resolve(sig) for sig in signal_ids}

    # Substrate/policy failures → error / invalid (§6a, §10a, E2).
    if run["timed_out"] or run["output_truncated"]:
        for sig in required:
            verification[sig] = reward.ERROR
    tampered = bool(run["policy_violations"])  # E2

    score = reward.score(verification, reward_spec, required, tampered=tampered)

    execution_facts = {  # E1 — enters episode identity
        "exit_code": run["exit_code"],
        "platform": platform,
        "timed_out": run["timed_out"],
        "output_truncated": run["output_truncated"],
        "toolchain": dict(toolchain or {}),
    }

    receipt = {
        "episode_version": EPISODE_VERSION,
        "substrate_profile": task.get("substrate_profile", "residency.repository.v1"),
        "task_id": task.get("task_id") or identity.task_id(task),
        "reward_id": reward_spec.get("reward_id") or identity.reward_id(reward_spec),
        "subject": {"snapshot_id": snapshot.get("snapshot_id")
                    or identity.snapshot_id(snapshot)},
        "trace_id": run["trace"]["trace_id"],
        "outputs": {
            "finding_id": finding["finding_id"],
            "patch_id": patch["patch_id"] if patch else None,
        },
        "verification": verification,
        "verifier_versions": dict(verifier_versions),
        "reward": score["reward"],
        "status": score["status"],
        "validity": score["validity"],
        "replayability": "verification",
        "execution_facts": execution_facts,
    }
    receipt["episode_id"] = identity.episode_id(receipt)
    return receipt
