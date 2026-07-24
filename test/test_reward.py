"""Scoring-law battery for the TRAAVIIS reward engine (traaviis.reward).

Pure scoring only — no subprocess, no verifier execution, no I/O. Each test pins
one row of the frozen tables in RFC_EVIDENCE_RESIDENCY.md §6a (verifier state →
reward behavior) and §7 (weighted sum + hard floors), plus the four under-frozen
edges F1–F4 flagged in traaviis/reward.py for GPT-5.6.

Runs with pytest, or standalone: `python3 test/test_reward.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import reward as R  # noqa: E402


# The frozen Residency v1 rubric (RFC_TRAAVIIS_ARTIFACTS §2 / Evidence §7):
# weights sum to 1.0; floors reproduce §7's three fail-caps.
SPEC = {
    "reward_spec_version": "traaviis.reward.v1",
    "substrate_profile": "residency.repository.v1",
    "signals": {
        "citations":            {"verifier": "residency.citations.v1", "weight": 0.25},
        "patch":                {"verifier": "residency.patch.v1",     "weight": 0.20},
        "tests":                {"verifier": "residency.tests.v1",     "weight": 0.30},
        "identity":             {"verifier": "residency.identity.v1",  "weight": 0.15},
        "finding_completeness": {"verifier": "residency.finding.v1",   "weight": 0.10},
    },
    "floors": [
        {"when": "patch", "reward_max": 0.25},
        {"when": "citations", "reward_max": 0.25},
        {"when": "tests", "reward_max": 0.40},
    ],
    "aggregation": "terminal",
}

REQUIRED = ["citations", "patch", "tests", "identity"]


def all_pass():
    return {
        "citations": R.PASS, "patch": R.PASS, "tests": R.PASS,
        "identity": R.PASS, "finding_completeness": R.PASS,
        # non-scored verifiers present in the total map:
        "native": R.NOT_APPLICABLE, "oracle": R.NOT_APPLICABLE,
    }


def approx(a, b, eps=1e-9):
    return abs(a - b) < eps


# --------------------------------------------------------------------------- #
# frozen core: weighted sum (§6a pass/fail, §7)                               #
# --------------------------------------------------------------------------- #

def test_all_pass_is_full_reward():
    r = R.score(all_pass(), SPEC, REQUIRED)
    assert r["status"] == R.STATUS_OK
    assert r["validity"] == R.VALID
    assert approx(r["reward"], 1.0)


def test_fail_signal_loses_only_its_weight_before_floors():
    v = all_pass()
    v["identity"] = R.FAIL  # identity has no floor; costs exactly its 0.15
    r = R.score(v, SPEC, REQUIRED)
    assert r["status"] == R.STATUS_OK
    assert approx(r["reward"], 0.85)


def test_non_scored_verifier_state_does_not_move_reward():
    v = all_pass()
    v["native"] = R.PASS  # native/oracle are not reward signals
    v["oracle"] = R.FAIL
    r = R.score(v, SPEC, REQUIRED)
    assert approx(r["reward"], 1.0)


def test_non_required_signal_not_applicable_is_allowed_and_loses_weight():
    v = all_pass()
    v["finding_completeness"] = R.NOT_APPLICABLE  # not in REQUIRED
    r = R.score(v, SPEC, REQUIRED)
    assert r["status"] == R.STATUS_OK
    assert r["validity"] == R.VALID
    assert approx(r["reward"], 0.90)


# --------------------------------------------------------------------------- #
# frozen floors (§7)                                                          #
# --------------------------------------------------------------------------- #

def test_patch_fail_caps_reward_at_quarter():
    v = all_pass()
    v["patch"] = R.FAIL  # weighted sum would be 0.80; floor caps to 0.25
    r = R.score(v, SPEC, REQUIRED)
    assert approx(r["reward"], 0.25)


def test_citations_fail_caps_reward_at_quarter():
    v = all_pass()
    v["citations"] = R.FAIL  # sum 0.75 → cap 0.25
    r = R.score(v, SPEC, REQUIRED)
    assert approx(r["reward"], 0.25)


def test_tests_fail_caps_reward_at_four_tenths():
    v = all_pass()
    v["tests"] = R.FAIL  # sum 0.70 → cap 0.40
    r = R.score(v, SPEC, REQUIRED)
    assert approx(r["reward"], 0.40)


def test_lowest_applicable_floor_wins():
    v = all_pass()
    v["patch"] = R.FAIL   # cap 0.25
    v["tests"] = R.FAIL   # cap 0.40
    r = R.score(v, SPEC, REQUIRED)  # min(sum, 0.25, 0.40)
    assert approx(r["reward"], 0.25)


def test_floor_only_applies_on_fail_not_on_not_applicable():
    # A floor keys on FAIL; a non-required N/A signal must not trip a cap.
    spec = dict(SPEC, floors=[{"when": "finding_completeness", "reward_max": 0.1}])
    v = all_pass()
    v["finding_completeness"] = R.NOT_APPLICABLE
    r = R.score(v, spec, REQUIRED)
    assert approx(r["reward"], 0.90)  # not capped to 0.1


# --------------------------------------------------------------------------- #
# tamper / error / invalid-config (§6a) + flagged edges F2–F4                 #
# --------------------------------------------------------------------------- #

def test_tampered_subject_is_zero_and_invalid():
    r = R.score(all_pass(), SPEC, REQUIRED, tampered=True)
    assert r["reward"] == 0.0
    assert r["status"] == R.STATUS_INVALID
    assert r["validity"] == R.INVALID


def test_error_state_is_null_reward_and_error_status():  # F2
    v = all_pass()
    v["tests"] = R.ERROR  # substrate unavailability, not a wrong answer
    r = R.score(v, SPEC, REQUIRED)
    assert r["reward"] is None
    assert r["status"] == R.STATUS_ERROR
    assert r["validity"] == R.VALID


def test_required_not_applicable_is_invalid_config_null_reward():  # F3
    v = all_pass()
    v["patch"] = R.NOT_APPLICABLE  # patch is REQUIRED → invalid task config
    r = R.score(v, SPEC, REQUIRED)
    assert r["reward"] is None
    assert r["status"] == R.STATUS_INVALID
    assert r["validity"] == R.INVALID


def test_tamper_precedes_error():  # F4
    v = all_pass()
    v["tests"] = R.ERROR
    r = R.score(v, SPEC, REQUIRED, tampered=True)
    assert r["reward"] == 0.0  # tamper wins → 0, not null
    assert r["status"] == R.STATUS_INVALID


def test_error_precedes_invalid_config():  # F4
    v = all_pass()
    v["patch"] = R.NOT_APPLICABLE  # would be invalid-config
    v["tests"] = R.ERROR           # but an error dominates
    r = R.score(v, SPEC, REQUIRED)
    assert r["status"] == R.STATUS_ERROR
    assert r["reward"] is None


# --------------------------------------------------------------------------- #
# determinism + boundary validation                                           #
# --------------------------------------------------------------------------- #

def test_scoring_is_deterministic():
    v = all_pass()
    v["tests"] = R.FAIL
    assert R.score(v, SPEC, REQUIRED) == R.score(v, SPEC, REQUIRED)


def test_unknown_state_raises():
    v = all_pass()
    v["tests"] = "maybe"
    try:
        R.score(v, SPEC, REQUIRED)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown verifier state")


def test_missing_scored_signal_raises():
    v = all_pass()
    del v["identity"]
    try:
        R.score(v, SPEC, REQUIRED)
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing scored signal")


# --------------------------------------------------------------------------- #
# standalone runner (zero deps)                                               #
# --------------------------------------------------------------------------- #

def _main():
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures.append((name, exc))
            print(f"FAIL  {name}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
