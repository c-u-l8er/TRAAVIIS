"""Laws for the first genuine repair task (`residency-repair`).

Every earlier Residency fixture in this tree answers "did the agent produce an
admissible patch?". `evidence-residency` seeds `return 1` and accepts either
`return 1` or `return 2`, so its acceptance test cannot fail on the subject it
ships with; a reviewer reclassified it, correctly, as a conformance fixture
rather than a finding. This one asks "did the agent fix the bug?", and the
difference has to be visible in the artifacts, not just in the prose.

The shape:

    spec/contract.md   requires the module to return 2
    src/mod.py         returns 1
    target test        baseline exits 1  ->  patched exits 0
    health control     baseline exits 0  ->  patched exits 0

The laws below are mostly about the ways that shape can be faked. A task can
claim to reproduce a defect whose test was always green (R2/R3); a fixture can
declare an expectation its own subject does not meet (R6); a candidate can
satisfy the target by destroying what made it meaningful (R7); a "repair" can
leave the actual defect in place and still look admissible (R8).

Engine-dependent laws SKIP without a locatable Forge engine.

Run directly:      python3 test/test_repair_task.py
Run under pytest:  pytest test/test_repair_task.py
"""

import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import engine as _engine  # noqa: E402
from traaviis import evalsplit as ES, pack as P, scaffold as S  # noqa: E402
from traaviis import substrate_verifiers as SV  # noqa: E402

TEMPLATE = "residency-repair"
AGENT = [sys.executable, os.path.join(REPO, "test", "fixtures", "repair_agent.py")]


class Skip(Exception):
    pass


def _engine_or_skip():
    eng = _engine.try_load()
    if eng is None:
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    return eng


def _read(path):
    with open(path) as fh:
        return json.load(fh)


def _write(path, doc):
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2)


def _packed(tmp, mutate=None):
    """Scaffold the repair template, optionally mutate it, and pack it."""
    env = os.path.join(tmp, "env")
    S.materialize(TEMPLATE, env)
    if mutate is not None:
        mutate(env)
    out = os.path.join(tmp, "pkg")
    report = P.pack(env, out, engine=_engine_or_skip())
    return env, out, report


def _run(tmp, mode="ok", **kw):
    """Evaluate one candidate agent over the packed environment.

    The mode is an agent ARGUMENT, not an environment variable: the sealed
    runner never inherits the host environment (§10a), so `os.environ` cannot
    reach the agent. It also keeps `task-…` identical across the three
    candidates, which is what \"one task, three agents\" has to mean.
    """
    return ES.eval_split(tmp, "all", AGENT + [mode], **kw)


# --- R1: the subject actually contains the defect ----------------------------

def test_r1_the_seeded_subject_disagrees_with_its_own_spec():
    """A repair task whose subject is already correct is not a repair task."""
    files = S.scaffold(TEMPLATE)
    mod = files["subject/src/mod.py"].decode()
    spec = files["subject/spec/contract.md"].decode()

    assert mod.strip() == "return 1", mod
    assert "That integer is 2." in spec
    # The defect is the disagreement, so it must be stated by both halves: a spec
    # that merely omitted the requirement would leave nothing to repair.
    assert "return 2" not in mod


# --- R2/R3: the target test is red on the baseline, and says so --------------

def test_r2_the_target_test_declares_a_failing_baseline():
    task = json.loads(S.scaffold(TEMPLATE)["task.json"])
    plan = task["test_plan"]
    assert plan["test_plan_version"] == SV.TEST_PLAN_V2

    target, health = plan["commands"]
    assert target["baseline"]["allowed_exit_codes"] == [1]
    assert target["patched"]["allowed_exit_codes"] == [0]
    assert health["baseline"]["allowed_exit_codes"] == [0]
    assert health["patched"]["allowed_exit_codes"] == [0]


def test_r3_the_target_test_is_genuinely_red_on_the_seeded_subject():
    """Run the declared command against the seeded bytes and check it fails.

    Declaring `baseline: [1]` is a claim about the subject. This law checks the
    claim rather than trusting it -- a target test that happened to be green
    would make the whole task a conformance check with a misleading expectation
    attached.
    """
    files = S.scaffold(TEMPLATE)
    content = {
        "src/mod.py": files["subject/src/mod.py"].decode(),
        "spec/contract.md": files["subject/spec/contract.md"].decode(),
        "world/frozen.wrl": files["subject/world/frozen.wrl"].decode(),
    }
    plan = json.loads(files["task.json"])["test_plan"]

    _facts, executables = _resolve(plan)

    def bare(i):
        """The i-th command with its phase expectations stripped.

        A command that declares neither `baseline` nor `patched` is judged by
        the default rule -- exit 0 passes -- which is what \"is this test red
        right now?\" means independent of what the plan claims.
        """
        cmd = {k: v for k, v in plan["commands"][i].items()
               if k not in (SV.BASELINE, SV.PATCHED)}
        return dict(plan, commands=[cmd])

    t_state, t_rec = SV.run_command_set(bare(0), content,
                                        executables=executables)
    assert not SV.command_set_passed(t_state), t_rec
    h_state, _h_rec = SV.run_command_set(bare(1), content,
                                         executables=executables)
    assert SV.command_set_passed(h_state)

    # Judged by the plan's own baseline expectations, BOTH are satisfied -- which
    # is the entire content of "this test is supposed to fail right now".
    state, rec = SV.run_command_set(
        plan, content, executables=executables, phase=SV.BASELINE)
    assert SV.command_set_passed(state), rec
    assert [r["expected_exit_codes"] for r in rec] == [[1], [0]]


def _resolve(plan):
    from traaviis import toolchain
    return toolchain.resolve_toolchain(
        plan["toolchain_profile"], SV.test_plan_tools(plan))


# --- R4/R5: the repair scores 1.0 and the evidence reopens -------------------

def test_r4_the_repair_scores_full_reward_on_all_five_signals():
    tmp = tempfile.mkdtemp(prefix="trvs-repair-law-")
    try:
        _env, out, _r = _packed(tmp)
        report = _run(out, "ok")

        assert len(report["episodes"]) == 1
        ep = report["episodes"][0]
        assert ep["status"] == "ok", ep
        assert ep["validity"] == "valid", ep
        assert abs(ep["reward"] - 1.0) < 1e-9, ep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_r5_the_persisted_episode_reopens_closed():
    from traaviis import episode_bundle

    tmp = tempfile.mkdtemp(prefix="trvs-repair-law-")
    try:
        _env, out, _r = _packed(tmp)
        keep = os.path.join(tmp, "episodes")
        report = _run(out, "ok", output=keep)

        ep = report["episodes"][0]
        assert ep["persistence"]["status"] == "closed", ep["persistence"]
        assert report["totals"]["persistence_error"] == 0

        bundle = os.path.join(keep, ep["bundle"])
        reopened = episode_bundle.verify_episode_bundle(bundle)
        assert reopened["episode_id"] == ep["episode_id"], reopened
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- R6: a fixture that does not reproduce its own defect is an ERROR --------

def test_r6_a_baseline_that_does_not_reproduce_is_a_fixture_error():
    """The asymmetry, on the case that motivates it.

    Seed the subject already fixed while the plan still declares the target red.
    The baseline then contradicts the task, and that is a statement about the
    *fixture*, not about an agent who has not run yet -- so it must be `error`,
    never `fail`.
    """
    tmp = tempfile.mkdtemp(prefix="trvs-repair-law-")
    try:
        def prefix(env):
            with open(os.path.join(env, "subject", "src", "mod.py"), "w") as fh:
                fh.write("return 2\n")

        _env, out, _r = _packed(tmp, mutate=prefix)
        report = _run(out, "ok")

        ep = report["episodes"][0]
        assert ep["status"] == "error", ep
        assert ep["reward"] is None, ep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- R7: the health control catches a candidate that games the target -------

def test_r7_satisfying_the_target_by_wrecking_the_repository_fails():
    tmp = tempfile.mkdtemp(prefix="trvs-repair-law-")
    try:
        _env, out, _r = _packed(tmp)
        gamed = _run(out, "gutspec")
        honest = _run(out, "ok")

        bad, good = gamed["episodes"][0], honest["episodes"][0]
        # One task, two candidates: the mode rides in the agent's argv, so the
        # task bytes -- and therefore `task-…` -- are identical. If these ever
        # diverged the comparison below would be between two different tasks.
        assert bad["task_id"] == good["task_id"], (bad, good)
        assert bad["episode_id"] != good["episode_id"], (bad, good)
        # The gamed candidate genuinely satisfies the target check -- it really
        # does leave `return 2` on disk -- so nothing but the control stops it.
        assert abs(good["reward"] - 1.0) < 1e-9, good
        assert bad["reward"] is not None and bad["reward"] < good["reward"], bad
        assert bad["reward"] <= 0.4 + 1e-9, (
            "the tests cap did not apply; the control did not turn red", bad)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- R8: an admissible patch that does not fix the defect is not a repair ---

def test_r8_an_admissible_patch_that_misses_the_fix_still_fails():
    """`return 3` applies cleanly, cites correctly, and keeps the world's
    identity. Under `evidence-residency` that would have scored full marks."""
    tmp = tempfile.mkdtemp(prefix="trvs-repair-law-")
    try:
        _env, out, _r = _packed(tmp)
        report = _run(out, "nofix")

        ep = report["episodes"][0]
        assert ep["reward"] is not None, ep
        assert ep["reward"] <= 0.4 + 1e-9, (
            "a patch that did not fix the defect was not capped by tests", ep)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- R9: the repair template is not a renamed copy of the conformance one ----

def test_r9_two_templates_on_one_substrate_seed_different_subjects():
    """L3 compares templates across substrates. These two share one, so the
    "not a renamed skeleton" claim needs its own check here."""
    a = S.scaffold("evidence-residency")
    b = S.scaffold(TEMPLATE)

    assert json.loads(a["env.json"])["substrate_profile"] == \
        json.loads(b["env.json"])["substrate_profile"]
    assert set(a) != set(b), "the two templates seed the same file set"
    assert a["task.json"] != b["task.json"]

    a_plan = json.loads(a["task.json"])["test_plan"]["commands"][0]
    b_plan = json.loads(b["task.json"])["test_plan"]["commands"][0]
    assert "baseline" not in a_plan, (
        "the conformance template acquired a phase expectation")
    assert b_plan["baseline"]["allowed_exit_codes"] == [1]


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
