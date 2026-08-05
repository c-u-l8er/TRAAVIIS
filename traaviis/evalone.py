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

  * **A verifier that fails cannot erase the episode (``resolve_signal``).** Every
    verifier call goes through one guarded seam. A verifier that *raises*, or that
    *returns* something the ``VerifierResult`` contract refuses, becomes verification
    ``error`` with structured evidence naming which of the two happened — and the
    remaining signals are still resolved, so the verification map stays total. The
    episode is then ``status = error`` / ``validity = invalid`` / ``reward = None``
    by ``reward.score``'s existing F2 rule, **and it is emitted**: receipt, trace,
    per-signal evidence and bundle all survive. This is the fourth route into the
    grade-erasure class, closed the same way the first three were (malformed
    citations, deep JSON, non-UTF-8 filename): *the failure is classified and
    scored, never escalated into a crash that leaves nothing behind.*

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
import traceback
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from . import admission, execfacts, identity, patchapply, reward, runner, signals, verifiers
from . import substrate_verifiers as _sv, toolchain as _toolchain
from .execfacts import UnsupportedPolicyError
from .vcontext import VerifierContextV1, VerifierResult

__all__ = [
    "eval_one", "evaluate", "build_receipt_v1", "resolve_signal",
    "EPISODE_VERSION", "EVALUATION_RUN_VERSION", "VERIFIER_EVIDENCE_VERSION",
    "ERROR_ORIGIN_VERIFIER_EXCEPTION", "ERROR_ORIGIN_VERIFIER_PROTOCOL",
    "ERROR_CODE_VERIFIER_RAISED", "ERROR_CODE_INVALID_VERIFIER_RESULT",
    "RESULT_VIOLATIONS",
    "UnsupportedPolicyError",
]

#: The episode schema **newly minted** receipts declare, and therefore the
#: canonicalization they are sealed under (`identity.EPISODE_SCHEMES` maps one
#: to the other). Legacy receipts keep declaring whatever they were minted with
#: and keep verifying under it forever; this constant governs new episodes only.
#:
#: **It is deliberately still `v1`, and the reason is a measured prerequisite
#: rather than caution.** `build_receipt_v1` is shared by live evaluation *and*
#: by verification replay (`episode_bundle.verify_episode_bundle` rebuilds the
#: receipt through it and requires byte equality with the stored one). Replay
#: does not pass a version, so it stamps whatever this constant says. Flip this
#: line alone and every already-sealed v1 bundle stops closing: the derived
#: receipt would say `v2`, the stored one `v1`, and `checks["receipt"]` would
#: report "derived receipt differs from stored" on evidence that is perfectly
#: intact. Verified by execution, not by reading — see `test_evalone.py` E-B4,
#: which flips the constant in an isolated copy of the package and watches the
#: golden episode fail to close.
#:
#: So the cutover is one line *plus* one: `episode_bundle` must pass the stored
#: receipt's own declared `episode_version` into `build_receipt_v1`, so that
#: replay reproduces the document it is checking rather than the document this
#: build would mint today. That is the correct shape regardless — a verifier
#: that re-derives under its own current defaults is checking the wrong thing —
#: and `episode_bundle.py` is outside this change's ownership. The parameter it
#: needs already exists below; the caller is what is missing. E-B4 fails the
#: moment this constant moves without that caller, so the prerequisite cannot be
#: skipped by someone who only reads this comment.
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

# --- Error origin: WHICH LAYER failed, sealed as evidence ---------------------
#
# `reward` freezes exactly four verifier *states* — `pass | fail | not_applicable
# | error` — and this module adds none. **Origin is an orthogonal axis over the
# single state `error`**: it says which layer stopped working, while the state
# says what the rubric may conclude. Four origins are named in
# `traaviis.coverage`; two of them are *produced* here, and only those two are
# ever written into evidence:
#
#   verifier_exception   a wired verifier raised while evaluating
#   verifier_protocol    a wired verifier returned an object the result contract
#                        refuses (wrong type, unknown state, unhashable detail)
#
# The other two — `verifier_reported` (the verifier itself returned
# `VerifierResult(ERROR)`) and `substrate_override` (the run-level rule in
# `_finish_episode` overwrote every non-pseudo signal) — are **derived** by the
# reader from the receipt plus the task, and are deliberately NOT stamped into
# any evidence detail. That is not tidiness: stamping them would add bytes to the
# evidence of episodes that already exist, and every one of those bytes is inside
# `episode-…`. Because the two origins below are produced only on inputs that
# previously escaped as an uncaught exception, **no episode that has ever been
# sealed can gain them**, and no id can move. Same argument as
# `_finding_artifact`'s fallback.
#
# Prior art. JUnit XML has drawn exactly this line since Ant: `<failure>` is "a
# condition which the code has explicitly failed by using the mechanisms for that
# purpose", while `<error>` is "an unanticipated problem ... or a problem with
# the implementation of the test" — i.e. checker-side defects belong to `error`,
# never to `failure`. Its `type` attribute holds "the full class name of the
# exception", which is what `exception_type` is here. SARIF v2.1.0 makes the same
# split structural (a `notification` "describes a condition relevant to the tool
# itself, as opposed to being relevant to a target being analyzed by the tool")
# and, decisively for this design, keeps the volatile parts — `exception.stack`,
# `threadId`, `timeUtc` — on the notification, never on the `result` that gets
# fingerprinted. `_diagnostics` below is that same placement.
ERROR_ORIGIN_VERIFIER_EXCEPTION = "verifier_exception"
ERROR_ORIGIN_VERIFIER_PROTOCOL = "verifier_protocol"

#: The stable, host-independent code sealed for each. A *code*, not a message:
#: it names the law broken and nothing about the run that broke it.
ERROR_CODE_VERIFIER_RAISED = "VERIFIER_RAISED"
ERROR_CODE_INVALID_VERIFIER_RESULT = "INVALID_VERIFIER_RESULT"

#: How a returned object can fail the ``VerifierResult`` contract. One frozen
#: enumeration, sealed as ``violation`` beside the single ``INVALID_VERIFIER_RESULT``
#: code, so the four are distinguishable without four codes to keep in sync.
#: ``detail_not_canonical`` is the one that is not merely hygiene: a detail the
#: canonicalizer refuses would raise inside ``build_receipt_v1``, *after* this
#: seam and outside every guard, and erase the episode exactly as a raising
#: verifier did. It is caught here, at the boundary, by offering the detail to the
#: real hasher rather than re-deriving its rules — the same discipline as
#: ``_finding_artifact``.
RESULT_VIOLATIONS = (
    "not_a_verifier_result",   # not a VerifierResult at all
    "unknown_state",           # a VerifierResult whose state is not one of the four
    "detail_not_mapping",      # .detail is not a mapping
    "detail_not_canonical",    # .detail cannot be sealed by identity.canonical_bytes
)

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


def _finding_document(summary: Any, citations: Any) -> Dict[str, Any]:
    """The unsealed ``FindingV1`` body. Byte-identical to what this always built."""
    return {
        "finding_version": "residency.finding.v1",
        "claims": [{"statement": summary, "citations": citations}],
    }


def _finding_artifact(result: Any) -> Dict[str, Any]:
    """Build a ``FindingV1`` from the agent result, tolerating malformed JSON.

    A result that is not a JSON object — a list, a scalar, or ``None`` — yields an
    empty finding (blocker 7): never a crash, and the verifiers score it ``fail``.

    **One of the places the candidate's bytes reach the identity spine is here,
    so this is where *these* bytes are stopped from breaking it.** The stronger
    sentence this used to carry — "the candidate's bytes reach the identity
    spine here, so this is where they are stopped" — read as a statement of
    coverage and was false: an agent also reaches the spine through
    ``runner._scan``, which walks the workspace after the run and puts the
    *filenames it finds* into the trace's ``files_*`` digests. That route needs
    no ``result.json`` at all (see ``runner._evidence_name``). This guard is one
    caller's answer to one input; it is not the boundary.

    ``summary`` and ``citations`` come verbatim
    out of the agent's own ``result.json``; ``finding_id`` is inside
    ``identity._EPISODE_IDENTITY_KEYS``; so the agent *being evaluated* chooses
    bytes that are hashed into the evaluator's episode id. Some of those bytes
    are not hashable, and ordinary ``json.loads`` produces them: a ``"\\ud800"``
    escape parses to a lone surrogate that ``canonical_bytes`` cannot encode, a
    bare ``NaN`` token parses to a float the canonicalizer refuses
    (``CANONICAL_NON_FINITE``), and a citation nested a few tens of thousands
    deep parses fine and then exhausts the serializer's stack. All three were
    verified reachable from a plain agent-written result file.

    Before this guard the refusal escaped ``eval_one`` uncaught: no receipt, no
    episode bundle, no score — a **candidate could crash its own evaluation to
    avoid being scored by it**, and a crashed run is not comparable, so the bad
    score simply never existed. That is a reward-hacking surface, and it is
    closed by classifying the input the way §10a already classifies every other
    malformed agent output: *a malformed submission is a fact about the
    candidate.* It is scored, not escalated. ``error``/exit 2 is reserved for
    substrate unavailability, and an agent writing a lone surrogate is not the
    substrate being unavailable — it is the agent's answer.

    So an unhashable finding collapses to the **empty finding**, which is exactly
    what blocker 7 already yields for a result that is not an object, and which
    ``verify_citations`` and ``verify_finding_completeness`` both score ``fail``.
    The episode completes, the reward is capped by the citations floor, and the
    receipt is minted and persisted — which is the whole of what is claimed
    here, and less than an earlier draft claimed.

    **The receipt does not record *why* the finding is empty, and must not.**
    All three refusal kinds — and a submission that was simply never a JSON
    object — collapse to the *byte-identical* empty finding
    (``finding-62466b21…``), so ``outputs.finding_id`` cannot tell them apart.
    That is the design, not a gap in it: ``test_evalone``'s
    ``test_a_pathologically_nested_result_scores_what_an_empty_one_scores``
    states the reason as a law — a distinguishable state is a lever, and
    submitting garbage must not say anything about a candidate that submitting
    nothing does not. The receipt has no other slot for the distinction either;
    every key it carries is inside ``identity._EPISODE_IDENTITY_KEYS``, so
    adding one would move every episode id ever minted.

    What survives is *evidence*, not *receipt*: ``trace.result_file_digest``
    pins the exact bytes the agent wrote (and a missing file digests as the
    empty string, so "wrote garbage" and "wrote nothing" are distinguishable
    there), and the episode bundle persists the process ``stdout`` / ``stderr``.
    The distinction is available to anyone auditing the run and unavailable to
    anything scoring it. That is the correct split.

    Two things this deliberately does **not** do:

    * It does not re-derive the canonicalizer's rules. The authority on what can
      be hashed is ``identity.canonical_bytes``; a shape check copied up here
      would be a second, drifting copy of that domain. The finding is offered to
      the hasher and the refusal is honoured — so as ``identity.py`` tightens,
      this caller stays correct without being edited.
    * It does not *drop* the offending citation and keep the rest. Dropping is
      the version of this fix that pays the candidate: a submission of one good
      citation plus one unhashable one would lose the bad one and could then
      *pass* ``citations``, making garbage a way to launder a partial answer.
      Collapsing is all-or-nothing, so a malformed submission can only ever
      score what an empty one scores.

    Because the fallback runs only on inputs that previously raised, **no finding
    that has ever been minted changes id**: every input that produced an id
    before produces the same id now.
    """
    raw = result.get("finding") if isinstance(result, Mapping) else None
    if not isinstance(raw, Mapping):
        raw = {}
    citations = raw.get("citations", [])
    if not isinstance(citations, list):
        citations = []
    finding = _finding_document(raw.get("summary", ""), citations)
    try:
        finding["finding_id"] = identity.finding_id(finding)
    except (ValueError, TypeError, RecursionError):
        # Three named failures, one meaning. ValueError covers both
        # identity.IdentityError (the typed refusals: non-finite number,
        # non-string key) and UnicodeEncodeError (a lone surrogate, which is a
        # ValueError subclass); TypeError covers a value json.dumps cannot
        # serialize at all; RecursionError covers a document json.loads accepted
        # and json.dumps cannot re-emit — measured, a citation ~50 000 objects
        # deep parses and then blows the serializer's stack. Each one means the
        # candidate submitted something that cannot be sealed, and the empty
        # finding is the answer to all of them. Named individually rather than
        # as a bare `Exception` so a genuine bug in the spine still surfaces as
        # a bug instead of being scored as somebody's bad finding. The stack has
        # already unwound by the time this handler runs, so the fallback's own
        # hash has room.
        #
        # `identity.IdentityError` subclasses `ValueError`, so *every* typed
        # refusal the spine has — present and future, whatever its `code` —
        # lands here. Catching by `code` instead was considered and rejected:
        #
        #   * A code allowlist fails **open** into the exact hole this handler
        #     exists to close. A refusal added later with a code not on the list
        #     escapes `eval_one`, and an escaped refusal is a crashed episode:
        #     no receipt, nothing persisted, the bad score erased. Catching the
        #     class fails **closed** into a wrong score, which is visible in the
        #     receipt and can be argued with. Wrong-and-recorded beats
        #     absent-and-unarguable when the threat is a candidate deleting its
        #     own grade.
        #   * More decisively, `code` does not partition by blame, so the
        #     allowlist could not be written even in principle.
        #     `CANONICAL_ENCODING` is raised both by a candidate's lone
        #     surrogate in a citation *and* by the evaluator's own workspace
        #     rescan meeting a non-UTF-8 filename (the defect fixed in
        #     `runner._evidence_name`). One code, two blame-holders. A code
        #     names the law broken, never who broke it. `test_evalone` states
        #     this as a law rather than leaving it as an assertion here.
        #
        # Attribution comes from the **narrowness of the guarded input**, not
        # from the exception taxonomy: the `try` above covers exactly one call,
        # over exactly one document, built from the candidate's own bytes plus
        # two constants. Nothing evaluator-owned is offered to the hasher inside
        # this handler's reach, so there is nothing evaluator-owned for it to
        # misattribute. Keep it that way — widening the guarded region is what
        # would make the class catch wrong, not the class catch itself.
        finding = _finding_document("", [])
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


# --- The guarded verifier seam ----------------------------------------------
#
# One function, called by BOTH the live evaluation (`_finish_episode`) and the
# verification replay (`episode_bundle.verify_episode_bundle`), for exactly the
# same reason `build_receipt_v1` is shared by both: a receipt derived live and a
# receipt re-derived on replay must be byte-identical whenever the evidence
# agrees, and two copies of a classification rule are two chances to disagree.
# A raising verifier that were classified here and not there would produce a
# bundle that cannot be reopened, which is the acceptance bar failing quietly.


def _qualified_exception_type(cls: Any) -> str:
    """``"ValueError"`` for a builtin, ``"pkg.mod.Name"`` for anything else.

    **Host-independent by construction.** A class's ``__module__`` and
    ``__qualname__`` are properties of the source that defined it, not of the
    machine running it — unlike the exception's *message*, which routinely
    carries absolute paths, temp-directory names, pids and object addresses.
    That is the whole reason the type is sealed and the message is not. SARIF
    v2.1.0's ``exception.kind`` is the same choice ("the fully qualified type
    name of an object that was thrown"), as is JUnit's ``<error type=…>`` ("the
    full class name of the exception").

    The one residual: a class defined in a script run as ``__main__`` reports
    module ``"__main__"``, and the same class imported as a module would report
    its dotted path. That is a property of how the *verifier* was loaded, and it
    is stable within any one process — which is all replay requires, since a
    bundle is re-derived by the code that wired the verifier. A verifier defined
    in ``__main__`` is not a shipping configuration; the wiring registry imports
    every real implementation by module path.
    """
    name = getattr(cls, "__qualname__", None) or getattr(cls, "__name__", "") or ""
    module = getattr(cls, "__module__", "") or ""
    if module in ("builtins", "__builtin__", "exceptions", ""):
        return name
    return "%s.%s" % (module, name)


def _result_violation(result: Any) -> Optional[str]:
    """Which ``RESULT_VIOLATIONS`` clause ``result`` breaks, or ``None`` if it is sound.

    Checked in the order a reader would: is it the right kind of object, does it
    carry one of the four frozen states, is its detail a mapping, and can that
    detail actually be sealed. The last check runs the *real* canonicalizer over
    the real detail rather than re-deriving what it accepts, so this stays correct
    as ``identity.py`` tightens.
    """
    if not isinstance(result, VerifierResult):
        return "not_a_verifier_result"
    if result.state not in reward.STATES:
        return "unknown_state"
    if not isinstance(result.detail, Mapping):
        return "detail_not_mapping"
    try:
        identity.canonical_bytes(dict(result.detail))
    except (ValueError, TypeError, RecursionError):
        # The same three names, for the same three reasons, as
        # `_finding_artifact`: IdentityError/UnicodeEncodeError are ValueError,
        # TypeError is an unserializable value, RecursionError is a document the
        # emitter cannot re-emit.
        return "detail_not_canonical"
    return None


def _safe_str(obj: Any) -> str:
    """``str(obj)`` that cannot itself raise out of the guard.

    A handler that can raise is not a boundary. This only ever feeds *noncanonical*
    diagnostics, so a degraded string costs nothing and a propagating one would
    reopen the hole the guard exists to close.
    """
    try:
        return str(obj)
    except Exception:  # noqa: BLE001 -- deliberately total; see the docstring
        return "<unprintable>"


def _verifier_error(
    sig: str, verifier: Verifier, origin: str, code: str,
    sealed: Mapping[str, Any], diagnostics: Optional[Dict[str, Any]],
    operator: Mapping[str, Any],
) -> VerifierResult:
    """Build the ``error`` result for a failed verifier, splitting sealed from operator.

    ``sealed`` enters canonical evidence and therefore ``episode-…``: stable code,
    stable origin, the qualified type, the verifier's own implementation version.
    ``operator`` does not — it goes to ``diagnostics``, which is returned in the
    ``EvaluationRunV1`` artifacts for a human and is written to no bundle member.
    """
    detail: Dict[str, Any] = {
        "error_origin": origin,
        "error_code": code,
        # The version of the code that failed. Redundant with the receipt's
        # `verifier_versions[sig].implementation` and deliberately so: it makes
        # `evidence/verifiers/<sig>.json` answer "which build raised?" on its own,
        # and the two cannot disagree because both read `_impl_version` of the
        # same wired object.
        "verifier_implementation": _impl_version(verifier),
    }
    detail.update(sealed)
    if diagnostics is not None:
        record = {"signal": sig, "error_origin": origin, "error_code": code}
        record.update(sealed)
        record.update(operator)
        diagnostics[sig] = record
    return VerifierResult(reward.ERROR, detail)


def resolve_signal(
    sig: str,
    verifier: Optional[Verifier],
    context: VerifierContextV1,
    *,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> VerifierResult:
    """Resolve one signal through a verifier that is allowed to fail. Never raises.

    ``verifier`` is ``None`` for a pseudo-signal or an unwired one, which resolves
    to ``not_applicable`` — the pre-existing behaviour, unchanged. Otherwise the
    verifier is called inside a boundary, and two distinct failures become
    verification ``error`` with distinguishable sealed evidence:

      * it **raised**            → ``verifier_exception`` / ``VERIFIER_RAISED``
      * it **returned garbage**  → ``verifier_protocol`` / ``INVALID_VERIFIER_RESULT``

    These are different facts and are never merged. A raise is the checker
    breaking mid-procedure; a malformed return is the checker breaking its
    contract while believing it succeeded — the second is the more dangerous of
    the two, because without this check some of its shapes (``detail_not_canonical``)
    would still have killed receipt construction downstream, and some
    (``unknown_state``) would have reached ``reward.score`` as an unscoreable map.

    ``Exception``, deliberately **not** ``BaseException``. ``KeyboardInterrupt``,
    ``SystemExit`` and ``GeneratorExit`` keep their ordinary meanings: an operator
    interrupting a run must interrupt it, not silently mint an ``error`` episode
    that then gets persisted as though the verifier had an opinion.

    **``MemoryError`` is caught, and that is a deliberate divergence from the
    precedent in ``runner._read_result`` / ``batch.load_candidate_set`` /
    ``bundle.read_manifest``.** Those three name a narrow tuple and exclude
    ``MemoryError`` on two grounds; neither survives the move to this seam.

      1. *Attributability.* There, a caught refusal becomes the empty finding,
         which the verifiers score ``fail`` — **a number**. Catching an OOM would
         therefore make the same submission score 0.25 on a large host and 1.0 on
         a small one, and a host-dependent *reward* is worse than a visible crash.
         Here the classification is ``error``, and ``error`` is this system's
         existing word for "the host could not answer": ``reward.score``'s F2 rule
         gives it ``reward = None``, never ``0``, and downstream aggregation drops
         ``None`` rather than averaging it in. So catching ``MemoryError`` here does
         not make a submission *score* differently on different machines; it makes
         it score on one and decline to score on the other, which is the true
         report. The engine already does exactly this for a timeout — also a fact
         about the host, also sealed as ``error`` — in the substrate-run-failure
         branch of ``_finish_episode``, a few dozen lines below.
      2. *"It would close nothing anyway."* There, ``fh.read()`` had already loaded
         the file, so the handler could not have helped. Here it closes precisely
         the hole this change exists to close, and the hole is candidate-reachable:
         a verifier walks the finding, the patch and the patched tree, all built
         from bytes the candidate chose, and an escaped ``MemoryError`` is a
         crashed episode with no receipt and no bundle — the grade erased.

    The honest cost, stated rather than hidden: after a ``MemoryError`` the
    interpreter's state is not guaranteed, so the *sibling* verdicts in that one
    episode are best-effort. That is acceptable only because a single ``error``
    already forces the whole episode to ``status = error`` / ``validity = invalid``
    / ``reward = None``, so those sibling verdicts are **reported and never
    scored**. If the four states are ever changed so an ``error`` signal can
    coexist with a real reward number, this paragraph stops being true and the
    decision must be retaken.

    ``diagnostics``, when supplied, collects the operator-facing record — full
    message and traceback. It is never hashed and never written to a bundle.
    """
    if verifier is None:
        return VerifierResult(reward.NOT_APPLICABLE)
    try:
        result = verifier(context)
    except Exception as exc:  # noqa: BLE001 -- NOT BaseException; see the docstring
        return _verifier_error(
            sig, verifier,
            ERROR_ORIGIN_VERIFIER_EXCEPTION, ERROR_CODE_VERIFIER_RAISED,
            {"exception_type": _qualified_exception_type(type(exc))},
            diagnostics,
            {"message": _safe_str(exc),
             "traceback": "".join(traceback.format_exception(
                 type(exc), exc, exc.__traceback__))},
        )
    violation = _result_violation(result)
    if violation is not None:
        return _verifier_error(
            sig, verifier,
            ERROR_ORIGIN_VERIFIER_PROTOCOL, ERROR_CODE_INVALID_VERIFIER_RESULT,
            {"violation": violation},
            diagnostics,
            {"returned_type": _qualified_exception_type(type(result))},
        )
    return result


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
    episode_version=None,
) -> Dict[str, Any]:
    """Assemble the ``episode-…`` receipt and seal its ``episode_id``.

    The single point where an episode receipt is shaped. Both the live evaluation
    and the verification replay build receipts through here (via
    ``build_receipt_v1``) so the two can never structurally drift.

    ``episode_version`` selects the schema *and*, through
    ``identity.EPISODE_SCHEMES``, the canonicalization the id is computed under.
    It defaults to ``EPISODE_VERSION`` — what this build mints — and is passed
    explicitly when a receipt must be shaped as some *other* version: replaying
    a stored episode, which must reproduce the document it is checking rather
    than the document this build would mint today.

    An unknown version is refused by ``identity.episode_scheme`` on the very
    next line, when the id is sealed. This function does not pre-validate it,
    deliberately: one refusal, raised where the consequence is, rather than two
    that could disagree.

    ``canonicalization`` is emitted only for schemes that require it, so a v1
    receipt keeps the exact 15-key shape every v1 receipt on disk already has.
    It is a redundant self-declaration, not an input — the version chose the
    scheme, and ``episode_scheme`` refuses the pair if they disagree.
    """
    version = EPISODE_VERSION if episode_version is None else episode_version
    declaration = identity.episode_scheme_declaration(version)
    receipt = {
        "episode_version": version,
        **({"canonicalization": declaration} if declaration else {}),
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
    reward_spec, required, tampered, execution_facts, episode_version=None,
) -> "tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]":
    """ReceiptBuilderV1 — derive a complete receipt (+ evidence) from resolved parts.

    Shared by live evaluation (``evaluate``) and verification replay
    (``episode_bundle.verify_episode_bundle``) so a receipt produced live and the
    receipt re-derived on replay are byte-identical whenever the evidence agrees.

    ``episode_version`` defaults to what this build mints (``EPISODE_VERSION``).
    A **replay** must pass the stored receipt's own declared version instead:
    re-deriving under the current default would compare the document on disk
    against the document this build would produce today, which is a different
    question, and one that answers "differs" for a bundle whose evidence is
    entirely intact. See the note on ``EPISODE_VERSION`` for why supplying that
    argument is the named prerequisite for moving the constant, and E-B4 for the
    proof that skipping it breaks every sealed episode.

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
        episode_version=episode_version,
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

    # Every signal is resolved, in sorted order, through the one guarded seam.
    # Two properties that a bare `{sig: verifier(context) for sig in ...}` did
    # not have, and that the acceptance bar is stated in terms of:
    #
    #   * a verifier that fails is CLASSIFIED, not escalated. Before this, a raise
    #     escaped `eval_one` before the receipt existed — no receipt, no episode,
    #     nothing persisted — and since `batch`/`compare` refuse a pair when
    #     nothing was persisted, that was a fourth route into the same
    #     grade-erasure class as the malformed-citation, deep-JSON and
    #     non-UTF-8-filename defects already closed.
    #   * **the loop continues.** A failing verifier must not silence its
    #     siblings: the map stays total over every declared signal, so the other
    #     verifiers still report and `reward.score`'s totality precondition still
    #     holds. TAP's `Bail out!` is the opposite convention and is wrong for a
    #     rubric — abandoning the remaining checks would delete evidence about a
    #     candidate to describe a fault in the evaluator.
    #
    # Sorted rather than set-ordered so the operator diagnostics below are
    # produced in a stable sequence; the receipt itself is order-independent
    # (`identity.canonical_bytes` sorts keys), so this moves no id.
    results: Dict[str, VerifierResult] = {}
    diagnostics: Dict[str, Any] = {}
    for sig in sorted(signal_ids):
        wired = (None if sig in _PSEUDO_SIGNALS
                 else _resolver_for(sig, extra_verifiers))
        results[sig] = resolve_signal(sig, wired, context, diagnostics=diagnostics)
    verification = {sig: res.state for sig, res in results.items()}

    # --- Substrate run failure → error (§6a, exit-code semantics) -------------
    # Under a non-executing profile no process was launched, so there is no exit
    # code to compare and no output that could have been truncated: the whole
    # notion of a *substrate* run failure is inapplicable. Evaluating it anyway
    # would read exit_code=None, find it absent from allowed_exit_codes=[0], and
    # force every signal to ERROR — declaring every remote submission a substrate
    # failure. The replay in episode_bundle mirrors this branch exactly.
    if runner_profile in execfacts.NON_EXECUTING_PROFILES:
        run_error = False
    else:
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
        # Operator diagnostics for verifiers that failed: full exception message
        # and traceback, keyed by signal. **Noncanonical and non-persisted.** It
        # is not hashed, it is not a bundle member (`episode_bundle._populate`
        # writes only the members it names, and `_verify_tree_closure` would
        # reject an undeclared file), and it therefore cannot move an id or make
        # a bundle fail to close. The same placement SARIF uses: the volatile
        # parts of a failure live on the notification, never on the result that
        # gets fingerprinted. Empty for every episode in which no verifier failed.
        "verifier_diagnostics": diagnostics,
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
