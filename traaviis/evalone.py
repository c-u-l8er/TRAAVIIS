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

import hashlib
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from . import admission, execfacts, identity, patchapply, reward, runner, signals, verifiers
from . import substrate_verifiers as _sv, toolchain as _toolchain
from .execfacts import UnsupportedPolicyError
from .vcontext import VerifierContextV1, VerifierResult

__all__ = [
    "eval_one", "evaluate", "build_receipt_v1",
    "EPISODE_VERSION", "EVALUATION_RUN_VERSION", "VERIFIER_EVIDENCE_VERSION",
    "UnsupportedPolicyError",
]

EPISODE_VERSION = "traaviis.episode.v1"

# EvaluationRunV1: the *complete* internal result of one episode — the receipt plus
# the concrete evidence artifacts (trace, finding, patch, per-signal verifier
# evidence, and the captured process bytes) that the receipt's ids/digests attest.
# ``eval_one`` returns only the receipt (backward-compatible); ``evaluate`` returns
# the whole EvaluationRunV1 so the CLI can durably persist an episode bundle.
EVALUATION_RUN_VERSION = "traaviis.evaluation-run.v1"

# One verifier's canonical evidence object. Its sha256 over canonical bytes is what
# the receipt's ``verification_evidence[sig].digest`` pins (and thus what enters
# episode-), so the saved evidence file cannot drift while the episode id holds.
VERIFIER_EVIDENCE_VERSION = "traaviis.verifier-evidence.v1"

Verifier = Callable[[VerifierContextV1], VerifierResult]


def _sha256_canonical(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(identity.canonical_bytes(obj)).hexdigest()


def _evidence_object(sig: str, state: str, detail: Mapping[str, Any]) -> Dict[str, Any]:
    """The canonical per-signal evidence object written to the episode bundle.

    Carries the signal, its final state, and the verifier's structured evidence
    detail (e.g. tests' per-command exit codes + output digests, identity's
    before/after bindings). Hashed by ``_evidence_ref`` into the digest that enters
    episode-.
    """
    return {
        "evidence_version": VERIFIER_EVIDENCE_VERSION,
        "signal": sig,
        "state": state,
        "detail": dict(detail or {}),
    }


def _evidence_ref(evidence: Mapping[str, Any]) -> Dict[str, str]:
    """The ``{format, digest}`` reference sealed into the receipt's evidence map."""
    return {"format": VERIFIER_EVIDENCE_VERSION, "digest": _sha256_canonical(evidence)}

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


def _resolve_test_plan_toolchain(
    task: Mapping[str, Any], supplied: Optional[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """The ``execution_facts.toolchain`` facts for a ``TestPlanV2`` task, or ``supplied``.

    When the task carries a ``TestPlanV2`` and the caller supplied no explicit
    toolchain, resolve its logical tools — the *same* resolution the ``tests``
    verifier performs to run the commands (both go through
    ``substrate_verifiers.test_plan_tools`` + ``toolchain.resolve_toolchain``, so they
    agree) — and seal ``{version, executable_digest}`` per tool into
    ``execution_facts.toolchain``. The host executable path is never sealed. A
    resolution failure is swallowed here: the ``tests`` verifier independently
    surfaces it as an ``error`` signal (an inadmissible fixture), and
    ``execution_facts`` simply carries no toolchain for that failed episode. An
    explicit caller ``toolchain`` always wins (it describes the agent's own toolchain).
    """
    if supplied is not None:
        return supplied
    plan = task.get("test_plan")
    if not isinstance(plan, Mapping):
        return supplied
    tools = _sv.test_plan_tools(plan)
    if not tools:
        return supplied
    try:
        facts, _executables = _toolchain.resolve_toolchain(
            plan.get("toolchain_profile"), tools)
    except _toolchain.ToolchainError:
        return supplied
    return facts


def _patch_artifact(patch_text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(patch_text, str) or not patch_text.strip():
        return None
    patch = {"patch_version": "residency.patch.v1", "diff": patch_text}
    patch["patch_id"] = identity.patch_id(patch)
    return patch


def _resolver_for(
    sig: str,
    extra_verifiers: Mapping[str, Verifier],
    live_verifiers: Optional[Mapping[str, Verifier]] = None,
) -> Optional[Verifier]:
    live = _LIVE_VERIFIERS if live_verifiers is None else live_verifiers
    if sig in live:
        return live[sig]
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
    reward_spec: Mapping[str, Any], evidence_signals: Iterable[str],
    extra_verifiers: Mapping[str, Verifier],
    live_verifiers: Optional[Mapping[str, Verifier]] = None,
) -> Dict[str, Dict[str, Optional[str]]]:
    """Seal each *evidence* signal's ``{contract, implementation}`` version pair.

    ``evidence_signals`` is every non-pseudo declared verifier — the scored reward
    signals **plus** required and declared-``not_applicable`` gates (GPT-5.6 exact
    closure): an unweighted required gate can affect validity, so its implementation
    version must enter ``episode-…`` too, not only the scored subset.

    ``contract`` is the verifier id the *reward* declares it wants (the ask), or
    ``None`` for a gate the reward does not score; ``implementation`` is the
    ``.version`` of the code actually wired to answer it (the answer), or ``None``
    when no implementation is wired. The task can NOT override the implementation's
    own declared version (GPT-5.6 ruling): the implementation half always comes from
    the wired verifier, never from the task.
    """
    spec_signals = reward_spec.get("signals", {})
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for sig in evidence_signals:
        spec = spec_signals.get(sig)
        contract = spec.get("verifier") if isinstance(spec, Mapping) else None
        implementation = _impl_version(
            _resolver_for(sig, extra_verifiers, live_verifiers))
        out[sig] = {"contract": contract, "implementation": implementation}
    return out


def _required_config_error(
    sig: str, extra_verifiers: Mapping[str, Verifier],
    live_verifiers: Optional[Mapping[str, Verifier]] = None,
) -> Optional[str]:
    """Why a *required* signal cannot resolve to a real verdict, or ``None`` if fine.

    A required signal is invalid config when no verifier is wired for it, or when
    the wired verifier declares no implementation version — either way it can only
    ever fall back to ``not_applicable`` (F4), which a required signal may not be.
    """
    if sig in _PSEUDO_SIGNALS:
        return None
    verifier = _resolver_for(sig, extra_verifiers, live_verifiers)
    if verifier is None:
        return "no verifier is wired for this required signal"
    if _impl_version(verifier) is None:
        return "wired verifier declares no implementation version"
    return None


def _assemble_receipt(
    *, substrate_profile, task_id, reward_id, snapshot_id, trace_id, outputs,
    verification, verification_evidence, verifier_versions, score, execution_facts,
) -> Dict[str, Any]:
    """Assemble the 14-key ``episode-…`` receipt and seal its ``episode_id``.

    The single point where an episode receipt is shaped. Both the live evaluation
    and the verification replay build receipts through here (via
    ``build_receipt_v1``) so the two can never structurally drift.
    """
    receipt = {
        "episode_version": EPISODE_VERSION,
        "substrate_profile": substrate_profile,
        "task_id": task_id,
        "reward_id": reward_id,
        "subject": {"snapshot_id": snapshot_id},
        "trace_id": trace_id,
        "outputs": outputs,
        "verification": dict(verification),
        "verification_evidence": dict(verification_evidence),
        "verifier_versions": dict(verifier_versions),
        "reward": score["reward"],
        "status": score["status"],
        "validity": score["validity"],
        "replayability": "verification",
        "execution_facts": execution_facts,
    }
    receipt["episode_id"] = identity.episode_id(receipt)
    return receipt


def build_receipt_v1(
    *, substrate_profile, task_id, reward_id, snapshot_id, verifier_versions,
    trace_id, finding, patch, evidence_signals, verification, results,
    reward_spec, required, tampered, execution_facts,
) -> "tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]":
    """ReceiptBuilderV1 — derive a complete receipt (+ evidence) from resolved parts.

    Shared by live evaluation (``evaluate``) and verification replay
    (``episode_bundle.verify_episode_bundle``) so a receipt produced live and the
    receipt re-derived on replay are byte-identical whenever the evidence agrees.

    Inputs are the already-resolved pieces: the total ``verification`` state map
    (all declared signals), the per-signal ``results`` (for evidence detail), the
    ``evidence_signals`` to seal evidence + versions for (every non-pseudo declared
    verifier — scored signals plus required / declared-``not_applicable`` gates, per
    GPT-5.6 exact closure), the reward inputs, the ``tampered`` flag, and the
    reconstructed ``execution_facts``. The reward number is still scored from
    ``reward_spec.signals`` alone — an unweighted gate contributes evidence, not
    reward. Returns ``(receipt, verifier_evidence)`` where ``verifier_evidence`` is
    the per-signal canonical evidence object map the bundle persists.
    """
    score = reward.score(verification, reward_spec, required, tampered=tampered)

    verifier_evidence: Dict[str, Dict[str, Any]] = {}
    verification_evidence: Dict[str, Dict[str, str]] = {}
    for sig in evidence_signals:
        detail = results[sig].detail if sig in results else {}
        ev = _evidence_object(
            sig, verification.get(sig, reward.NOT_APPLICABLE), detail)
        verifier_evidence[sig] = ev
        verification_evidence[sig] = _evidence_ref(ev)

    outputs = {
        "finding_id": finding["finding_id"] if finding else None,
        "patch_id": patch["patch_id"] if patch else None,
    }
    receipt = _assemble_receipt(
        substrate_profile=substrate_profile,
        task_id=task_id,
        reward_id=reward_id,
        snapshot_id=snapshot_id,
        trace_id=trace_id,
        outputs=outputs,
        verification=verification,
        verification_evidence=verification_evidence,
        verifier_versions=verifier_versions,
        score=score,
        execution_facts=execution_facts,
    )
    return receipt, verifier_evidence


def _run_v1(receipt: Any, artifacts: Any) -> Dict[str, Any]:
    """Wrap a receipt + its artifacts as an ``EvaluationRunV1``."""
    return {
        "evaluation_run_version": EVALUATION_RUN_VERSION,
        "receipt": receipt,
        "artifacts": artifacts,
    }


def _admit_episode(
    task: Mapping[str, Any],
    content: Mapping[str, str],
    reward_spec: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    extra_verifiers: Optional[Mapping[str, Verifier]] = None,
    runner_profile: str = runner.RUNNER_PROFILE,
) -> Dict[str, Any]:
    """Everything provable *before* an agent runs. Returns the admitted plan.

    This is the kernel's ``start`` phase (``traaviis.kernel``). It performs, in
    the frozen order: signal-id admission, evidence-signal derivation, declared-id
    verification, subject binding, task cross-binding, run-policy honesty, the
    ``{contract, implementation}`` verifier-version seal, and the F4 configuration
    preflight. It runs nothing and writes nothing.

    The returned plan is an in-process record, not an artifact: it is never
    hashed, never serialized, and mints no id. ``plan["unresolved"]`` is the F4
    verdict — a non-empty list means the agent must not run and
    ``_invalid_config_run`` already knows the receipt.

    Raises ``admission.AdmissionError`` if a declared id is wrong, the task does
    not reference the supplied reward/snapshot, or ``content`` does not bind to
    ``snapshot``. Raises ``UnsupportedPolicyError`` if the run policy demands a
    guarantee the trusted-local runner does not deliver.
    """
    extra_verifiers = dict(extra_verifiers or {})
    plan = task.get("verifier_plan", {})
    required = list(plan.get("required", []))
    declared_na = list(plan.get("not_applicable", []))
    policy = task.get("agent_run_policy", {})

    # --- SignalIDV1 admission (exact closure) ---------------------------------
    # Every signal id that will become a receipt/manifest key or an evidence file
    # stem must match the frozen grammar before it is used to build any path.
    signals.validate_signal_ids(reward_spec.get("signals", {}), where="reward signal")
    signals.validate_signal_ids(required, where="required signal")
    signals.validate_signal_ids(declared_na, where="not_applicable signal")
    signals.validate_signal_ids(extra_verifiers, where="extra_verifier signal")

    # Evidence + versions cover every non-pseudo declared verifier — the scored
    # reward signals plus required / declared-not_applicable gates — so an
    # unweighted gate's implementation version + evidence digest enter episode-.
    evidence_signals = sorted(
        (set(reward_spec.get("signals", {})) | set(required) | set(declared_na))
        - set(_PSEUDO_SIGNALS))

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
    execfacts.validate_run_policy(policy, runner_profile)

    # Seal each evidence signal's {contract, implementation} version pair from the
    # reward's ask + the wired verifier's own declared version (never the task's).
    verifier_versions = _verifier_versions_map(
        reward_spec, evidence_signals, extra_verifiers)

    substrate_profile = task.get("substrate_profile", "residency.repository.v1")

    # --- Config preflight (F4): a required signal that cannot resolve is invalid -
    # Either no verifier is wired for it, or the wired verifier declares no
    # implementation version — both leave it stuck at not_applicable, which a
    # required signal may never be. A non-empty `unresolved` means the agent must
    # not run at all; the receipt is already determined (`_invalid_config_run`).
    unresolved = [s for s in required if _required_config_error(s, extra_verifiers)]

    return {
        "task": task,
        "content": content,
        "reward_spec": reward_spec,
        "snapshot": snapshot,
        "policy": policy,
        "required": required,
        "declared_na": declared_na,
        "evidence_signals": evidence_signals,
        "extra_verifiers": extra_verifiers,
        "task_id": task_id_v,
        "reward_id": reward_id_v,
        "snapshot_id": snap_id,
        "verifier_versions": verifier_versions,
        "substrate_profile": substrate_profile,
        "unresolved": unresolved,
    }


def _invalid_config_run(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """The complete ``EvaluationRunV1`` for an invalid-config episode (F4).

    Invalid task configuration: the agent is refused, so there is no trace, no
    outputs, no execution and no evidence — and therefore nothing that could
    compete in post-run precedence. ``artifacts`` is ``None`` because there are
    genuinely none, not because they were dropped.
    """
    receipt = _assemble_receipt(
        substrate_profile=plan["substrate_profile"],
        task_id=plan["task_id"],
        reward_id=plan["reward_id"],
        snapshot_id=plan["snapshot_id"],
        trace_id=None,
        outputs={"finding_id": None, "patch_id": None},
        verification={s: reward.NOT_APPLICABLE for s in plan["unresolved"]},
        verification_evidence={},
        verifier_versions=plan["verifier_versions"],
        score={"reward": None, "status": reward.STATUS_INVALID,
               "validity": reward.INVALID},
        execution_facts=None,
    )
    return _run_v1(receipt, None)


def _finish_episode(
    plan: Mapping[str, Any],
    run: Mapping[str, Any],
    *,
    platform: Any = "unknown",
    toolchain: Optional[Mapping[str, Any]] = None,
    runner_profile: str = runner.RUNNER_PROFILE,
) -> Dict[str, Any]:
    """Everything that happens *after* the agent ran. Returns an ``EvaluationRunV1``.

    This is the kernel's ``finalize`` phase (``traaviis.kernel``). ``run`` is the
    ``runner.RunResult`` — the only input that could not be known at admission.
    It reads the finding and patch, applies the candidate diff to a fresh copy of
    the sealed content, runs every declared signal through the uniform
    ``VerifierContextV1`` seam, applies the substrate-run-failure override, resolves
    the test-plan toolchain, builds ``execution_facts`` and seals the receipt through
    the one shared ``build_receipt_v1``.

    It launches nothing. Splitting the pipeline here is what makes the kernel
    substrate-neutral: everything above this line is "what an episode is", and
    the one thing between the two phases is "how an agent is invoked".
    """
    task = plan["task"]
    content = plan["content"]
    reward_spec = plan["reward_spec"]
    snapshot = plan["snapshot"]
    policy = plan["policy"]
    required = plan["required"]
    declared_na = plan["declared_na"]
    extra_verifiers = plan["extra_verifiers"]

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

    def resolve(sig: str) -> VerifierResult:
        if sig in _PSEUDO_SIGNALS:
            return VerifierResult(reward.NOT_APPLICABLE)
        verifier = _resolver_for(sig, extra_verifiers)
        if verifier is None:
            return VerifierResult(reward.NOT_APPLICABLE)
        return verifier(context)

    results = {sig: resolve(sig) for sig in signal_ids}
    verification = {sig: res.state for sig, res in results.items()}

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

    # TestPlanV2: the resolved test-plan toolchain (logical tool → concrete host
    # version + binary digest) enters execution_facts — attested, not identity in
    # the task. Identical to the resolution the tests verifier ran to execute the
    # commands. An explicit caller-supplied toolchain (the agent's own) takes
    # precedence.
    toolchain = _resolve_test_plan_toolchain(task, toolchain)

    execution_facts = execfacts.build_execution_facts(
        run, runner_profile=runner_profile, platform=platform, toolchain=toolchain)

    # One evidence object per *scored* reward signal is sealed into the receipt by
    # build_receipt_v1 — the same builder the verification replay uses, so a live
    # receipt and a re-derived one cannot drift.
    receipt, verifier_evidence = build_receipt_v1(
        substrate_profile=plan["substrate_profile"],
        task_id=plan["task_id"],
        reward_id=plan["reward_id"],
        snapshot_id=plan["snapshot_id"],
        verifier_versions=plan["verifier_versions"],
        trace_id=run["trace"]["trace_id"],
        finding=finding,
        patch=patch,
        evidence_signals=plan["evidence_signals"],
        verification=verification,
        results=results,
        reward_spec=reward_spec,
        required=required,
        tampered=tampered,
        execution_facts=execution_facts,
    )

    artifacts = {
        "trace": run["trace"],
        "finding": finding,
        "patch": patch,
        "verifier_evidence": verifier_evidence,
        "process": {
            "stdout": run["stdout"],
            "stderr": run["stderr"],
            "stdout_truncated": run["stdout_truncated"],
            "stderr_truncated": run["stderr_truncated"],
            # The observed write-escape list (R3/E2). Persisted so replay can
            # re-attest it against the trace's policy_violations_digest and
            # reconstruct the tampered verdict (a nonempty list → tampered).
            "policy_violations": list(run["policy_violations"]),
        },
    }
    return _run_v1(receipt, artifacts)


def evaluate(
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
    """Run one Residency episode and return the complete ``EvaluationRunV1``.

    The returned dict is ``{evaluation_run_version, receipt, artifacts}``:

      * ``receipt``   — the ``episode-…`` receipt (see ``eval_one`` for the
                        receipt-only view).
      * ``artifacts`` — the concrete evidence the receipt attests: ``trace``,
                        ``finding`` / ``patch`` (or ``None``), the per-signal
                        ``verifier_evidence`` objects (whose canonical digests are
                        sealed into ``receipt.verification_evidence``), and the
                        captured ``process`` bytes (``stdout`` / ``stderr`` and
                        their truncation flags). ``artifacts`` is ``None`` for an
                        invalid-config episode that never ran the agent (F4).

    ``content`` is the normalized (LF) text/bytes map of the frozen subject;
    ``snapshot`` is its sealed ``SnapshotV1``. ``agent_command`` is an argv vector.
    The task's ``agent_run_policy`` governs the controlled run. ``extra_verifiers``
    inject the substrate verifiers (``tests`` / ``identity``) as
    ``(context) -> VerifierResult``; each verifier's ``.version`` becomes the
    ``implementation`` half of its sealed ``verifier_versions`` entry.

    Since the Episode Kernel Closure this function is the **local command
    adapter**, not the pipeline: it opens a one-task kernel over the admitted
    inputs and drives ``start`` → ``runner.run_agent`` → ``finalize``. The receipt
    it returns is byte-for-byte the receipt it returned before, because the two
    kernel phases are the same code in the same order with the subprocess lifted
    out from between them.

    Raises ``admission.AdmissionError`` if a declared id is wrong, the task does not
    reference the supplied reward/snapshot, or ``content`` does not bind to
    ``snapshot`` — the receipt would otherwise lie about its inputs. Raises
    ``UnsupportedPolicyError`` if the run policy demands a guarantee the trusted-local
    runner does not deliver (e.g. a network sandbox).
    """
    # Imported here, not at module scope: `kernel` imports the three phase
    # functions above, so a top-level import would be a cycle. The kernel owns
    # the session lifecycle; this module owns the episode.
    from . import kernel as _kernel

    k = _kernel.local_kernel(
        task, content, reward_spec, snapshot=snapshot,
        extra_verifiers=extra_verifiers, platform=platform,
        toolchain=toolchain, runner_profile=runner_profile)
    return _kernel.run_episode(k, k.list_tasks()[0], agent_command)


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
    """Run one Residency episode and return only the ``episode-…`` receipt dict.

    Backward-compatible thin wrapper over ``evaluate`` for callers that want the
    receipt alone; ``evaluate`` returns the complete ``EvaluationRunV1`` (receipt +
    evidence artifacts) the CLI persists into a durable episode bundle.
    """
    return evaluate(
        task, content, agent_command, reward_spec,
        snapshot=snapshot, extra_verifiers=extra_verifiers,
        platform=platform, toolchain=toolchain, runner_profile=runner_profile,
    )["receipt"]
