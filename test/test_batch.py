"""Laws for `trvs batch` -- the serial candidate-by-task matrix (§8).

A batch is `eval_split` in one direction and `ComparisonV1` in the other, and
nothing underneath. Almost every law here is a statement about a way that
composition could quietly stop being true:

- a candidate set could be validated lazily, so a typo costs N agent runs (B1);
- two candidates could collide onto one output directory (B2);
- a command could be a shell string somebody has to split (B3);
- the host environment could leak into a candidate (B4);
- a candidate could answer different task bytes than its rivals (B5);
- the run order could depend on file order (B6);
- a second registry could judge what the first one ran (B7-B9);
- a pair with a missing side could be given a fabricated relation (B14);
- one candidate's failure could abandon the rest of the matrix (B15);
- a `candidate_key` could leak into something content-addressed (B18-B19);
- a half-written matrix could be published (B22-B23).

The expensive half -- packing a two-task environment and running three
candidates over it -- is built once and shared, because most laws are readings
of the *same* published batch.

Engine-dependent laws SKIP without a locatable Forge engine.

Run directly:      python3 test/test_batch.py
Run under pytest:  pytest test/test_batch.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import batch as B  # noqa: E402
from traaviis import engine as _engine  # noqa: E402
from traaviis import episode_bundle  # noqa: E402
from traaviis import evalsplit as ES, pack as P, scaffold as S  # noqa: E402
from traaviis import runner, wiring  # noqa: E402

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
_FIXTURE = {}

#: Three candidates over one split. The keys are deliberately *not* in the
#: order they are written here: `candidate_order` is sorted, and B6 is only a
#: law if the declared order could have been mistaken for the run order.
_CANDIDATES = [("repair", "ok"), ("gutspec", "gutspec"), ("nofix", "nofix")]


def _two_task_package(tmp, name="pkg"):
    """A package with *two* tasks over one shared subject.

    The template ships one task, and a one-task split cannot distinguish "the
    matrix is per task" from "the matrix happens to have one row". The second
    task differs only in its objective text, so it is a different `task-` over
    the same subject and the same reward -- which is exactly the shape a real
    split has.
    """
    eng = _engine_or_skip()
    env = os.path.join(tmp, "env-" + name)
    S.materialize(TEMPLATE, env)

    with open(os.path.join(env, "task.json")) as fh:
        task = json.load(fh)
    task["instructions"]["objective"] += " (variant b of the same repair)"
    with open(os.path.join(env, "task-b.json"), "w") as fh:
        json.dump(task, fh, indent=2)

    manifest_path = os.path.join(env, "env.json")
    with open(manifest_path) as fh:
        manifest = json.load(fh)
    manifest["tasks"].append("task-b.json")
    manifest["splits"]["all"].append("task-b.json")
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)

    package = os.path.join(tmp, name)
    P.pack(env, package, engine=eng)
    return package


def _candidate_set(pairs=_CANDIDATES):
    return {"candidate_set_version": B.CANDIDATE_SET_VERSION,
            "candidates": [{"candidate_key": key, "argv": AGENT + [mode]}
                           for key, mode in pairs]}


def _fixture():
    """One published batch: three candidates over a two-task split."""
    if _FIXTURE:
        return _FIXTURE
    _engine_or_skip()
    tmp = tempfile.mkdtemp(prefix="trvs-batch-law-")
    _FIXTURE["tmp"] = tmp
    _FIXTURE["package"] = _two_task_package(tmp)
    _FIXTURE["output"] = os.path.join(tmp, "batch-out")
    _FIXTURE["report"] = B.run_batch(
        _FIXTURE["package"], "all", _candidate_set(), _FIXTURE["output"],
        registry=_registry())
    return _FIXTURE


def _registry():
    return wiring.default_registry(_engine_or_skip())


def _member(name):
    """One member document of the published batch, by relative path."""
    with open(os.path.join(_fixture()["output"], name.replace("/", os.sep))) as fh:
        return json.load(fh)


def _row(report, task_id):
    return next(r for r in report["tasks"] if r["task_id"] == task_id)


def _run(tmp_name, candidates, *, package=None, split="all", **kw):
    """A second, independent batch -- for the laws that need their own run."""
    f = _fixture()
    output = os.path.join(f["tmp"], tmp_name)
    shutil.rmtree(output, ignore_errors=True)
    kw.setdefault("registry", _registry())
    return B.run_batch(package or f["package"], split, candidates, output, **kw), output


def _cli(*argv):
    return subprocess.run(
        [sys.executable, "-m", "traaviis.cli", "batch", *argv],
        cwd=REPO, capture_output=True, text=True)


def _refuses(code, candidates, output_name="refused"):
    """Assert `run_batch` refuses this candidate set with `code`."""
    f = _fixture()
    output = os.path.join(f["tmp"], output_name)
    shutil.rmtree(output, ignore_errors=True)
    try:
        B.run_batch(f["package"], "all", candidates, output,
                    registry=_registry())
    except B.BatchError as ex:
        assert ex.code == code, (ex.code, str(ex))
        assert not os.path.exists(output), \
            "a refused batch left an output directory behind"
        return ex
    raise AssertionError("expected %s" % code)


# --- B1: the whole plan is admitted before anything runs -------------------

def test_b1_the_candidate_set_is_validated_before_any_launch():
    """A batch is N candidates x M tasks. Discovering a bad fifth candidate
    after four candidates have run costs four agent runs to learn something
    that was readable from the plan -- so the plan is read whole, first.

    Proven by refusing a set whose *last* candidate is malformed while its
    first is perfectly runnable, and then asserting that nothing ran: no
    output directory, and no episode anywhere on disk.
    """
    f = _fixture()
    doc = _candidate_set()
    doc["candidates"].append({"candidate_key": "late", "argv": []})

    launched = []
    real = runner.run_agent

    def spy(*a, **kw):  # a batch that validates late would trip this
        launched.append(a)
        return real(*a, **kw)

    runner.run_agent = spy
    try:
        _refuses("CANDIDATE_ARGV_INVALID", doc, "b1-out")
    finally:
        runner.run_agent = real
    assert launched == [], "an agent ran before the candidate set was admitted"
    assert not os.path.exists(os.path.join(f["tmp"], "b1-out"))


def test_b1b_a_bad_package_or_split_costs_no_agent_run():
    """The same rule one level up: the package and the split are admitted
    before the candidate set is even reached, so an unknown split does not run
    the first candidate to find out."""
    f = _fixture()
    output = os.path.join(f["tmp"], "b1b-out")
    launched = []
    real = runner.run_agent
    runner.run_agent = lambda *a, **kw: launched.append(a)
    try:
        try:
            B.run_batch(f["package"], "no-such-split", _candidate_set(), output,
                        registry=_registry())
        except ES.SplitError as ex:
            assert ex.code == "SPLIT_UNKNOWN", ex.code
        else:
            raise AssertionError("an unknown split was accepted")
    finally:
        runner.run_agent = real
    assert launched == []
    assert not os.path.exists(output)


# --- B2: keys label the columns, so they must be distinct and usable -------

def test_b2_duplicate_and_malformed_candidate_keys_are_refused():
    """A key is a report label, a directory name and half of a comparison
    filename. Two candidates sharing one would silently overwrite each other's
    episodes; a key with a path separator would write outside its column; a key
    containing the pair separator would make `a--b--c.json` ambiguous."""
    _refuses("CANDIDATE_KEY_DUPLICATE",
             _candidate_set([("same", "ok"), ("same", "nofix")]), "b2-dup")

    for bad in ("../escape", "with/slash", "", ".hidden", "a--b", "x" * 100,
                None, 7, ["list"]):
        doc = _candidate_set([("placeholder", "ok")])
        doc["candidates"][0]["candidate_key"] = bad
        _refuses("CANDIDATE_KEY_INVALID", doc, "b2-key")


def test_b2b_the_candidate_set_itself_must_be_well_formed():
    """Every structural failure has its own code. A batch that fell back to a
    default for any of these would run something the caller did not write."""
    cases = [
        ("CANDIDATE_SET_MALFORMED", []),
        ("CANDIDATE_SET_MALFORMED", "candidates.json"),
        ("CANDIDATE_SET_VERSION", {"candidates": []}),
        ("CANDIDATE_SET_VERSION",
         {"candidate_set_version": "traaviis.candidate-set.v2", "candidates": []}),
        ("CANDIDATE_SET_EMPTY",
         {"candidate_set_version": B.CANDIDATE_SET_VERSION, "candidates": []}),
        ("CANDIDATE_SET_MALFORMED",
         {"candidate_set_version": B.CANDIDATE_SET_VERSION, "candidates": {}}),
    ]
    for code, doc in cases:
        try:
            B.validate_candidate_set(doc)
        except B.BatchError as ex:
            assert ex.code == code, (code, ex.code, doc)
        else:
            raise AssertionError("accepted %r" % (doc,))

    extra = _candidate_set()
    extra["evaluation_note"] = "hello"
    try:
        B.validate_candidate_set(extra)
    except B.BatchError as ex:
        assert ex.code == "CANDIDATE_SET_MALFORMED", ex.code
    else:
        raise AssertionError("an unknown top-level field was accepted")


# --- B3: commands are argv, because the batch has no shell -----------------

def test_b3_candidate_commands_are_argv_arrays_not_shell_strings():
    """A shell string would have to be split by *some* shell's quoting rules,
    and two hosts do not have to agree on those. The refusal is separate from
    "not a list" so the message can say why."""
    doc = _candidate_set()
    doc["candidates"][0]["argv"] = "%s repair_agent.py ok" % sys.executable
    ex = _refuses("CANDIDATE_ARGV_INVALID", doc, "b3-out")
    assert "shell" in str(ex).lower(), str(ex)

    for bad in ([], None, {}, ["ok", 3], [None]):
        doc = _candidate_set()
        doc["candidates"][0]["argv"] = bad
        _refuses("CANDIDATE_ARGV_INVALID", doc, "b3-out")


# --- B4: the host environment does not reach a candidate -------------------

def test_b4_the_host_environment_is_not_inherited_by_candidates():
    """§10a: the child environment is built *from the run policy*, never
    inherited. A batch adds a second reason to care -- an inherited variable
    would be a difference between candidates that the sealed environment cannot
    account for, and the whole matrix assumes there are none.

    Two halves: the candidate set has nowhere to put one, and the seam that
    builds the child environment ignores the host's.
    """
    doc = _candidate_set()
    doc["candidates"][0]["env"] = {"TRVS_BATCH_CANARY": "leaked"}
    ex = _refuses("CANDIDATE_SET_MALFORMED", doc, "b4-out")
    assert "env" in str(ex), str(ex)

    canary = "TRVS_BATCH_LEAK_CANARY"
    os.environ[canary] = "leaked"
    try:
        sealed = runner._seal_env({"toolchain_profile": "residency.python-host.v1",
                                   "environment": {"DECLARED": "yes"}})
    finally:
        del os.environ[canary]
    assert canary not in sealed, sealed
    assert sealed.get("DECLARED") == "yes", sealed
    assert "HOME" not in sealed and "PATH" not in sealed, sealed


# --- B5-B6: one frozen task set, one deterministic order -------------------

def test_b5_every_candidate_answers_the_same_frozen_task_ids():
    """Candidate mode rides in `argv`, so the task bytes -- and therefore the
    `task-` ids -- are identical across the matrix. If a candidate could move a
    task id, the columns would be scored by different rubrics and the
    comparison would be meaningless."""
    f = _fixture()
    report = f["report"]
    assert len(report["task_ids"]) == 2, report["task_ids"]

    for key in report["candidate_order"]:
        evaluation = _member("candidates/%s/evaluation.json" % key)
        assert [e["task_id"] for e in evaluation["episodes"]] == report["task_ids"]
        assert evaluation["env_id"] == report["env_id"]
        assert evaluation["subject"] == \
            _member("candidates/%s/evaluation.json"
                    % report["candidate_order"][0])["subject"]

    for row in report["tasks"]:
        assert sorted(row["episodes"]) == sorted(report["candidate_order"])


def test_b6_execution_order_is_deterministic_and_sorted():
    """Sorted by `candidate_key`, never by the order the file happened to list
    them in -- so two people who wrote the same three candidates in different
    orders get the same batch."""
    f = _fixture()
    assert f["report"]["candidate_order"] == sorted(
        k for k, _ in _CANDIDATES), f["report"]["candidate_order"]
    assert [k for k, _ in _CANDIDATES] != sorted(k for k, _ in _CANDIDATES), \
        "the fixture declares the candidates already sorted, so this proves nothing"

    shuffled = list(reversed(_CANDIDATES))
    validated = B.validate_candidate_set(_candidate_set(shuffled))
    assert [c["candidate_key"] for c in validated] == \
        sorted(k for k, _ in _CANDIDATES)


# --- B7-B9: one registry, and everything says so ---------------------------

def test_b7_exactly_one_registry_serves_the_whole_batch():
    """Built once, before anything runs, and handed to every `eval_split` and
    every replay. Two registries would mean the comparison judged episodes by
    verifiers other than the ones that produced them."""
    f = _fixture()
    seen = []
    real = ES.eval_split

    def spy(*a, **kw):
        seen.append(kw.get("registry"))
        return real(*a, **kw)

    registry = _registry()
    ES.eval_split = spy
    try:
        report, _ = _run("b7-out", _candidate_set([("a", "ok"), ("b", "nofix")]),
                         registry=registry)
    finally:
        ES.eval_split = real

    assert len(seen) == 2, seen
    assert all(r is registry for r in seen), seen
    assert report["runtime_context"]["registry_version"] == \
        registry.registry_version


def test_b8_every_evaluation_attests_that_registry():
    f = _fixture()
    expected = f["report"]["runtime_context"]
    assert expected["wiring"] == "registry", expected
    assert expected["verifiers_available"], expected
    for key in f["report"]["candidate_order"]:
        context = _member("candidates/%s/evaluation.json" % key)["runtime_context"]
        assert context == expected, (key, context)


def test_b9_every_comparison_attests_that_registry():
    f = _fixture()
    expected = f["report"]["runtime_context"]
    members = [entry["comparison_member"]
               for row in f["report"]["tasks"] for entry in row["comparisons"]]
    assert members, "no comparison was written"
    for member in members:
        assert _member(member)["runtime_context"] == expected, member


# --- B10-B11: one episode per cell, with its own derived id ----------------

def test_b10_each_candidate_task_pair_produces_at_most_one_episode():
    f = _fixture()
    report = f["report"]
    for entry in report["candidates"]:
        ids = entry["episode_ids"]
        assert len(ids) == len(report["task_ids"]), entry
        assert len(set(ids)) == len(ids), entry
        home = os.path.join(f["output"], "candidates", entry["candidate_key"],
                            "episodes")
        assert sorted(os.listdir(home)) == sorted(ids), home
    assert report["totals"]["episode_count"] == \
        len(report["task_ids"]) * len(report["candidate_order"])


def test_b11_episodes_retain_their_independently_derived_ids():
    """The batch names episodes; it does not mint them. Every `episode-` in the
    index re-derives from the bundle's own bytes, through the same replay
    `verify-episode` performs."""
    f = _fixture()
    for entry in f["report"]["candidates"]:
        key = entry["candidate_key"]
        for episode_id in entry["episode_ids"]:
            bundle = os.path.join(f["output"], "candidates", key, "episodes",
                                  episode_id)
            closure = episode_bundle.verify_episode_bundle(
                bundle, extra_verifiers=_verifiers())
            assert closure["outcome"] == episode_bundle.OUTCOME_CLOSED, closure
            assert closure["episode_id"] == episode_id, closure

    every = [i for e in f["report"]["candidates"] for i in e["episode_ids"]]
    assert len(set(every)) == len(every), "two cells claimed one episode id"


def _verifiers():
    registry = _registry()
    return {s: registry.get(s) for s in registry.available()}


# --- B12-B13: every pair, once, deterministically --------------------------

def test_b12_all_unordered_candidate_pairs_are_considered_per_task():
    """Every pair once, in sorted-key direction. Both directions would be two
    readings of one relation that are free to disagree; the reverse remains
    available from `trvs compare`."""
    f = _fixture()
    report = f["report"]
    order = report["candidate_order"]
    expected = {(order[i], order[j])
                for i in range(len(order)) for j in range(i + 1, len(order))}
    assert len(expected) == 3, expected

    for row in report["tasks"]:
        seen = {(e["left_candidate"], e["right_candidate"])
                for e in row["comparisons"] + row["refusals"]}
        assert seen == expected, (row["task_id"], seen)
        for left, right in seen:
            assert left < right, (left, right)

    written = sorted(os.listdir(os.path.join(f["output"], "comparisons")))
    assert written == sorted(report["task_ids"]), written
    for task_id in report["task_ids"]:
        names = sorted(os.listdir(
            os.path.join(f["output"], "comparisons", task_id)))
        assert names == sorted("%s--%s.json" % pair for pair in expected), names


def test_b13_closed_pairs_receive_deterministic_comparison_reports():
    """The member on disk is exactly the `ComparisonV1` the standalone command
    would produce for those two bundles, and the index quotes its relation
    rather than recomputing one."""
    from traaviis import comparison as C
    f = _fixture()
    report = f["report"]
    row = _row(report, report["task_ids"][0])
    assert row["comparisons"], row

    for entry in row["comparisons"]:
        member = _member(entry["comparison_member"])
        assert member["comparison_version"] == C.COMPARISON_VERSION, member
        assert member["task_id"] == row["task_id"], member
        assert member["relation"] == entry["relation"], entry

        left, right = entry["left_candidate"], entry["right_candidate"]
        direct = C.compare_episodes(
            _bundle(left, row["task_id"]), _bundle(right, row["task_id"]),
            registry=_registry())
        assert direct == member, entry["comparison_member"]


def _bundle(key, task_id):
    f = _fixture()
    evaluation = _member("candidates/%s/evaluation.json" % key)
    entry = next(e for e in evaluation["episodes"] if e["task_id"] == task_id)
    return os.path.join(f["output"], "candidates", key, "episodes",
                        entry["bundle"])


# --- B14-B15: a lost cell refuses; it does not stop the batch --------------

def test_b14_and_b15_a_lost_candidate_refuses_its_pairs_and_the_batch_goes_on():
    """`aaa` cannot be launched at all, so it retains no episode. Two things
    must follow, and they pull in opposite directions:

    - every pair involving it is a **typed refusal** naming both sides -- never
      a fabricated relation, and never a comparison written to disk;
    - the candidates that sort *after* it still run, still persist, and are
      still compared with each other.

    It sorts first precisely so that abandoning the batch on its failure would
    be visible as an empty matrix rather than a missing last column.
    """
    f = _fixture()
    doc = _candidate_set([("nofix", "nofix"), ("repair", "ok")])
    doc["candidates"].append(
        {"candidate_key": "aaa", "argv": [os.path.join(f["tmp"], "no-such-agent")]})

    report, output = _run("b14-out", doc)
    assert report["candidate_order"] == ["aaa", "nofix", "repair"]

    for row in report["tasks"]:
        codes = {(r["left_candidate"], r["right_candidate"]): r
                 for r in row["refusals"]}
        assert set(codes) == {("aaa", "nofix"), ("aaa", "repair")}, codes
        for pair, refusal in codes.items():
            assert refusal["code"] == "EPISODE_UNAVAILABLE", refusal
            assert "aaa" in refusal["detail"], refusal
        # the pair that *is* comparable still is
        assert [(c["left_candidate"], c["right_candidate"])
                for c in row["comparisons"]] == [("nofix", "repair")]
        assert row["episodes"]["aaa"]["episode_id"] is None, row
        assert row["episodes"]["repair"]["reward"] == 1.0, row

        written = os.listdir(os.path.join(output, "comparisons", row["task_id"]))
        assert written == ["nofix--repair.json"], written

    assert report["totals"]["refusal_count"] == 4, report["totals"]
    assert report["totals"]["comparison_count"] == 2, report["totals"]
    # The batch itself completed: a candidate that lost every cell is a result.
    assert os.path.isfile(os.path.join(output, "batch.json"))


def test_b15b_a_crashing_candidate_still_produces_evidence():
    """A different kind of failure, and it must *not* become a refusal: an
    agent that runs and exits non-zero produces a real, closed, replayable
    episode with no reward. There is evidence; it simply did not score."""
    f = _fixture()
    doc = _candidate_set([("repair", "ok")])
    doc["candidates"].append(
        {"candidate_key": "crash", "argv": [sys.executable, "-c", "raise SystemExit(3)"]})

    report, output = _run("b15b-out", doc)
    for row in report["tasks"]:
        cell = row["episodes"]["crash"]
        assert cell["episode_id"], cell
        assert cell["status"] == "error", cell
        assert cell["reward"] is None, cell
        assert row["refusals"] == [], row
        assert len(row["comparisons"]) == 1, row
    _FIXTURE["b15b"] = (report, output)


# --- B16-B17: the two reward rules survive the matrix ----------------------

def test_b16_a_null_reward_stays_incomparable_inside_a_batch():
    """The rule `compare` enforces per pair has to survive aggregation: an
    episode that did not score is `incomparable`, and the delta is `None`. A
    matrix is exactly where imputing zero would be tempting, because a column
    of zeros ranks so much more neatly than a column of holes."""
    if "b15b" not in _FIXTURE:
        test_b15b_a_crashing_candidate_still_produces_evidence()
    report, output = _FIXTURE["b15b"]

    for row in report["tasks"]:
        entry = row["comparisons"][0]
        assert entry["relation"]["reward"] == "incomparable", entry
        assert entry["relation"]["right_minus_left"] is None, entry
        assert row["episodes"]["crash"]["reward"] is None, row

    blob = json.dumps(report)
    assert '"reward": 0.0' not in blob and '"reward": 0,' not in blob, \
        "a missing score was imputed as zero somewhere in the report"
    assert report["totals"]["unscored_count"] == len(report["task_ids"]), \
        report["totals"]
    assert report["totals"]["scored_count"] == len(report["task_ids"]), \
        report["totals"]


def test_b17_equal_reward_preserves_the_trace_difference():
    """`nofix` and `gutspec` both score 0.4 by different routes. The matrix
    reports them equal *and* keeps the fact that they got there differently --
    there is no tie-breaker folding one into the other."""
    f = _fixture()
    found = 0
    for row in f["report"]["tasks"]:
        for entry in row["comparisons"]:
            if entry["relation"]["reward"] != "equal":
                continue
            found += 1
            assert entry["relation"]["right_minus_left"] == 0, entry
            assert entry["relation"]["same_trace"] is False, entry
            assert entry["relation"]["same_episode"] is False, entry
            member = _member(entry["comparison_member"])
            assert any(v for v in member["differences"].values()), member
    assert found == len(f["report"]["task_ids"]), \
        "the fixture produced no equal-reward pair, so this proved nothing"


# --- B18-B19: a candidate key is a label, not an identity ------------------

_LADDER = ("snap-", "task-", "rew-", "trace-", "episode-", "env-",
           "finding-", "patch-", "sem-", "bundle-")

_FORBIDDEN = ("batch-", "candidate-", "agent-", "compare-")


def test_b18_candidate_labels_enter_no_traaviis_identity():
    """Rename every candidate and rerun: every `episode-`, `task-`, `trace-`
    and `env-` is unchanged. Only the report moved, which is what "a local
    report label" has to mean if it means anything."""
    renamed = [("alpha", "ok"), ("beta", "gutspec"), ("gamma", "nofix")]
    report, _ = _run("b18-out", _candidate_set(renamed))

    original = _fixture()["report"]
    assert report["candidate_order"] == ["alpha", "beta", "gamma"]
    assert report["task_ids"] == original["task_ids"]
    assert report["env_id"] == original["env_id"]

    was = {mode: key for key, mode in _CANDIDATES}
    now = {mode: key for key, mode in renamed}
    for mode in ("ok", "gutspec", "nofix"):
        before = next(c for c in original["candidates"]
                      if c["candidate_key"] == was[mode])["episode_ids"]
        after = next(c for c in report["candidates"]
                     if c["candidate_key"] == now[mode])["episode_ids"]
        assert before == after, (mode, before, after)


def test_b19_no_batch_agent_candidate_or_compare_id_appears():
    """The ladder stops where it stopped. A batch is a reading of artifacts
    that already exist, so an id for it would be a rung nobody can re-derive."""
    f = _fixture()
    blob = json.dumps(f["report"], sort_keys=True)
    for prefix in _FORBIDDEN:
        assert prefix not in blob, prefix

    found = {p for p in _LADDER if p in blob}
    assert {"task-", "episode-", "env-"} <= found, found

    for base, _dirs, files in os.walk(f["output"]):
        for name in files:
            for prefix in _FORBIDDEN:
                assert not name.startswith(prefix), os.path.join(base, name)

    assert not any(k.endswith("_id") and isinstance(v, str)
                   and v.startswith(_FORBIDDEN)
                   for k, v in f["report"].items()), f["report"].keys()
    assert "batch_id" not in f["report"], f["report"].keys()


# --- B20-B21: the index is portable ----------------------------------------

_ABS_PATH = re.compile(r'"(?:/|[A-Za-z]:\\\\)[^"]{2,}"')
_WALL_CLOCK = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d")


def test_b20_no_absolute_path_or_wall_clock_enters_the_index():
    """`batch.json` is the document a reader takes away. A host path or a
    timestamp in it would make two identical matrices over identical evidence
    differ, and would leak where the run happened."""
    f = _fixture()
    blob = json.dumps(f["report"], sort_keys=True)
    assert not _ABS_PATH.search(blob), _ABS_PATH.search(blob).group(0)
    assert not _WALL_CLOCK.search(blob), _WALL_CLOCK.search(blob).group(0)
    assert f["tmp"] not in blob
    assert sys.executable not in blob


def test_b21_output_members_use_only_relative_deterministic_paths():
    """Every path the index quotes is relative to the output directory and
    resolves inside it, so the whole batch can be moved or archived whole."""
    f = _fixture()
    members = [c["evaluation"] for c in f["report"]["candidates"]]
    members += [e["comparison_member"]
                for row in f["report"]["tasks"] for e in row["comparisons"]]
    assert members

    root = os.path.realpath(f["output"])
    for member in members:
        assert not member.startswith("/") and ".." not in member.split("/"), member
        assert "\\" not in member, member
        full = os.path.realpath(os.path.join(root, member.replace("/", os.sep)))
        assert full.startswith(root + os.sep), member
        assert os.path.isfile(full), member


# --- B22-B23: the matrix is published whole or not at all ------------------

def test_b22_the_final_output_directory_is_published_atomically():
    """One rename publishes the whole matrix, so a reader never sees a batch
    with two of three candidates in it. Proven by watching `os.replace`: at the
    moment it is called, the destination does not exist and the staged tree is
    already complete."""
    f = _fixture()
    output = os.path.join(f["tmp"], "b22-out")
    shutil.rmtree(output, ignore_errors=True)
    observed = {}
    real = os.replace

    def watch(src, dst):
        if os.path.abspath(dst) == os.path.abspath(output):
            observed["existed"] = os.path.exists(dst)
            observed["staged"] = sorted(os.listdir(src))
            observed["batch"] = os.path.isfile(os.path.join(src, "batch.json"))
        return real(src, dst)

    os.replace = watch
    try:
        B.run_batch(f["package"], "all", _candidate_set([("a", "ok"), ("b", "nofix")]),
                    output, registry=_registry())
    finally:
        os.replace = real

    assert observed, "the output directory was not published by a rename"
    assert observed["existed"] is False, observed
    assert observed["staged"] == ["batch.json", "candidates", "comparisons"], observed
    assert observed["batch"] is True, observed
    assert os.path.isdir(output)


def test_b22b_an_existing_output_path_is_refused():
    """Never merged into, never overwritten: a batch published on top of an
    older one would silently be half of each."""
    f = _fixture()
    before = _tree(f["output"])

    empty = os.path.join(f["tmp"], "b22b-empty")
    os.makedirs(empty, exist_ok=True)
    for existing in (f["output"], empty):
        try:
            B.run_batch(f["package"], "all", _candidate_set(), existing,
                        registry=_registry())
        except B.BatchError as ex:
            assert ex.code == "OUTPUT_EXISTS", ex.code
        else:
            raise AssertionError("wrote into an existing path: %s" % existing)

    # Refused *before* anything ran, so the published batch is untouched -- an
    # even-numbered rerun must not be able to damage the batch it collided with.
    assert _tree(f["output"]) == before
    assert os.listdir(empty) == []


def _tree(root):
    return sorted(os.path.relpath(os.path.join(base, name), root)
                  for base, _dirs, files in os.walk(root) for name in files)


def test_b23_a_failed_batch_leaves_no_directory_and_no_partial_index():
    """An infrastructure failure part-way through publishes nothing. Three
    failure points, one rule: no output directory, and no staging directory
    left behind to be mistaken for one.
    """
    f = _fixture()
    parent = f["tmp"]
    before = {n for n in os.listdir(parent) if n.startswith(".trvs-batch-")}

    # (a) refused before anything runs
    _refuses("CANDIDATE_KEY_DUPLICATE",
             _candidate_set([("x", "ok"), ("x", "nofix")]), "b23-out")

    # (b) an episode that evaluated but could not be written
    output = os.path.join(parent, "b23-write")
    real = episode_bundle.write_episode_bundle

    def fail(*a, **kw):
        raise OSError(30, "Read-only file system")

    episode_bundle.write_episode_bundle = fail
    try:
        B.run_batch(f["package"], "all", _candidate_set([("a", "ok")]), output,
                    registry=_registry())
    except B.BatchError as ex:
        assert ex.code == "EPISODE_NOT_PERSISTED", ex.code
    else:
        raise AssertionError("a batch survived being unable to write an episode")
    finally:
        episode_bundle.write_episode_bundle = real
    assert not os.path.exists(output)

    # (c) the index itself could not be written
    output = os.path.join(parent, "b23-index")
    real_write = B._write_json

    def fail_index(document, path):
        if os.path.basename(path) == "batch.json":
            raise OSError(28, "No space left on device")
        return real_write(document, path)

    B._write_json = fail_index
    try:
        B.run_batch(f["package"], "all", _candidate_set([("a", "ok")]), output,
                    registry=_registry())
    except OSError:
        pass
    else:
        raise AssertionError("a batch survived being unable to write its index")
    finally:
        B._write_json = real_write
    assert not os.path.exists(output)

    after = {n for n in os.listdir(parent) if n.startswith(".trvs-batch-")}
    assert after == before, sorted(after - before)


# --- B24: the same evidence reads the same way -----------------------------

def test_b24_two_runs_over_equivalent_evidence_produce_identical_reports():
    """The agents are deterministic, so a second batch over the same package
    with the same candidate set must produce a byte-identical index. Anything
    that varies -- a timestamp, a temp path, an iteration order -- shows up
    here as a diff.
    """
    first, _ = _run("b24-a", _candidate_set([("a", "ok"), ("b", "nofix")]))
    second, _ = _run("b24-b", _candidate_set([("a", "ok"), ("b", "nofix")]))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# --- B25-B26: the matrix says the right thing ------------------------------

def test_b25_the_honest_repair_outranks_nofix_and_gutspec_per_task():
    """The point of the whole slice. On every task, the candidate that cited
    the spec and actually fixed the defect outranks both the admissible
    non-fix and the candidate that edited the spec to match the bug."""
    f = _fixture()
    for row in f["report"]["tasks"]:
        assert row["episodes"]["repair"]["reward"] == 1.0, row
        assert row["episodes"]["nofix"]["reward"] == 0.4, row
        assert row["episodes"]["gutspec"]["reward"] == 0.4, row

        by_pair = {(e["left_candidate"], e["right_candidate"]): e["relation"]
                   for e in row["comparisons"]}
        assert by_pair[("gutspec", "repair")]["reward"] == "right_higher"
        assert by_pair[("nofix", "repair")]["reward"] == "right_higher"
        assert by_pair[("gutspec", "nofix")]["reward"] == "equal"
        assert by_pair[("nofix", "repair")]["right_minus_left"] == 0.6


def test_b26_pairing_never_crosses_task_ids():
    """Every comparison is between two episodes of *one* task. Crossing them
    would rank two answers to different questions -- which `compare` refuses
    outright, so the batch must never construct such a pair to begin with."""
    f = _fixture()
    for row in f["report"]["tasks"]:
        for entry in row["comparisons"]:
            member = _member(entry["comparison_member"])
            assert member["task_id"] == row["task_id"], entry
            assert entry["comparison_member"].startswith(
                "comparisons/%s/" % row["task_id"]), entry
            for side in ("left", "right"):
                key = entry["%s_candidate" % side]
                assert member[side]["episode_id"] == \
                    row["episodes"][key]["episode_id"], (entry, side)

    seen = set()
    for row in f["report"]["tasks"]:
        for entry in row["comparisons"]:
            seen.add(entry["comparison_member"])
    assert len(seen) == len(f["report"]["task_ids"]) * 3, seen


# --- B27-B28: serial runs, and replay-only comparison ----------------------

def test_b27_the_batch_runs_agents_serially_never_concurrently():
    """Serial is in the name of the report for a reason: two agents running at
    once share a host, and a candidate that lost a race would be scored for it.
    Proven by observing that no candidate's run begins before the previous
    one's has ended."""
    f = _fixture()
    live, overlaps, order = [], [], []
    real = ES.eval_split

    def spy(package, split, agent_command, **kw):
        if live:
            overlaps.append(list(live))
        live.append(agent_command)
        order.append(agent_command[-1])
        try:
            return real(package, split, agent_command, **kw)
        finally:
            live.pop()

    ES.eval_split = spy
    try:
        _run("b27-out", _candidate_set([("a", "ok"), ("b", "nofix")]))
    finally:
        ES.eval_split = real

    assert overlaps == [], overlaps
    assert order == ["ok", "nofix"], order


def test_b28_comparing_does_not_rerun_the_candidate_agents():
    """Once the episodes are persisted, the comparison half of the batch is a
    pure reading. An agent launched during it would mean the ranking depended
    on a run nobody kept the evidence for."""
    f = _fixture()
    launched = []
    real = runner.run_agent

    def spy(*a, **kw):
        launched.append(a)
        return real(*a, **kw)

    row = f["report"]["tasks"][0]
    runner.run_agent = spy
    try:
        report = B._task_row(
            os.path.join(f["tmp"], "b28-scratch"), row["task_id"],
            f["report"]["candidate_order"],
            {k: _member("candidates/%s/evaluation.json" % k)
             for k in f["report"]["candidate_order"]},
            {k: {t: _bundle(k, t) for t in f["report"]["task_ids"]}
             for k in f["report"]["candidate_order"]},
            {k: {} for k in f["report"]["candidate_order"]},
            _registry(), None)
    finally:
        runner.run_agent = real

    assert launched == [], "the comparison half launched an agent"
    assert [(e["left_candidate"], e["right_candidate"])
            for e in report["comparisons"]] == \
        [(e["left_candidate"], e["right_candidate"]) for e in row["comparisons"]]
    assert report["refusals"] == []


# --- B29-B30: the closures that came before stay closed --------------------

def test_b29_the_comparison_api_ambiguity_closure_remains_green():
    """The batch consumes `compare_episodes` directly, which is the reason that
    API's dual-wiring defect had to close first. Re-asserted here so a future
    change to the batch's call site cannot quietly reopen it."""
    import inspect
    from traaviis import comparison as C

    params = inspect.signature(C.compare_episodes).parameters
    assert "runtime_context" not in params, sorted(params)
    assert set(params) == {"left_dir", "right_dir", "registry",
                           "extra_verifiers"}, sorted(params)

    f = _fixture()
    left = _bundle("nofix", f["report"]["task_ids"][0])
    right = _bundle("repair", f["report"]["task_ids"][0])
    try:
        C.compare_episodes(left, right, registry=_registry(),
                           extra_verifiers=_verifiers())
    except C.ComparisonError as ex:
        assert ex.code == "VERIFIER_WIRING_AMBIGUOUS", ex.code
    else:
        raise AssertionError("expected VERIFIER_WIRING_AMBIGUOUS")

    # The batch itself never supplies the caller seam.
    source = inspect.getsource(B)
    assert "extra_verifiers" not in source, \
        "the batch grew a second verifier seam"


def test_b30_the_cli_reports_the_matrix_and_exits_0():
    """The command-level contract: a completed batch exits 0 whatever the
    candidates scored, `--json` emits the index the library returned, and the
    human form names every candidate and every relation."""
    f = _fixture()
    doc = os.path.join(f["tmp"], "b30-candidates.json")
    with open(doc, "w") as fh:
        json.dump(_candidate_set([("a", "ok"), ("b", "gutspec")]), fh)

    output = os.path.join(f["tmp"], "b30-out")
    shutil.rmtree(output, ignore_errors=True)
    p = _cli(f["package"], "all", "--candidates", doc, "--output", output, "--json")
    assert p.returncode == 0, (p.returncode, p.stderr[-800:])
    report = json.loads(p.stdout)
    assert report["batch_version"] == B.BATCH_VERSION
    assert report["candidate_order"] == ["a", "b"]
    with open(os.path.join(output, "batch.json")) as fh:
        assert json.load(fh) == report

    output = os.path.join(f["tmp"], "b30-human")
    shutil.rmtree(output, ignore_errors=True)
    p = _cli(f["package"], "all", "--candidates", doc, "--output", output)
    assert p.returncode == 0, (p.returncode, p.stderr[-800:])
    for expected in ("environment", "split", "candidates", "comparisons",
                     "left scored higher", "batch"):
        assert expected in p.stdout, (expected, p.stdout)


def test_b30b_an_unadmitted_batch_exits_2_from_the_cli():
    """Admission, a malformed plan and a refused output all exit 2 -- and none
    of them leaves an output directory."""
    f = _fixture()
    bad = os.path.join(f["tmp"], "b30b-candidates.json")
    with open(bad, "w") as fh:
        fh.write("{not json")
    output = os.path.join(f["tmp"], "b30b-out")
    p = _cli(f["package"], "all", "--candidates", bad, "--output", output)
    assert p.returncode == 2, (p.returncode, p.stderr)
    assert not os.path.exists(output)

    good = os.path.join(f["tmp"], "b30b-good.json")
    with open(good, "w") as fh:
        json.dump(_candidate_set([("a", "ok")]), fh)
    p = _cli(f["package"], "nope", "--candidates", good, "--output", output)
    assert p.returncode == 2, (p.returncode, p.stderr)
    assert not os.path.exists(output)

    p = _cli(os.path.join(f["tmp"], "no-such-package"), "all",
             "--candidates", good, "--output", output)
    assert p.returncode == 2, (p.returncode, p.stderr)
    assert not os.path.exists(output)


# ------------------------------------------------------------------- runner
def main():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    passed = failed = skipped = 0
    for name, t in tests:
        try:
            t()
        except Skip as ex:
            print("SKIP %s (%s)" % (name, ex))
            skipped += 1
        except Exception:
            import traceback
            print("FAIL %s" % name)
            traceback.print_exc()
            failed += 1
        else:
            print("PASS %s" % name)
            passed += 1
    print("\n%d passed, %d skipped, %d failed" % (passed, skipped, failed))
    if _FIXTURE.get("tmp"):
        shutil.rmtree(_FIXTURE["tmp"], ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
