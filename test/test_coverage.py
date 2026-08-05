"""Law battery for verifier response coverage (``traaviis.coverage``).

The reading exists because ``traaviis.reward`` gives a ``fail`` and a
``not_applicable`` the identical ``0.0``, so a receipt cannot distinguish "the
verifier checked and said no" from "no verifier ever ran". C3 proves that loss on
the real Residency rubric; everything after it is the recovery.

The laws are grouped:

  C1-C2    totality and the partition -- the reading accounts for every declared
           signal exactly once, so nothing can be silently dropped.
  C3-C6    the two collapses this feature exists to undo: ``not_applicable``
           versus *unavailable* (C4/C5) and verifier ``error`` versus legitimate
           abstention (C6).
  C7-C9    required versus optional, and what was missing for each requirement
           that went unanswered.
  C10-C12  count coverage versus weight coverage versus reward -- three different
           readings that must not be averaged into one.
  C13-C15  coverage is not a score and not a validity judgement.
  C16-C18  determinism and purity: same sealed bytes, same reading, byte for byte.
  C19-C21  no denominator is reported as ``None``, never as zero coverage.
  C22-C23  boundary validation, mirroring ``reward.score``.
  C24-C26  the real committed episodes, including the ``not_applicable`` case.
  C27      **no id moves** -- every committed episode id re-derives unchanged.
  C28-C29  the CLI surfaces.
  C30      non-vacuity: each degradation below turns the law it targets red.
  C31-C32  the v2 taxonomy: ``unimplemented`` -> ``unwired``, the four states
           untouched, and the origin vocabulary agreeing with its producer.
  C33-C34  the second collapse inside ``errored`` -- a substrate override is not
           the verifier reporting, and a verifier fault is read from evidence or
           reported as undetermined, never guessed.
  C35      non-vacuity for C33/C34, by source-level deletion in isolated copies.

Runs with pytest, or standalone: ``python3 test/test_coverage.py``.
"""

import contextlib
import copy
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import coverage as C  # noqa: E402
from traaviis import identity as I  # noqa: E402
from traaviis import reward as R  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


class Skip(Exception):
    """The house skip signal, spelled the same way as every other battery.

    Deliberately not `pytest.skip`: every file in `test/` is also a self-running
    script with zero dependencies, and `_main` below is what the release gate
    actually executes. Under pytest with an engine present no skip fires at all,
    so the two runners agree.
    """


def _engine_or_skip():
    """Skip rather than fail when no Forge engine is reachable.

    Matches `test_kernel._engine_or_skip`. A law that needs the engine and does
    not skip reports a defect on a machine that simply does not have one, which
    is how the release packet's engine-absent gate came to be red on a tree
    where nothing was wrong.
    """
    from traaviis import engine as _engine

    eng = _engine.try_load()
    if eng is None:
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    return eng
FLAGSHIP = os.path.join(
    REPO, "examples", "eval-one", "episodes",
    "episode-42d0bb07e5f83e9e57518bf5cd3717e2a1e3aa45aa5821e36ac19206a3d73299")
DEMO = os.path.join(REPO, "examples", "eval-one", "residency-demo")
STUB_AGENT = os.path.join(HERE, "fixtures", "stub_agent.py")


# --------------------------------------------------------------------------- #
# fixtures: the frozen Residency v1 rubric, as it actually ships               #
# --------------------------------------------------------------------------- #

SPEC = {
    "reward_spec_version": "traaviis.reward.v1",
    "signals": {
        "citations":            {"verifier": "residency.citations.v1", "weight": 0.25},
        "patch":                {"verifier": "residency.patch.v1",     "weight": 0.20},
        "tests":                {"verifier": "residency.tests.v1",     "weight": 0.30},
        "identity":             {"verifier": "residency.identity.v1",  "weight": 0.15},
        "finding_completeness": {"verifier": "residency.finding.v1",   "weight": 0.10},
    },
    "caps": [
        {"when": {"signal": "patch", "state": "fail"}, "reward_max": 0.25},
        {"when": {"signal": "citations", "state": "fail"}, "reward_max": 0.25},
        {"when": {"signal": "tests", "state": "fail"}, "reward_max": 0.40},
    ],
    "aggregation": "terminal",
}

IMPLS = {
    "citations": "traaviis.citations-impl.v1",
    "patch": "traaviis.patch-impl.v1",
    "tests": "traaviis.tests-impl.v1",
    "identity": "forge.identity.v1@api-1",
    "finding_completeness": "traaviis.finding-completeness-impl.v1",
}


def task(required=("citations", "patch", "finding_completeness", "tests", "identity"),
         not_applicable=("native", "oracle"), allowed_exit_codes=None):
    doc = {"task_spec_version": "traaviis.task.v1",
           "verifier_plan": {"required": list(required),
                             "not_applicable": list(not_applicable)}}
    if allowed_exit_codes is not None:
        doc["agent_run_policy"] = {"allowed_exit_codes": list(allowed_exit_codes)}
    return doc


def facts(exit_code=0, termination="exited", truncated=False,
          profile="residency.trusted-local.v1"):
    """An ``execution_facts.v1`` object in exactly the shape ``execfacts`` seals.

    Hand-built rather than produced by a run, because the point of C33 is what a
    *reader* can derive from bytes it was handed; producing them through
    ``execfacts.build_execution_facts`` would test that this fixture agrees with
    that function, which is a different (and weaker) claim.
    """
    return {
        "execution_facts_version": "residency.execution-facts.v1",
        "runner": {"profile": profile},
        "platform": {"os": "linux", "arch": "x86_64"},
        "toolchain": {"profile": None, "resolved": {}},
        "agent_process": {"termination": termination, "exit_code": exit_code,
                          "stdout_truncated": bool(truncated),
                          "stderr_truncated": False},
        "sandbox": {"filesystem": "observed", "network": "unrestricted"},
    }


def receipt(verification, *, wired=None, evidence=None, execution_facts=None):
    """A receipt carrying exactly the three fields the reading consults.

    ``wired`` names the signals whose ``verifier_versions`` entry gets a real
    implementation string; every other non-pseudo signal gets the ``None``
    implementation the engine seals for an unwired verifier. ``native`` /
    ``oracle`` get no entry at all, which is exactly what ``evalone`` does --
    ``evidence_signals`` subtracts the pseudo signals before the map is built.
    """
    wired = set(IMPLS if wired is None else wired)
    versions = {}
    for sig in verification:
        if sig in ("native", "oracle"):
            continue
        versions[sig] = {
            "contract": (SPEC["signals"].get(sig) or {}).get("verifier"),
            "implementation": IMPLS.get(sig) if sig in wired else None,
        }
    ev = {s: {"format": "traaviis.verifier-evidence.v1", "digest": "sha256:00"}
          for s in (versions if evidence is None else evidence)}
    doc = {"episode_version": "traaviis.episode.v1",
           "verification": dict(verification),
           "verifier_versions": versions,
           "verification_evidence": ev}
    if execution_facts is not None:
        doc["execution_facts"] = execution_facts
    return doc


def evidence_for(sig, detail):
    """One signal's saved ``traaviis.verifier-evidence.v1`` object, as a bundle keeps it."""
    return {sig: {"evidence_version": "traaviis.verifier-evidence.v1",
                  "signal": sig, "state": R.ERROR, "detail": dict(detail)}}


def all_pass():
    return {"citations": R.PASS, "patch": R.PASS, "tests": R.PASS,
            "identity": R.PASS, "finding_completeness": R.PASS,
            "native": R.NOT_APPLICABLE, "oracle": R.NOT_APPLICABLE}


def read(verification, *, wired=None, spec=None, tsk=None):
    return C.response_coverage(receipt(verification, wired=wired),
                               spec or SPEC, tsk or task())


def approx(a, b, eps=1e-9):
    return abs(a - b) < eps


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _bundle_docs(bundle):
    return (_load(os.path.join(bundle, "receipt.json")),
            _load(os.path.join(bundle, "reward.json")),
            _load(os.path.join(bundle, "task.json")))


# --------------------------------------------------------------------------- #
# degradations, for the C30 vacuity proof                                      #
# --------------------------------------------------------------------------- #

@contextlib.contextmanager
def _patched(name, fn):
    original = getattr(C, name)
    setattr(C, name, fn)
    try:
        yield
    finally:
        setattr(C, name, original)


def blind_to_versions():
    """The naive reading: classify from the four states alone.

    This is the implementation anyone writes first -- ``not_applicable`` is one
    bucket -- and it is precisely what loses the distinction between a verifier
    that abstained and a verifier that never existed.
    """
    def _classify(state, versions, sig):
        if state in (R.PASS, R.FAIL):
            return C.ANSWERED
        if state == R.ERROR:
            return C.ERRORED
        return C.ABSTAINED
    return _patched("_classify", _classify)


def error_folded_into_abstention():
    """Treat ``error`` as just another way of declining."""
    def _classify(state, versions, sig):
        if state in (R.PASS, R.FAIL):
            return C.ANSWERED
        return C.ABSTAINED
    return _patched("_classify", _classify)


def fail_is_not_an_answer():
    """The score-shaped confusion: only a ``pass`` counts as coverage."""
    def _classify(state, versions, sig):
        if state == R.PASS:
            return C.ANSWERED
        if state == R.ERROR:
            return C.ERRORED
        return C.ABSTAINED
    return _patched("_classify", _classify)


def no_missing_note():
    """Report the gap without saying what was absent."""
    return _patched("_missing_note", lambda cls, sig, record: "")


# --------------------------------------------------------------------------- #
# C1-C2: totality and the partition                                            #
# --------------------------------------------------------------------------- #

def test_c1_the_reading_is_total_over_the_verification_map():
    """Every signal the receipt declares appears in the reading, exactly once.

    The verification map is contractually total over every declared verifier
    (RFC Evidence Residency §6). A coverage reading that quietly dropped a signal
    would be a coverage reading with a coverage gap, which is the joke that
    writes itself; so the denominator is the map, stated openly as ``declared``.
    """
    v = all_pass()
    v["identity"] = R.NOT_APPLICABLE
    v["tests"] = R.ERROR
    r = read(v, wired=["citations", "patch", "finding_completeness", "tests"])
    assert r["declared"] == sorted(v)
    assert set(r["signals"]) == set(v)
    assert r["declared"] == sorted(r["declared"]), "declared is canonically ordered"


def test_c2_the_response_classes_partition_the_declared_signals():
    """answered / declined / errored cover everything and overlap in nothing.

    Not a tautology: the three lists are built from five response classes, and
    the whole point of the sub-classification is that ``declined`` is the union of
    three different facts. If the union ever stopped covering the map, a signal
    could be neither answered nor accounted for as unanswered -- the exact silent
    hole this reading is supposed to close.
    """
    v = all_pass()
    v["identity"] = R.NOT_APPLICABLE
    v["tests"] = R.ERROR
    v["patch"] = R.FAIL
    r = read(v, wired=["citations", "finding_completeness", "patch", "tests"])
    buckets = [r["answered"], r["declined"], r["errored"]]
    flat = [s for b in buckets for s in b]
    assert sorted(flat) == r["declared"]
    assert len(flat) == len(set(flat)), "a signal landed in two buckets"
    # ...and `declined` is itself exactly the three abstention kinds.
    assert sorted(r["abstained"] + r["unwired"] + r["structural"]) \
        == sorted(r["declined"])
    # ...and `declined` is exactly the receipt's own not_applicable set, so a
    # reader can check it against the sealed bytes by eye.
    assert r["declined"] == sorted(s for s, st in v.items()
                                   if st == R.NOT_APPLICABLE)


# --------------------------------------------------------------------------- #
# C3-C6: the collapses this feature undoes                                     #
# --------------------------------------------------------------------------- #

def test_c3_reward_gives_fail_and_not_applicable_the_identical_number():
    """The information loss, demonstrated on the shipped rubric before the fix.

    ``identity`` carries weight 0.15 and no cap. An episode whose identity
    verifier *checked and rejected* and an episode whose identity verifier
    *never ran* produce byte-identical scoring output from ``reward.score``:
    same reward, same status, same validity. Nothing downstream of the receipt
    can tell those two runs apart -- that is the whole motivation, asserted
    rather than asserted-about.
    """
    failed = all_pass()
    failed["identity"] = R.FAIL
    absent = all_pass()
    absent["identity"] = R.NOT_APPLICABLE
    optional = ["citations", "patch", "finding_completeness"]  # identity optional
    a = R.score(failed, SPEC, optional)
    b = R.score(absent, SPEC, optional)
    assert a == b, "the premise of this module is wrong"
    assert approx(a["reward"], 0.85) and a["status"] == R.STATUS_OK
    # And the reading separates exactly what the reward merged.
    ra = read(failed, tsk=task(required=optional))
    rb = read(absent, tsk=task(required=optional))
    assert ra["answered"] != rb["answered"]
    assert approx(ra["weight_coverage"], 1.0)
    assert approx(rb["weight_coverage"], 0.85)


def test_c4_not_applicable_from_real_code_is_abstention_not_unavailability():
    """The same receipt string, two different facts, told apart by what was sealed.

    ``verification["identity"] == "not_applicable"`` is written by two entirely
    different events. ``evalone.resolve`` falls back to ``not_applicable`` when
    *no verifier is wired* -- so the string is the engine's default. It is also
    what a wired verifier returns when it legitimately has nothing to check
    (``substrate_verifiers``: ``tests`` with no test plan, ``identity`` with no
    bindings). ``verifier_versions[sig]["implementation"]`` is the only sealed
    byte that separates them, and until now nothing read it.
    """
    v = all_pass()
    v["identity"] = R.NOT_APPLICABLE
    wired = read(v, wired=list(IMPLS))               # a real impl was sealed
    unwired = read(v, wired=set(IMPLS) - {"identity"})  # implementation: null

    assert wired["signals"]["identity"]["state"] == R.NOT_APPLICABLE
    assert unwired["signals"]["identity"]["state"] == R.NOT_APPLICABLE
    assert wired["abstained"] == ["identity"] and wired["unwired"] == []
    assert unwired["unwired"] == ["identity"] and unwired["abstained"] == []
    # Both decline; the receipts differ only in the version seal.
    assert wired["declined"] == unwired["declined"]
    assert approx(wired["weight_coverage"], unwired["weight_coverage"])
    assert wired["abstained_weight"] == 0.15
    assert unwired["unwired_weight"] == 0.15


def test_c5_a_signal_with_no_evidence_obligation_is_structural_not_a_gap():
    """``native`` / ``oracle`` owe nothing, and are not counted as coverage gaps.

    Derived from the *absence* of a ``verifier_versions`` key, never from a
    hardcoded name list: ``evalone`` subtracts the pseudo signals before building
    that map, so absence of the key is the sealed record of "no obligation was
    declared". Counting them would make every clean episode read as permanently
    2 short and train readers to skip the section.

    Unless the task required one. Then the absence of any obligation is a live
    contradiction -- nothing could ever have answered it -- and it must be shouted
    about rather than dropped, which is the second half of this law.
    """
    r = read(all_pass())
    assert r["structural"] == ["native", "oracle"]
    assert "native" not in r["obligated"] and r["obligated"] == sorted(SPEC["signals"])
    assert r["required_unanswered"] == [] and r["optional_unanswered"] == []
    assert approx(r["signal_coverage"], 1.0)

    demanded = read(all_pass(),
                    tsk=task(required=list(SPEC["signals"]) + ["oracle"]))
    assert demanded["required_unanswered"] == ["oracle"]
    assert "oracle" in demanded["obligated"]
    assert "verifier_versions" in demanded["signals"]["oracle"]["missing"]


def test_c6_an_error_is_never_an_abstention():
    """Substrate unavailability and "there was nothing to check" stay apart.

    ``reward`` already separates them at the scoring level (§6a F2: an ``error``
    episode is ``status = error`` with ``reward = None``, never ``0``). The
    reading keeps the same wall standing one level up, in its own vocabulary:
    ``errored`` is its own class with its own weight, and it never joins
    ``declined``.
    """
    v = all_pass()
    v["tests"] = R.ERROR
    v["identity"] = R.NOT_APPLICABLE
    r = read(v, wired=list(IMPLS))
    assert r["errored"] == ["tests"]
    assert "tests" not in r["declined"]
    assert r["abstained"] == ["identity"]
    assert approx(r["errored_weight"], 0.30)
    assert approx(r["abstained_weight"], 0.15)
    assert r["signals"]["tests"]["response"] == C.ERRORED
    assert r["signals"]["identity"]["response"] == C.ABSTAINED
    # And the scoring layer agrees this is an error episode, not a low score.
    assert R.score(v, SPEC, ["citations"])["reward"] is None


# --------------------------------------------------------------------------- #
# C7-C9: required versus optional, and what was missing                        #
# --------------------------------------------------------------------------- #

def test_c7_required_and_optional_unanswered_are_reported_separately():
    """A requirement that went unanswered is a different severity from an option.

    Both are reported; neither is inferred from the other. ``required`` comes
    from the task's ``verifier_plan``, which is a different document from the
    rubric that supplies the weights -- so the split cannot be reconstructed from
    the reward spec alone, and reporting it is not redundant.
    """
    v = all_pass()
    v["tests"] = R.NOT_APPLICABLE
    v["identity"] = R.NOT_APPLICABLE
    r = read(v, wired=["citations", "patch", "finding_completeness"],
             tsk=task(required=["citations", "patch", "tests"]))
    assert r["required"] == ["citations", "patch", "tests"]
    assert r["optional"] == ["finding_completeness", "identity", "native", "oracle"]
    assert r["required_unanswered"] == ["tests"]
    assert r["optional_unanswered"] == ["identity"]


def test_c8_every_unanswered_obligated_signal_says_what_was_missing():
    """No silent gap. Each one names the evidence or capability that was absent.

    A reading that reports "3 of 5" and stops has moved the question rather than
    answered it. The three causes are materially different -- no verifier existed,
    the verifier could not complete, the subject had nothing to check -- and the
    consumer that has to act on the number needs to know which.
    """
    v = all_pass()
    v["tests"] = R.ERROR
    v["identity"] = R.NOT_APPLICABLE
    v["patch"] = R.NOT_APPLICABLE
    r = read(v, wired=["citations", "finding_completeness", "tests", "identity"])
    gaps = r["required_unanswered"] + r["optional_unanswered"]
    assert sorted(gaps) == ["identity", "patch", "tests"]
    for sig in gaps:
        note = r["signals"][sig]["missing"]
        assert isinstance(note, str) and note.strip(), sig
    assert "substrate" in r["signals"]["tests"]["missing"]
    assert "wired verifier implementation" in r["signals"]["patch"]["missing"]
    assert "inapplicable" in r["signals"]["identity"]["missing"]
    # An answered signal has nothing missing, and says so by carrying no key.
    assert "missing" not in r["signals"]["citations"]


def test_c9_an_abstention_records_whether_the_plan_anticipated_it():
    """A verifier that declines something the task never declared is news.

    ``verifier_plan.not_applicable`` is the author saying in advance "this signal
    is out of scope here". A runtime abstention on a signal the plan did *not*
    list is a different fact -- the subject turned out to have nothing to check
    where the author expected something -- and the note says which happened.
    """
    v = all_pass()
    v["identity"] = R.NOT_APPLICABLE
    anticipated = read(v, wired=list(IMPLS),
                       tsk=task(not_applicable=["native", "oracle", "identity"]))
    surprising = read(v, wired=list(IMPLS))
    assert anticipated["signals"]["identity"]["declared_not_applicable"] is True
    assert surprising["signals"]["identity"]["declared_not_applicable"] is False
    assert "did NOT declare" in surprising["signals"]["identity"]["missing"]
    assert "in advance" in anticipated["signals"]["identity"]["missing"]


# --------------------------------------------------------------------------- #
# C10-C12: count, weight and reward are three different readings               #
# --------------------------------------------------------------------------- #

def test_c10_signal_count_and_rubric_weight_are_reported_separately():
    """"4 of 5 answered" and "70% of the weight" are not the same statement.

    A rubric may put any share of its mass on one signal. Reporting only the
    count hides a rubric that lost its heaviest signal; reporting only the weight
    hides a rubric that lost most of its signals cheaply. Both are printed, and
    they are never averaged into one headline number.
    """
    v = all_pass()
    v["tests"] = R.NOT_APPLICABLE          # 0.30 of 1.00 -- the heaviest signal
    r = read(v, wired=set(IMPLS) - {"tests"})
    assert approx(r["signal_coverage"], 4.0 / 5.0)   # 80% of the signals
    assert approx(r["weight_coverage"], 0.70)        # 70% of the weight
    assert not approx(r["signal_coverage"], r["weight_coverage"])


def test_c11_a_fail_is_an_answer_and_costs_no_coverage():
    """The load-bearing separation from the reward.

    A ``fail`` is the verifier working: it looked, it judged, it said no. It
    costs reward (and may trip a cap) and it costs *nothing* in coverage. If a
    fail lowered coverage, coverage would be a second, worse copy of the reward
    -- and the one thing it must never be is a score.
    """
    v = all_pass()
    v["patch"] = R.FAIL
    r = read(v)
    assert r["answered"] == sorted(SPEC["signals"])
    assert approx(r["weight_coverage"], 1.0)
    assert approx(r["signal_coverage"], 1.0)
    assert r["declined"] == ["native", "oracle"] and r["errored"] == []
    # ...while the reward moved a long way, capped by the patch-fail rule.
    assert approx(R.score(v, SPEC, ["citations"])["reward"], 0.25)


def test_c12_the_weight_classes_sum_to_the_declared_weight():
    """Nothing in the rubric goes unaccounted for.

    Every unit of declared rubric weight lands in exactly one of the five class
    totals. This is the weight-side twin of C2, and it is what lets a reader
    treat ``declared_weight - scored_weight`` as "weight that never entered play"
    without having to trust that phrase.
    """
    v = all_pass()
    v["tests"] = R.ERROR
    v["identity"] = R.NOT_APPLICABLE
    v["patch"] = R.NOT_APPLICABLE
    r = read(v, wired=["citations", "finding_completeness", "tests", "identity"])
    total = (r["scored_weight"] + r["abstained_weight"] + r["unwired_weight"]
             + r["structural_weight"] + r["errored_weight"])
    assert approx(total, r["declared_weight"])
    assert approx(r["declared_weight"], 1.0)
    assert approx(r["scored_weight"], 0.35)


# --------------------------------------------------------------------------- #
# C13-C15: coverage is not a score, and not a validity judgement               #
# --------------------------------------------------------------------------- #

def test_c13_full_coverage_does_not_mean_valid():
    """Every verifier answered; the episode is still invalid.

    A tampered subject is ``reward 0 / validity invalid`` (§6a) no matter how
    completely the verifiers responded. Coverage says how much of the rubric was
    in play, and an episode can have all of it in play and still be thrown out.
    """
    v = all_pass()
    r = read(v)
    assert approx(r["weight_coverage"], 1.0)
    scored = R.score(v, SPEC, list(SPEC["signals"]), tampered=True)
    assert scored["validity"] == R.INVALID and scored["reward"] == 0.0


def test_c14_low_coverage_does_not_mean_invalid():
    """Barely half the rubric answered; the episode is perfectly valid.

    This is the committed ``residency-demo`` shape (C25) in miniature: no
    applicable assertion failed, so ``validity`` is ``valid`` and nothing about
    the episode is wrong -- while 45% of the rubric was never adjudicated. Those
    are two true statements that a single float cannot carry.
    """
    v = all_pass()
    v["tests"] = R.NOT_APPLICABLE
    v["identity"] = R.NOT_APPLICABLE
    optional = ["citations", "patch", "finding_completeness"]
    scored = R.score(v, SPEC, optional)
    assert scored["validity"] == R.VALID and scored["status"] == R.STATUS_OK
    r = read(v, wired=optional, tsk=task(required=optional))
    assert approx(r["weight_coverage"], 0.55)
    assert r["required_unanswered"] == []


def test_c15_the_reading_never_reports_which_signals_passed():
    """No pass list anywhere, at any depth, on purpose.

    A ``passed`` field would be read as partial credit within about four seconds
    of the first screenshot, and the reading would become the score it was built
    to sit beside. The per-signal ``state`` is carried (it is a sealed fact and
    the classification must be checkable against it), but no aggregate ever
    counts passes, and neither summary line mentions one.
    """
    r = read(all_pass())
    assert not any(k for k in r if "pass" in k.lower())
    assert not any("pass" in k.lower() for rec in r["signals"].values() for k in rec)
    assert "pass" not in C.coverage_line(r).lower()
    assert not any("pass" in line.lower() for line in C.coverage_lines(r))
    # Nor does it carry reward, status or validity: those live in the receipt and
    # are printed beside the reading, never inside it.
    for forbidden in ("reward", "status", "validity"):
        assert forbidden not in r


# --------------------------------------------------------------------------- #
# C16-C18: determinism and purity                                              #
# --------------------------------------------------------------------------- #

def test_c16_weight_sums_do_not_depend_on_dict_order():
    """Two consumers deriving the reading independently get the same float.

    Not hypothetical on this rubric: summing {0.25, 0.20, 0.30, 0.15, 0.10} in
    different orders really does yield 0.9999999999999999, 1.0 and
    1.0000000000000002. The reading accumulates in sorted signal-id order, so the
    result is a function of the sealed documents rather than of whichever
    insertion order a JSON parser happened to hand back.
    """
    orders = set()
    for perm in itertools.permutations(SPEC["signals"].items()):
        total = 0.0
        for _, binding in perm:
            total += binding["weight"]
        orders.add(repr(total))
    assert len(orders) > 1, "the premise of this law is gone; pick worse weights"

    baseline = read(all_pass())
    for perm in itertools.permutations(sorted(SPEC["signals"])):
        spec = dict(SPEC, signals={s: SPEC["signals"][s] for s in perm})
        v = {s: all_pass()[s] for s in perm}
        v.update({"native": R.NOT_APPLICABLE, "oracle": R.NOT_APPLICABLE})
        r = C.response_coverage(receipt(v), spec, task())
        assert json.dumps(r, sort_keys=True) == json.dumps(baseline, sort_keys=True)


def test_c17_the_reading_mutates_none_of_its_inputs():
    """Pure: three documents in, one new dict out, nothing touched.

    It is handed the live receipt an evaluation just produced. A reading that
    wrote a single key back into that dict would change the bytes the episode id
    was computed over -- the exact failure mode this whole design is arranged to
    avoid, arriving through the back door.
    """
    rec, spec, tsk = receipt(all_pass()), copy.deepcopy(SPEC), task()
    before = [json.dumps(d, sort_keys=True) for d in (rec, spec, tsk)]
    C.response_coverage(rec, spec, tsk)
    after = [json.dumps(d, sort_keys=True) for d in (rec, spec, tsk)]
    assert before == after


def test_c18_the_reading_is_json_serializable_and_reproduces_itself():
    """A reading survives the wire, because ``verify-episode --json`` puts it there."""
    r = read(all_pass())
    assert json.loads(json.dumps(r)) == r
    assert read(all_pass()) == r
    assert r["coverage_version"] == "traaviis.response-coverage.v2"


# --------------------------------------------------------------------------- #
# C19-C21: no denominator is None, never zero                                  #
# --------------------------------------------------------------------------- #

def test_c19_an_absent_denominator_reports_none_not_zero():
    """A rubric with no weight has no weight coverage -- which is not 0%.

    ``0.0`` would say "none of the rubric was answered". ``None`` says "there is
    no rubric to have answered". Printing the first for the second is the same
    species of lie as scoring an unavailable verifier as a fail.
    """
    empty = dict(SPEC, signals={}, caps=[])
    r = read({"native": R.NOT_APPLICABLE, "oracle": R.NOT_APPLICABLE},
             spec=empty, tsk=task(required=[]))
    assert r["weight_coverage"] is None and r["declared_weight"] == 0.0
    assert r["signal_coverage"] is None and r["obligated"] == []
    assert "no rubric weight declared" in C.coverage_line(r)


def test_c20_an_error_episode_still_reports_its_coverage():
    """The documented departure from the ROADMAP §C sketch, asserted.

    §C proposes coverage ``None`` on an ``error`` episode, "mirroring the reward
    rule". The mirror does not hold. ``reward`` is ``None`` because reward is a
    claim about the *agent*, and a fallen-over substrate supports no partial
    claim about the agent. Coverage is a claim about the *verifiers*, and an
    errored episode is exactly where "how much did we manage to check?" carries
    the most information. Suppressing it would also re-collapse ``errored`` into
    the same silence as "nothing was declared" -- undoing C6 at the last step.
    """
    v = all_pass()
    v["tests"] = R.ERROR
    scored = R.score(v, SPEC, ["citations"])
    assert scored["reward"] is None and scored["status"] == R.STATUS_ERROR
    r = read(v, wired=list(IMPLS))
    assert r["weight_coverage"] is not None
    assert approx(r["weight_coverage"], 0.70)
    assert approx(r["errored_weight"], 0.30)


def test_c21_zero_coverage_and_no_coverage_are_different_readings():
    """The pair that makes C19 mean something.

    An episode where every scored signal errored genuinely has ``0.0`` weight
    coverage -- a real number about a real rubric. An episode with no rubric has
    ``None``. Both are reported, and they are not the same value.
    """
    v = {s: R.ERROR for s in SPEC["signals"]}
    v.update({"native": R.NOT_APPLICABLE, "oracle": R.NOT_APPLICABLE})
    r = read(v, wired=list(IMPLS))
    assert r["weight_coverage"] == 0.0
    assert r["weight_coverage"] is not None


# --------------------------------------------------------------------------- #
# C22-C23: boundary validation                                                 #
# --------------------------------------------------------------------------- #

def test_c22_an_unknown_verifier_state_is_a_caller_bug():
    """Same posture as ``reward.score``: refuse rather than classify a fifth state.

    The four states are frozen. A reading that silently bucketed an unknown
    string as "declined" would report a coverage gap for what is actually a
    corrupt receipt, and the operator would go looking for a missing verifier.
    """
    v = all_pass()
    v["tests"] = "probably_fine"
    try:
        read(v)
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unknown state")


def test_c23_a_scored_or_required_signal_missing_from_the_map_is_refused():
    """The verification map is contractually total; a hole in it is not a gap.

    A scored signal absent from the map has no state at all. Treating that as
    "unanswered" would let a truncated receipt read as an honest partial run,
    which is the one failure mode this reading must not be able to launder.
    """
    for drop, required in (("tests", ["citations"]),
                           ("citations", ["citations"])):
        v = all_pass()
        del v[drop]
        rec = receipt(v)
        try:
            C.response_coverage(rec, SPEC, task(required=required))
        except ValueError:
            continue
        raise AssertionError("expected ValueError for missing %r" % drop)


# --------------------------------------------------------------------------- #
# C24-C26: the real committed episodes                                         #
# --------------------------------------------------------------------------- #

def test_c24_the_flagship_five_signal_episode_reads_fully_covered():
    """The committed all-pass episode: reward 1.0 and 100% of the rubric in play.

    Read from the bundle's own sealed bytes with no engine, no runner, and no
    agent -- which is the derivability claim made concrete on a real artifact.
    """
    rec, spec, tsk = _bundle_docs(FLAGSHIP)
    assert rec["reward"] == 1.0 and rec["validity"] == "valid"
    r = C.response_coverage(rec, spec, tsk)
    assert r["answered"] == ["citations", "finding_completeness", "identity",
                             "patch", "tests"]
    assert r["structural"] == ["native", "oracle"]
    assert r["required_unanswered"] == [] and r["optional_unanswered"] == []
    assert approx(r["weight_coverage"], 1.0) and approx(r["scored_weight"], 1.0)
    assert approx(r["signal_coverage"], 1.0)


def test_c25_the_residency_demo_episode_is_the_case_this_feature_exists_for():
    """A committed bundle that scores ``0.55 / ok / valid`` with 45% never checked.

    ``examples/eval-one/residency-demo`` declares a five-signal rubric and no
    ``test_plan`` or ``identity_policy``, so ``wiring.declared_signals`` wires
    neither ``tests`` nor ``identity``; ``evalone.resolve`` falls back to
    ``not_applicable`` for both and seals ``implementation: null``. The receipt
    then reports a valid mid-range score with nothing anywhere on it to say that
    nearly half the rubric was never adjudicated by anything.

    This runs the real agent through the real CLI, so the receipt asserted here
    is one a user can produce; the reading is then derived from it and the two
    committed spec documents alone.
    """
    env = dict(os.environ, TMPDIR=os.environ.get("TMPDIR", "/tmp"))
    proc = subprocess.run(
        [sys.executable, "-m", "traaviis.cli", "eval-one", DEMO,
         "--agent", sys.executable, STUB_AGENT,
         "--platform", "linux-x86_64", "--json"],
        cwd=REPO, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    rec = json.loads(proc.stdout)
    assert rec["reward"] == 0.55
    assert rec["status"] == "ok" and rec["validity"] == "valid"

    spec = _load(os.path.join(DEMO, "reward.json"))
    tsk = _load(os.path.join(DEMO, "task.json"))
    r = C.response_coverage(rec, spec, tsk)
    assert r["unwired"] == ["identity", "tests"]
    assert r["abstained"] == [] and r["errored"] == []
    assert approx(r["weight_coverage"], 0.55)
    assert approx(r["unwired_weight"], 0.45)
    assert approx(r["signal_coverage"], 3.0 / 5.0)
    # Optional, so the episode is legitimately valid -- and the 0.45 is still
    # reported. Coverage and validity, side by side, disagreeing.
    assert r["required_unanswered"] == []
    assert r["optional_unanswered"] == ["identity", "tests"]
    for sig in ("identity", "tests"):
        assert "not a verdict" in r["signals"][sig]["missing"]


def test_c26_a_wired_verifier_that_abstains_reads_differently_from_the_demo():
    """The other ``not_applicable``, constructed because no bundle commits one.

    No committed episode has a *wired* verifier returning ``not_applicable``: the
    CLI only wires ``tests`` when the task declares a ``test_plan``, and only
    wires ``identity`` when it declares an ``identity_policy`` -- so on this
    corpus every ``not_applicable`` is the unwired fallback. The abstention case
    is therefore built here from the residency-demo receipt, changed in exactly
    one byte-level respect: the sealed ``implementation`` for ``identity``.

    That single difference must flip the reading, and must flip nothing else.
    """
    v = all_pass()
    v["identity"] = R.NOT_APPLICABLE
    v["tests"] = R.NOT_APPLICABLE
    unwired = read(v, wired=["citations", "patch", "finding_completeness"])
    wired = read(v, wired=list(IMPLS))
    assert unwired["unwired"] == ["identity", "tests"]
    assert wired["abstained"] == ["identity", "tests"]
    # Everything a naive reading would look at is identical between them.
    for key in ("declared", "answered", "declined", "errored", "obligated",
                "scored_weight", "declared_weight", "weight_coverage",
                "signal_coverage", "required_unanswered", "optional_unanswered"):
        assert unwired[key] == wired[key], key


# --------------------------------------------------------------------------- #
# C27: no id moves                                                             #
# --------------------------------------------------------------------------- #

def test_c27_computing_the_reading_moves_no_identity():
    """The load-bearing law. Every committed id re-derives, unchanged.

    Coverage adds no field to any hashed document: it is derived from
    ``receipt.verification``, ``receipt.verifier_versions``, the ``rew-…`` signal
    map and ``task.verifier_plan``, all of which are already sealed. The proof is
    to re-derive every id in the committed bundle *after* the reading has been
    taken over the same in-memory documents, and require byte equality with the
    ids on disk -- so any mutation, any injected key, any reordering shows up as
    a moved id rather than as a passing test.
    """
    rec, spec, tsk = _bundle_docs(FLAGSHIP)
    sealed = {
        "episode_id": (rec["episode_id"], I.episode_id),
        "reward_id": (spec["reward_id"], I.reward_id),
        "task_id": (tsk["task_id"], I.task_id),
    }
    for field, (declared, compute) in sealed.items():
        assert compute(rec if field == "episode_id"
                       else spec if field == "reward_id" else tsk) == declared

    reading = C.response_coverage(rec, spec, tsk)
    assert reading  # the reading was actually taken over these very objects

    for field, (declared, compute) in sealed.items():
        got = compute(rec if field == "episode_id"
                      else spec if field == "reward_id" else tsk)
        assert got == declared, "%s moved: %s != %s" % (field, got, declared)

    # And the reading is not in the receipt, which is the other half of the claim.
    assert "coverage" not in rec
    assert C.COVERAGE_VERSION not in json.dumps(rec)


# --------------------------------------------------------------------------- #
# C28-C29: the CLI surfaces                                                    #
# --------------------------------------------------------------------------- #

def _trvs(*argv):
    return subprocess.run(
        [sys.executable, "-m", "traaviis.cli"] + list(argv),
        cwd=REPO, env=dict(os.environ), capture_output=True, text=True)


def test_c28_verify_episode_prints_the_reading_in_both_forms():
    """Human output and ``--json`` both carry it, from the saved bundle alone.

    ``verify-episode`` is the surface that matters most: it is the command a
    third party runs on evidence somebody else produced, which is precisely the
    audience for "what did the verifier actually check?".

    Engine-dependent, and it must say so. The flagship's `identity` signal is
    replayed by the Forge engine, so with no engine reachable the command exits
    2 (`unavailable`) -- correctly. This law asserted `returncode == 0`
    unconditionally, which made it fail in the release packet's engine-absent
    gate: an engine-dependent law that did not skip is exactly what that gate
    exists to catch, and it caught it. Skipping is the honest answer, because
    the claim here is about what the command *prints*, not about whether a
    verifier could be located.
    """
    _engine_or_skip()

    human = _trvs("verify-episode", FLAGSHIP)
    assert human.returncode == 0, human.stderr
    assert "coverage" in human.stdout
    assert "5/5 signals answered" in human.stdout

    as_json = _trvs("verify-episode", FLAGSHIP, "--json")
    assert as_json.returncode == 0, as_json.stderr
    report = json.loads(as_json.stdout)
    assert report["coverage"]["coverage_version"] == C.COVERAGE_VERSION
    assert approx(report["coverage"]["weight_coverage"], 1.0)
    # Attached beside the checks, never inside them: a reading is not a verdict.
    assert "coverage" not in report["checks"]


def test_c29_the_reading_changes_no_verdict_and_no_exit_code():
    """Coverage is commentary printed beside the closure result, not a gate.

    A bundle that closes with 55% of its rubric unanswered still closes, and
    ``verify-episode`` still exits 0. If the reading could move the exit code it
    would become a check, and a check that fires on "the task did not require
    this signal" would be unusable. The `checks` block is asserted byte-identical
    to what the closure verifier itself produced.
    """
    from traaviis import cli, episode_bundle

    # The same verifier wiring the CLI itself builds, so the only difference
    # between the two reports can be the reading.
    extra, _ = cli._wire_episode_verifiers(None)  # `verify-episode` is needs_engine=False
    direct = episode_bundle.verify_episode_bundle(FLAGSHIP, extra_verifiers=extra)
    as_json = _trvs("verify-episode", FLAGSHIP, "--json")
    report = json.loads(as_json.stdout)
    assert report["outcome"] == direct["outcome"]
    assert report["ok"] is direct["ok"]
    assert json.dumps(report["checks"], sort_keys=True) == \
        json.dumps(direct["checks"], sort_keys=True)
    assert "coverage" in report and "coverage" not in direct
    assert as_json.returncode == (0 if direct["ok"] else as_json.returncode)


# --------------------------------------------------------------------------- #
# C30: non-vacuity                                                             #
# --------------------------------------------------------------------------- #

def test_c30_the_laws_go_red_when_the_logic_they_test_is_removed():
    """A law that would pass without the code it tests is decoration.

    Each block below deletes exactly one piece of the reading and asserts the law
    that claims it fails. ``blind_to_versions`` is the naive first
    implementation -- classify from the four states alone -- and it is the one
    that matters, because it is what this module would have been if
    ``verifier_versions`` had gone unread.
    """
    v = all_pass()
    v["identity"] = R.NOT_APPLICABLE

    # C4 / C5 / C25 / C26: without reading verifier_versions, "no verifier was
    # ever wired" and "the verifier abstained" become the same reading.
    aggregates = ("answered", "declined", "abstained", "unwired",
                  "structural", "errored", "obligated", "required_unanswered",
                  "optional_unanswered", "scored_weight", "abstained_weight",
                  "unwired_weight", "structural_weight", "errored_weight",
                  "weight_coverage", "signal_coverage")
    with blind_to_versions():
        unwired = read(v, wired=set(IMPLS) - {"identity"})
        wired = read(v, wired=list(IMPLS))
        assert unwired["unwired"] == [] == wired["unwired"]
        assert "identity" in unwired["abstained"] and "identity" in wired["abstained"]
        # Every aggregate a consumer would act on is now identical between "no
        # verifier existed" and "the verifier abstained": C4 has nothing to see.
        for key in aggregates:
            assert unwired[key] == wired[key], key
        # ...and native/oracle stop being structural, so C5's denominator breaks.
        assert read(all_pass())["structural"] == []
        assert len(read(all_pass())["obligated"]) == 7

    # C6 / C20: folding error into abstention loses the errored weight entirely.
    err = all_pass()
    err["tests"] = R.ERROR
    with error_folded_into_abstention():
        r = read(err, wired=list(IMPLS))
        assert r["errored"] == []
        assert r["errored_weight"] == 0.0
        assert r["abstained_weight"] == 0.30

    # C11 / C3: making a fail cost coverage turns the reading into the reward.
    failed = all_pass()
    failed["patch"] = R.FAIL
    with fail_is_not_an_answer():
        r = read(failed)
        assert not approx(r["weight_coverage"], 1.0)
        assert approx(r["weight_coverage"], 0.80)
        assert "patch" in r["declined"]

    # C8: the gap is still counted, but nothing says what was absent.
    with no_missing_note():
        r = read(v, wired=set(IMPLS) - {"identity"})
        assert r["required_unanswered"] == ["identity"]
        assert r["signals"]["identity"]["missing"] == ""

    # And with everything restored, each of those assertions inverts.
    unwired = read(v, wired=set(IMPLS) - {"identity"})
    assert unwired["unwired"] == ["identity"]
    assert any(unwired[k] != read(v, wired=list(IMPLS))[k] for k in aggregates)
    assert read(err, wired=list(IMPLS))["errored"] == ["tests"]
    assert approx(read(failed)["weight_coverage"], 1.0)
    assert read(v, wired=set(IMPLS) - {"identity"})["signals"]["identity"]["missing"]


# --------------------------------------------------------------------------- #
# C31-C35: the v2 taxonomy -- `unwired`, and the failure-origin axis          #
# --------------------------------------------------------------------------- #

def test_c31_unimplemented_overstated_what_the_bytes_prove():
    """The rename, argued from the shipped counter-example rather than from taste.

    ``unimplemented`` claims no implementation exists. The residency-demo episode
    -- the very episode this module was written for -- is a case where that is
    **false**: its task declares ``tests`` and ``identity`` not_applicable, and
    both implementations are sitting in this repository. What the receipt actually
    records is a null ``implementation`` in ``verifier_versions``, which supports
    "nothing was wired here" and nothing stronger.

    The two readings imply opposite actions -- *unimplemented* says go write the
    verifier, *unwired* says go fix the wiring -- so the overstatement is not
    cosmetic.

    The four verifier states are untouched. Availability (``unwired`` /
    ``unavailable``) and failure origin are orthogonal axes over those four, never
    new members of them; a fifth state would change ``reward.score``'s domain and
    move every receipt that carries it.
    """
    from traaviis import substrate_verifiers as SV

    demo = _load(os.path.join(DEMO, "task.json"))
    assert set(demo["verifier_plan"]["not_applicable"]) == {
        "native", "oracle", "tests", "identity"}
    assert callable(SV.tests_verifier), "the tests implementation is right here"
    assert callable(SV.make_identity_verifier), "as is the identity one"

    assert C.COVERAGE_VERSION == "traaviis.response-coverage.v2"
    assert C.UNWIRED == "unwired"
    assert not hasattr(C, "UNIMPLEMENTED"), \
        "a deprecated alias is a second name for one fact -- the exact collapse " \
        "this module exists to undo"

    # The four states, unchanged, and none of the new vocabulary among them.
    assert R.STATES == frozenset({"pass", "fail", "not_applicable", "error"})
    for word in C.RESPONSE_CLASSES + C.ERROR_ORIGINS + (C.AVAILABILITY_UNAVAILABLE,):
        assert word not in R.STATES, word

    # ...and the wire format follows the rename in both directions.
    v = all_pass()
    v["identity"] = R.NOT_APPLICABLE
    r = read(v, wired=set(IMPLS) - {"identity"})
    assert r["unwired"] == ["identity"] and r["unwired_weight"] == 0.15
    assert "unimplemented" not in r and "unimplemented_weight" not in r
    assert "unimplemented" not in json.dumps(r)


def test_c32_the_producer_and_the_reader_spell_the_origins_the_same():
    """``evalone`` seals two origins; this module names four. They must agree.

    The vocabulary is deliberately duplicated rather than imported -- this
    module's claim is that it is a pure function of three sealed documents, and
    importing the orchestrator (which pulls in the runner, subprocess and the
    admission stack) for four string constants would make the import list say
    otherwise. Duplication plus a law, which is the same trade
    ``_isolated_traaviis`` makes across two batteries. This is the law.
    """
    from traaviis import evalone as E

    assert E.ERROR_ORIGIN_VERIFIER_EXCEPTION == C.ORIGIN_VERIFIER_EXCEPTION
    assert E.ERROR_ORIGIN_VERIFIER_PROTOCOL == C.ORIGIN_VERIFIER_PROTOCOL
    for origin in (E.ERROR_ORIGIN_VERIFIER_EXCEPTION,
                   E.ERROR_ORIGIN_VERIFIER_PROTOCOL):
        assert origin in C.ERROR_ORIGINS, origin
    # The two the reader derives are its own and appear in no sealed document.
    assert C.ORIGIN_SUBSTRATE_OVERRIDE in C.ERROR_ORIGINS
    assert C.ORIGIN_VERIFIER_REPORTED in C.ERROR_ORIGINS
    assert C.ORIGIN_UNDETERMINED not in C.ERROR_ORIGINS, \
        "the absence of an origin is not an origin"


def test_c33_a_substrate_override_is_not_the_verifier_reporting_error():
    """The collapse inside ``errored``, undone -- and what it takes to undo it.

    A signal reaches ``error`` by two routes that the receipt spells identically.
    Either the wired verifier returned ``VerifierResult(ERROR)``, or the run-level
    rule in ``evalone._finish_episode`` overwrote **every** non-pseudo signal
    because the agent process timed out, was truncated, or exited outside
    ``allowed_exit_codes``. Prose saying "the verifier reported error" is simply
    false on the second route, and the second route is the common one.

    **``allowed_exit_codes`` is what makes this derivation need the task.** The
    brief for this change said the two derivable origins come from "the receipt
    plus ``execution_facts``" -- but ``execution_facts`` seals the exit code and
    not the set it is compared against, which lives in
    ``task.agent_run_policy``. The pair below is the proof: identical receipts,
    identical facts, opposite verdicts, because one task allows exit 3 and the
    other does not. A reading built from the receipt alone would get one of them
    wrong.
    """
    v = {s: R.ERROR for s in IMPLS}
    v.update({"native": R.NOT_APPLICABLE, "oracle": R.NOT_APPLICABLE})
    rec = receipt(v, execution_facts=facts(exit_code=3))

    refused = C.response_coverage(rec, SPEC, task(allowed_exit_codes=[0]))
    assert refused["substrate_override"] is True
    assert refused["errored_by_origin"] == {
        C.ORIGIN_SUBSTRATE_OVERRIDE: sorted(IMPLS)}
    for sig in IMPLS:
        rec_sig = refused["signals"][sig]
        assert rec_sig["error_origin"] == C.ORIGIN_SUBSTRATE_OVERRIDE
        assert "this says nothing about the verifier" in rec_sig["missing"]
        assert "the wired verifier itself reported" not in rec_sig["missing"]

    allowed = C.response_coverage(rec, SPEC, task(allowed_exit_codes=[0, 3]))
    assert allowed["substrate_override"] is False, \
        "the exit-code set lives in the task; a receipt-only derivation is wrong"
    for sig in IMPLS:
        assert allowed["signals"][sig]["error_origin"] != C.ORIGIN_SUBSTRATE_OVERRIDE

    # A timeout and a truncated capture are the other two triggers, and a
    # non-executing profile launched nothing so it can have neither.
    for kwargs in ({"termination": "timed_out", "exit_code": None},
                   {"truncated": True}):
        r = C.response_coverage(receipt(v, execution_facts=facts(**kwargs)),
                                SPEC, task(allowed_exit_codes=[0]))
        assert r["substrate_override"] is True, kwargs
    ors = facts(exit_code=None, termination="not_executed",
                profile="traaviis.ors-submission.v1")
    assert C.response_coverage(receipt(v, execution_facts=ors), SPEC,
                               task())["substrate_override"] is False

    # And a receipt that carries no facts at all is *undetermined*, not False.
    assert read(v)["substrate_override"] is None


def test_c34_a_verifier_fault_is_read_from_evidence_and_never_guessed():
    """``verifier_exception`` / ``verifier_protocol`` come from the evidence or not at all.

    The receipt pins each signal's evidence **only by digest**, so those two
    origins are unreachable from the receipt alone. When the evidence is supplied
    they are reported exactly as sealed; when it is not, the reading says
    ``None`` -- undetermined -- and never the plausible ``verifier_reported``.
    Guessing there would reproduce the very sentence v2 exists to stop.

    The reading does not re-attest the evidence against the receipt's digest.
    That binding is ``verify_episode_bundle``'s verdict, and a second, weaker copy
    of it here would violate the "boundary validation only" rule this module
    already states for the task/reward cross-binding.
    """
    v = all_pass()
    v["tests"] = R.ERROR
    rec = receipt(v, execution_facts=facts(exit_code=0))
    tsk = task(allowed_exit_codes=[0])

    blind = C.response_coverage(rec, SPEC, tsk)
    assert blind["signals"]["tests"]["error_origin"] is None
    assert blind["errored_by_origin"] == {C.ORIGIN_UNDETERMINED: ["tests"]}
    assert "cannot be settled" in blind["signals"]["tests"]["missing"]
    assert "the wired verifier itself reported" not in \
        blind["signals"]["tests"]["missing"]

    raised = C.response_coverage(rec, SPEC, tsk, evidence_for("tests", {
        "error_origin": "verifier_exception", "error_code": "VERIFIER_RAISED",
        "exception_type": "ValueError",
        "verifier_implementation": IMPLS["tests"]}))
    got = raised["signals"]["tests"]
    assert got["error_origin"] == C.ORIGIN_VERIFIER_EXCEPTION
    assert got["error_code"] == "VERIFIER_RAISED"
    assert got["exception_type"] == "ValueError"
    assert "ValueError" in got["missing"] and "raised while evaluating" in got["missing"]

    returned = C.response_coverage(rec, SPEC, tsk, evidence_for("tests", {
        "error_origin": "verifier_protocol",
        "error_code": "INVALID_VERIFIER_RESULT",
        "violation": "detail_not_canonical",
        "verifier_implementation": IMPLS["tests"]}))
    got = returned["signals"]["tests"]
    assert got["error_origin"] == C.ORIGIN_VERIFIER_PROTOCOL
    assert got["violation"] == "detail_not_canonical"
    assert got["error_origin"] != raised["signals"]["tests"]["error_origin"]

    # A verifier that reported `error` itself -- today's `tests` verifier does
    # exactly this for a toolchain or runner failure, with a free-text `reason`.
    # From the receipt's side that IS `verifier_reported`; sub-kinding it as
    # `toolchain_error` would mean changing sealed detail strings, and every one
    # of them is inside a `verification_evidence[sig].digest`, hence inside
    # `episode-...`. A migration, not a rename -- recorded, not silently done.
    reported = C.response_coverage(rec, SPEC, tsk, evidence_for("tests", {
        "reason": "toolchain resolution failed: no such tool"}))
    assert reported["signals"]["tests"]["error_origin"] == C.ORIGIN_VERIFIER_REPORTED
    assert reported["signals"]["tests"]["error_code"] is None

    # When the substrate override fired AND the verifier also raised, both facts
    # survive: the origin is the override (it is what set the state), and the
    # verifier's own code is still reported beside it.
    both = C.response_coverage(
        receipt(v, execution_facts=facts(exit_code=3)), SPEC,
        task(allowed_exit_codes=[0]),
        evidence_for("tests", {"error_origin": "verifier_exception",
                               "error_code": "VERIFIER_RAISED",
                               "exception_type": "ValueError"}))
    assert both["signals"]["tests"]["error_origin"] == C.ORIGIN_SUBSTRATE_OVERRIDE
    assert both["signals"]["tests"]["error_code"] == "VERIFIER_RAISED"


def _isolated_coverage(*edits):
    """A private copy of ``traaviis/`` with literal source edits, importable.

    The same instrument as ``test_evalone._isolated_traaviis`` and
    ``test_canonical.isolated_package``, duplicated for the third time rather than
    shared, for the reason those two already state: a helper shared across
    batteries is a coupling that outlives the reason for it.
    """
    _COUNTER[0] += 1
    name = "traaviis_coverage_%d" % _COUNTER[0]
    root = tempfile.mkdtemp(prefix="trvs-coverage-probe-")
    shutil.copytree(os.path.join(REPO, "traaviis"), os.path.join(root, name),
                    ignore=shutil.ignore_patterns("__pycache__"))
    for relpath, old, new in edits:
        path = os.path.join(root, name, relpath)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        assert old in text, "edit target absent from %s: %r" % (relpath, old)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(old, new, 1))
    sys.path.insert(0, root)

    def cleanup():
        if root in sys.path:
            sys.path.remove(root)
        for key in [k for k in sys.modules if k.split(".")[0] == name]:
            del sys.modules[key]
        shutil.rmtree(root, ignore_errors=True)

    try:
        package = __import__(name, fromlist=["coverage"])
        __import__(name + ".coverage")
    except Exception:
        cleanup()
        raise
    return package, cleanup


_COUNTER = [0]


def test_c35_the_origin_laws_go_red_when_the_derivations_are_deleted():
    """Non-vacuity for C33/C34, by source-level deletion in isolated copies.

    Deletion rather than monkeypatching: a patched module proves the law *can* be
    made to fail, not that the shipped source is what makes it pass. Three probes,
    each removing exactly one derivation.
    """
    v = {s: R.ERROR for s in IMPLS}
    v.update({"native": R.NOT_APPLICABLE, "oracle": R.NOT_APPLICABLE})
    override_rec = receipt(v, execution_facts=facts(exit_code=3))
    override_task = task(allowed_exit_codes=[0])
    reason_ev = evidence_for("tests", {"reason": "baseline run failed"})

    # (a) delete the substrate-override derivation. The override episode then
    #     reads `verifier_reported` -- the precise false sentence C33 exists to
    #     stop, restored by removing one line.
    blind_to_facts = ("coverage.py",
                      '    facts = receipt.get("execution_facts")',
                      '    return False  # deletion probe\n'
                      '    facts = receipt.get("execution_facts")')
    pkg, cleanup = _isolated_coverage(blind_to_facts)
    try:
        r = pkg.coverage.response_coverage(
            override_rec, SPEC, override_task, reason_ev)
        assert r["substrate_override"] is False
        assert r["signals"]["tests"]["error_origin"] == "verifier_reported", \
            "the probe did not apply; (a) proves nothing"
        assert "the wired verifier itself reported" in r["signals"]["tests"]["missing"]
    finally:
        cleanup()

    # (b) stop reading the sealed evidence. A raise then reads as the verifier
    #     having reported an error of its own -- two different facts, one name.
    raised_ev = evidence_for("tests", {"error_origin": "verifier_exception",
                                       "error_code": "VERIFIER_RAISED",
                                       "exception_type": "ValueError"})
    healthy = receipt(v, execution_facts=facts(exit_code=0))
    blind_to_evidence = ("coverage.py",
                         "    if not isinstance(verifier_evidence, Mapping):\n"
                         "        return {}",
                         "    if True:\n"
                         "        return {}")
    pkg, cleanup = _isolated_coverage(blind_to_evidence)
    try:
        r = pkg.coverage.response_coverage(healthy, SPEC, override_task, raised_ev)
        assert r["signals"]["tests"]["error_origin"] != "verifier_exception", \
            "the probe did not apply; (b) proves nothing"
        assert r["signals"]["tests"]["error_code"] is None
    finally:
        cleanup()

    # (c) guess instead of abstaining. With no evidence at all the reading claims
    #     the verifier reported it -- which is exactly the unfounded claim.
    guessing = ("coverage.py",
                "    if override is None or not detail:",
                "    if False:")
    pkg, cleanup = _isolated_coverage(guessing)
    try:
        r = pkg.coverage.response_coverage(healthy, SPEC, override_task)
        assert r["signals"]["tests"]["error_origin"] == "verifier_reported", \
            "the probe did not apply; (c) proves nothing"
    finally:
        cleanup()

    # And with everything restored, each of those inverts.
    restored = C.response_coverage(override_rec, SPEC, override_task, reason_ev)
    assert restored["substrate_override"] is True
    assert restored["signals"]["tests"]["error_origin"] == C.ORIGIN_SUBSTRATE_OVERRIDE
    assert C.response_coverage(healthy, SPEC, override_task, raised_ev)[
        "signals"]["tests"]["error_origin"] == C.ORIGIN_VERIFIER_EXCEPTION
    assert C.response_coverage(healthy, SPEC, override_task)[
        "signals"]["tests"]["error_origin"] is None


def test_c36_the_shipped_command_reports_the_sealed_origin():
    """The taxonomy is worthless if the command nobody bypasses does not show it.

    C34 proves the *reading* recovers the origin from sealed evidence. This proves
    the **shipped `trvs verify-episode`** does, which is a different claim and was
    false when the taxonomy landed: `cli._episode_documents` returned three
    documents, so `response_coverage` was called without `verifier_evidence` and
    every verifier-fault episode printed an undetermined origin -- exactly the
    episodes the taxonomy exists to explain.

    The receipt pins verifier evidence only by *digest*, so the origin genuinely
    cannot be recovered from the receipt alone; the fourth document is not a
    convenience. Both output forms are asserted, because a consumer reading
    `--json` and an operator reading the terminal must not be told different
    things.

    Driven through the real entry point rather than by calling `_print_coverage`,
    because the defect was precisely that the shipped command did not pass an
    argument -- a test that called the helper directly would have passed
    throughout.
    """
    sys.path.insert(0, HERE)
    try:
        import test_evalone as TE
    finally:
        sys.path.remove(HERE)

    extra = {"tests": TE._boom_tests, "identity": TE._pass_identity}
    run, task_doc = TE._run_with(extra)
    path, report = TE._bundle(run, task_doc, extra, "c36")
    assert report["outcome"] == "closed", report.get("checks")

    # Exit 2 (`unavailable`) is the *correct* verdict and is not what this law
    # is about: the raising verifier is a fixture, so the shipped registry cannot
    # reproduce it on replay and closure is honestly undecidable. The reading is
    # commentary printed beside the verdict, so it must appear whatever the
    # verdict was -- asserting a specific exit code here would be testing the
    # closure checks, which C29 already owns.
    as_json = _trvs("verify-episode", path, "--json")
    assert as_json.stdout, as_json.stderr
    signals = json.loads(as_json.stdout)["coverage"]["signals"]
    assert signals["tests"]["error_origin"] == C.ORIGIN_VERIFIER_EXCEPTION, \
        signals["tests"]
    assert signals["tests"]["exception_type"].endswith("VerifierBoom"), \
        signals["tests"]

    human = _trvs("verify-episode", path)
    assert "raised while evaluating" in human.stdout, human.stdout

    # Non-vacuous: the three-document call is what shipped, and it cannot know.
    from traaviis import cli as _cli
    documents = _cli._episode_documents(path)
    assert len(documents) == 4, "the evidence map is the fourth document"
    assert C.response_coverage(*documents[:3])[
        "signals"]["tests"]["error_origin"] is None, \
        "without the evidence the origin must stay undetermined, never guessed"


# --------------------------------------------------------------------------- #
# standalone runner (zero deps)                                               #
# --------------------------------------------------------------------------- #

def _main():
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    failures, skipped = [], []
    for name, fn in tests:
        try:
            fn()
            print("PASS  %s" % name)
        except Skip as exc:
            # Counted, not swallowed. A skipped law that printed nothing would
            # read as a law that does not exist, and the battery runner tallies
            # by these line prefixes.
            skipped.append((name, exc))
            print("SKIP  %s (%s)" % (name, exc))
        except AssertionError as exc:
            failures.append((name, exc))
            print("FAIL  %s: %s" % (name, exc))
    # The house summary shape. The runner accepts `N/N passed` too, but that form
    # cannot say how many were skipped, and on a machine with no engine the
    # difference between "skipped" and "did not run" is the whole point.
    print("\n%d passed, %d skipped, %d failed"
          % (len(tests) - len(failures) - len(skipped), len(skipped),
             len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
