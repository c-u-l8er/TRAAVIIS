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

Edge rulings from GPT-5.6 (closure), implemented here:

  F1  Reward caps are ``{"when": {"signal": <id>, "state": <state>}, "reward_max":
      <float>}`` under a ``caps`` list (renamed from ``floors``): the trigger state
      is **explicit**, not implied. A cap applies iff that signal is in that state;
      multiple caps take the minimum. This reproduces §7 (patch-fail ≤ 0.25,
      citations-fail ≤ 0.25, tests-fail ≤ 0.40) with the state made explicit.
  F2  An ``error`` episode is ``status = error`` / ``validity = invalid`` /
      ``reward = None``. ``error`` is still not ``fail`` — it does not assert the
      agent was wrong — but it is **not a valid completed evaluation**.
  F3  A required signal reporting ``not_applicable`` is invalid task configuration:
      ``status = invalid`` / ``validity = invalid`` / ``reward = None``. Preferably
      caught by the orchestrator's preflight *before* the agent runs; this is the
      runtime safety net.
  F4  Preflight validates configuration and refuses to run if invalid; invalid
      config therefore never competes in post-run precedence. Post-run precedence
      when events coexist: **tamper → error → normal scoring**.

Because ``caps`` bytes enter ``rew-…`` (see ``identity.reward_id``), the F1 cap
shape is load-bearing and is now frozen per this ruling.
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
    caps = spec.get("caps", []) or []
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

    # F4 post-run precedence: tamper → error → invalid-config → normal. (Static
    # config-invalidity is preflighted by the orchestrator and prevents execution;
    # the required→not_applicable check here is the runtime safety net.)
    if tampered:
        return _result(0.0, STATUS_INVALID, INVALID)

    if any(verification[sig] == ERROR for sig in verification):
        return _result(None, STATUS_ERROR, INVALID)  # F2: error is not a valid eval

    if any(verification[sig] == NOT_APPLICABLE for sig in required):
        return _result(None, STATUS_INVALID, INVALID)  # F3

    reward = 0.0
    for sig, binding in signals.items():
        if verification[sig] == PASS:
            reward += float(binding["weight"])  # type: ignore[index]

    for cap in caps:  # F1: explicit {when: {signal, state}, reward_max}
        when = cap["when"]  # type: ignore[index]
        sig = when["signal"]  # type: ignore[index]
        state = when["state"]  # type: ignore[index]
        if verification.get(sig) == state:
            reward = min(reward, float(cap["reward_max"]))  # type: ignore[index]

    return _result(reward, STATUS_OK, VALID)
