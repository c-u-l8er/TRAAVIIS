"""Mutation laws for `trvs pack` and the §5a substrate admission interface.

`pack` is where identity is earned, so these laws are about refusing to earn it
dishonestly: identity must be recomputed from bytes, closure must be verified
before anything is written, presentation must not move `env-`, and the emitted
package must survive being reopened and re-derived from disk.

Engine-dependent laws SKIP without a locatable Forge engine; the structural laws
run everywhere.

Run directly:      python3 test/test_pack.py
Run under pytest:  pytest test/test_pack.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import identity, pack as P, scaffold as S  # noqa: E402
from traaviis import engine as _engine  # noqa: E402
from traaviis.substrates import AdmissionError  # noqa: E402

FIXTURE_AGENT = os.path.join(REPO, "test", "fixtures", "residency_agent.py")


class Skip(Exception):
    pass


def _engine_or_skip():
    eng = _engine.try_load()
    if eng is None:
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    return eng


def _scaffolded(tmp, template, name="env"):
    dest = os.path.join(tmp, name)
    S.materialize(template, dest)
    return dest


def _edit_json(path, mutate):
    with open(path) as fh:
        doc = json.load(fh)
    mutate(doc)
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2)


def _packed(tmp, template="evidence-residency"):
    env = _scaffolded(tmp, template)
    out = os.path.join(tmp, "pkg")
    return env, out, P.pack(env, out, engine=_engine_or_skip())


# --- P1: identity is recomputed, never accepted ------------------------------

def test_p1_pack_derives_every_id_from_bytes():
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        env, out, report = _packed(tmp)
        manifest = json.load(open(os.path.join(out, "environment.json")))
        assert manifest["env_id"] == identity.environment_id(manifest)
        task = json.load(open(os.path.join(out, "task.json")))
        assert task["task_id"] == identity.task_id(task)
        reward = json.load(open(os.path.join(out, "reward.json")))
        assert reward["reward_id"] == identity.reward_id(reward)
        snap = json.load(open(os.path.join(out, "snapshot.json")))
        assert snap["snapshot_id"] == identity.snapshot_id(snap)
        # And the task is bound to THOSE ids, not to a scaffold-level reference.
        assert task["reward_id"] == reward["reward_id"]
        assert task["subject"]["snapshot_id"] == snap["snapshot_id"]
        assert "reward_spec" not in task and "snapshot_def" not in task["subject"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_p1_prebound_identity_is_refused():
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        for path, key, value in (
            ("env.json", "env_id", "env-deadbeef"),
            ("reward.json", "reward_id", "rew-deadbeef"),
            ("task.json", "task_id", "task-deadbeef"),
        ):
            env = _scaffolded(tmp, "evidence-residency", name="env-" + key)
            _edit_json(os.path.join(env, path), lambda d: d.__setitem__(key, value))
            out = os.path.join(tmp, "out-" + key)
            try:
                P.pack(env, out, engine=_engine.try_load())
            except AdmissionError as ex:
                assert ex.code == "SOURCE_PREBOUND", (key, ex.code)
            else:
                raise AssertionError("pack accepted a pre-bound %s" % key)
            assert not os.path.exists(out), key
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- P2: identity moves with content ----------------------------------------

def test_p2_subject_bytes_move_the_snapshot_and_the_environment():
    _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        _, out_a, a = _packed(tmp)
        env_b = _scaffolded(tmp, "evidence-residency", name="env-b")
        with open(os.path.join(env_b, "subject", "src", "mod.py"), "w") as fh:
            fh.write("return 2\n")
        b = P.pack(env_b, os.path.join(tmp, "pkg-b"), engine=_engine.try_load())
        assert a["subject"]["snapshot_id"] != b["subject"]["snapshot_id"]
        assert a["env_id"] != b["env_id"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_p2_task_instructions_move_the_task_and_the_environment():
    _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        _, _, a = _packed(tmp)
        env_b = _scaffolded(tmp, "evidence-residency", name="env-b")
        _edit_json(os.path.join(env_b, "task.json"),
                   lambda d: d["instructions"].__setitem__("objective", "something else"))
        b = P.pack(env_b, os.path.join(tmp, "pkg-b"), engine=_engine.try_load())
        assert a["tasks"][0]["task_id"] != b["tasks"][0]["task_id"]
        assert a["env_id"] != b["env_id"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- P3: presentation does NOT move env- (RFC §5) ---------------------------

def test_p3_renaming_the_environment_does_not_move_env_id():
    _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        _, _, a = _packed(tmp)
        env_b = _scaffolded(tmp, "evidence-residency", name="env-b")

        def rename(d):
            d["name"] = "a completely different display name"
            d["description"] = "and a rewritten description"

        _edit_json(os.path.join(env_b, "env.json"), rename)
        b = P.pack(env_b, os.path.join(tmp, "pkg-b"), engine=_engine.try_load())
        assert a["env_id"] == b["env_id"], "presentation moved env-"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_p3_identity_allowlist_is_explicit():
    # The law above must hold by construction, not by the accident of a value.
    manifest = {
        "environment_version": "traaviis.environment.v1",
        "substrate_profile": "residency.repository.v1",
        "subject": {"snapshot_id": "snap-x"},
        "tasks": [], "rewards": [], "profiles": {}, "splits": {},
    }
    base = identity.environment_id(manifest)
    assert identity.environment_id({**manifest, "name": "n",
                                    "description": "d"}) == base
    assert identity.environment_id({**manifest, "splits": {"a": []}}) != base
    assert identity.environment_id({**manifest,
                                    "subject": {"snapshot_id": "snap-y"}}) != base


# --- P4/P5: closure is verified before writing, and failure writes nothing ---

def test_p4_unanswerable_verifier_plan_is_refused_before_write():
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        env = _scaffolded(tmp, "evidence-residency")
        _edit_json(os.path.join(env, "task.json"),
                   lambda d: d["verifier_plan"]["required"].append("telepathy"))
        out = os.path.join(tmp, "pkg")
        try:
            P.pack(env, out, engine=_engine.try_load())
        except AdmissionError as ex:
            assert ex.code == "CLOSURE_VERIFIER", ex.code
            assert "telepathy" in str(ex)
        else:
            raise AssertionError("pack accepted an unanswerable verifier plan")
        assert not os.path.exists(out), "a refused pack still wrote a package"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_p4_split_naming_an_unknown_task_is_refused():
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        env = _scaffolded(tmp, "evidence-residency")
        _edit_json(os.path.join(env, "env.json"),
                   lambda d: d["splits"].__setitem__("all", ["ghost.json"]))
        out = os.path.join(tmp, "pkg")
        try:
            P.pack(env, out, engine=_engine.try_load())
        except AdmissionError as ex:
            assert ex.code == "SPLIT_UNRESOLVED", ex.code
        else:
            raise AssertionError("pack accepted a dangling split member")
        assert not os.path.exists(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_p5_subject_drift_is_caught_before_write():
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        env = _scaffolded(tmp, "evidence-residency")
        with open(os.path.join(env, "subject", "extra.txt"), "w") as fh:
            fh.write("undeclared\n")
        out = os.path.join(tmp, "pkg")
        try:
            P.pack(env, out, engine=_engine.try_load())
        except AdmissionError as ex:
            assert ex.code == "SUBJECT_DRIFT", ex.code
            assert "extra.txt" in str(ex.detail)
        else:
            raise AssertionError("pack accepted a subject that drifted from its definition")
        assert not os.path.exists(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_p5_non_empty_destination_is_refused():
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        env = _scaffolded(tmp, "evidence-residency")
        out = os.path.join(tmp, "pkg")
        os.makedirs(out)
        with open(os.path.join(out, "keep.txt"), "w") as fh:
            fh.write("mine\n")
        try:
            P.pack(env, out, engine=_engine.try_load())
        except AdmissionError as ex:
            assert ex.code == "DEST_NOT_EMPTY", ex.code
        else:
            raise AssertionError("pack overwrote a non-empty destination")
        assert os.listdir(out) == ["keep.txt"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- P6: reopen actually re-derives from the written bytes ------------------

def test_p6_tampering_with_a_written_package_fails_reopen():
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        _, out, _ = _packed(tmp)
        from traaviis import substrates
        sub = substrates.for_profile("residency.repository.v1")
        sub.reopen_package(out, engine=eng)  # clean package reopens

        _edit_json(os.path.join(out, "task.json"),
                   lambda d: d["instructions"].__setitem__("objective", "tampered"))
        try:
            sub.reopen_package(out, engine=eng)
        except AdmissionError as ex:
            assert ex.code == "REOPEN_TASK_ID", ex.code
        else:
            raise AssertionError("a tampered task survived reopen")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_p6_tampering_with_subject_bytes_fails_reopen():
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        _, out, _ = _packed(tmp)
        from traaviis import substrates
        sub = substrates.for_profile("residency.repository.v1")
        with open(os.path.join(out, "subject", "src", "mod.py"), "w") as fh:
            fh.write("return 999\n")
        try:
            sub.reopen_package(out, engine=eng)
        except AdmissionError as ex:
            assert ex.code == "REOPEN_SUBJECT_BYTES", ex.code
        else:
            raise AssertionError("a tampered subject survived reopen")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- P7: the identity policy is bound to a real, recomputed identity --------

def test_p7_identity_policy_before_id_is_computed_not_declared():
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        env, out, _ = _packed(tmp)
        task = json.load(open(os.path.join(out, "task.json")))
        pinned = task["identity_policy"]["must_remain"]["world"]
        assert pinned["before_id"].startswith("sem-"), pinned
        with open(os.path.join(out, "subject", "world", "frozen.wrl")) as fh:
            src = fh.read()
        assert eng.lower_source(src)["semantic_artifact_id"] == pinned["before_id"]

        # A scaffold that pre-declares before_id is refused: pack recomputes.
        env_b = _scaffolded(tmp, "evidence-residency", name="env-b")
        _edit_json(
            os.path.join(env_b, "task.json"),
            lambda d: d["identity_policy"]["must_remain"]["world"].__setitem__(
                "before_id", "sem-deadbeef"))
        try:
            P.pack(env_b, os.path.join(tmp, "pkg-b"), engine=eng)
        except AdmissionError as ex:
            assert ex.code == "SOURCE_PREBOUND", ex.code
        else:
            raise AssertionError("pack accepted a declared before_id")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- P8: determinism ---------------------------------------------------------

def test_p8_packing_the_same_scaffold_twice_is_identical():
    _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        env_a = _scaffolded(tmp, "evidence-residency", name="a")
        env_b = _scaffolded(tmp, "evidence-residency", name="b")
        ra = P.pack(env_a, os.path.join(tmp, "pa"), engine=_engine.try_load())
        rb = P.pack(env_b, os.path.join(tmp, "pb"), engine=_engine.try_load())
        assert ra["env_id"] == rb["env_id"]
        assert ra["tasks"] == rb["tasks"] and ra["rewards"] == rb["rewards"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- P9: the TRVM profile re-lowers its subject -----------------------------

def test_p9_world_subject_is_lowered_and_reopened():
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        env = _scaffolded(tmp, "golden-spinner")
        out = os.path.join(tmp, "pkg")
        report = P.pack(env, out, engine=eng)
        sem = report["subject"]["semantic_artifact_id"]
        assert sem.startswith("sem-")
        with open(os.path.join(out, "world.wrl")) as fh:
            assert eng.lower_source(fh.read())["semantic_artifact_id"] == sem
        assert report["runnable"] is False  # no tasks -> not an eval bundle

        from traaviis import substrates
        sub = substrates.for_profile("trvm.world.v1")
        with open(os.path.join(out, "world.wrl"), "a") as fh:
            fh.write("\n[orb:extra]{pose}\n")
        try:
            sub.reopen_package(out, engine=eng)
        except AdmissionError as ex:
            assert ex.code == "REOPEN_SUBJECT_ID", ex.code
        else:
            raise AssertionError("an edited world survived reopen")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_p9_world_subject_without_an_engine_is_a_typed_failure():
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        env = _scaffolded(tmp, "golden-spinner")
        out = os.path.join(tmp, "pkg")
        try:
            P.pack(env, out, engine=None)
        except AdmissionError as ex:
            assert ex.code == "ENGINE_UNAVAILABLE", ex.code
        else:
            raise AssertionError("pack lowered a world without an engine")
        assert not os.path.exists(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- P10: the packed residency environment is actually runnable -------------

def test_p10_packed_environment_runs_end_to_end_and_scores():
    _engine_or_skip()
    if not os.path.isfile(FIXTURE_AGENT):
        raise Skip("residency fixture agent not present")
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        _, out, report = _packed(tmp)
        assert report["runnable"] is True
        manifest = json.load(open(os.path.join(out, "bundle.json")))
        assert manifest["eval_bundle_version"] == P.EVAL_BUNDLE_VERSION

        argv = [sys.executable, "-m", "traaviis.cli", "eval-one", out,
                "--agent", sys.executable, FIXTURE_AGENT,
                "--platform", "linux-x86_64", "--json"]
        proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        receipt = json.loads(proc.stdout)
        assert receipt["status"] == "ok", receipt.get("verification")
        assert receipt["validity"] == "valid"
        assert receipt["reward"] == 1
        assert receipt["task_id"] == report["tasks"][0]["task_id"]
        assert receipt["subject"]["snapshot_id"] == \
            report["subject"]["snapshot_id"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- CLI contract ------------------------------------------------------------

def test_cli_pack_reports_and_refuses():
    _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-pack-law-")
    try:
        env = _scaffolded(tmp, "evidence-residency")
        out = os.path.join(tmp, "pkg")
        argv = [sys.executable, "-m", "traaviis.cli", "pack", env, out, "--json"]
        proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        report = json.loads(proc.stdout)
        assert report["env_id"].startswith("env-")
        assert report["reopened"] is True

        proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
        assert proc.returncode == 2 and "DEST_NOT_EMPTY" in proc.stderr, proc.stderr

        proc = subprocess.run(
            [sys.executable, "-m", "traaviis.cli", "pack",
             os.path.join(tmp, "nope"), os.path.join(tmp, "x")],
            cwd=REPO, capture_output=True, text=True)
        assert proc.returncode == 2 and "no such environment" in proc.stderr
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
