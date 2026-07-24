"""`trvs eval-one` orchestrator — the one-shot Residency evaluation pipeline.

Wires the frozen steps of ``RFC_EVIDENCE_RESIDENCY.md`` §10 into a single
deterministic episode: **admit** the subject (verify declared ids + bind the
snapshot to the working content) → **preflight** the reward configuration →
run the agent under policy → capture the trace → read the finding + patch →
run every verifier through the uniform ``VerifierContextV1`` seam → score the
reward → emit the ``episode-…`` receipt. Every identity is content-addressed via
``traaviis.identity``; scoring is the pure engine in ``traaviis.reward``.

GPT-5.6 Eval-One Closure rulings implemented here:

  * **Admission before execution.** A declared ``reward_id`` / ``task_id`` /
    ``snapshot_id`` is recomputed and reconciled (``admission.verify_declared_id``);
    the working ``content`` is proven to be exactly the sealed subject
    (``admission.admit_subject`` → ``verify_materialization``); and the task is
    cross-bound to the *verified* reward + snapshot it references
    (``admission.cross_bind_task``) so a fabricated ``rew-…`` / ``snap-…`` can never
    be scored against unrelated inputs. Any mismatch, or an absolute / ``..`` path,
    raises ``AdmissionError`` and no agent runs.

  * **Config preflight (F4).** If a *required* signal has no live or injected
    verifier — or the wired verifier declares no implementation version — it can
    never resolve to anything but ``not_applicable``, an invalid task configuration.
    That is caught **before** the agent runs and returns an invalid receipt
    (``status = invalid`` / ``reward = None``), so invalid config never competes in
    post-run precedence.

  * **Honest run policy (R2).** The trusted-local runner is not a sandbox; a policy
    demanding an enforcement it can't deliver (e.g. a network sandbox) is rejected
    at preflight (``UnsupportedPolicyError``) rather than sealing a false
    ``execution_facts`` sandbox label.

  * **Verifier versions ``{contract, implementation}``.** Each scored signal seals
    both the reward's declared verifier *contract* and the *implementation* version
    (``.version``) of the code actually wired to answer it; the task cannot override
    the implementation half.

  * **Uniform verifier interface (step 10).** Every verifier — pure or substrate —
    is called as ``verifier(context) -> VerifierResult``. The three pure verifiers
    come from ``traaviis.verifiers``; ``tests`` / ``identity`` are injected via
    ``extra_verifiers`` (see ``traaviis.substrate_verifiers``). Any signal without a
    resolver defaults to ``not_applicable``.

  * **Agent-result validation (blocker 7).** A result file that parses to a JSON
    list or scalar (not an object) never crashes finding construction — it yields an
    empty finding, which the verifiers score as ``fail``.

  * **Substrate run failure → error (§6a, exit-code semantics).** A timeout,
    truncated output, or an exit code outside ``allowed_exit_codes`` (default
    ``[0]``) is substrate unavailability: the run-dependent signals report ``error``
    so the episode is ``status = error`` / ``reward = None``, never a false ``fail``.

  * **execution_facts.v1 (E1).** The run-environment facts are the versioned,
    structured object built by ``traaviis.execfacts`` (honest sandbox labels, R2).

Post-run precedence (F4) is owned by ``reward.score``: tamper → error → normal.
A write outside ``writable_paths`` is *observed* (R3) and, when present, marks the
episode tampered (``reward = 0`` / ``validity = invalid``).
"""

from typing import Any, Callable, Dict, Mapping, Optional

from . import admission, execfacts, identity, patchapply, reward, runner, verifiers
from .vcontext import VerifierContextV1, VerifierResult

__all__ = ["eval_one", "EPISODE_VERSION", "UnsupportedPolicyError"]

EPISODE_VERSION = "traaviis.episode.v1"

Verifier = Callable[[VerifierContextV1], VerifierResult]


class UnsupportedPolicyError(Exception):
    """The task's run policy asks for a guarantee the trusted-local runner can't give.

    The runner is a *trusted-local* process runner, not a sandbox: it observes
    filesystem writes by rescan and applies no network isolation. A task that
    requests, e.g., ``network:"disabled"`` is demanding an enforcement this runner
    does not deliver — accepting it would seal a dishonest ``execution_facts``
    sandbox label. Such a policy is rejected at preflight, before the agent runs.
    """

# The three substrate-independent verifiers, wired live through the uniform seam.
_LIVE_VERIFIERS: Dict[str, Verifier] = {
    "citations": verifiers.citations_verifier,
    "patch": verifiers.patch_verifier,
    "finding_completeness": verifiers.finding_completeness_verifier,
}

# Non-scored bookkeeping verifiers that always resolve not-applicable here.
_PSEUDO_SIGNALS = ("native", "oracle")


def _finding_artifact(result: Any) -> Dict[str, Any]:
    """Build a ``FindingV1`` from the agent result, tolerating malformed JSON.

    A result that is not a JSON object — a list, a scalar, or ``None`` — yields an
    empty finding (blocker 7): never a crash, and the verifiers score it ``fail``.
    """
    raw = result.get("finding") if isinstance(result, Mapping) else None
    if not isinstance(raw, Mapping):
        raw = {}
    summary = raw.get("summary", "")
    citations = raw.get("citations", [])
    if not isinstance(citations, list):
        citations = []
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


def _resolver_for(sig: str, extra_verifiers: Mapping[str, Verifier]) -> Optional[Verifier]:
    if sig in _LIVE_VERIFIERS:
        return _LIVE_VERIFIERS[sig]
    if sig in extra_verifiers:
        return extra_verifiers[sig]
    return None


def _impl_version(verifier: Optional[Verifier]) -> Optional[str]:
    """The implementation version the wired verifier declares (``.version``), or None.

    A verifier that is unwired, or wired but carries no ``.version``, has no
    implementation version — for a *required* signal that is an invalid config.
    """
    if verifier is None:
        return None
    version = getattr(verifier, "version", None)
    return version if isinstance(version, str) else None


def _verifier_versions_map(
    reward_spec: Mapping[str, Any], extra_verifiers: Mapping[str, Verifier],
) -> Dict[str, Dict[str, Optional[str]]]:
    """Seal each scored signal's ``{contract, implementation}`` version pair.

    ``contract`` is the verifier id the *reward* declares it wants (the ask);
    ``implementation`` is the ``.version`` of the code actually wired to answer it
    (the answer), or ``None`` when no implementation is wired. The task can NOT
    override the implementation's own declared version (GPT-5.6 ruling): the
    implementation half always comes from the wired verifier, never from the task.
    """
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for sig, spec in reward_spec.get("signals", {}).items():
        contract = spec.get("verifier") if isinstance(spec, Mapping) else None
        implementation = _impl_version(_resolver_for(sig, extra_verifiers))
        out[sig] = {"contract": contract, "implementation": implementation}
    return out


def _required_config_error(
    sig: str, extra_verifiers: Mapping[str, Verifier],
) -> Optional[str]:
    """Why a *required* signal cannot resolve to a real verdict, or ``None`` if fine.

    A required signal is invalid config when no verifier is wired for it, or when
    the wired verifier declares no implementation version — either way it can only
    ever fall back to ``not_applicable`` (F4), which a required signal may not be.
    """
    if sig in _PSEUDO_SIGNALS:
        return None
    verifier = _resolver_for(sig, extra_verifiers)
    if verifier is None:
        return "no verifier is wired for this required signal"
    if _impl_version(verifier) is None:
        return "wired verifier declares no implementation version"
    return None


def _check_run_policy(policy: Mapping[str, Any], runner_profile: str) -> None:
    """Reject a run policy the trusted-local runner cannot honestly satisfy.

    The runner profile states the sandbox guarantee it actually delivers (see
    ``execfacts.RUNNER_PROFILES``). A task may only request the network posture the
    profile really provides (``unrestricted`` for the trusted-local runner) — or
    leave it unset. Any stronger request (e.g. ``disabled``) is refused *before*
    the agent runs, so no episode ever seals a sandbox label it did not enforce.
    """
    caps = execfacts.RUNNER_PROFILES.get(runner_profile)
    if caps is None:
        raise UnsupportedPolicyError(f"unknown runner profile {runner_profile!r}")
    requested = policy.get("network")
    supported = caps["network"]
    if requested is not None and requested != supported:
        raise UnsupportedPolicyError(
            f"runner profile {runner_profile!r} is a trusted-local process runner, "
            f"not a sandbox: it cannot honor network={requested!r} (it provides only "
            f"network={supported!r}). Declare network={supported!r} + "
            f"runner_profile={runner_profile!r} for an honest episode."
        )


def eval_one(
    task: Mapping[str, Any],
    content: Mapping[str, str],
    agent_command,
    reward_spec: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    extra_verifiers: Optional[Mapping[str, Verifier]] = None,
    platform: Any = "unknown",
    toolchain: Optional[Mapping[str, Any]] = None,
    runner_profile: str = runner.RUNNER_PROFILE,
) -> Dict[str, Any]:
    """Run one Residency episode and return the ``episode-…`` receipt dict.

    ``content`` is the normalized (LF) text/bytes map of the frozen subject;
    ``snapshot`` is its sealed ``SnapshotV1``. ``agent_command`` is an argv vector.
    The task's ``agent_run_policy`` governs the controlled run. ``extra_verifiers``
    inject the substrate verifiers (``tests`` / ``identity``) as
    ``(context) -> VerifierResult``; each verifier's ``.version`` becomes the
    ``implementation`` half of its sealed ``verifier_versions`` entry.

    Raises ``admission.AdmissionError`` if a declared id is wrong, the task does not
    reference the supplied reward/snapshot, or ``content`` does not bind to
    ``snapshot`` — the receipt would otherwise lie about its inputs. Raises
    ``UnsupportedPolicyError`` if the run policy demands a guarantee the trusted-local
    runner does not deliver (e.g. a network sandbox).
    """
    extra_verifiers = dict(extra_verifiers or {})
    plan = task.get("verifier_plan", {})
    required = list(plan.get("required", []))
    declared_na = list(plan.get("not_applicable", []))
    policy = task.get("agent_run_policy", {})

    # --- Preflight admission: verify declared ids + bind the subject ----------
    reward_id_v = admission.verify_declared_id(
        reward_spec, "reward_id", identity.reward_id)
    task_id_v = admission.verify_declared_id(task, "task_id", identity.task_id)
    snap_id = admission.admit_subject(
        snapshot, content, binary_paths=snapshot.get("binary_paths", ()))
    # Each artifact is now internally consistent; prove the task actually references
    # *these* verified artifacts (never a fabricated rew-…/snap-… against unrelated
    # inputs) before anything runs.
    admission.cross_bind_task(task, reward_id_v, snap_id)

    # --- Preflight policy: refuse a run posture the runner can't honestly keep --
    _check_run_policy(policy, runner_profile)

    # Seal each scored signal's {contract, implementation} version pair from the
    # reward's ask + the wired verifier's own declared version (never the task's).
    verifier_versions = _verifier_versions_map(reward_spec, extra_verifiers)

    substrate_profile = task.get("substrate_profile", "residency.repository.v1")

    def _receipt(*, trace_id, outputs, verification, score, execution_facts):
        receipt = {
            "episode_version": EPISODE_VERSION,
            "substrate_profile": substrate_profile,
            "task_id": task_id_v,
            "reward_id": reward_id_v,
            "subject": {"snapshot_id": snap_id},
            "trace_id": trace_id,
            "outputs": outputs,
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

    # --- Config preflight (F4): a required signal that cannot resolve is invalid -
    # Either no verifier is wired for it, or the wired verifier declares no
    # implementation version — both leave it stuck at not_applicable, which a
    # required signal may never be.
    unresolved = [s for s in required if _required_config_error(s, extra_verifiers)]
    if unresolved:
        # Invalid task configuration: refuse to run the agent (F4). No trace, no
        # outputs, no execution — this never competes in post-run precedence.
        return _receipt(
            trace_id=None,
            outputs={"finding_id": None, "patch_id": None},
            verification={s: reward.NOT_APPLICABLE for s in unresolved},
            score={"reward": None, "status": reward.STATUS_INVALID,
                   "validity": reward.INVALID},
            execution_facts=None,
        )

    # --- Controlled run → trace + outputs (steps 2-4) -------------------------
    run = runner.run_agent(agent_command, content, policy)
    finding = _finding_artifact(run["result"])
    patch = _patch_artifact(run["patch_text"])

    # Apply the candidate patch to a fresh copy of the sealed content so substrate
    # verifiers (tests / identity) see the patched tree; a bad diff → no patched
    # tree, which those verifiers score honestly.
    patched_content: Optional[Dict[str, str]] = None
    if patch is not None:
        try:
            patched_content = patchapply.apply_unified_diff(content, patch["diff"])
        except patchapply.PatchError:
            patched_content = None

    context = VerifierContextV1(
        task=task,
        snapshot=snapshot,
        original_content=content,
        run=run,
        finding=finding,
        patch=patch,
        patched_content=patched_content,
    )

    # --- Total verification map over every declared signal (uniform seam) -----
    signal_ids = set(reward_spec.get("signals", {})) | set(required) \
        | set(declared_na) | set(_PSEUDO_SIGNALS)

    def resolve(sig: str) -> str:
        if sig in _PSEUDO_SIGNALS:
            return reward.NOT_APPLICABLE
        verifier = _resolver_for(sig, extra_verifiers)
        if verifier is None:
            return reward.NOT_APPLICABLE
        return verifier(context).state

    verification = {sig: resolve(sig) for sig in signal_ids}

    # --- Substrate run failure → error (§6a, exit-code semantics) -------------
    allowed_exit_codes = list(policy.get("allowed_exit_codes", [0]))
    bad_exit = (not run["timed_out"]) and run["exit_code"] not in allowed_exit_codes
    run_error = run["timed_out"] or run["output_truncated"] or bad_exit
    if run_error:
        # Every signal here consumes the run's outputs; a substrate-level run
        # failure is unavailability (error), not evidence of a wrong answer.
        for sig in verification:
            if sig not in _PSEUDO_SIGNALS:
                verification[sig] = reward.ERROR

    tampered = bool(run["policy_violations"])  # R3/E2: observed write escape

    score = reward.score(verification, reward_spec, required, tampered=tampered)

    execution_facts = execfacts.build_execution_facts(
        run, runner_profile=runner_profile, platform=platform, toolchain=toolchain)

    return _receipt(
        trace_id=run["trace"]["trace_id"],
        outputs={
            "finding_id": finding["finding_id"],
            "patch_id": patch["patch_id"] if patch else None,
        },
        verification=verification,
        score=score,
        execution_facts=execution_facts,
    )
