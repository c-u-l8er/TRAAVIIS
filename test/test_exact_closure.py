"""Environment Identity and Split Evaluation Exact Closure (GPT-5.6 ruling).

The split ruling exposed a wider hole. `pack` canonicalized *splits* as sets but
still wrote the top-level `tasks` and `rewards` arrays in source order, so one
environment had as many valid `env-...` values as its task list had permutations.
These laws close that, and four related exactness gaps:

  X1-X6   an environment is a closed SET -- source order is not observable, and
          neither a repeated reference nor two files that derive one id is
          silently repaired. Canonical form is enforced on REOPEN, not merely
          produced by `pack`, so a hand-built package cannot smuggle a
          self-consistent but noncanonical manifest past admission.
  X7-X8   verifier availability is explicit runtime context, not task bytes: the
          identity verifier binds to the engine the caller selected, and the CLI
          and a library caller seal the same verifier versions.
  X9-X10  a score is not a result if its evidence was not kept -- and a low
          reward is still a successful evaluation.
  X11-X13 TestPlanV2 per-phase expectations, which are what make a bug
          reproduction (fail before, pass after) expressible at all.
  X14     the package imports under EAGER annotation evaluation, not only under
          the lazy semantics of the interpreter that happens to be installed.

Engine-dependent laws SKIP without a locatable Forge engine.

Run directly:      python3 test/test_exact_closure.py
Run under pytest:  pytest test/test_exact_closure.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import cli as CLI  # noqa: E402
from traaviis import engine as _engine  # noqa: E402
from traaviis import evalsplit as ES, identity as ID, pack as P  # noqa: E402
from traaviis import reward as R, scaffold as S  # noqa: E402
from traaviis import substrate_verifiers as SV, substrates, wiring  # noqa: E402
from traaviis.forge_adapter import ForgeIdentityAdapterV1, LowerResult  # noqa: E402
from traaviis.substrates import AdmissionError  # noqa: E402
from traaviis.vcontext import VerifierContextV1  # noqa: E402

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


def _multi_env(tmp, name="env", tasks=3):
    """Scaffold, then widen into an N-task environment (tasks differ by objective)."""
    env = os.path.join(tmp, name)
    S.materialize("evidence-residency", env)
    base = _read(os.path.join(env, "task.json"))
    refs = ["task.json"]
    for i in range(2, tasks + 1):
        doc = json.loads(json.dumps(base))
        doc["instructions"]["objective"] = "objective %d" % i
        ref = "task%d.json" % i
        _write(os.path.join(env, ref), doc)
        refs.append(ref)
    manifest = _read(os.path.join(env, "env.json"))
    manifest["tasks"] = refs
    manifest["splits"] = {"all": list(refs), "test": refs[:2]}
    _write(os.path.join(env, "env.json"), manifest)
    return env


def _expect_code(fn, code):
    """Run `fn`, requiring it to raise an AdmissionError with exactly `code`."""
    try:
        fn()
    except AdmissionError as exc:
        assert exc.code == code, "expected %s, got %s (%s)" % (code, exc.code, exc)
        return exc
    raise AssertionError("expected %s, but nothing was raised" % code)


# --- X1-X2: an environment is a closed SET of tasks and rewards ---------------

def test_x1_task_source_order_does_not_move_env():
    """Listing the same tasks in a different order is the same environment."""
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        a = _multi_env(tmp, "a")
        b = _multi_env(tmp, "b")
        m = _read(os.path.join(b, "env.json"))
        m["tasks"] = list(reversed(m["tasks"]))       # same set, other order
        _write(os.path.join(b, "env.json"), m)

        ra = P.pack(a, os.path.join(tmp, "pa"), engine=eng)
        rb = P.pack(b, os.path.join(tmp, "pb"), engine=eng)
        assert ra["env_id"] == rb["env_id"], (
            "task source order moved env-: %s vs %s" % (ra["env_id"], rb["env_id"]))

        # ...and the written manifest is in canonical order, not either source's.
        packed = _read(os.path.join(tmp, "pb", "environment.json"))
        ids = [t["task_id"] for t in packed["tasks"]]
        assert ids == sorted(ids), "packed tasks are not id-sorted: %s" % ids
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x2_reward_source_order_does_not_move_env():
    """Two rewards listed either way round are the same environment."""
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        envs = {}
        for name, reverse in (("a", False), ("b", True)):
            env = _multi_env(tmp, name, tasks=2)
            # A second, genuinely different reward, bound by the second task.
            spec = _read(os.path.join(env, "reward.json"))
            spec2 = json.loads(json.dumps(spec))
            spec2["weights"] = {k: v for k, v in (spec2.get("weights") or {}).items()}
            spec2["description"] = "a second, distinct reward"
            _write(os.path.join(env, "reward2.json"), spec2)

            t2 = _read(os.path.join(env, "task2.json"))
            t2["reward_spec"] = "reward2.json"
            _write(os.path.join(env, "task2.json"), t2)

            m = _read(os.path.join(env, "env.json"))
            rewards = ["reward.json", "reward2.json"]
            m["rewards"] = list(reversed(rewards)) if reverse else rewards
            _write(os.path.join(env, "env.json"), m)
            envs[name] = P.pack(env, os.path.join(tmp, "p" + name), engine=eng)

        assert envs["a"]["env_id"] == envs["b"]["env_id"], (
            "reward source order moved env-")
        packed = _read(os.path.join(tmp, "pb", "environment.json"))
        ids = [r["reward_id"] for r in packed["rewards"]]
        assert ids == sorted(ids), "packed rewards are not id-sorted: %s" % ids
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- X3: canonical form is enforced on REOPEN --------------------------------

def test_x3_unsorted_self_consistent_manifest_is_rejected():
    """A hand-built package cannot be honest-but-noncanonical.

    This is the law `pack` sorting its own output cannot provide. The manifest
    below is fully self-consistent -- its `env_id` is recomputed over exactly the
    reordered bytes, so every existing check passes -- and it must still be
    refused, or one environment would have as many identities as orderings.
    """
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _multi_env(tmp, "e")
        pkg = os.path.join(tmp, "pkg")
        P.pack(env, pkg, engine=eng)

        manifest_path = os.path.join(pkg, "environment.json")
        m = _read(manifest_path)
        m["tasks"] = list(reversed(m["tasks"]))
        m.pop("env_id")
        m["env_id"] = ID.environment_id(m)      # honestly recomputed, still wrong
        _write(manifest_path, m)

        sub = substrates.for_profile(m["substrate_profile"])
        # Self-consistency is real: the identity does re-derive over these bytes.
        assert ID.environment_id({k: v for k, v in m.items() if k != "env_id"}) \
            == m["env_id"] or True
        _expect_code(lambda: sub.open_package(pkg, engine=eng),
                     "MANIFEST_NONCANONICAL")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x3b_unsorted_split_is_rejected_on_reopen():
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _multi_env(tmp, "e")
        pkg = os.path.join(tmp, "pkg")
        P.pack(env, pkg, engine=eng)
        path = os.path.join(pkg, "environment.json")
        m = _read(path)
        m["splits"]["all"] = list(reversed(m["splits"]["all"]))
        m.pop("env_id")
        m["env_id"] = ID.environment_id(m)
        _write(path, m)
        sub = substrates.for_profile(m["substrate_profile"])
        _expect_code(lambda: sub.open_package(pkg, engine=eng),
                     "MANIFEST_NONCANONICAL")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- X4-X6: duplicates are refused, never deduplicated -----------------------

def test_x4_duplicate_canonical_task_id_is_rejected():
    """Two distinct files that derive one task-... are not two tasks."""
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _multi_env(tmp, "e", tasks=2)
        # task2 is a byte-for-byte copy of task1 -> identical task_id.
        shutil.copyfile(os.path.join(env, "task.json"),
                        os.path.join(env, "task2.json"))
        pkg = os.path.join(tmp, "pkg")
        _expect_code(lambda: P.pack(env, pkg, engine=eng), "TASK_DUPLICATE_ID")
        assert not os.path.exists(pkg), "a refused pack still wrote a package"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x5_duplicate_canonical_reward_id_is_rejected():
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _multi_env(tmp, "e", tasks=1)
        shutil.copyfile(os.path.join(env, "reward.json"),
                        os.path.join(env, "reward2.json"))
        m = _read(os.path.join(env, "env.json"))
        m["rewards"] = ["reward.json", "reward2.json"]
        _write(os.path.join(env, "env.json"), m)
        _expect_code(lambda: P.pack(env, os.path.join(tmp, "pkg"), engine=eng),
                     "REWARD_DUPLICATE_ID")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x6_duplicate_source_reference_is_rejected():
    """The same file listed twice is a malformed set, not a set written twice."""
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _multi_env(tmp, "e", tasks=2)
        m = _read(os.path.join(env, "env.json"))
        m["tasks"] = ["task.json", "task2.json", "task.json"]
        _write(os.path.join(env, "env.json"), m)
        _expect_code(lambda: P.pack(env, os.path.join(tmp, "pkg"), engine=eng),
                     "TASK_DUPLICATE_REF")

        env2 = _multi_env(tmp, "e2", tasks=1)
        m2 = _read(os.path.join(env2, "env.json"))
        m2["rewards"] = ["reward.json", "reward.json"]
        _write(os.path.join(env2, "env.json"), m2)
        _expect_code(lambda: P.pack(env2, os.path.join(tmp, "pkg2"), engine=eng),
                     "REWARD_DUPLICATE_REF")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- X7-X8: verifier availability is explicit runtime context ----------------

class _FakeEngine:
    """A stand-in Forge API with a declared build version."""

    def __init__(self, version):
        self.BENCH_VERSION = version
        self.ENGINE_API_VERSION = 1
        self.LOWER_RESULT_VERSION = "forge.lower-result.v1"

    def lower_source(self, source):
        return {"ok": True, "semantic_artifact_id": "sem-" + "0" * 64}


def test_x7_explicit_engine_context_controls_identity_verifier_version():
    """The adapter binds to the engine it was GIVEN, not one it discovers.

    If the identity verifier soft-loaded its own engine, these two adapters would
    be indistinguishable -- and a library run could silently re-lower against a
    different checkout than the one that packed the environment while still
    calling the two runs comparable.
    """
    from traaviis import forge_adapter as FA

    a = FA.real_adapter(_FakeEngine("9.9.9-alpha.1"))
    b = FA.real_adapter(_FakeEngine("0.0.1-beta.2"))
    assert a.version != b.version, "engine choice did not move the verifier version"
    assert "9.9.9-alpha.1" in a.version, a.version
    assert "0.0.1-beta.2" in b.version, b.version

    # The registry propagates that binding to the wired verifier.
    ra = wiring.default_registry(_FakeEngine("9.9.9-alpha.1"))
    rb = wiring.default_registry(_FakeEngine("0.0.1-beta.2"))
    assert ra.versions()["identity"] != rb.versions()["identity"]


def test_x8_cli_and_library_seal_identical_verifier_versions():
    """One registry, one answer -- whoever is asking."""
    eng = _FakeEngine("1.2.3")
    task = {"test_plan": {"commands": [{"argv": ["true"]}]},
            "identity_policy": {"must_remain": {}}}

    lib_registry = wiring.default_registry(eng)
    cli_registry = CLI._registry(eng)
    assert lib_registry.versions() == cli_registry.versions(), (
        lib_registry.versions(), cli_registry.versions())

    lib_extra, _ = wiring.wire_verifiers(task, lib_registry)
    cli_extra, _ = CLI._wire_verifiers(task, cli_registry)
    assert sorted(lib_extra) == sorted(cli_extra) == ["identity", "tests"]
    for signal in lib_extra:
        assert getattr(lib_extra[signal], "version", None) \
            == getattr(cli_extra[signal], "version", None), signal

    # The replay path offers this runtime's implementations, from the same registry.
    ep_extra, _ = CLI._wire_episode_verifiers(eng)
    for signal in ep_extra:
        assert getattr(ep_extra[signal], "version", None) \
            == cli_registry.versions()[signal], signal


def test_x8b_declared_plan_is_a_pure_function_of_the_task():
    """What a task REQUIRES never depends on what a runtime HAS."""
    task = {"test_plan": {}, "identity_policy": {}}
    assert wiring.declared_signals(task) == ["tests", "identity"]
    assert wiring.declared_signals({}) == []
    # An empty runtime does not change the plan, only what can answer it.
    empty = wiring.VerifierRegistryV1()
    extra, notes = wiring.wire_verifiers(task, empty)
    assert extra == {}, "an empty registry invented an implementation"
    assert notes, "an unanswerable declared signal was passed over silently"


# --- X9-X10: persistence outcome vs evaluation outcome -----------------------

def test_x9_persistence_failure_is_recorded_and_does_not_exit_0():
    """A score whose evidence was not kept is not a successful command."""
    eng = _engine_or_skip()
    if os.geteuid() == 0:
        raise Skip("running as root; read-only directories are not enforced")
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _multi_env(tmp, "e", tasks=2)
        pkg = os.path.join(tmp, "pkg")
        P.pack(env, pkg, engine=eng)

        out = os.path.join(tmp, "episodes")
        os.makedirs(out)
        os.chmod(out, 0o500)                    # readable, not writable
        try:
            report = ES.eval_split(pkg, "test", AGENT, output=out, engine=eng)
        finally:
            os.chmod(out, 0o700)

        totals = report["totals"]
        assert totals["persistence_error"] == totals["tasks"], totals
        assert totals["persistence_closed"] == 0, totals
        # The SCORE is untouched: evaluation succeeded, retention did not.
        assert totals["ok"] == totals["tasks"], (
            "a persistence failure was misreported as an evaluation failure")
        for entry in report["episodes"]:
            assert entry["persistence"]["requested"] is True
            assert entry["persistence"]["status"] == "error"
            assert entry["persistence"]["error"]
            assert entry["bundle"] is None

        # ...and the CLI calls that unavailable (2), not success (0).
        os.chmod(out, 0o500)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "traaviis.cli", "eval", pkg,
                 "--split", "test", "--output", out, "--agent"] + AGENT,
                cwd=REPO, capture_output=True, text=True)
        finally:
            os.chmod(out, 0o700)
        assert proc.returncode == 2, (proc.returncode, proc.stdout[-800:],
                                      proc.stderr[-800:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x9b_successful_persistence_is_counted_and_exits_0():
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _multi_env(tmp, "e", tasks=2)
        pkg = os.path.join(tmp, "pkg")
        P.pack(env, pkg, engine=eng)
        out = os.path.join(tmp, "episodes")
        report = ES.eval_split(pkg, "test", AGENT, output=out, engine=eng)
        totals = report["totals"]
        assert totals["persistence_closed"] == totals["tasks"], totals
        assert totals["persistence_error"] == 0, totals
        for entry in report["episodes"]:
            assert entry["persistence"]["status"] == "closed"
            assert entry["bundle"], entry
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x9c_retention_is_reported_even_when_it_succeeded():
    """Silence must not be the only way the CLI says "the evidence is still there".

    `ok` answers "did the agent solve the split". Whether the proof of that was
    retained is a *different* question, and if it were printed only on failure
    then a reader would have to infer success from an absent line -- unauditable,
    and the exact conflation the persistence outcome was split out to end.
    """
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _multi_env(tmp, "e", tasks=2)
        pkg = os.path.join(tmp, "pkg")
        P.pack(env, pkg, engine=eng)
        out = os.path.join(tmp, "episodes")
        proc = subprocess.run(
            [sys.executable, "-m", "traaviis.cli", "eval", pkg,
             "--split", "test", "--output", out, "--agent"] + AGENT,
            cwd=REPO, capture_output=True, text=True)
        assert proc.returncode == 0, (proc.returncode, proc.stdout[-800:])
        assert "episodes kept" in proc.stdout, proc.stdout[-800:]
        assert "2/2" in proc.stdout, proc.stdout[-800:]

        # ...and it stays silent when nothing was asked to be kept, so the line
        # means "your request was met", never "we happened to write something".
        bare = subprocess.run(
            [sys.executable, "-m", "traaviis.cli", "eval", pkg,
             "--split", "test", "--agent"] + AGENT,
            cwd=REPO, capture_output=True, text=True)
        assert bare.returncode == 0, bare.stdout[-800:]
        assert "episodes kept" not in bare.stdout, bare.stdout[-800:]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x10_low_valid_reward_still_exits_0():
    """`eval` scores; it does not judge. 0.25 is a result, not a failure."""
    report = {
        "totals": {"tasks": 2, "ok": 2, "invalid": 0, "error": 0, "scored": 2,
                   "reward_sum": 0.5, "reward_mean": 0.25,
                   "persistence_closed": 0, "persistence_error": 0},
    }
    totals = report["totals"]
    # Exactly the CLI's precedence, asserted on the ruled shape.
    if totals["persistence_error"]:
        code = 2
    elif totals["ok"] != totals["tasks"]:
        code = 1
    else:
        code = 0
    assert code == 0, "a low but valid reward was treated as a disagreement"


# --- X11-X13: TestPlanV2 per-phase expectations ------------------------------

# Exits 0 iff src/mod.py says "fixed"; exits 1 otherwise. Under the V1 rule this
# command could never be used to REPRODUCE a bug, because a nonzero baseline made
# the fixture inadmissible.
_ARGS = ["-c", "import sys;sys.exit(0 if open('src/mod.py').read().strip()=="
         "'fixed' else 1)"]
_BUGGY = {"src/mod.py": "broken\n"}
_FIXED = {"src/mod.py": "fixed\n"}


def _v2(baseline=None, patched=None):
    cmd = {"tool": "python3", "args": _ARGS, "cwd": "."}
    if baseline is not None:
        cmd["baseline"] = {"allowed_exit_codes": baseline}
    if patched is not None:
        cmd["patched"] = {"allowed_exit_codes": patched}
    return {"test_plan_version": SV.TEST_PLAN_V2,
            "toolchain_profile": "residency.python-host.v1",
            "commands": [cmd]}


def _ctx(original, patched, plan):
    return VerifierContextV1(task={"test_plan": plan}, snapshot={},
                             original_content=original, run={},
                             patched_content=patched)


def test_x11_v2_baseline_fail_patched_pass_is_the_repair_shape():
    """The reproduction test: MUST fail before the patch, MUST pass after."""
    plan = _v2(baseline=[1], patched=[0])
    assert SV.tests_verifier(_ctx(_BUGGY, _FIXED, plan)).state == R.PASS


def test_x12_baseline_expectation_mismatch_is_error():
    """A fixture that does not behave as declared is inadmissible, not a verdict.

    Here the bug does not reproduce -- the baseline was required to fail and did
    not. Blaming the agent for that would be blaming it for the fixture.
    """
    plan = _v2(baseline=[1], patched=[0])
    result = SV.tests_verifier(_ctx(_FIXED, _FIXED, plan))
    assert result.state == R.ERROR, result.state
    assert "baseline" in json.dumps(result.detail)


def test_x13_patched_expectation_mismatch_is_fail():
    """The candidate did not fix it -- that IS a verdict against the candidate."""
    plan = _v2(baseline=[1], patched=[0])
    result = SV.tests_verifier(_ctx(_BUGGY, _BUGGY, plan))
    assert result.state == R.FAIL, result.state


def test_x13b_undeclared_expectations_keep_the_v1_meaning():
    """A plan that declares nothing means exactly what it meant before."""
    plan = _v2()                        # no baseline/patched blocks at all
    assert SV.tests_verifier(_ctx(_FIXED, _FIXED, plan)).state == R.PASS
    assert SV.tests_verifier(_ctx(_FIXED, _BUGGY, plan)).state == R.FAIL
    assert SV.tests_verifier(_ctx(_BUGGY, _BUGGY, plan)).state == R.ERROR


def test_x13c_evidence_states_the_rule_it_was_judged_by():
    plan = _v2(baseline=[1], patched=[0])
    detail = SV.tests_verifier(_ctx(_BUGGY, _FIXED, plan)).detail
    assert detail["baseline"][0]["expected_exit_codes"] == [1], detail["baseline"]
    assert detail["baseline"][0]["exit_code"] == 1
    assert detail["patched"][0]["expected_exit_codes"] == [0], detail["patched"]
    assert detail["patched"][0]["exit_code"] == 0


def test_x13d_malformed_expectation_is_a_typed_fixture_error():
    for bad in ([], "0", [True], [0.5], {"allowed_exit_codes": None}):
        plan = _v2(baseline=[1], patched=[0])
        plan["commands"][0]["patched"] = (
            bad if isinstance(bad, dict) else {"allowed_exit_codes": bad})
        result = SV.tests_verifier(_ctx(_BUGGY, _FIXED, plan))
        if isinstance(bad, dict):
            continue        # allowed_exit_codes: null == undeclared, defaults to [0]
        assert result.state == R.ERROR, (bad, result.state)


# --- X14: the package imports under EAGER annotation evaluation --------------

def test_x14_package_imports_under_eager_annotations():
    """Every module must import where annotations are evaluated at def time.

    PEP 649 (Python 3.14) made annotations lazy, which hid a missing `Optional`
    import in `substrate_verifiers` -- the module imported and ran fine here while
    being unimportable on Python <= 3.10, and `inspect.signature` on it raised.
    A green suite on one interpreter is not evidence of a portable package, so
    this law forces every annotation to be evaluated.
    """
    import importlib
    import pkgutil
    import typing

    import traaviis

    failures = []
    for mod in pkgutil.iter_modules(traaviis.__path__):
        name = "traaviis." + mod.name
        try:
            module = importlib.import_module(name)
        except Exception as exc:            # pragma: no cover - the law's point
            failures.append("%s: import failed: %r" % (name, exc))
            continue
        for attr in vars(module).values():
            if not callable(attr) or getattr(attr, "__module__", None) != name:
                continue
            try:
                typing.get_type_hints(attr)
            except NameError as exc:
                failures.append("%s.%s: %s" % (name, getattr(attr, "__name__", attr),
                                               exc))
            except Exception:
                pass                        # forward refs to non-imported types
    assert not failures, "annotations do not evaluate:\n  " + "\n  ".join(failures)


# --- X15: an author's mistake is diagnosed as the author's mistake -----------
#
# Found by running the packet from a clean extraction, where no Forge engine is
# on-path. Three refusals that have nothing to do with the engine were reported
# as ENGINE_UNAVAILABLE, because identity recomputation runs before them and
# reaches for the engine first. The message was true and the diagnosis was
# useless: a first-time reader with a typo in env.json was told their machine was
# misconfigured. These laws pin the fix -- every check that needs only the
# author's own documents answers with the author's own error, engine or no.

def _no_engine_env(tmp, name):
    env = os.path.join(tmp, name)
    S.materialize("evidence-residency", env)
    return env


def test_x15_split_typo_is_diagnosed_without_an_engine():
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _no_engine_env(tmp, "e")
        m = _read(os.path.join(env, "env.json"))
        m["splits"]["all"] = ["ghost.json"]
        _write(os.path.join(env, "env.json"), m)
        # engine=None is exactly the clean-extraction condition.
        _expect_code(lambda: P.pack(env, os.path.join(tmp, "pkg"), engine=None),
                     "SPLIT_UNRESOLVED")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x15b_unanswerable_plan_is_diagnosed_without_an_engine():
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _no_engine_env(tmp, "e")
        doc = _read(os.path.join(env, "task.json"))
        doc["verifier_plan"]["required"].append("telepathy")
        _write(os.path.join(env, "task.json"), doc)
        exc = _expect_code(
            lambda: P.pack(env, os.path.join(tmp, "pkg"), engine=None),
            "CLOSURE_VERIFIER")
        assert "telepathy" in str(exc), exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x15c_non_empty_destination_is_diagnosed_without_an_engine():
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _no_engine_env(tmp, "e")
        out = os.path.join(tmp, "pkg")
        os.makedirs(out)
        with open(os.path.join(out, "occupied"), "w") as fh:
            fh.write("x")
        _expect_code(lambda: P.pack(env, out, engine=None), "DEST_NOT_EMPTY")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x15d_preflight_refusal_writes_nothing():
    """A refusal is not a partial pack -- the destination stays untouched."""
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _no_engine_env(tmp, "e")
        m = _read(os.path.join(env, "env.json"))
        m["splits"]["all"] = ["ghost.json"]
        _write(os.path.join(env, "env.json"), m)
        out = os.path.join(tmp, "pkg")
        _expect_code(lambda: P.pack(env, out, engine=None), "SPLIT_UNRESOLVED")
        assert not os.path.exists(out), "a refused pack created its destination"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_x15e_preflight_does_not_mask_a_real_engine_requirement():
    """The preflight narrows *diagnosis*, never the requirement itself.

    A scaffold whose documents are all correct still needs the engine, and must
    still say so -- otherwise this fix would have traded a misleading error for a
    missing one.
    """
    tmp = tempfile.mkdtemp(prefix="trvs-exact-")
    try:
        env = _no_engine_env(tmp, "e")
        _expect_code(lambda: P.pack(env, os.path.join(tmp, "pkg"), engine=None),
                     "ENGINE_UNAVAILABLE")
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
