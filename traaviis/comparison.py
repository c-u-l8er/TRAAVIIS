"""`trvs compare` -- pairwise comparison of two closed episode bundles (§8).

Answers *"which of these two candidates did better on this task, and where did
they differ?"* over evidence that already exists. It runs **no agent**: both
sides are opened and replayed to `closed` first, and a bundle that will not
reverify is not comparable.

The admission order is the whole contract:

    open left  -> verification-replay to CLOSED
    open right -> verification-replay to CLOSED
    require the same task_id
    emit ComparisonV1

Requiring one `task_id` is what makes the two numbers mean the same thing. A
`task-…` fixes the task bytes, and through them the frozen subject and the
reward binding, so both episodes were scored by one rubric. Comparing rewards
across two tasks would be comparing two different questions.

`ComparisonV1` is an **ordinary deterministic report**. It carries no id of its
own and mints nothing: there is no `compare-…` rung, because a comparison is a
*reading* of two sealed episodes, not a new artifact anyone needs to re-derive.
Everything in it is already addressed by the ids it quotes.

Two rules the reward relation must not break:

- **A null reward is `incomparable`, never zero.** An errored or unscored
  episode did not score badly; it did not score. Imputing zero would silently
  rank a fixture failure below a bad-but-real attempt.
- **Equal rewards do not hide a different trace.** There is no secondary
  tie-breaker. Two candidates that scored the same *are* equal under the rubric,
  and the fact that they got there differently is reported separately as a trace
  relation rather than folded into the ranking.
"""

import json
import os
import tempfile
from typing import Any, Dict, Mapping, Optional

from .substrates import AdmissionError

__all__ = [
    "COMPARISON_VERSION",
    "ComparisonError",
    "compare_episodes",
    "write_comparison",
    "REWARD_LEFT_HIGHER",
    "REWARD_RIGHT_HIGHER",
    "REWARD_EQUAL",
    "REWARD_INCOMPARABLE",
]

COMPARISON_VERSION = "traaviis.comparison.v1"

#: The four reward relations. `incomparable` is not a failure of the comparison
#: -- it is the correct answer when at least one side has no score.
REWARD_LEFT_HIGHER = "left_higher"
REWARD_RIGHT_HIGHER = "right_higher"
REWARD_EQUAL = "equal"
REWARD_INCOMPARABLE = "incomparable"

#: Receipt fields lifted verbatim into each side of the report.
_SIDE_FIELDS = ("episode_id", "status", "validity", "reward", "trace_id",
                "outputs", "verification", "verifier_versions",
                "execution_facts")

#: The fields a difference is computed over. Each is reported independently:
#: collapsing them would hide *which* kind of divergence occurred.
_DIFF_FIELDS = ("outputs", "verification", "verification_evidence",
                "verifier_versions", "execution_facts")


class ComparisonError(AdmissionError):
    """A typed comparison failure. Same shape as an admission failure."""


def _replay(bundle_dir, extra_verifiers, side):
    """Open and reverify one bundle, or raise the typed refusal.

    A mismatch and an unavailable bundle are deliberately different codes: one
    says the evidence disagrees with itself, the other says this runtime cannot
    judge it. Only the first is a statement about the episode.
    """
    from . import episode_bundle

    if not os.path.isdir(bundle_dir):
        raise ComparisonError(
            "EPISODE_UNAVAILABLE",
            "%s: no such episode directory: %s" % (side, bundle_dir))

    report = episode_bundle.verify_episode_bundle(
        bundle_dir, extra_verifiers=extra_verifiers)
    outcome = report.get("outcome")

    if outcome == episode_bundle.OUTCOME_MISMATCH:
        raise ComparisonError(
            "EPISODE_EVIDENCE_MISMATCH",
            "%s: bundle did not reverify (evidence mismatch)" % side,
            {"bundle": os.path.basename(os.path.realpath(bundle_dir)),
             "episode_id": report.get("episode_id")})
    if outcome != episode_bundle.OUTCOME_CLOSED:
        raise ComparisonError(
            "EPISODE_UNAVAILABLE",
            "%s: bundle could not be judged here (%s)" % (side, outcome),
            {"bundle": os.path.basename(os.path.realpath(bundle_dir)),
             "outcome": outcome})

    receipt = _read_receipt(bundle_dir, side)
    return receipt


def _read_receipt(bundle_dir, side):
    path = os.path.join(bundle_dir, "receipt.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError) as ex:
        raise ComparisonError(
            "EPISODE_UNAVAILABLE",
            "%s: could not read sealed receipt: %s" % (side, ex))


def _reward_relation(left, right):
    """The directed relation between two rewards under one rubric.

    Returns `(relation, right_minus_left)`. The delta is `None` whenever the
    relation is `incomparable`, so a consumer cannot accidentally arithmetic its
    way past a missing score.
    """
    lr, rr = left.get("reward"), right.get("reward")
    if not isinstance(lr, (int, float)) or not isinstance(rr, (int, float)):
        return REWARD_INCOMPARABLE, None
    if isinstance(lr, bool) or isinstance(rr, bool):
        return REWARD_INCOMPARABLE, None

    delta = rr - lr
    if delta > 0:
        return REWARD_RIGHT_HIGHER, delta
    if delta < 0:
        return REWARD_LEFT_HIGHER, delta
    return REWARD_EQUAL, delta


def _side(receipt):
    """One side of the report: the sealed fields, quoted, nothing derived."""
    return {field: receipt.get(field) for field in _SIDE_FIELDS}


def _difference(left_value, right_value):
    """A per-field difference, or None when the two sides agree.

    Mappings are reduced to the keys that actually differ, so a reader sees the
    changed signal rather than two full copies of a mostly identical map. Any
    other value is quoted whole.
    """
    if left_value == right_value:
        return None
    if isinstance(left_value, Mapping) and isinstance(right_value, Mapping):
        changed = {}
        for key in sorted(set(left_value) | set(right_value)):
            lv, rv = left_value.get(key), right_value.get(key)
            if lv != rv:
                changed[key] = {"left": lv, "right": rv}
        return changed or None
    return {"left": left_value, "right": right_value}


def _differences(left, right):
    out = {}
    for field in _DIFF_FIELDS:
        out[field] = _difference(left.get(field), right.get(field))
    return out


def _wire(registry, extra_verifiers):
    """Resolve the one replay seam, and derive its attestation from it.

    Two seams, mutually exclusive, exactly as `eval_split` has:

    - `registry=`        take the implementations from this registry, and name
                         it in the report;
    - `extra_verifiers=` the caller brought its own, so there is no registry to
                         interrogate and the report must say so.

    Returns `(implementations, runtime_context)` **together**, from one branch,
    because that is the only structure in which the report cannot describe a
    different runtime than the one that did the work. An earlier version took
    both and a `runtime_context=` besides: supplying `extra_verifiers` and
    `registry` replayed with the former while attesting the latter, and a caller
    could hand in a fabricated context outright. The CLI never did either, which
    is precisely why it went unnoticed -- so the fix is structural rather than a
    warning in the docstring.
    """
    if registry is not None and extra_verifiers is not None:
        raise ComparisonError(
            "VERIFIER_WIRING_AMBIGUOUS",
            "pass either registry= (implementations from that registry) or "
            "extra_verifiers= (caller-supplied implementations), not both")

    if extra_verifiers is not None:
        # No registry stands behind these, so none is claimed. A copy, so a
        # later mutation by the caller cannot desynchronize the replay from the
        # attestation that describes it.
        return dict(extra_verifiers), _runtime_context(None)

    from . import wiring
    if registry is None:
        registry = wiring.default_registry(None)
    implementations = {}
    for signal in registry.available():
        impl = registry.get(signal)
        if impl is not None:
            implementations[signal] = impl
    return implementations, _runtime_context(registry)


def compare_episodes(left_dir, right_dir, *, registry=None,
                     extra_verifiers=None):
    """Compare two closed episode bundles over one task. Returns `ComparisonV1`.

    Wire the replay with `registry=` *or* `extra_verifiers=`, never both. There
    is deliberately no `runtime_context=` parameter: the attestation is derived
    from whichever seam performed the replay, so it cannot be supplied
    independently of the implementations it describes.

    Raises `ComparisonError` -- never a bare exception -- if the wiring is
    ambiguous, if either bundle is unavailable or fails to reverify, or if the
    two do not share a `task_id`. Nothing is written and no agent is launched.
    """
    verifiers, runtime_context = _wire(registry, extra_verifiers)

    left = _replay(left_dir, verifiers, "left")
    right = _replay(right_dir, verifiers, "right")

    if left.get("task_id") != right.get("task_id"):
        raise ComparisonError(
            "TASK_MISMATCH",
            "episodes answer different tasks; there is no shared rubric to "
            "compare them under",
            {"left_task_id": left.get("task_id"),
             "right_task_id": right.get("task_id")})

    relation, delta = _reward_relation(left, right)

    return {
        "comparison_version": COMPARISON_VERSION,
        "substrate_profile": left.get("substrate_profile"),
        "task_id": left.get("task_id"),
        "reward_id": left.get("reward_id"),
        "subject": left.get("subject"),
        "runtime_context": runtime_context,
        "left": _side(left),
        "right": _side(right),
        "relation": {
            "reward": relation,
            "right_minus_left": delta,
            "same_episode": left.get("episode_id") == right.get("episode_id"),
            "same_trace": left.get("trace_id") == right.get("trace_id"),
        },
        "differences": _differences(left, right),
    }


def _runtime_context(registry):
    """Attest the registry that supplied the *replay* verifiers.

    A comparison is only as trustworthy as the implementations that reverified
    the two bundles, so the report names them for the same reason
    `EvaluationV1` does.

    Private, and reached only from `_wire`. Deriving the attestation next to the
    seam that produced the implementations is what keeps the two from drifting.
    """
    if registry is None:
        return {"registry_version": None, "wiring": "caller_supplied",
                "verifiers_available": [], "verifier_versions": {}}
    return {
        "registry_version": registry.registry_version,
        "wiring": "registry",
        "verifiers_available": registry.available(),
        "verifier_versions": registry.versions(),
    }


def write_comparison(comparison, path):
    """Write a `ComparisonV1` atomically. Returns the path written.

    Same discipline as every other artifact writer here: a temp file in the
    destination directory, then `os.replace`. A reader never sees a partial
    comparison, and a failed write leaves no stub behind to be mistaken for one.
    """
    path = os.path.abspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".comparison-", dir=directory)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(comparison, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
