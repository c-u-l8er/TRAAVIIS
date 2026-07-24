"""Deterministic reward scoring for TRAAVIIS episodes.

A **pure function** over a sealed verification map and a ``RewardSpecV1``. No I/O,
no subprocess, no verifier execution: the verifier *states* are inputs here; this
module only turns states + weights + floors into a ``reward`` number and the
``status`` / ``validity`` a receipt carries, per the frozen tables in
``RFC_EVIDENCE_RESIDENCY.md`` §6a (state → reward behavior) and §7 (weighted sum +
hard floors). It is the RLVR scoring core: identical inputs give an identical
reward, so an episode's score is auditable and reproducible.

Frozen and unambiguous (implemented exactly):

- Verifier states (§6) are ``pass | fail | not_applicable | error``; the
  verification map is *total* over every declared reward signal.
- A ``pass`` signal contributes its ``weight``; ``fail`` / ``not_applicable``
  contribute zero (§6a). ``reward`` is the weighted sum over the spec's signals
  (§7). Non-signal verifiers in the map (e.g. ``native`` / ``oracle``) are not
  scored.
- ``not_applicable`` is allowed only for a signal the task does not require; a
  **required** signal that reports ``not_applicable`` is an invalid task
  configuration (§6a).
- A **tampered** subject is an invalid episode: ``reward = 0`` and
  ``validity = invalid`` (§6a, §7).
- An ``error`` state is substrate unavailability, *not* evidence of a wrong
  answer: the episode's ``status`` becomes ``error`` and ``reward`` is ``None``,
  never ``0`` (§6a). Downstream aggregation drops ``None`` rewards; it never
  averages them in as zeros.

Under-frozen edges — decisions made here and **flagged for GPT-5.6** (the RFC
shows floors as ``[ … ]`` and does not pin these rare interactions):

  F1  Floor shape. A floor is ``{"when": <signal_id>, "reward_max": <float>}`` and
      caps the final reward iff that signal's state is ``fail``; multiple caps
      take the minimum. This reproduces §7 exactly (patch-fail ≤ 0.25,
      citations-fail ≤ 0.25, tests-fail ≤ 0.40). The "tampered snapshot → 0"
      rule is the tamper path, not a floor object.
  F2  ``error`` episodes are ``validity = valid`` (substrate unavailability is
      not invalidity); ``status = error``; ``reward = None``.
  F3  Invalid task configuration (a required signal reporting
      ``not_applicable``) is ``status = invalid`` / ``validity = invalid`` /
      ``reward = None`` (scoring is not meaningful) — distinct from a tamper,
      which is ``reward = 0``.
  F4  Precedence when several apply: **tamper → error → invalid-config →
      normal**. (Tamper voids everything; an ``error`` means scoring could not be
      computed at all, so it dominates a static config problem.)

Because ``floors`` bytes enter ``rew-…`` (see ``identity.reward_id``), the F1
floor shape is the load-bearing item for GPT-5.6 to ratify before any real
``RewardSpecV1`` is authored.
"""

from typing import Iterable, Mapping, Optional

__all__ = [
    "PASS", "FAIL", "NOT_APPLICABLE", "ERROR", "STATES",
    "STATUS_OK", "STATUS_ERROR", "STATUS_INVALID",
    "VALID", "INVALID",
    "score",
]

# --- Verifier states (RFC Evidence Residency §6) -----------------------------
PASS = "pass"
FAIL = "fail"
NOT_APPLICABLE = "not_applicable"
ERROR = "error"
STATES = frozenset({PASS, FAIL, NOT_APPLICABLE, ERROR})

# --- Receipt status / validity vocab (RFC §11) -------------------------------
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_INVALID = "invalid"
VALID = "valid"
INVALID = "invalid"


def _result(reward: Optional[float], status: str, validity: str) -> dict:
    return {"reward": reward, "status": status, "validity": validity}


def score(
    verification: Mapping[str, str],
    spec: Mapping[str, object],
    required: Iterable[str],
    *,
    tampered: bool = False,
) -> dict:
    """Score one episode into ``{reward, status, validity}``.

    ``verification`` maps every declared verifier id to one of ``STATES`` (total
    map, §6). ``spec`` is a ``RewardSpecV1`` whose ``signals`` are the scored
    subset ``{signal_id: {"verifier", "weight"}}`` with optional ``floors``.
    ``required`` is the set of signal ids the task marks required (its
    ``verifier_plan.required``). Returns the receipt score fields.

    Boundary validation only: an unknown state, or a required/scored signal
    absent from the (contractually total) verification map, is a caller bug and
    raises ``ValueError``.
    """
    signals: Mapping[str, Mapping[str, object]] = spec.get("signals", {})  # type: ignore[assignment]
    floors = spec.get("floors", []) or []
    required = set(required)

    for sig, state in verification.items():
        if state not in STATES:
            raise ValueError(f"unknown verifier state for {sig!r}: {state!r}")
    for sig in signals:
        if sig not in verification:
            raise ValueError(f"scored signal {sig!r} missing from verification map")
    for sig in required:
        if sig not in verification:
            raise ValueError(f"required signal {sig!r} missing from verification map")

    # F4 precedence: tamper → error → invalid-config → normal.
    if tampered:
        return _result(0.0, STATUS_INVALID, INVALID)

    if any(verification[sig] == ERROR for sig in verification):
        return _result(None, STATUS_ERROR, VALID)  # F2

    if any(verification[sig] == NOT_APPLICABLE for sig in required):
        return _result(None, STATUS_INVALID, INVALID)  # F3

    reward = 0.0
    for sig, binding in signals.items():
        if verification[sig] == PASS:
            reward += float(binding["weight"])  # type: ignore[index]

    for floor in floors:  # F1
        when = floor["when"]  # type: ignore[index]
        if when in verification and verification[when] == FAIL:
            reward = min(reward, float(floor["reward_max"]))  # type: ignore[index]

    return _result(reward, STATUS_OK, VALID)
