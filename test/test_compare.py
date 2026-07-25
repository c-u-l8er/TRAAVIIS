"""Laws for `trvs compare` -- pairwise comparison of two closed episodes (§8).

`compare` answers *"which of these two candidates did better, and where did they
differ?"* over evidence that already exists. It runs no agent, mints no id, and
writes nothing unless asked. The laws below are mostly about the ways that
sentence can quietly stop being true:

- a comparison could be produced over a bundle that never reverified (C1, C22);
- it could relaunch the agent instead of reading the evidence (C2);
- it could rank two episodes scored by different rubrics (C3);
- it could impute `0` for an episode that did not score (C9);
- it could fold a different trace into an equal score (C10);
- it could invent a `compare-…` rung nobody can re-derive (C20);
- it could perturb the episodes it read (C21).

The expensive half -- packing an environment and running four candidates -- is
built once and shared, because every law here is a statement about the *same*
four sealed bundles.

Engine-dependent laws SKIP without a locatable Forge engine.

Run directly:      python3 test/test_compare.py
Run under pytest:  pytest test/test_compare.py
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import comparison as C  # noqa: E402
from traaviis import engine as _engine  # noqa: E402
from traaviis import episode_bundle  # noqa: E402
from traaviis import evalsplit as ES, pack as P, scaffold as S  # noqa: E402
from traaviis import wiring  # noqa: E402

TEMPLATE = "residency-repair"
AGENT = [sys.executable, os.path.join(REPO, "test", "fixtures", "repair_agent.py")]


class Skip(Exception):
    pass


def _engine_or_skip():
    eng = _engine.try_load()
    if eng is None:
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    return eng


# --------------------------------------------------------------- fixtures
#: Built once. Every law reads the same sealed bundles, so a law that mutated
#: one would be visible to the others -- which is itself the point of C21.
_FIXTURE = {}


def _fixture():
    """Four sealed episode bundles: three candidates on one task, one on another.

    `ok` / `nofix` / `gutspec` answer *one* task -- the mode rides in the agent's
    argv, so the task bytes are identical across all three. `null` is the
    fixture-error case from the repair battery's R6: the subject is seeded
    already fixed, so the baseline contradicts the plan, the episode is `error`
    and its reward is `None`. Its subject differs, so it necessarily answers a
    *different* task -- which makes it the honest input for both C3 and C9.
    """
    if _FIXTURE:
        return _FIXTURE
    eng = _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-compare-law-")
    _FIXTURE["tmp"] = tmp

    def pack(name, mutate=None):
        env = os.path.join(tmp, "env-" + name)
        S.materialize(TEMPLATE, env)
        if mutate is not None:
            mutate(env)
        out = os.path.join(tmp, "pkg-" + name)
        P.pack(env, out, engine=eng)
        return out

    def episode(package, mode, label):
        keep = os.path.join(tmp, "ep-" + label)
        report = ES.eval_split(package, "all", AGENT + [mode], output=keep)
        entry = report["episodes"][0]
        assert entry["persistence"]["status"] == "closed", entry
        _FIXTURE[label + "_entry"] = entry
        return os.path.join(keep, entry["bundle"])

    repair = pack("repair")
    for mode in ("ok", "nofix", "gutspec"):
        _FIXTURE[mode] = episode(repair, mode, mode)

    def already_fixed(env):
        with open(os.path.join(env, "subject", "src", "mod.py"), "w") as fh:
            fh.write("return 2\n")

    _FIXTURE["null"] = episode(pack("prefixed", already_fixed), "ok", "null")
    assert _FIXTURE["null_entry"]["reward"] is None, _FIXTURE["null_entry"]
    return _FIXTURE


def _registry():
    return wiring.default_registry(_engine_or_skip())


def _compare(left, right, **kw):
    kw.setdefault("registry", _registry())
    return C.compare_episodes(left, right, **kw)


def _copy(bundle, name):
    """A writable copy of a sealed bundle, for the tamper laws."""
    dest = os.path.join(_fixture()["tmp"], "copy-" + name)
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(bundle, dest)
    return dest


def _tamper(bundle, field="reward", value=0.99):
    path = os.path.join(bundle, "receipt.json")
    with open(path) as fh:
        receipt = json.load(fh)
    receipt[field] = value
    with open(path, "w") as fh:
        json.dump(receipt, fh, indent=2)
    return bundle


def _tree_digest(root):
    """A digest over every byte under `root`, paths included."""
    h = hashlib.sha256()
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(base, name)
            h.update(os.path.relpath(full, root).encode())
            with open(full, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def _cli(*argv):
    return subprocess.run(
        [sys.executable, "-m", "traaviis.cli", "compare", *argv],
        cwd=REPO, capture_output=True, text=True)


# --- C1: neither side is compared until it has reverified ------------------

def test_c1_each_side_is_independently_replayed_to_closed():
    """A comparison is a reading of two *closed* bundles. Tamper either one and
    the pair stops being comparable -- and the refusal names the side, because
    "one of these two is wrong" is not an actionable finding."""
    f = _fixture()

    for side, (left, right) in (
            ("left", (_tamper(_copy(f["ok"], "c1l")), f["nofix"])),
            ("right", (f["ok"], _tamper(_copy(f["nofix"], "c1r"))))):
        try:
            _compare(left, right)
        except C.ComparisonError as ex:
            assert ex.code == "EPISODE_EVIDENCE_MISMATCH", ex.code
            assert str(ex).count(side) >= 1, (side, str(ex))
        else:
            raise AssertionError("a tampered %s bundle was compared" % side)


# --- C2: comparison runs no agent ------------------------------------------

def test_c2_no_agent_is_launched():
    """The single seam that crosses into an agent process is `runner.run_agent`.
    Make it explode: a comparison that still succeeds provably never called it.

    Replay *does* spawn processes -- the declared test commands -- but those are
    verification, not the candidate, which is exactly the distinction being
    protected.
    """
    from traaviis import runner
    f = _fixture()

    def refuse(*a, **kw):
        raise AssertionError("compare launched an agent")

    original = runner.run_agent
    runner.run_agent = refuse
    try:
        report = _compare(f["ok"], f["nofix"])
    finally:
        runner.run_agent = original
    assert report["relation"]["reward"] == C.REWARD_LEFT_HIGHER, report["relation"]


# --- C3: one task, or no comparison ----------------------------------------

def test_c3_two_different_tasks_are_refused():
    """Requiring one `task_id` is what makes the two numbers mean the same
    thing. Both of these bundles are perfectly closed; they are still not
    comparable, and the refusal quotes both ids so the reader can see why."""
    f = _fixture()
    try:
        _compare(f["ok"], f["null"])
    except C.ComparisonError as ex:
        assert ex.code == "TASK_MISMATCH", ex.code
        assert ex.detail["left_task_id"] != ex.detail["right_task_id"], ex.detail
        return
    raise AssertionError("expected TASK_MISMATCH")


# --- C4/C5: identity of the two sides --------------------------------------

def test_c4_a_bundle_compared_with_itself_is_the_same_episode():
    f = _fixture()
    report = _compare(f["ok"], f["ok"])
    rel = report["relation"]
    assert rel["same_episode"] is True, rel
    assert rel["same_trace"] is True, rel
    assert rel["reward"] == C.REWARD_EQUAL, rel
    assert all(v is None for v in report["differences"].values()), \
        report["differences"]


def test_c5_three_candidates_still_answer_one_task():
    f = _fixture()
    report = _compare(f["ok"], f["gutspec"])
    assert report["task_id"] == f["ok_entry"]["task_id"]
    assert report["task_id"] == f["gutspec_entry"]["task_id"]
    assert report["relation"]["same_episode"] is False
    assert report["reward_id"], "the shared rubric was not quoted"


# --- C6/C7/C8: the reward relation -----------------------------------------

def test_c6_the_repair_outranks_the_admissible_nonfix():
    f = _fixture()
    rel = _compare(f["ok"], f["nofix"])["relation"]
    assert rel["reward"] == C.REWARD_LEFT_HIGHER, rel
    assert rel["right_minus_left"] < 0, rel


def test_c7_the_repair_outranks_the_gamed_candidate():
    f = _fixture()
    rel = _compare(f["ok"], f["gutspec"])["relation"]
    assert rel["reward"] == C.REWARD_LEFT_HIGHER, rel
    assert rel["right_minus_left"] < 0, rel


def test_c8_the_delta_is_exactly_right_minus_left():
    """Stated as arithmetic on the two quoted rewards, so a consumer never has
    to guess which direction the sign points."""
    f = _fixture()
    report = _compare(f["nofix"], f["ok"])
    rel = report["relation"]
    assert rel["reward"] == C.REWARD_RIGHT_HIGHER, rel
    expected = report["right"]["reward"] - report["left"]["reward"]
    assert abs(rel["right_minus_left"] - expected) < 1e-12, rel


# --- C9: a null reward is incomparable, never zero -------------------------

def test_c9_a_null_reward_is_incomparable_never_zero():
    """The end-to-end case: an errored episode compared with *itself*.

    Both sides are byte-identical, so a naive implementation reports `equal` --
    and that would be a lie, because neither side scored. Two absences are not
    a tie.
    """
    f = _fixture()
    report = _compare(f["null"], f["null"])
    rel = report["relation"]
    assert report["left"]["reward"] is None, report["left"]
    assert rel["reward"] == C.REWARD_INCOMPARABLE, rel
    assert rel["reward"] != C.REWARD_EQUAL
    # The delta is withheld, not zeroed: a consumer cannot arithmetic its way
    # past a missing score.
    assert rel["right_minus_left"] is None, rel
    assert rel["same_episode"] is True, rel


def test_c9b_one_missing_score_is_enough_and_a_bool_is_not_a_score():
    """The relation itself, over the two cases a bundle cannot easily stage."""
    assert C._reward_relation({"reward": 1.0}, {"reward": None}) == \
        (C.REWARD_INCOMPARABLE, None)
    assert C._reward_relation({"reward": None}, {"reward": 0.0}) == \
        (C.REWARD_INCOMPARABLE, None)
    assert C._reward_relation({}, {}) == (C.REWARD_INCOMPARABLE, None)
    # `True` is an `int` in Python. A boolean is a verdict, not a score, and
    # admitting one would silently rank it as 1.0.
    assert C._reward_relation({"reward": True}, {"reward": 1.0}) == \
        (C.REWARD_INCOMPARABLE, None)


# --- C10: equal rewards do not hide a different trace ----------------------

def test_c10_equal_rewards_do_not_hide_a_different_trace():
    """`nofix` and `gutspec` both hit the tests cap, so under the rubric they
    *are* equal. They got there by different routes, and that is reported as a
    trace relation rather than folded into the ranking as a tie-breaker."""
    f = _fixture()
    report = _compare(f["nofix"], f["gutspec"])
    rel = report["relation"]
    assert rel["reward"] == C.REWARD_EQUAL, rel
    assert rel["right_minus_left"] == 0, rel
    assert rel["same_trace"] is False, rel
    assert rel["same_episode"] is False, rel
    changed = [k for k, v in report["differences"].items() if v is not None]
    assert changed, "two different traces reported no difference at all"


# --- C11/C12: divergences are reported per field, never collapsed ----------

def test_c11_signal_state_changes_are_reported_per_signal():
    f = _fixture()
    diff = _compare(f["ok"], f["gutspec"])["differences"]
    verification = diff["verification"]
    assert verification, "a pass/fail flip produced no verification difference"
    assert "tests" in verification, verification
    assert verification["tests"] == {"left": "pass", "right": "fail"}, verification
    # The evidence that backs the signal is a separate field: a reader can see
    # that the verdict moved *and* that the evidence under it moved.
    assert diff["verification_evidence"], diff


def test_c12_finding_and_patch_ids_are_reported_independently():
    """`ok` and `gutspec` cite the same finding and ship different patches, so
    only `patch_id` may appear. Collapsing the two into one "outputs differ"
    bit would lose which artifact actually moved."""
    f = _fixture()
    outputs = _compare(f["ok"], f["gutspec"])["differences"]["outputs"]
    assert "patch_id" in outputs, outputs
    assert "finding_id" not in outputs, outputs
    assert outputs["patch_id"]["left"] != outputs["patch_id"]["right"]

    both = C._difference({"finding_id": "a", "patch_id": "p"},
                         {"finding_id": "b", "patch_id": "q"})
    assert set(both) == {"finding_id", "patch_id"}, both


# --- C13/C14: runtime divergence is visible --------------------------------

def test_c13_verifier_version_differences_are_visible():
    """Two episodes verified by different builds are not the same measurement.

    On one machine the two sides share a runtime, so the honest report is
    `None`; the law is that a drift *would* surface, naming only the signal
    that drifted.
    """
    f = _fixture()
    assert _compare(f["ok"], f["nofix"])["differences"]["verifier_versions"] is None

    left = {"tests": {"contract": "c1", "implementation": "impl-a"},
            "identity": {"contract": "c2", "implementation": "impl-x"}}
    right = json.loads(json.dumps(left))
    right["tests"]["implementation"] = "impl-b"
    drift = C._difference(left, right)
    assert set(drift) == {"tests"}, drift
    assert drift["tests"]["left"]["implementation"] == "impl-a"


def test_c14_execution_context_differences_are_visible():
    f = _fixture()
    report = _compare(f["ok"], f["nofix"])
    assert report["differences"]["execution_facts"] is None, \
        "two runs on one host reported a fabricated context difference"

    left = report["left"]["execution_facts"]
    assert left, "the report quoted no execution facts"
    right = json.loads(json.dumps(left))
    right["platform"] = {"arch": "aarch64", "os": "darwin"}
    moved = C._difference(left, right)
    assert set(moved) == {"platform"}, moved
    assert moved["platform"]["right"]["os"] == "darwin"


# --- C15: the report names the runtime that did the replay -----------------

def test_c15_runtime_context_attests_the_replay_registry():
    """A comparison is only as trustworthy as the implementations that
    reverified the two bundles, so the report names them."""
    f = _fixture()
    registry = _registry()
    ctx = _compare(f["ok"], f["nofix"], registry=registry)["runtime_context"]
    assert ctx["wiring"] == "registry", ctx
    assert ctx["registry_version"] == wiring.VERIFIER_REGISTRY_VERSION, ctx
    assert ctx["verifiers_available"] == registry.available(), ctx
    assert ctx["verifier_versions"] == registry.versions(), ctx


def test_c15b_caller_supplied_verifiers_still_say_so():
    """The same seam distinction `trvs eval` makes: a caller that brings its own
    implementations has no registry to attest, and the report must not claim
    one."""
    f = _fixture()
    registry = _registry()
    extra = {s: registry.get(s) for s in registry.available()}
    ctx = C.compare_episodes(
        f["ok"], f["nofix"], extra_verifiers=extra)["runtime_context"]
    assert ctx["wiring"] == "caller_supplied", ctx
    assert ctx["registry_version"] is None, ctx
    assert ctx["verifiers_available"] == [], ctx


def test_c15c_registry_plus_caller_verifiers_is_refused():
    """Two seams, one replay. Supplying both used to replay with the caller's
    implementations while attesting the registry -- a report describing a
    runtime that did not do the work. There is no defensible precedence rule
    between them, so the pair is refused rather than silently ranked."""
    f = _fixture()
    registry = _registry()
    extra = {s: registry.get(s) for s in registry.available()}
    try:
        C.compare_episodes(f["ok"], f["nofix"],
                           registry=registry, extra_verifiers=extra)
    except C.ComparisonError as ex:
        assert ex.code == "VERIFIER_WIRING_AMBIGUOUS", ex.code
        return
    raise AssertionError("expected VERIFIER_WIRING_AMBIGUOUS")


def test_c15d_a_runtime_context_cannot_be_supplied_at_all():
    """Attestation is derived, never accepted.

    `compare_episodes` used to take `runtime_context=` and copy it into the
    report verbatim, so a caller could name a registry that never ran. The
    parameter is gone: the only way to obtain a context is to perform the
    replay that it describes.
    """
    import inspect
    f = _fixture()

    params = inspect.signature(C.compare_episodes).parameters
    assert "runtime_context" not in params, sorted(params)
    assert set(params) == {"left_dir", "right_dir", "registry",
                           "extra_verifiers"}, sorted(params)

    try:
        C.compare_episodes(f["ok"], f["nofix"],
                           runtime_context={"wiring": "registry",
                                            "registry_version": "fabricated"})
    except TypeError:
        pass
    else:
        raise AssertionError("a fabricated runtime context was accepted")


def test_c15e_registry_replay_attests_that_exact_registry():
    """The attestation names the object that supplied the implementations --
    not `default_registry()`, and not whatever the runtime could have built.

    Two halves, because there are two ways to be wrong. The *attestation* half
    hands in a registry carrying a version string no default could produce, and
    requires the report to quote it. The *implementation* half hands in a
    registry that cannot answer `identity` and requires the replay to refuse:
    the same pair closes under the default registry, so a comparison that still
    appeared would have been judged by implementations nobody supplied.
    """
    from traaviis import substrate_verifiers as SV
    f = _fixture()
    default = _registry()

    class MarkedRegistry(wiring.VerifierRegistryV1):
        registry_version = "traaviis.verifier-registry.v1+compare-law-c15e"

    marked = MarkedRegistry(tests=default.get("tests"),
                            identity=default.get("identity"))
    ctx = C.compare_episodes(
        f["ok"], f["nofix"], registry=marked)["runtime_context"]

    assert ctx["wiring"] == "registry", ctx
    assert ctx["registry_version"] == MarkedRegistry.registry_version, ctx
    assert ctx["registry_version"] != default.registry_version, ctx
    assert ctx["verifiers_available"] == marked.available(), ctx
    assert ctx["verifier_versions"] == marked.versions(), ctx

    # The implementations follow the same seam. `identity` is declared by this
    # task, so a registry that lacks it cannot judge the bundle -- and says so,
    # rather than judging it with something the caller never handed over.
    assert "identity" in default.available(), \
        "the default registry cannot answer identity, so this proves nothing"
    partial = wiring.VerifierRegistryV1(
        tests=SV.tests_verifier, identity=None,
        notes=["identity verifier unavailable: compare law C15e"])
    try:
        C.compare_episodes(f["ok"], f["nofix"], registry=partial)
    except C.ComparisonError as ex:
        assert ex.code == "EPISODE_UNAVAILABLE", ex.code
    else:
        raise AssertionError(
            "a registry with no identity verifier still produced a comparison")


def test_c15f_caller_supplied_replay_always_attests_caller_supplied():
    """Whatever the caller injects, and however much it resembles a registry's
    output, the report declines to claim a registry -- because there is none."""
    f = _fixture()
    registry = _registry()
    extra = {s: registry.get(s) for s in registry.available()}

    for injected in (extra, dict(extra)):
        ctx = C.compare_episodes(
            f["ok"], f["nofix"], extra_verifiers=injected)["runtime_context"]
        assert ctx == {"registry_version": None, "wiring": "caller_supplied",
                       "verifiers_available": [], "verifier_versions": {}}, ctx


def test_c15g_the_cli_path_is_unchanged_by_the_closure():
    """The CLI only ever supplied `registry=`, so it never triggered either
    defect -- and the fix must not move it. Its report is compared byte-for-byte
    against the library called the same way."""
    f = _fixture()
    p = _cli(f["ok"], f["nofix"], "--json")
    assert p.returncode == 0, (p.returncode, p.stderr[-800:])

    direct = C.compare_episodes(f["ok"], f["nofix"], registry=_registry())
    assert p.stdout == json.dumps(direct, indent=2, sort_keys=True) + "\n", \
        p.stdout[:400]

    human = _cli(f["ok"], f["nofix"])
    assert human.returncode == 0, human.stderr[-400:]
    for expected in ("task", "relation", "same episode", "differs in"):
        assert expected in human.stdout, (expected, human.stdout)


# --- C16: the report is portable -------------------------------------------

_ABS_PATH = re.compile(r'"(?:/|[A-Za-z]:\\\\)[^"]{2,}"')
_WALL_CLOCK = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d")


def test_c16_the_report_carries_no_host_paths_or_wall_clock():
    """A comparison quotes sealed evidence, so it must mean the same thing on a
    machine that never had these directories. A local path or a timestamp would
    make two identical readings of identical evidence differ."""
    f = _fixture()
    blob = json.dumps(_compare(f["ok"], f["gutspec"]), sort_keys=True)
    assert not _ABS_PATH.search(blob), _ABS_PATH.search(blob).group(0)
    assert not _WALL_CLOCK.search(blob), _WALL_CLOCK.search(blob).group(0)
    assert f["tmp"] not in blob


# --- C17/C18: the reading is directed, and it is a function ----------------

def test_c17_reversing_the_inputs_reverses_the_relation():
    f = _fixture()
    forward = _compare(f["ok"], f["nofix"])
    reverse = _compare(f["nofix"], f["ok"])

    assert forward["relation"]["reward"] == C.REWARD_LEFT_HIGHER
    assert reverse["relation"]["reward"] == C.REWARD_RIGHT_HIGHER
    assert abs(forward["relation"]["right_minus_left"] +
               reverse["relation"]["right_minus_left"]) < 1e-12
    assert forward["left"] == reverse["right"]
    assert forward["right"] == reverse["left"]
    # `same_episode` / `same_trace` are symmetric relations and must not move.
    for key in ("same_episode", "same_trace"):
        assert forward["relation"][key] == reverse["relation"][key], key


def test_c18_two_readings_of_one_pair_are_byte_identical():
    f = _fixture()
    first = json.dumps(_compare(f["ok"], f["nofix"]), sort_keys=True, indent=2)
    second = json.dumps(_compare(f["ok"], f["nofix"]), sort_keys=True, indent=2)
    assert first == second


# --- C19: writing is all-or-nothing ----------------------------------------

def test_c19_the_written_report_is_atomic_and_complete():
    f = _fixture()
    report = _compare(f["ok"], f["nofix"])
    out_dir = tempfile.mkdtemp(prefix="trvs-compare-out-")
    try:
        path = C.write_comparison(report, os.path.join(out_dir, "comparison.json"))
        with open(path) as fh:
            assert json.load(fh) == report
        assert os.listdir(out_dir) == ["comparison.json"], os.listdir(out_dir)

        # A write that dies mid-stream leaves no stub to be mistaken for a
        # comparison, and no temp file behind either.
        target = os.path.join(out_dir, "doomed.json")
        original = C.json.dump

        def boom(*a, **kw):
            raise OSError("disk full")

        C.json.dump = boom
        try:
            C.write_comparison(report, target)
        except OSError:
            pass
        else:
            raise AssertionError("a failed write reported success")
        finally:
            C.json.dump = original
        assert not os.path.exists(target)
        assert sorted(os.listdir(out_dir)) == ["comparison.json"], \
            os.listdir(out_dir)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# --- C20/C21: a comparison is a reading, not an artifact -------------------

#: Every id prefix a `ComparisonV1` is allowed to quote. The identity ladder
#: (RFC Artifacts §1) plus the two Residency output ids. A comparison may quote
#: any of these; it may mint none of them, and it may not introduce a rung of
#: its own.
_LADDER = ("snap-", "task-", "rew-", "trace-", "episode-", "env-",
           "finding-", "patch-", "sem-", "bundle-")


def test_c20_the_comparison_mints_no_identity_of_its_own():
    """There is no `compare-…` rung. Everything in the report is already
    addressed by the ids it quotes, so a comparison is not an artifact anyone
    needs to re-derive."""
    f = _fixture()
    report = _compare(f["ok"], f["nofix"])
    assert "comparison_id" not in report, report.keys()

    blob = json.dumps(report, sort_keys=True)
    assert "compare-" not in blob
    assert "eval-" not in blob

    minted = set(re.findall(r'"([a-z_]+-)[0-9a-f]{16,}"', blob))
    assert minted <= set(_LADDER), minted


def test_c21_comparing_does_not_disturb_the_episodes():
    f = _fixture()
    before = {side: _tree_digest(f[side]) for side in ("ok", "nofix", "gutspec")}
    _compare(f["ok"], f["nofix"])
    _compare(f["gutspec"], f["ok"])
    after = {side: _tree_digest(f[side]) for side in ("ok", "nofix", "gutspec")}
    assert before == after, "a comparison rewrote the evidence it read"
    for side in ("ok", "nofix", "gutspec"):
        assert not os.path.exists(f[side] + ".comparison"), side


# --- C22/C23: the exit contract --------------------------------------------

def test_c22_tampered_evidence_exits_1_and_writes_nothing():
    f = _fixture()
    bad = _tamper(_copy(f["nofix"], "c22"))
    out_dir = tempfile.mkdtemp(prefix="trvs-compare-out-")
    try:
        target = os.path.join(out_dir, "comparison.json")
        p = _cli(f["ok"], bad, "--json", "--output", target)
        assert p.returncode == 1, (p.returncode, p.stderr[-800:])
        assert "EPISODE_EVIDENCE_MISMATCH" in p.stderr, p.stderr[-800:]
        assert p.stdout.strip() == "", p.stdout[:400]
        assert os.listdir(out_dir) == [], os.listdir(out_dir)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_c23_an_unjudgeable_pair_exits_2():
    """Three different ways to be unjudgeable, one exit code: this runtime could
    not answer, which is not a verdict against either candidate."""
    f = _fixture()

    missing = _cli(os.path.join(f["tmp"], "no-such-episode"), f["ok"])
    assert missing.returncode == 2, (missing.returncode, missing.stderr[-400:])
    assert "EPISODE_UNAVAILABLE" in missing.stderr

    mismatched = _cli(f["ok"], f["null"])
    assert mismatched.returncode == 2, (mismatched.returncode,
                                        mismatched.stderr[-400:])
    assert "TASK_MISMATCH" in mismatched.stderr

    # A runtime with no substrate verifiers cannot reverify a bundle that
    # scored substrate signals, so it must decline rather than pass it.
    try:
        C.compare_episodes(f["ok"], f["nofix"], extra_verifiers={})
    except C.ComparisonError as ex:
        assert ex.code == "EPISODE_UNAVAILABLE", ex.code
    else:
        raise AssertionError("a bundle was judged with no verifiers wired")


def test_c23b_a_produced_comparison_exits_0_even_when_the_sides_disagree():
    """`compare` reports a relation; it does not grade either candidate. A
    correctly reported "left won" is a successful comparison."""
    f = _fixture()
    p = _cli(f["ok"], f["gutspec"], "--json")
    assert p.returncode == 0, (p.returncode, p.stderr[-800:])
    report = json.loads(p.stdout)
    assert report["relation"]["reward"] == C.REWARD_LEFT_HIGHER, report["relation"]
    assert report["comparison_version"] == C.COMPARISON_VERSION


# --- C24: comparison did not weaken replay ---------------------------------

def test_c24_every_bundle_still_reopens_closed_afterwards():
    """The replay guarantee is the premise `compare` rests on, so it is checked
    again *after* every law above has read these bundles."""
    f = _fixture()
    registry = _registry()
    extra = {s: registry.get(s) for s in registry.available()}
    for label in ("ok", "nofix", "gutspec", "null"):
        report = episode_bundle.verify_episode_bundle(f[label], extra_verifiers=extra)
        assert report["outcome"] == episode_bundle.OUTCOME_CLOSED, (label, report)
        assert report["episode_id"] == f[label + "_entry"]["episode_id"], label


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
    if _FIXTURE.get("tmp"):
        shutil.rmtree(_FIXTURE["tmp"], ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
