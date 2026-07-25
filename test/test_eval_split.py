"""Mutation laws for `trvs eval` -- running an agent over a split (RFC §7).

`eval-one` proved one task. `eval` runs a *split* and emits one `episode-...`
per task. These laws are mostly about what it must NOT do:

  * it must not invent an artifact id (the ladder stops at `env-...`);
  * it must not run a single agent process before the package reopens, the
    split resolves, and the subject tree admits;
  * it must not let one bad task abort the split, nor report a refusal as a
    score of zero;
  * it must not let split *order* be observable -- a split is a set.

Engine-dependent laws SKIP without a locatable Forge engine.

Run directly:      python3 test/test_eval_split.py
Run under pytest:  pytest test/test_eval_split.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import evalsplit as ES, pack as P, scaffold as S  # noqa: E402
from traaviis import engine as _engine  # noqa: E402
from traaviis.substrates import AdmissionError  # noqa: E402

AGENT = [sys.executable, os.path.join(REPO, "test", "fixtures",
                                      "residency_agent.py")]


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


def _multi_env(tmp, name="env", bad_task=False):
    """Scaffold, then widen it into a three-task environment with splits.

    Task ids differ only by their objective, so the tasks are genuinely distinct
    artifacts over the same subject and reward -- which is exactly the shape a
    split is for. With `bad_task`, the third task requires an `identity` signal
    while declaring no identity policy, so it is an invalid-config episode: a
    task that *runs and is recorded as bad*, not one that aborts the split.
    """
    env = os.path.join(tmp, name)
    S.materialize("evidence-residency", env)
    base = _read(os.path.join(env, "task.json"))

    refs = ["task.json"]
    for i, objective in enumerate(("second objective", "third objective"), start=2):
        doc = json.loads(json.dumps(base))
        doc["instructions"]["objective"] = objective
        if bad_task and i == 3:
            doc.pop("identity_policy", None)  # requires `identity`, cannot wire it
        ref = "task%d.json" % i
        _write(os.path.join(env, ref), doc)
        refs.append(ref)

    manifest = _read(os.path.join(env, "env.json"))
    manifest["tasks"] = refs
    manifest["splits"] = {"all": list(refs), "test": refs[:2]}
    _write(os.path.join(env, "env.json"), manifest)
    return env


def _packed(tmp, **kw):
    env = _multi_env(tmp, **kw)
    out = os.path.join(tmp, "pkg")
    report = P.pack(env, out, engine=_engine_or_skip())
    return env, out, report


# --- E1: a split is a SET -----------------------------------------------------

def test_split_order_does_not_move_env_id():
    """Listing the same tasks in a different order is the same split (§3)."""
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        a = _multi_env(tmp, "a")
        b = _multi_env(tmp, "b")
        m = _read(os.path.join(b, "env.json"))
        m["splits"]["all"] = list(reversed(m["splits"]["all"]))
        m["splits"]["test"] = list(reversed(m["splits"]["test"]))
        _write(os.path.join(b, "env.json"), m)

        ra = P.pack(a, os.path.join(tmp, "pa"), engine=eng)
        rb = P.pack(b, os.path.join(tmp, "pb"), engine=eng)
        assert ra["env_id"] == rb["env_id"], "split order moved env-"

        # ...and the packed manifest stores the canonical (sorted) set.
        packed = _read(os.path.join(tmp, "pa", "environment.json"))
        for members in packed["splits"].values():
            assert members == sorted(members)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_duplicate_split_member_is_refused():
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        env = _multi_env(tmp)
        m = _read(os.path.join(env, "env.json"))
        m["splits"]["test"] = ["task.json", "task.json"]
        _write(os.path.join(env, "env.json"), m)
        try:
            P.pack(env, os.path.join(tmp, "pkg"), engine=eng)
        except AdmissionError as ex:
            assert ex.code == "SPLIT_DUPLICATE", ex.code
        else:
            raise AssertionError("a repeated split member was accepted")
        assert not os.path.exists(os.path.join(tmp, "pkg"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- E2: nothing runs until the package reopens -------------------------------

def test_tampered_package_refuses_before_any_agent_runs():
    """A tampered task must fail the reopen -- not after N episodes."""
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        doc = _read(os.path.join(out, "task.json"))
        doc["instructions"]["objective"] = "tampered after packing"
        _write(os.path.join(out, "task.json"), doc)

        marker = os.path.join(tmp, "agent-ran")
        agent = [sys.executable, "-c",
                 "open(%r,'w').write('x')" % marker]
        try:
            ES.eval_split(out, "all", agent)
        except AdmissionError as ex:
            assert ex.code == "REOPEN_TASK_ID", ex.code
        else:
            raise AssertionError("a tampered package was evaluated")
        assert not os.path.exists(marker), "the agent ran despite a refusal"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_drifted_subject_refuses_before_any_agent_runs():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        target = os.path.join(out, "subject", "src", "mod.py")
        with open(target, "a") as fh:
            fh.write("# drift\n")

        marker = os.path.join(tmp, "agent-ran")
        agent = [sys.executable, "-c", "open(%r,'w').write('x')" % marker]
        try:
            ES.eval_split(out, "all", agent)
        except AdmissionError:
            pass
        else:
            raise AssertionError("a drifted subject was evaluated")
        assert not os.path.exists(marker)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- E3: the split itself must resolve ---------------------------------------

def test_unknown_and_empty_splits_are_typed_refusals():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        manifest = _read(os.path.join(out, "environment.json"))

        try:
            ES.resolve_split(manifest, "nope")
        except AdmissionError as ex:
            assert ex.code == "SPLIT_UNKNOWN", ex.code
            assert "all" in str(ex) and "test" in str(ex), "known splits not named"
        else:
            raise AssertionError("an unknown split resolved")

        empty = dict(manifest, splits=dict(manifest["splits"], empty=[]))
        try:
            ES.resolve_split(empty, "empty")
        except AdmissionError as ex:
            assert ex.code == "SPLIT_EMPTY", ex.code
        else:
            raise AssertionError("an empty split resolved")

        foreign = dict(manifest,
                       splits=dict(manifest["splits"], odd=["task-" + "0" * 64]))
        try:
            ES.resolve_split(foreign, "odd")
        except AdmissionError as ex:
            assert ex.code == "SPLIT_UNRESOLVED", ex.code
        else:
            raise AssertionError("a foreign task resolved inside a split")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- E4: run order is derived, not observed ----------------------------------

def test_run_order_is_sorted_task_ids():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        manifest = _read(os.path.join(out, "environment.json"))
        shuffled = dict(manifest)
        shuffled["splits"] = dict(manifest["splits"])
        shuffled["splits"]["all"] = list(reversed(manifest["splits"]["all"]))
        assert ES.resolve_split(shuffled, "all") == \
            ES.resolve_split(manifest, "all") == sorted(manifest["splits"]["all"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- E5: the index invents no identity ---------------------------------------

def test_evaluation_index_carries_no_identity_of_its_own():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        report = ES.eval_split(out, "test", AGENT)

        assert report["evaluation_version"] == "traaviis.evaluation.v1"
        forbidden = ("evaluation_id", "eval_id", "run_id", "split_id", "bundle_id")
        for key in forbidden:
            assert key not in report, "the index invented %s" % key
        # No `<prefix>-<hex>` literal anywhere except the ids it *reports*
        # (env-, task-, episode-, snap-), each of which is derived elsewhere.
        import re
        allowed = {"env", "task", "episode", "snap", "sem"}
        blob = json.dumps(report)
        for prefix in set(re.findall(r"\b([a-z]+)-[0-9a-f]{16,}", blob)):
            assert prefix in allowed, "unexpected id family %r in the index" % prefix
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- E6/E10: one episode per task, totals are arithmetic over them ------------

def test_one_episode_per_task_and_totals_agree():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, report_pack = _packed(tmp)
        report = ES.eval_split(out, "all", AGENT)

        assert len(report["episodes"]) == 3
        assert report["env_id"] == report_pack["env_id"]
        ids = [e["episode_id"] for e in report["episodes"]]
        assert len(set(ids)) == 3, "distinct tasks produced a shared episode id"
        assert [e["task_id"] for e in report["episodes"]] == \
            sorted(e["task_id"] for e in report["episodes"])

        totals = report["totals"]
        assert totals["tasks"] == 3
        assert totals["ok"] == sum(1 for e in report["episodes"]
                                   if e["status"] == "ok")
        scored = [e["reward"] for e in report["episodes"] if e["reward"] is not None]
        assert totals["scored"] == len(scored)
        assert abs(totals["reward_sum"] - sum(scored)) < 1e-12
        assert abs(totals["reward_mean"] - (sum(scored) / len(scored))) < 1e-12
        assert all(e["reward"] == 1 for e in report["episodes"]), report["episodes"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- E9: a bad task is recorded, not fatal -----------------------------------

def test_a_bad_task_is_recorded_and_the_split_continues():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp, bad_task=True)
        report = ES.eval_split(out, "all", AGENT)

        assert len(report["episodes"]) == 3, "one bad task aborted the split"
        bad = [e for e in report["episodes"] if e["status"] != "ok"]
        assert len(bad) == 1, [e["status"] for e in report["episodes"]]
        assert report["totals"]["ok"] == 2
        # The good tasks still scored; the bad one is not silently a zero.
        good = [e for e in report["episodes"] if e["status"] == "ok"]
        assert all(e["reward"] == 1 for e in good)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- E8: persisted episodes are real, closed bundles -------------------------

def test_persisted_episodes_reverify_and_index_is_written():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        dest = os.path.join(tmp, "episodes")
        report = ES.eval_split(out, "test", AGENT, output=dest)
        ES.write_evaluation(report, os.path.join(dest, "evaluation.json"))

        index = _read(os.path.join(dest, "evaluation.json"))
        assert index == report

        for entry in report["episodes"]:
            assert entry["bundle"] == entry["episode_id"], entry
            bundle = os.path.join(dest, entry["bundle"])
            assert os.path.isdir(bundle)
            proc = subprocess.run(
                [sys.executable, "-m", "traaviis.cli", "verify-episode", bundle],
                cwd=REPO, capture_output=True, text=True)
            assert proc.returncode == 0, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- E11: a substrate that cannot be evaluated says so -----------------------

def test_world_package_is_refused_by_name():
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        env = os.path.join(tmp, "world-env")
        S.materialize("golden-spinner", env)
        out = os.path.join(tmp, "world-pkg")
        P.pack(env, out, engine=eng)
        try:
            ES.open_environment(out, engine=eng)
        except AdmissionError as ex:
            assert ex.code == "SUBSTRATE_NOT_EVALUABLE", ex.code
        else:
            raise AssertionError("a world package was accepted for evaluation")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_non_package_directory_is_refused():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        try:
            ES.open_environment(tmp)
        except AdmissionError as ex:
            assert ex.code == "ENV_MISSING", ex.code
        else:
            raise AssertionError("a bare directory opened as an environment")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- E13: determinism --------------------------------------------------------

def test_two_runs_of_the_same_split_agree():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        a = ES.eval_split(out, "test", AGENT)
        b = ES.eval_split(out, "test", AGENT)
        assert [e["episode_id"] for e in a["episodes"]] == \
            [e["episode_id"] for e in b["episodes"]]
        assert a["totals"] == b["totals"]
        assert a == b
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- CLI contract ------------------------------------------------------------

def test_cli_eval_exit_codes_and_json():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        dest = os.path.join(tmp, "episodes")
        argv = [sys.executable, "-m", "traaviis.cli", "eval", out,
                "--split", "test", "--output", dest, "--json", "--agent"] + AGENT
        proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        report = json.loads(proc.stdout)
        assert report["totals"]["ok"] == 2
        assert os.path.isfile(os.path.join(dest, "evaluation.json"))

        proc = subprocess.run(
            [sys.executable, "-m", "traaviis.cli", "eval", out,
             "--split", "nope", "--agent"] + AGENT,
            cwd=REPO, capture_output=True, text=True)
        assert proc.returncode == 2 and "SPLIT_UNKNOWN" in proc.stderr, proc.stderr

        proc = subprocess.run(
            [sys.executable, "-m", "traaviis.cli", "eval",
             os.path.join(tmp, "nowhere"), "--split", "all", "--agent"] + AGENT,
            cwd=REPO, capture_output=True, text=True)
        assert proc.returncode == 2 and "no such package" in proc.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_eval_reports_disagreement_with_exit_1():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp, bad_task=True)
        proc = subprocess.run(
            [sys.executable, "-m", "traaviis.cli", "eval", out,
             "--split", "all", "--agent"] + AGENT,
            cwd=REPO, capture_output=True, text=True)
        assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
        assert "ok" in proc.stdout and "2/3" in proc.stdout, proc.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- E14..E18: the report states how it was scored and what ran it -----------
#
# `EvaluationV1` is not content-addressed, so nothing in it is pinned by a hash.
# That makes the two things a reader would otherwise have to assume -- the
# denominator of the mean, and whether the declared verifiers were actually
# available -- into claims the report has to make out loud.

def test_aggregation_profile_states_the_denominator_it_used():
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        report = ES.eval_split(out, "all", AGENT)

        prof = report["aggregation_profile"]
        assert prof["profile"] == ES.AGGREGATION_PROFILE
        assert prof["statistic"] == "arithmetic_mean"
        assert prof["population"] == "scored_episodes"
        assert prof["unscored_policy"] == "excluded"
        # The declared population is the mean's actual denominator, not a label
        # sitting next to a number computed some other way.
        totals = report["totals"]
        assert prof["scored"] == totals["scored"]
        assert prof["scored"] + prof["unscored"] == totals["tasks"]
        assert abs(totals["reward_mean"]
                   - totals["reward_sum"] / prof["scored"]) < 1e-12
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unscored_episodes_are_excluded_not_imputed_as_zero():
    """The distinction the profile exists to make, on a split that shows it.

    One of the three tasks is invalid-config, so it never produces a reward. Under
    `excluded` the mean is 1.0 over two episodes; imputing a zero would report
    0.667 and describe a run that did not happen. The profile has to agree with
    whichever the code did, and the code has to do the one it declares.
    """
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp, bad_task=True)
        report = ES.eval_split(out, "all", AGENT)

        prof, totals = report["aggregation_profile"], report["totals"]
        assert totals["tasks"] == 3
        assert prof["unscored"] >= 1, "the bad task still produced a reward"
        assert prof["scored"] == totals["scored"] < totals["tasks"]
        assert abs(totals["reward_mean"] - 1.0) < 1e-12, (
            "the mean was taken over all tasks, which imputes a score for a "
            "task that was never scored")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_runtime_context_attests_the_registry_that_actually_ran():
    from traaviis import wiring

    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        eng = _engine_or_skip()
        _env, out, _r = _packed(tmp)
        registry = wiring.default_registry(eng)
        report = ES.eval_split(out, "test", AGENT, registry=registry)

        ctx = report["runtime_context"]
        assert ctx["wiring"] == "registry"
        assert ctx["registry_version"] == wiring.VERIFIER_REGISTRY_VERSION
        assert ctx["verifiers_available"] == registry.available()
        assert ctx["verifier_versions"] == registry.versions()
        # Prose about *why* something was unavailable is not a finding, and a
        # report is exactly where prose gets quoted as one.
        assert "notes" not in ctx
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_caller_supplied_verifiers_are_attested_as_such():
    """"No registry was consulted" and "the registry could answer nothing" are
    different facts, and the report must not collapse them into one empty list."""
    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        report = ES.eval_split(out, "test", AGENT,
                               extra_verifiers_for=lambda t: {})

        ctx = report["runtime_context"]
        assert ctx["wiring"] == "caller_supplied"
        assert ctx["registry_version"] is None
        assert ctx["verifiers_available"] == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_registry_missing_a_verifier_is_visible_in_the_report():
    """An engine-less run must not read like an engine-present one.

    This is the whole point of the attestation: without it, a run that never
    executed the identity verifier produces a report indistinguishable from one
    that did, and the weaker run silently corroborates the stronger.
    """
    from traaviis import wiring

    tmp = tempfile.mkdtemp(prefix="trvs-eval-law-")
    try:
        _env, out, _r = _packed(tmp)
        full = wiring.default_registry(_engine_or_skip())
        thin = wiring.VerifierRegistryV1(
            tests=full.get("tests"), identity=None,
            notes=["identity verifier unavailable in this runtime"])

        rich = ES.eval_split(out, "test", AGENT, registry=full)
        poor = ES.eval_split(out, "test", AGENT, registry=thin)

        assert "identity" in rich["runtime_context"]["verifiers_available"]
        assert "identity" not in poor["runtime_context"]["verifiers_available"]
        assert rich["runtime_context"] != poor["runtime_context"]
        # ...and the note explaining the absence is still not copied through.
        assert json.dumps(poor["runtime_context"]).count("unavailable") == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
