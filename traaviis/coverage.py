"""``traaviis.response-coverage.v2`` — how much of the rubric the verifiers
actually answered, read off already-sealed bytes.

Why this exists
---------------
``traaviis.reward`` gives a ``fail`` and a ``not_applicable`` the **identical**
contribution of ``0.0`` (RFC Evidence Residency §6a; ``reward.score`` only adds
weight on ``PASS``). That is correct arithmetic and lossy reporting: an episode
whose ``identity`` verifier looked and said *no* and an episode whose ``identity``
verifier never existed both land on the same float, with the same
``status = ok`` and the same ``validity = valid``. Content-addressed identity
proves *which* episode you are looking at; it does not tell you how much of the
rubric was in play when the number was produced. This module recovers the second
half.

The committed ``examples/eval-one/residency-demo`` bundle is the case in point:
it scores ``reward 0.55``, ``status ok``, ``validity valid`` — and 0.45 of its
declared rubric weight was never adjudicated, because no ``tests`` or
``identity`` implementation was wired at all.

Naming
------
**verifier response coverage** (module-internally just "the reading"). Not
"verifier coverage": three unrelated things already own that phrase, and a reader
will land on one of them —

  * *source-code coverage* — which lines of the subject were executed;
  * *behavioural / task coverage* — which behaviours the suite exercises;
  * *benchmark-distribution coverage* — which slice of a task population a
    leaderboard sampled.

This is none of those. It measures **which of the verifiers this episode declared
actually returned a verdict**, and what share of the rubric's weight those
verdicts carried. The word *response* is what carries the distinction, so it is
never dropped. ``evidence coverage`` is the acceptable short alias in prose; the
code uses ``response_coverage`` throughout.

What it is not
--------------
*Not a score, and not a validity judgement.* The reading deliberately never
reports which signals **passed** — that is the reward's job, and a field listing
passes would immediately be read as partial credit. An episode can post
``weight_coverage 1.0`` and be ``invalid`` (every verifier answered; one answered
``fail`` under a hard cap, or the subject was tampered). An episode can post
``weight_coverage 0.55`` and be perfectly ``valid``, having failed none of the
assertions that were actually applicable. Coverage and validity are orthogonal
readings of the same sealed bytes and are printed side by side, never merged.

Purity, and why no id moves
---------------------------
A pure function: three sealed documents in, one plain dict out. No I/O, no
engine, no verifier execution, no clock. Every input — ``receipt.verification``,
``receipt.verifier_versions``, the ``rew-…`` signal map, and
``task.verifier_plan`` — is already inside a saved episode bundle and already
hash-bound to ``episode-…``. The reading is therefore *derivable by any second
consumer from the receipt they already have*, which is exactly why it must not be
stored: adding a ``coverage`` key to any hashed document would move every
``episode-…`` downstream of it in order to record something those bytes already
determine. Same rung as ``ComparisonV1`` — a reading of sealed artifacts that
correctly mints no id.

Float determinism is part of that promise: two consumers must get the same bytes.
Every weight sum here is accumulated in **sorted signal-id order**, so the
floating-point result is a function of the sealed documents and not of dict
iteration order.

The four states, and the two collapses this undoes
--------------------------------------------------
``reward`` freezes four verifier states (§6): ``pass | fail | not_applicable |
error``. **v2 adds none** — availability and failure origin are *orthogonal axes*
over those four states, never new members of them. The reading classifies each
declared signal into a **response class**:

  ``answered``       state ``pass`` or ``fail`` — the verifier reached a verdict.
                     A ``fail`` is an answer. It costs reward and costs no
                     coverage; that separation is the whole point.
  ``abstained``      state ``not_applicable`` **and** a non-null ``implementation``
                     is sealed in ``verifier_versions`` — real code ran and
                     legitimately declared the signal inapplicable (``tests`` with
                     no test plan, ``identity`` with no bindings).
  ``unwired``        state ``not_applicable`` and the sealed ``implementation`` is
                     ``None`` — **no verifier was connected for this episode**.
                     ``evalone.resolve_signal`` falls back to ``not_applicable``
                     for an unwired signal, so this state is the engine's default,
                     not anybody's verdict.
  ``structural``     state ``not_applicable`` and the signal carries no
                     ``verifier_versions`` entry at all — the episode declared no
                     evidence obligation for it (the ``native`` / ``oracle``
                     pseudo-signals). Derived from the *absence of the key*, never
                     from a hardcoded name list.
  ``errored``        state ``error`` — something in the checking apparatus did not
                     complete. Never merged with ``abstained``: one says "there was
                     nothing to check", the other says "we could not check". *Which*
                     part did not complete is the second axis, below.

So the two collapses the field keeps making are undone in different places.
``not_applicable`` versus *unavailable* is undone by ``abstained`` versus
``unwired`` — and note that both of those are the string ``"not_applicable"`` in
the receipt, indistinguishable without ``verifier_versions``, which nothing else
reads. Verifier **error** versus legitimate **abstention** is undone by
``errored`` versus ``abstained``.

Why ``unimplemented`` was renamed to ``unwired`` (v1 → v2)
----------------------------------------------------------
``unimplemented`` overstated what the bytes prove, and the shipped example is the
counter-example. ``examples/eval-one/residency-demo/task.json`` declares
``verifier_plan.not_applicable: ["native","oracle","tests","identity"]``, and both
``substrate_verifiers.tests_verifier`` and ``substrate_verifiers.make_identity_verifier``
**exist in this repository**. The implementations were never missing; they were
not *connected* for that episode. All the receipt records is a null
``implementation`` in ``verifier_versions``, and "nothing was wired here" is
exactly what that supports — no more.

The distinction is not pedantry, because the two readings imply opposite actions.
*Unimplemented* tells a reader to go write the verifier. *Unwired* tells them to
go fix the wiring, which for this episode is the true and much cheaper answer.

The rename **costs no ids**: nothing in this module is hashed. The reading is a
pure function of three already-sealed documents, mints no id, and is forbidden
from being written into any document that does — see "Purity" above, and the
``test_coverage`` law that re-derives every committed episode/task/reward id
unchanged across this change.

Availability and origin: the second axis
----------------------------------------
``unwired`` above is one value of an *availability* axis; the rest of that axis
lives outside this module and is named here so the vocabulary is written down
once:

  ``unwired``        an implementation exists or may exist, but was not connected
                     for this episode. A response class here.
  ``unavailable``    the sealed implementation was required but cannot be
                     reproduced now. **Not a coverage class**: it is a property of
                     a *replay*, not of a receipt, and it is already reported by
                     ``episode_bundle.OUTCOME_UNAVAILABLE`` when the freshly wired
                     verifier versions do not match what the receipt sealed. A
                     receipt on its own cannot know it.

And a *failure-origin* axis over the single state ``error``:

  ``verifier_reported``   the wired verifier itself returned ``VerifierResult(ERROR)``
  ``substrate_override``  the run-level rule in ``evalone._finish_episode``
                          overwrote every non-pseudo signal to ``error`` because
                          the agent process timed out, was truncated, or exited
                          outside ``allowed_exit_codes``
  ``verifier_exception``  a wired verifier raised while evaluating
  ``verifier_protocol``   a wired verifier returned an invalid result

``runner_error`` and ``toolchain_error`` — "the verifier could not execute its
declared procedure" — are **not** separate origins in the code today, and saying
so is more useful than pretending otherwise. ``substrate_verifiers.tests_verifier``
already reports them, but it reports them as ordinary
``VerifierResult(ERROR, {"reason": "toolchain resolution failed: …"})`` /
``{"reason": "baseline run failed", …}``: from the receipt's point of view they
are ``verifier_reported``, sub-kinded by a **free-text** ``reason``. Promoting them
to stable codes would mean changing those detail strings, and every one of them is
inside a sealed ``verification_evidence[sig].digest`` and therefore inside
``episode-…``. That is a migration, not a rename, and it is out of scope here; it
is recorded as an open edge rather than silently done or silently forgotten.

**Origin names which layer failed, never whose fault it is.** A verifier can raise
because of a bug in the verifier *or* because the candidate submitted a patch that
drove it there — one origin, two blame-holders, exactly as ``evalone``'s
``_finding_artifact`` records for ``CANONICAL_ENCODING``. METR's Vivaria ships a
``serverOrTask`` member in its ``ErrorSource`` enum for the same reason. This
reading therefore never claims attribution, and when it cannot even determine the
origin it says ``None`` rather than guessing (see ``verifier_evidence`` below).

How each origin is obtained
---------------------------
``substrate_override`` is **derived**, from the receipt's own ``execution_facts``
plus the task's ``agent_run_policy``. Note the second half: the brief for this
change said "derivable from the existing receipt plus ``execution_facts``", and
that is *almost* right — ``allowed_exit_codes`` lives in the task, not in
``execution_facts``, so a task declaring a non-default allowed set cannot be
adjudicated from the receipt alone. This function already takes the task, so
nothing is missing; the claim just needed narrowing.

``verifier_exception`` / ``verifier_protocol`` are **read** from the per-signal
verifier evidence, which the receipt pins only by digest. They are therefore
available only when the caller supplies ``verifier_evidence`` (the objects saved
as ``evidence/verifiers/<signal>.json``). Without it, an errored signal that was
not a substrate override reports ``error_origin: None`` — "undetermined" — and
never the plausible-looking ``verifier_reported``, because asserting the verifier
reported it when the reading has not looked is precisely the false prose v2 exists
to stop.

Departure from the ROADMAP §C sketch, stated plainly
----------------------------------------------------
``ROADMAP.md`` §C proposes that "an ``error`` episode reports coverage ``None``
rather than 0 (mirroring the reward rule)". This module does **not** do that, and
the reason is that the mirror does not hold. ``reward`` is ``None`` on error
because reward is a claim *about the agent*, and when the substrate fell over
there is no honest partial claim to make about the agent. Coverage is a claim
*about the verifiers*, and an errored episode is precisely the case where "what
did the verifier actually manage to check?" carries the most information.
Returning ``None`` there would suppress the reading exactly when it is most
needed, and — worse — would re-collapse ``errored`` into the same silence as "no
rubric weight was declared". The reading is therefore always computed; ``error``
is kept distinct by a dedicated ``errored`` class and ``errored_weight``, never by
absence. ``weight_coverage`` is ``None`` only when ``declared_weight`` is ``0.0``
(there is no denominator), and ``signal_coverage`` only when nothing was obligated.
"""

from typing import Any, Dict, List, Mapping, Optional

from . import execfacts as _execfacts
from . import reward as _reward

__all__ = [
    "COVERAGE_VERSION",
    "ANSWERED", "ABSTAINED", "UNWIRED", "STRUCTURAL", "ERRORED",
    "RESPONSE_CLASSES",
    "AVAILABILITY_UNWIRED", "AVAILABILITY_UNAVAILABLE",
    "ORIGIN_VERIFIER_REPORTED", "ORIGIN_SUBSTRATE_OVERRIDE",
    "ORIGIN_VERIFIER_EXCEPTION", "ORIGIN_VERIFIER_PROTOCOL",
    "ORIGIN_UNDETERMINED", "ERROR_ORIGINS",
    "response_coverage",
    "coverage_line",
    "coverage_lines",
]

COVERAGE_VERSION = "traaviis.response-coverage.v2"

# --- Response classes --------------------------------------------------------
ANSWERED = "answered"
ABSTAINED = "abstained"
UNWIRED = "unwired"
STRUCTURAL = "structural"
ERRORED = "errored"

#: Every class a declared signal can fall into. Total over the four frozen
#: verifier states: ``pass``/``fail`` → answered, ``error`` → errored, and
#: ``not_applicable`` → one of the three abstention kinds, discriminated by what
#: ``verifier_versions`` sealed.
RESPONSE_CLASSES = (ANSWERED, ABSTAINED, UNWIRED, STRUCTURAL, ERRORED)

# --- Availability axis (orthogonal to the four states) ------------------------
#: An implementation exists or may exist, but was not connected for this episode.
AVAILABILITY_UNWIRED = UNWIRED
#: The sealed implementation was required and cannot be reproduced now. Reported
#: by ``episode_bundle`` as a *replay* outcome; a receipt alone cannot know it, so
#: no signal is ever classified this way here. Named for one vocabulary, not two.
AVAILABILITY_UNAVAILABLE = "unavailable"

# --- Failure-origin axis over the single state ``error`` ----------------------
ORIGIN_VERIFIER_REPORTED = "verifier_reported"
ORIGIN_SUBSTRATE_OVERRIDE = "substrate_override"
ORIGIN_VERIFIER_EXCEPTION = "verifier_exception"
ORIGIN_VERIFIER_PROTOCOL = "verifier_protocol"

#: Not an origin — the explicit absence of one. Used only as an
#: ``errored_by_origin`` key, where the per-signal ``error_origin`` is ``None``.
ORIGIN_UNDETERMINED = "undetermined"

#: The complete origin vocabulary. Deliberately re-stated here rather than
#: imported from ``evalone``: this module's whole claim is that it is a pure
#: function of three sealed documents, and importing the orchestrator — which
#: pulls in the runner, subprocess and the admission stack — to obtain four
#: string constants would make that false for a reader checking the import list.
#: ``evalone`` owns the two it *produces*; ``test_coverage`` pins that the two
#: spellings agree, so the duplication cannot drift silently. Same trade the two
#: batteries make with ``_isolated_traaviis``: duplication plus a law, over a
#: coupling that outlives its reason.
ERROR_ORIGINS = (ORIGIN_VERIFIER_REPORTED, ORIGIN_SUBSTRATE_OVERRIDE,
                 ORIGIN_VERIFIER_EXCEPTION, ORIGIN_VERIFIER_PROTOCOL)

#: The two origins ``evalone`` seals into verifier evidence. The other two are
#: derived by this module and appear in no sealed document.
_SEALED_ORIGINS = (ORIGIN_VERIFIER_EXCEPTION, ORIGIN_VERIFIER_PROTOCOL)

_ORIGIN_NOTE = {
    ORIGIN_SUBSTRATE_OVERRIDE: (
        "a completed agent run — the run failed at the substrate, so this signal "
        "was overwritten to `error` before any verdict could count; this says "
        "nothing about the verifier (see the substrate-override line above)"),
    ORIGIN_VERIFIER_REPORTED: (
        "a completed verifier run — the wired verifier itself reported `error`, "
        "i.e. it could not execute its declared procedure, so this signal was "
        "never adjudicated"),
    ORIGIN_VERIFIER_EXCEPTION: (
        "a verifier that returns instead of raising — the wired verifier raised "
        "while evaluating (%s), so it produced no verdict; the episode is "
        "error/invalid with a null reward, and the evidence was still kept"),
    ORIGIN_VERIFIER_PROTOCOL: (
        "a well-formed verifier result — the wired verifier returned an object "
        "the VerifierResult contract refuses (%s), so it produced no verdict"),
    None: (
        "a determinable cause — this signal is `error`, but the origin cannot be "
        "settled from what this reading was given: either no verifier evidence "
        "was supplied (the receipt pins it only by digest) or the receipt carried "
        "no execution_facts to rule the substrate override in or out"),
}

_MISSING = {
    UNWIRED: (
        "a wired verifier implementation — the episode sealed a null "
        "`implementation`, so `not_applicable` here is the engine's fallback for a "
        "signal no verifier was connected to, not a verdict%s"),
    STRUCTURAL: (
        "an evidence obligation — the task's verifier_plan REQUIRES this signal "
        "yet the episode sealed no verifier_versions entry for it, so no verifier "
        "could ever have answered it"),
}


def _owed(cls: str, required: bool) -> bool:
    """Was this signal obligated to answer?

    Everything except ``structural`` is: the episode sealed a contract or an
    implementation slot for it, so somebody was on the hook. A ``structural``
    signal — ``native`` / ``oracle`` under the residency profile — carries no
    obligation and is excluded from the denominator, because counting it would
    make every clean episode read as having two permanent gaps and teach readers
    to skip the section.

    Unless the task **required** it. A required signal with no evidence obligation
    is a live contradiction (nothing could ever answer it), and that is exactly the
    thing this reading must shout about rather than quietly drop.
    """
    return cls != STRUCTURAL or required


def _classify(state: str, versions: Mapping[str, Any], sig: str) -> str:
    """Which response class one declared signal falls into.

    ``pass``/``fail`` answer; ``error`` errors; ``not_applicable`` splits three
    ways on what ``verifier_versions`` sealed for the signal — key absent means no
    obligation was declared, a ``None`` implementation means nothing was wired, and
    a real implementation string means real code abstained.
    """
    if state in (_reward.PASS, _reward.FAIL):
        return ANSWERED
    if state == _reward.ERROR:
        return ERRORED
    entry = versions.get(sig)
    if entry is None:
        return STRUCTURAL
    impl = entry.get("implementation") if isinstance(entry, Mapping) else None
    return ABSTAINED if impl else UNWIRED


def _substrate_override(
    receipt: Mapping[str, Any], task: Mapping[str, Any],
) -> Optional[bool]:
    """Did the run-level substrate-failure rule set every non-pseudo signal to ``error``?

    The exact mirror of the branch in ``evalone._finish_episode`` (and of its twin
    in ``episode_bundle.verify_episode_bundle``), rebuilt from sealed bytes rather
    than from a live ``RunResult``:

      * a **non-executing** runner profile launched nothing, so there is no
        substrate failure to have — the same gate both of those branches carry;
      * ``termination == "timed_out"`` is ``run["timed_out"]``;
      * ``stdout_truncated or stderr_truncated`` is ``run["output_truncated"]``
        (``runner`` computes the latter as exactly that disjunction);
      * otherwise the exit code must be in the task's ``allowed_exit_codes``.

    Returns ``None`` when the receipt does not carry enough to decide — an
    invalid-config episode seals ``execution_facts: null``, and a receipt whose
    facts are malformed must not be guessed at. ``None`` means *undetermined*, and
    is never silently read as ``False``.
    """
    facts = receipt.get("execution_facts")
    if not isinstance(facts, Mapping):
        return None
    runner = facts.get("runner")
    profile = runner.get("profile") if isinstance(runner, Mapping) else None
    if profile in _execfacts.NON_EXECUTING_PROFILES:
        return False
    proc = facts.get("agent_process")
    if not isinstance(proc, Mapping):
        return None
    if proc.get("termination") == "timed_out":
        return True
    if proc.get("stdout_truncated") or proc.get("stderr_truncated"):
        return True
    policy = task.get("agent_run_policy") or {}
    allowed = list(policy.get("allowed_exit_codes", [0]))
    return proc.get("exit_code") not in allowed


def _sealed_error_detail(
    verifier_evidence: Optional[Mapping[str, Any]], sig: str,
) -> Mapping[str, Any]:
    """The ``detail`` of one signal's saved verifier evidence, or ``{}``.

    ``verifier_evidence`` maps signal → the ``traaviis.verifier-evidence.v1``
    object saved at ``evidence/verifiers/<signal>.json``. This reading does **not**
    re-attest it against ``receipt.verification_evidence[sig].digest``: that
    binding is ``episode_bundle.verify_episode_bundle``'s verdict, and a second,
    weaker copy of it here would be the "boundary validation only" rule this module
    already states for the task/reward cross-binding. What is reported is what the
    supplied evidence says.
    """
    if not isinstance(verifier_evidence, Mapping):
        return {}
    entry = verifier_evidence.get(sig)
    if not isinstance(entry, Mapping):
        return {}
    detail = entry.get("detail")
    return detail if isinstance(detail, Mapping) else {}


def _error_origin(
    detail: Mapping[str, Any], override: Optional[bool],
) -> Optional[str]:
    """Which layer produced this signal's ``error``, or ``None`` if undetermined.

    Precedence is causal, not preferential. When the run-level rule fired it
    *overwrote* the state — whatever the verifier had said or failed to say, the
    ``error`` in the receipt is the override's — so ``substrate_override`` wins.
    The verifier's own failure is not lost: ``error_code`` is reported beside this
    from the same evidence, so an episode where the substrate failed **and** a
    verifier raised shows both facts rather than one.
    """
    if override:
        return ORIGIN_SUBSTRATE_OVERRIDE
    sealed = detail.get("error_origin")
    if sealed in _SEALED_ORIGINS:
        return sealed
    if override is None or not detail:
        # Either the override could not be ruled in or out, or no evidence was
        # supplied to look at. "The verifier reported error" is the plausible
        # answer here and is exactly the sentence v2 exists to stop being said
        # without looking.
        return None
    return ORIGIN_VERIFIER_REPORTED


def _missing_note(cls: str, sig: str, record: Mapping[str, Any]) -> str:
    """What evidence or capability was missing, in one sentence, per signal."""
    declared_na = bool(record.get("declared_not_applicable"))
    if cls == ERRORED:
        origin = record.get("error_origin")
        note = _ORIGIN_NOTE.get(origin, _ORIGIN_NOTE[None])
        if origin == ORIGIN_VERIFIER_EXCEPTION:
            return note % (record.get("exception_type") or "exception type unrecorded")
        if origin == ORIGIN_VERIFIER_PROTOCOL:
            return note % (record.get("violation") or "violation unrecorded")
        return note
    if cls == UNWIRED:
        # Row one of the ruled dispositions: a signal the task declared
        # not_applicable AND that was unwired is legitimately `not_applicable`,
        # and reads very differently from one the plan never mentioned.
        return _MISSING[UNWIRED] % (
            "; the task's verifier_plan declared it not_applicable in advance, so "
            "the state is the ruled outcome and not a gap"
            if declared_na else
            "; the task's verifier_plan did NOT declare it not_applicable, so this "
            "is an unanticipated wiring gap")
    if cls in _MISSING:
        return _MISSING[cls]
    # ABSTAINED: nothing was *unavailable*; the subject had nothing to check. Say
    # which it was, and whether the task anticipated it — an abstention the plan
    # did not declare is a different fact from one it did.
    anticipated = ("the task's verifier_plan declared it not_applicable in advance"
                   if declared_na else
                   "the task's verifier_plan did NOT declare it not_applicable")
    return ("applicable subject matter — `%s` ran and declared %s inapplicable; %s"
            % (record.get("implementation"), sig, anticipated))


def response_coverage(
    receipt: Mapping[str, Any],
    reward_spec: Mapping[str, Any],
    task: Mapping[str, Any],
    verifier_evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """The verifier response coverage reading for one sealed episode.

    ``receipt`` is an ``EpisodeReceiptV1``; ``reward_spec`` the ``RewardSpecV1``
    it names by ``rew-…``; ``task`` the ``TaskSpecV1`` it names by ``task-…``.
    All three are members of a saved ``episode-<id>/`` bundle. Nothing else is
    consulted: no verifier is run, no file is opened, no clock is read.

    ``verifier_evidence`` is optional and is the map ``{signal: <the object saved
    at evidence/verifiers/<signal>.json>}``. It is what makes ``verifier_exception``
    and ``verifier_protocol`` distinguishable, because the receipt pins those
    evidence objects only by digest. Omit it and every errored signal that was not
    a substrate override reports ``error_origin: None`` — undetermined — which is
    the honest reading rather than the plausible one. The parameter is last and
    defaults to ``None`` so the three-document call sites are unchanged.

    The three documents are taken as already cross-bound — ``evalone`` /
    ``episode_bundle`` prove the ``rew-…`` and ``task-…`` references before any
    of this is reachable, and re-checking them here would be a second, weaker
    copy of that gate. Boundary validation only: an unknown verifier state, or a
    scored signal absent from the (contractually total) verification map, is a
    caller bug and raises ``ValueError``, mirroring ``reward.score``.

    Returns a plain JSON-serializable dict. It is a *reading*: it mints no id, is
    never hashed, and must never be written into a document that is.
    """
    verification: Mapping[str, str] = receipt.get("verification") or {}
    versions: Mapping[str, Any] = receipt.get("verifier_versions") or {}
    evidence: Mapping[str, Any] = receipt.get("verification_evidence") or {}
    scored: Mapping[str, Mapping[str, Any]] = reward_spec.get("signals") or {}
    plan = task.get("verifier_plan") or {}
    required = set(plan.get("required") or ())
    declared_na = set(plan.get("not_applicable") or ())

    for sig, state in verification.items():
        if state not in _reward.STATES:
            raise ValueError("unknown verifier state for %r: %r" % (sig, state))
    for sig in scored:
        if sig not in verification:
            raise ValueError(
                "scored signal %r missing from verification map" % sig)
    for sig in required:
        if sig not in verification:
            raise ValueError(
                "required signal %r missing from verification map" % sig)

    # Sorted once, and every traversal below reuses it: the weight sums must be a
    # function of the sealed bytes, not of dict insertion order, or two consumers
    # deriving "the same" reading get different floats in the last place.
    declared: List[str] = sorted(verification)

    # The run-level substrate-failure verdict is a property of the *episode*, not
    # of any one signal: when it fired it overwrote every non-pseudo signal at
    # once. Derived once, here, and reported at the top level as well as per
    # signal, so a reader can see that seven `error`s are one event.
    override = _substrate_override(receipt, task)

    per_signal: Dict[str, Dict[str, Any]] = {}
    weight_by_class = {cls: 0.0 for cls in RESPONSE_CLASSES}
    declared_weight = 0.0

    for sig in declared:
        state = verification[sig]
        cls = _classify(state, versions, sig)
        entry = versions.get(sig)
        entry = entry if isinstance(entry, Mapping) else {}
        binding = scored.get(sig)
        is_scored = isinstance(binding, Mapping)
        weight = float(binding["weight"]) if is_scored else None
        if weight is not None:
            declared_weight += weight
            weight_by_class[cls] += weight

        is_required = sig in required
        owed = _owed(cls, is_required)
        record: Dict[str, Any] = {
            "state": state,
            "response": cls,
            "required": is_required,
            "obligated": owed,
            "scored": is_scored,
            "weight": weight,
            "declared_not_applicable": sig in declared_na,
            "contract": entry.get("contract"),
            "implementation": entry.get("implementation"),
            "evidence_sealed": sig in evidence,
        }
        if cls == ERRORED:
            # The failure-origin axis. `error_origin` is the origin of the state
            # the receipt records; `error_code` / `exception_type` / `violation`
            # are whatever the sealed evidence carried, reported even when the
            # override took precedence for the state — the substrate falling over
            # does not un-raise a verifier that also raised.
            detail = _sealed_error_detail(verifier_evidence, sig)
            record["error_origin"] = _error_origin(detail, override)
            record["error_code"] = detail.get("error_code")
            for key in ("exception_type", "violation"):
                if key in detail:
                    record[key] = detail[key]
        if owed and cls != ANSWERED:
            record["missing"] = _missing_note(cls, sig, record)
        per_signal[sig] = record

    def _ids(*classes) -> List[str]:
        want = set(classes)
        return [s for s in declared if per_signal[s]["response"] in want]

    def _unanswered(want_required: bool) -> List[str]:
        return [s for s in declared
                if per_signal[s]["obligated"]
                and per_signal[s]["response"] != ANSWERED
                and per_signal[s]["required"] is want_required]

    answered = _ids(ANSWERED)
    # `declined` is exactly the receipt's own ``not_applicable`` set — checkable
    # against the sealed bytes by eye — and the three sub-lists say which kind of
    # not_applicable each one was. Collapsing them is the dishonesty; hiding the
    # union would make the reading uncheckable.
    declined = _ids(ABSTAINED, UNWIRED, STRUCTURAL)
    errored = _ids(ERRORED)
    obligated = [s for s in declared if per_signal[s]["obligated"]]

    # Errored signals grouped by which layer failed. The keys are exactly the
    # origins actually observed, so an empty dict means no signal errored — never
    # "we did not look". An undetermined origin gets the explicit key
    # ``"undetermined"`` rather than a JSON-null key: a null key survives
    # ``json.dumps`` only by silent coercion, and dies outright under
    # ``sort_keys=True`` beside string keys.
    by_origin: Dict[str, List[str]] = {}
    for sig in errored:
        key = per_signal[sig].get("error_origin") or ORIGIN_UNDETERMINED
        by_origin.setdefault(key, []).append(sig)

    scored_weight = weight_by_class[ANSWERED]
    return {
        "coverage_version": COVERAGE_VERSION,

        # --- the denominator, stated openly ---
        "declared": declared,
        "obligated": obligated,
        "required": sorted(required & set(declared)),
        "optional": [s for s in declared if s not in required],

        # --- who answered, who did not, and in which way ---
        "answered": answered,
        "declined": declined,
        "abstained": _ids(ABSTAINED),
        "unwired": _ids(UNWIRED),
        "structural": _ids(STRUCTURAL),
        "errored": errored,
        "required_unanswered": _unanswered(True),
        "optional_unanswered": _unanswered(False),

        # --- which layer failed, for the signals that errored ---
        # `substrate_override` is a property of the whole episode: True/False, or
        # None when the receipt does not carry enough to decide.
        "substrate_override": override,
        "errored_by_origin": dict(sorted(by_origin.items())),

        # --- rubric weight: raw counts and weight are different readings ---
        "scored_weight": scored_weight,
        "declared_weight": declared_weight,
        "abstained_weight": weight_by_class[ABSTAINED],
        "unwired_weight": weight_by_class[UNWIRED],
        "structural_weight": weight_by_class[STRUCTURAL],
        "errored_weight": weight_by_class[ERRORED],

        # --- ratios. None means "no denominator", never "zero coverage". ---
        "weight_coverage": (scored_weight / declared_weight
                            if declared_weight else None),
        "signal_coverage": (len(answered) / len(obligated)
                            if obligated else None),

        "signals": per_signal,
    }


def coverage_line(reading: Mapping[str, Any]) -> str:
    """The one-line summary, for a report that already prints the reward.

    Weight and signal count are printed as two readings and never averaged into
    one: a rubric can put 0.45 of its weight on one signal, so "3 of 5 answered"
    and "0.55 of 1.00 in play" are different facts about the same episode.
    """
    wc = reading.get("weight_coverage")
    weight = ("%.4g/%.4g weight (%.0f%%)"
              % (reading["scored_weight"], reading["declared_weight"], wc * 100)
              if wc is not None else
              "%.4g/%.4g weight (no rubric weight declared)"
              % (reading["scored_weight"], reading["declared_weight"]))
    parts = ["%s · %d/%d signals answered"
             % (weight, len(reading["answered"]), len(reading["obligated"]))]
    for label, key in (("declined", "declined"), ("errored", "errored")):
        n = len(reading[key])
        if n:
            parts.append("%d %s" % (n, label))
    return " · ".join(parts)


def coverage_lines(reading: Mapping[str, Any], indent: str = "  ") -> List[str]:
    """The full human block: the summary, then one line per signal that did not
    answer saying *what was missing*.

    An unanswered signal with no stated cause is the failure mode this whole
    feature exists to prevent, so the per-signal reason is not optional detail —
    it is the deliverable. Required signals are marked, because a required signal
    that did not answer is a different severity from an optional one.
    """
    lines = ["coverage " + coverage_line(reading)]
    # One event, many signals. When the run-level rule fired it overwrote every
    # non-pseudo signal at once, so repeating "the substrate failed" once per
    # signal would read as N independent failures. Said once, here, and the
    # per-signal notes point back at it.
    if reading.get("substrate_override"):
        lines.append(
            "substrate override: the agent run failed (timeout, truncated "
            "capture, or an exit code outside allowed_exit_codes), so all %d "
            "errored signals were overwritten at once"
            % len(reading.get("errored") or ()))
    unanswered = reading["required_unanswered"] + reading["optional_unanswered"]
    for sig in sorted(unanswered):
        rec = reading["signals"][sig]
        mark = "required" if rec["required"] else "optional"
        weight = "no weight" if rec["weight"] is None else "weight %.4g" % rec["weight"]
        lines.append("%s%s (%s, %s, %s)" % (indent, sig, mark, weight, rec["response"]))
        lines.append("%s%smissing: %s" % (indent, indent, rec["missing"]))
    return lines
