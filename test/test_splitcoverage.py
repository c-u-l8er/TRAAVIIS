"""Laws for `traaviis.split-coverage.v1` — aggregating coverage over a split.

`response_coverage` reads one episode. This aggregates a split, and the whole
difficulty is that **"aggregate" has two defensible arithmetics and they
disagree**:

    pooled   sum the numerators, sum the denominators, divide.
             Every unit of rubric weight counts once.
    mean     average the per-episode ratios.
             Every episode counts once.

On a split of one heavy fully-answered task and nine light unanswered ones those
read 50% and 10%. Either is a correct answer to a different question, and a
reader shown one of them and told it is "the coverage" has been misled about the
other. So both are reported and neither is named *the* coverage — the same
refusal `coverage` already makes between weight and signal count.

The laws:

    S1  pooled and mean are separate readings, and they really do diverge
    S2  a task with no receipt is counted and excluded, and both halves matter
    S3  an `error` episode stays in the denominator
    S4  `None` means no denominator, never zero coverage
    S5  mixing runner profiles is not strict-comparison eligible
    S6  a best-effort profile is not strict-comparison eligible
    S7  the aggregate is pure: no I/O, no clock, no identity
    S8  it says *which* signals went unanswered, not only how much weight did
    S9  it composes with a real `response_coverage` reading end to end
    S10 (item 12) two episodes score 0.55 and mean completely different things
    S11 the aggregate of that pair reports the gap the rewards hide

Run directly:      python3 test/test_splitcoverage.py
Run under pytest:  pytest test/test_splitcoverage.py
"""
import ast
import inspect
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import coverage as C  # noqa: E402
from traaviis import execfacts as EF  # noqa: E402

BEST_EFFORT = "residency.trusted-local.v1"
CERTIFIED = EF.CERTIFIED_RUNNER_PROFILE


class Skip(Exception):
    pass


def _reading(scored, declared, answered, obligated,
             required_unanswered=(), unwired=(), errored_by_origin=None):
    """A `response_coverage`-shaped reading, hand-built for arithmetic laws.

    Hand-built rather than derived from a real episode, deliberately: these laws
    are about what the *aggregation* does with numbers, and generating the
    numbers from real episodes would make the arithmetic depend on whichever
    fixtures happen to exist. S9 is the one that composes with the real reader.
    """
    return {
        "scored_weight": scored, "declared_weight": declared,
        "answered": ["a%d" % i for i in range(answered)],
        "obligated": ["o%d" % i for i in range(obligated)],
        "weight_coverage": (scored / declared) if declared else None,
        "signal_coverage": (answered / obligated) if obligated else None,
        "required_unanswered": list(required_unanswered),
        "unwired": list(unwired),
        "errored_by_origin": dict(errored_by_origin or {}),
    }


def _episode(task_id, reading, status="ok", profile=BEST_EFFORT):
    return {"task_id": task_id, "status": status, "reading": reading,
            "runner_profile": profile}


# ============================================ S1: two arithmetics, not one

def test_s1_pooled_and_mean_are_two_readings_and_they_diverge():
    """One heavy answered task and nine light unanswered ones: 50% and 10%.

    This is the whole reason both are reported. If they always agreed the second
    would be noise; they do not, and the gap is as large as the weighting is
    skewed. A single "coverage" number for this split would be defensible
    whichever one it was and misleading either way.

    The divergence is *constructed* rather than looked for, so the law states an
    exact pair rather than an inequality that a rounding change could satisfy.
    """
    episodes = [_episode("heavy", _reading(9.0, 9.0, 5, 5))]
    episodes += [_episode("light%d" % i, _reading(0.0, 1.0, 0, 5))
                 for i in range(9)]

    aggregate = C.split_coverage(episodes)
    assert abs(aggregate["pooled_weight_coverage"] - 0.5) < 1e-9, aggregate
    assert abs(aggregate["mean_weight_coverage"] - 0.1) < 1e-9, aggregate
    assert aggregate["pooled_weight_coverage"] != aggregate["mean_weight_coverage"]

    # ...and the pooled *signal* reading is a third number again, because a
    # rubric can put most of its weight on one of its signals. Three readings,
    # none of them averageable into the others.
    assert abs(aggregate["pooled_signal_coverage"] - 0.1) < 1e-9, aggregate

    # Neither is called "coverage". A key by that name would be read as the
    # answer and would make the other two decorative.
    assert "weight_coverage" not in aggregate, sorted(aggregate)
    assert "coverage" not in aggregate, sorted(aggregate)


# ================================= S2-S4: denominators, and what is not in them

def test_s2_an_unreadable_task_is_counted_and_excluded():
    """A task with no receipt is in `tasks`, absent from every ratio, and named.

    Two wrong answers this rules out. Dropping it silently inflates coverage by
    discarding exactly the tasks that went worst — the agent would not launch,
    admission refused the fixture. Scoring it zero invents an adjudication that
    never happened: nothing was measured, so there is no numerator *or*
    denominator to contribute.

    The gap between `tasks` and `read` is therefore reported rather than left
    for a reader to notice, and the task ids are listed so the gap can be
    investigated instead of merely counted.
    """
    episodes = [_episode("ok1", _reading(1.0, 1.0, 2, 2)),
                _episode("ok2", _reading(0.0, 1.0, 0, 2)),
                _episode("lost", None, status="error")]

    aggregate = C.split_coverage(episodes)
    assert aggregate["tasks"] == 3
    assert aggregate["read"] == 2
    assert aggregate["unreadable"] == 1
    assert aggregate["unreadable_task_ids"] == ["lost"]

    # The ratios are over the two that were read, not over three.
    assert abs(aggregate["pooled_weight_coverage"] - 0.5) < 1e-9
    assert abs(aggregate["mean_weight_coverage"] - 0.5) < 1e-9

    # And an unreadable episode makes the split ineligible for strict
    # comparison whatever else is true: a coverage number computed over a
    # subset of the tasks that were asked is not a coverage number for the
    # split.
    assert "unreadable_episodes:1" in aggregate["uncertifiable_reasons"]


def test_s3_an_error_episode_stays_in_the_denominator():
    """Coverage must not be improvable by breaking a verifier.

    An episode whose verifiers errored has a receipt, a reading and a real
    denominator — its signals are `errored`, which is *not* answered. If such an
    episode were excluded, a split could raise its coverage by making verifiers
    fail, which is the shape of every erasure route in this repository.

    The 9E ruling put it directly: an error episode is not a successful
    evaluation, it stays invalid, and it must remain in split-level coverage so
    that `reward: null` is never mistaken for a good result.

    Measured as a comparison between two splits that differ only in whether the
    error episode is present, because "it is included" is a claim about the
    denominator and the denominator is what a reader cannot see.
    """
    good = _episode("t1", _reading(2.0, 2.0, 2, 2))
    errored = _episode("t2", _reading(0.0, 2.0, 0, 2,
                                      errored_by_origin={"verifier_exception":
                                                         ["tests", "identity"]}),
                       status="error")

    without = C.split_coverage([good])
    with_error = C.split_coverage([good, errored])

    assert without["pooled_weight_coverage"] == 1.0
    assert abs(with_error["pooled_weight_coverage"] - 0.5) < 1e-9, with_error
    assert with_error["read"] == 2, "the errored episode was dropped"
    assert with_error["by_status"]["error"] == 1
    assert with_error["errored_by_origin"] == {"verifier_exception": 2}


def test_s4_none_means_no_denominator_and_never_zero_coverage():
    """An empty split reads `None`, not `0.0`.

    The rule `response_coverage` already states, carried up: a ratio with no
    denominator is *unknown*, and reporting it as zero would be the strongest
    possible claim made from the least possible evidence. It is the same
    inference-from-absence that the containment work spent two slices removing.
    """
    empty = C.split_coverage([])
    assert empty["pooled_weight_coverage"] is None
    assert empty["mean_weight_coverage"] is None
    assert empty["pooled_signal_coverage"] is None
    assert empty["mean_signal_coverage"] is None
    assert empty["tasks"] == 0 and empty["read"] == 0

    # A split whose rubric declares no weight at all is the same case, and must
    # not read as fully covered either.
    weightless = C.split_coverage([_episode("t", _reading(0.0, 0.0, 0, 0))])
    assert weightless["pooled_weight_coverage"] is None, weightless
    assert weightless["mean_weight_coverage"] is None, weightless


# ============================ S5-S6: an aggregate is as weak as its weakest member

def test_s5_mixing_runner_profiles_is_not_strict_comparison_eligible():
    """One certified episode and one best-effort one do not make a certified number.

    This is the laundering path: aggregate a certified episode with a
    best-effort one and the result inherits the stronger label from neither, but
    *looks* like a single figure that could be published. The profiles are
    listed so the mixture is visible, and the aggregate is refused for strict
    comparison whatever the ratios say.
    """
    mixed = C.split_coverage([
        _episode("a", _reading(1.0, 1.0, 1, 1), profile=CERTIFIED),
        _episode("b", _reading(1.0, 1.0, 1, 1), profile=BEST_EFFORT)])

    assert mixed["pooled_weight_coverage"] == 1.0, "the arithmetic is unaffected"
    assert mixed["strict_comparison_eligible"] is False
    assert "mixed_runner_profiles" in mixed["uncertifiable_reasons"]
    assert mixed["runner_profiles"] == sorted([CERTIFIED, BEST_EFFORT])


def test_s6_a_best_effort_split_is_not_strict_comparison_eligible():
    """Every episode in this tree today is best-effort, and the aggregate says so.

    A uniform best-effort split has nothing inconsistent about it — the number
    is arithmetically fine and useful for developing coverage semantics, which
    is exactly what the 9F ruling authorized it for. What it may not do is enter
    a strict comparison or be published as evidence-grade, and the aggregate
    carries that refusal itself rather than relying on a caller to remember.

    The certified case is asserted too, so this law fails if
    `strict_comparison_eligible` ever degenerates into "always false" — which
    would pass the interesting half of this test for the wrong reason.
    """
    best_effort = C.split_coverage([_episode("a", _reading(1.0, 1.0, 1, 1))])
    assert best_effort["strict_comparison_eligible"] is False
    assert ("uncertified_runner_profile:" + BEST_EFFORT
            in best_effort["uncertifiable_reasons"])

    certified = C.split_coverage([
        _episode("a", _reading(1.0, 1.0, 1, 1), profile=CERTIFIED)])
    assert certified["strict_comparison_eligible"] is True, certified

    # An unclassified profile is refused rather than assumed fine — the same
    # rule `execfacts.strict_comparison_eligible` applies one level down.
    unknown = C.split_coverage([
        _episode("a", _reading(1.0, 1.0, 1, 1), profile="something.made.up")])
    assert unknown["strict_comparison_eligible"] is False
    assert any(r.startswith("uncertified_runner_profile:")
               for r in unknown["uncertifiable_reasons"])


# ================================================ S7-S9: shape, detail, and reality

def test_s7_the_aggregate_is_pure_and_mints_no_identity():
    """No I/O, no clock, no id — read off the parse tree.

    `response_coverage` earns its "derivable by any second consumer" claim by
    touching nothing; the aggregate is a function of its outputs and must earn
    the same one. An aggregate that opened a file would be reading something the
    episodes do not already determine, and an aggregate that minted an id would
    be storing a value those bytes already fix — which is why neither this nor
    `ComparisonV1` has one.
    """
    tree = ast.parse(inspect.getsource(C.split_coverage))
    calls = {ast.unparse(n.func) for n in ast.walk(tree)
             if isinstance(n, ast.Call)}
    for forbidden in ("open", "os.listdir", "os.stat", "time.time",
                      "time.monotonic", "subprocess.run", "print"):
        assert forbidden not in calls, "%s is not pure: %s" % ("split_coverage",
                                                               forbidden)
    aggregate = C.split_coverage([_episode("a", _reading(1.0, 1.0, 1, 1))])
    for key in aggregate:
        assert not key.endswith("_id") or key == "unreadable_task_ids", key
    assert "split_coverage_version" in aggregate
    assert aggregate["split_coverage_version"] == C.SPLIT_COVERAGE_VERSION
    # It names the per-episode version it aggregates, so a reading built from a
    # different `response_coverage` cannot be mistaken for this one.
    assert aggregate["coverage_version"] == C.COVERAGE_VERSION


def test_s8_the_aggregate_says_which_signals_went_unanswered():
    """A number says how much is missing; this says what.

    "0.55 covered" is not actionable and "`tests` unanswered in 7 of 10 tasks"
    is. The union is kept with counts rather than as a set, because one task
    missing `identity` and seven missing it are different facts about a split
    and a set flattens them into the same one.
    """
    episodes = [_episode("t%d" % i,
                         _reading(0.5, 1.0, 1, 2,
                                  required_unanswered=["tests"],
                                  unwired=["identity"]))
                for i in range(7)]
    episodes.append(_episode("t7", _reading(1.0, 1.0, 2, 2)))

    aggregate = C.split_coverage(episodes)
    assert aggregate["required_unanswered"] == {"tests": 7}, aggregate
    assert aggregate["unwired"] == {"identity": 7}, aggregate

    lines = C.split_coverage_lines(aggregate)
    assert any("tests x7" in line for line in lines), lines
    assert any("NOT strict-comparison eligible" in line for line in lines), lines


def test_s9_it_composes_with_a_real_response_coverage_reading():
    """End to end on the shipped episode, not on hand-built numbers.

    S1-S8 are arithmetic laws over a reading whose shape this file writes down,
    which proves the aggregation and proves nothing about whether that shape is
    the real one. This law takes the actual `response_coverage` output for the
    committed `residency-demo` episode and aggregates it — so a field renamed in
    `coverage` breaks here rather than silently producing a number over keys
    that no longer exist.
    """
    import json

    # Discovered rather than named. A hard-coded `episode-<64 hex>` path is a
    # pin on one checkout's identity, and this law is about the reader composing
    # with the aggregate -- not about which episode happens to be committed.
    episodes = os.path.join(REPO, "examples", "eval-one", "episodes")
    if not os.path.isdir(episodes):
        raise Skip("no committed episodes/ directory in this checkout")
    found = [os.path.join(episodes, name)
             for name in sorted(os.listdir(episodes))
             if name.startswith("episode-")
             and os.path.isfile(os.path.join(episodes, name,
                                             "episode-bundle.json"))]
    if not found:
        raise Skip("no sealed episode bundle to read")

    bundle = found[0]

    def member(*parts):
        with open(os.path.join(bundle, *parts), encoding="utf-8") as fh:
            return json.load(fh)

    manifest = member("episode-bundle.json")
    members = manifest["members"]
    receipt = member(members["receipt"])
    # The manifest calls it `reward`, not `reward_spec`. Read the member name
    # from the manifest rather than from what the argument is called at the
    # other end -- guessing it is how a law comes to skip on a tree that has
    # the thing it was looking for.
    reward_spec = member(members["reward"])
    task = member(members["task"])

    reading = C.response_coverage(receipt, reward_spec, task)
    aggregate = C.split_coverage([
        {"task_id": receipt.get("task_id"), "status": receipt.get("status"),
         "reading": reading,
         "runner_profile": ((receipt.get("execution_facts") or {}).get("runner")
                            or {}).get("profile")}])

    assert aggregate["tasks"] == 1 and aggregate["read"] == 1
    # A single-episode split must agree with the episode it contains, on both
    # arithmetics. If pooled and mean disagreed here, one of them is wrong.
    assert aggregate["pooled_weight_coverage"] == reading["weight_coverage"]
    assert aggregate["mean_weight_coverage"] == reading["weight_coverage"]
    assert aggregate["strict_comparison_eligible"] is False, \
        "a shipped best-effort episode must not certify a split"


# ============================ S10-S11: the paired 0.55 demonstration (item 12)

#: The demo rubric: five signals, and the three that are wired sum to exactly
#: 0.55. That is not a coincidence chosen to be tidy -- it is the committed
#: `examples/eval-one/residency-demo` weighting, and it is why 0.55 is the
#: number this demonstration is named after.
_DEMO_SIGNALS = {
    "citations":            {"verifier": "residency.citations.v1", "weight": 0.25},
    "patch":                {"verifier": "residency.patch.v1",     "weight": 0.20},
    "tests":                {"verifier": "residency.tests.v1",     "weight": 0.30},
    "identity":             {"verifier": "residency.identity.v1",  "weight": 0.15},
    "finding_completeness": {"verifier": "residency.finding.v1",   "weight": 0.10},
}

_DEMO_REWARD = {
    "reward_spec_version": "traaviis.reward.v1",
    "substrate_profile": "residency.repository.v1",
    "signals": dict(_DEMO_SIGNALS),
    "aggregation": "terminal",
}

_DEMO_TASK = {
    "task_spec_version": "traaviis.task.v1",
    "substrate_profile": "residency.repository.v1",
    "verifier_plan": {"required": [], "not_applicable": []},
}


def _demo_receipt(verification, wired):
    """A receipt shaped like the demo's, with `verifier_versions` doing the work.

    `wired` is the set of signals that had an implementation. `coverage` reads
    exactly this to tell an *abstention* (a wired verifier that looked and
    declined) from an *unwired* signal (nothing was ever asked) -- the two land
    on the same `not_applicable` state and on the same 0.0 reward, and the
    sealed `implementation` is the only thing that separates them.
    """
    versions = {}
    for signal in _DEMO_SIGNALS:
        versions[signal] = {
            "contract": _DEMO_SIGNALS[signal]["verifier"],
            "implementation": ("impl.%s.v1" % signal) if signal in wired else None,
        }
    return {
        "episode_version": "traaviis.episode.v1",
        "status": "ok", "validity": "valid",
        "verification": dict(verification),
        "verifier_versions": versions,
        "execution_facts": {"runner": {"profile": BEST_EFFORT}},
    }


def test_s10_two_episodes_score_0_55_and_mean_completely_different_things():
    """The paired demonstration: one number, two epistemic states.

    **A — everything was adjudicated.** All five verifiers wired. `tests` and
    `identity` ran, looked, and said *no*. Reward 0.55, and 100% of the rubric
    was answered. The 0.45 that is missing from the score is a **verdict**.

    **B — nearly half was never asked.** Only three verifiers wired. `tests` and
    `identity` return `not_applicable` because nothing was ever there to answer
    them. Reward 0.55, and 55% of the rubric was answered. The 0.45 that is
    missing from the score is a **gap**.

    `reward.score` gives a `fail` and a `not_applicable` the identical 0.0
    (§6a), so the two receipts post the same number, the same `status: ok` and
    the same `validity: valid`. Nothing in the reward can tell them apart. That
    is the entire justification for `response_coverage` existing, and this law
    is the demonstration of it rather than the assertion.

    Both halves are asserted, and the first is the one that makes the second
    mean anything: if the rewards differed, the pair would be showing that a
    score distinguishes them, which is the opposite claim.
    """
    from traaviis import reward as R

    adjudicated = {"citations": R.PASS, "patch": R.PASS,
                   "finding_completeness": R.PASS,
                   "tests": R.FAIL, "identity": R.FAIL}
    unasked = {"citations": R.PASS, "patch": R.PASS,
               "finding_completeness": R.PASS,
               "tests": R.NOT_APPLICABLE, "identity": R.NOT_APPLICABLE}

    all_five = set(_DEMO_SIGNALS)
    three = {"citations", "patch", "finding_completeness"}

    a = _demo_receipt(adjudicated, all_five)
    b = _demo_receipt(unasked, three)

    # --- identical by every number the reward reports ---
    scored_a = R.score(adjudicated, _DEMO_REWARD, [])
    scored_b = R.score(unasked, _DEMO_REWARD, [])
    assert abs(scored_a["reward"] - 0.55) < 1e-9, scored_a
    assert scored_a == scored_b, (scored_a, scored_b)
    assert scored_a["status"] == "ok" and scored_a["validity"] == "valid"

    # --- and completely different by the reading ---
    reading_a = C.response_coverage(a, _DEMO_REWARD, _DEMO_TASK)
    reading_b = C.response_coverage(b, _DEMO_REWARD, _DEMO_TASK)

    assert abs(reading_a["weight_coverage"] - 1.0) < 1e-9, reading_a
    assert abs(reading_b["weight_coverage"] - 0.55) < 1e-9, reading_b
    assert reading_a["unwired"] == []
    assert reading_b["unwired"] == ["identity", "tests"]
    assert abs(reading_b["unwired_weight"] - 0.45) < 1e-9, reading_b

    # A's missing 0.45 is a verdict; B's is a gap. The receipts agree on the
    # number and disagree on what it means, which is the whole point.
    assert reading_a["answered"] == sorted(all_five)
    assert set(reading_b["answered"]) == three


def test_s11_the_aggregate_of_the_pair_reports_the_gap_the_rewards_hide():
    """A split of the two: identical rewards, and coverage that says so.

    The pair aggregated is the demonstration at split level. The reward mean is
    0.55 either way -- it cannot move, because the two episodes score the same
    -- while the coverage reading places the split at 77.5% pooled and names
    `tests` and `identity` as unanswered in one task of the two.

    That is the deliverable of items 11 and 12 together: a split-level number
    that a reward mean cannot produce, over evidence a reward mean cannot see.
    """
    from traaviis import reward as R

    adjudicated = {"citations": R.PASS, "patch": R.PASS,
                   "finding_completeness": R.PASS,
                   "tests": R.FAIL, "identity": R.FAIL}
    unasked = {"citations": R.PASS, "patch": R.PASS,
               "finding_completeness": R.PASS,
               "tests": R.NOT_APPLICABLE, "identity": R.NOT_APPLICABLE}

    episodes = [
        {"task_id": "adjudicated", "status": "ok",
         "runner_profile": BEST_EFFORT,
         "reading": C.response_coverage(
             _demo_receipt(adjudicated, set(_DEMO_SIGNALS)),
             _DEMO_REWARD, _DEMO_TASK)},
        {"task_id": "unasked", "status": "ok",
         "runner_profile": BEST_EFFORT,
         "reading": C.response_coverage(
             _demo_receipt(unasked, {"citations", "patch",
                                     "finding_completeness"}),
             _DEMO_REWARD, _DEMO_TASK)},
    ]

    aggregate = C.split_coverage(episodes)
    assert aggregate["tasks"] == 2 and aggregate["read"] == 2
    # (1.0 + 0.55) / 2 on both arithmetics, because the two rubrics weigh the
    # same -- pooled and mean coincide exactly when the denominators do, which
    # is worth pinning so S1's divergence reads as a property of skew rather
    # than as noise.
    assert abs(aggregate["pooled_weight_coverage"] - 0.775) < 1e-9, aggregate
    assert abs(aggregate["mean_weight_coverage"] - 0.775) < 1e-9, aggregate

    # The reward mean cannot move; the coverage can. That gap is the feature.
    rewards = [R.score(adjudicated, _DEMO_REWARD, [])["reward"],
               R.score(unasked, _DEMO_REWARD, [])["reward"]]
    assert rewards[0] == rewards[1] == 0.55
    assert aggregate["pooled_weight_coverage"] != rewards[0]

    assert aggregate["unwired"] == {"identity": 1, "tests": 1}, aggregate
    assert aggregate["strict_comparison_eligible"] is False


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = skipped = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print("PASS %s" % t.__name__)
        except Skip as s:
            skipped += 1
            print("SKIP %s (%s)" % (t.__name__, s))
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (t.__name__, e))
    print("\n%d passed, %d skipped, %d failed" % (passed, skipped, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
