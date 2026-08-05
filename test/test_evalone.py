"""End-to-end battery for the eval-one orchestrator (traaviis.evalone, RFC §10).

Drives the full one-shot pipeline against the deterministic stub agent: snapshot
-> controlled run -> trace -> finding/patch -> verifiers -> reward -> episode
receipt. Pins the happy path (valid full-reward episode), a bad-patch fail with
the §7 floor, a tampered (policy-violation) invalid episode, a required-but-
deferred verifier producing invalid-config, and episode-identity stability vs.
movement.

Runs with pytest, or standalone: `python3 test/test_evalone.py`.
"""

import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import admission as ADM  # noqa: E402
from traaviis import evalone as E  # noqa: E402
from traaviis import identity as I  # noqa: E402
from traaviis import reward as R  # noqa: E402
from traaviis import snapshot as S  # noqa: E402
from traaviis import substrate_verifiers as SV  # noqa: E402
from traaviis.vcontext import VerifierResult  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = os.path.join(HERE, "fixtures", "stub_agent.py")

_TMP = {}


def _tmp():
    if "d" not in _TMP:
        _TMP["d"] = tempfile.mkdtemp(prefix="trvs-evalone-law-")
    return _TMP["d"]


def _stable_interpreter():
    """`sys.executable` reached through a fixture-named symlink.

    The canonical trace records `os.path.basename` of an absolute argv token
    (`runner._normalize_command`, R4) so a host's *directory* layout cannot move
    `trace-`. The basename itself still enters it, and `trace_id` is one of
    `identity._EPISODE_IDENTITY_KEYS`, so the basename of the running
    interpreter reaches `episode-`. That basename is a property of the host, not
    of this fixture: the same Python is `python3` here, `python3.11` under a
    distro alias and `python` in a virtualenv. Naming the interpreter ourselves
    is what would let an `episode-` pinned in this file be a statement about the
    fixture instead of a statement about whoever ran it. Falls back to the real
    path if symlinks are unavailable, so a platform without them degrades to the
    old behavior rather than failing to import.

    Copied deliberately from `test_kernel._stable_interpreter` -- the same
    defect, closed the same way, so the two batteries do not drift apart.
    """
    link = os.path.join(_tmp(), "toolchain", "python3")
    try:
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if not os.path.exists(link):
            os.symlink(os.path.realpath(sys.executable), link)
        return link
    except (OSError, NotImplementedError, AttributeError):
        return sys.executable


INTERPRETER = _stable_interpreter()
AGENT = [INTERPRETER, STUB]

# The frozen subject the stub was written against.
CONTENT = {"spec/one.md": "alpha\nbeta\n", "src/mod.py": "return 1\n"}

REWARD_SPEC = {
    "reward_spec_version": "traaviis.reward.v1",
    "substrate_profile": "residency.repository.v1",
    "signals": {
        "citations":            {"verifier": "residency.citations.v1", "weight": 0.25},
        "patch":                {"verifier": "residency.patch.v1",     "weight": 0.20},
        "tests":                {"verifier": "residency.tests.v1",     "weight": 0.30},
        "identity":             {"verifier": "residency.identity.v1",  "weight": 0.15},
        "finding_completeness": {"verifier": "residency.finding.v1",   "weight": 0.10},
    },
    "caps": [  # F1: explicit trigger state, renamed from "floors"
        {"when": {"signal": "patch", "state": "fail"}, "reward_max": 0.25},
        {"when": {"signal": "citations", "state": "fail"}, "reward_max": 0.25},
        {"when": {"signal": "tests", "state": "fail"}, "reward_max": 0.40},
    ],
    "aggregation": "terminal",
}

# A structured toolchain in the shape execution_facts.v1 seals (E1). Changing the
# resolved python version moves episode- (see test_toolchain_change_moves_episode).
TOOLCHAIN = {"profile": "cpython-3.11",
             "resolved": {"python": {"version": "3.11.4"}}}


def _content_hash(text):
    data = text.encode("utf-8").replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _snapshot():
    # A real sealed subject so admission binds content<->snapshot exactly.
    snap = {"snapshot_version": S.SNAPSHOT_VERSION,
            "files": {k: _content_hash(v) for k, v in CONTENT.items()},
            "exclusions": [], "binary_paths": [], "file_modes": {},
            "base_revision": None, "visible_config": {}}
    snap["snapshot_id"] = I.snapshot_id(snap)
    return snap


def _task(required):
    return {
        "task_spec_version": "traaviis.task.v1",
        "substrate_profile": "residency.repository.v1",
        "subject": {"snapshot_id": _snapshot()["snapshot_id"]},
        "instructions": {"objective": "demo"},
        "reward_id": I.reward_id(REWARD_SPEC),
        "verifier_plan": {"required": required,
                          "not_applicable": ["native", "oracle"]},
        "termination": {"mode": "one_shot"},
        "agent_run_policy": {
            "policy_version": "traaviis.agent-run-policy.v1",
            "command_mode": "argv", "shell": False, "network": "unrestricted",
            "timeout_seconds": 30, "max_output_bytes": 4194304,
            # NO ambient `PATH` here, deliberately. `runner._seal_env` (R1)
            # *discards* any caller-supplied `PATH` -- the toolchain resolver
            # owns it -- so a `PATH` declared here never reached the agent: the
            # sealed child environment is `{"TRAAVIIS_STUB_MODE": ...}` either
            # way. But `agent_run_policy` is inside `task-`
            # (`identity.canonicalize_task`), and `task_id` is inside
            # `episode-`, so an ambient `PATH` moved this fixture's identity
            # while changing nothing the identity is meant to describe.
            # Measured, not assumed: with the host `PATH` this fixture minted
            # `task-89e2eae7…`, and under a bogus one `task-31b7158a…`. On this
            # host `PATH` carries a per-session directory, so the same tree
            # minted a different `episode-` in every session -- which is exactly
            # the multi-session investigation `test_kernel`'s
            # `PRE_LINEARIZATION_EPISODE_ID` note records. No id is pinned in
            # this file today; the leak is closed so that pinning one later is
            # safe. See `test_ambient_environment_does_not_reach_identity`.
            "environment": {"TRAAVIIS_STUB_MODE": "ok"},
            "writable_paths": ["."],
            "result_path": "result.json", "patch_path": "candidate.patch",
        },
    }


def _pass_tests(context):
    return VerifierResult(R.PASS)  # injected substrate verifier stand-in


def _pass_identity(context):
    return VerifierResult(R.PASS)


# Required signals now demand a wired verifier with an implementation version;
# stamp the injected stand-ins so they are admissible config, not a deferred gap.
_pass_tests.version = "residency.tests.v1"
_pass_identity.version = "residency.identity.v1"


ALL_PASS = {"tests": _pass_tests, "identity": _pass_identity}


def _evaluate(mode, required, extra=ALL_PASS, env=None):
    """The complete `EvaluationRunV1` (receipt **and** artifacts) for one mode."""
    task = _task(required)
    if env is not None:
        task["agent_run_policy"]["environment"] = env
    else:
        task["agent_run_policy"]["environment"]["TRAAVIIS_STUB_MODE"] = mode
    return E.evaluate(
        task, CONTENT, AGENT, REWARD_SPEC,
        snapshot=_snapshot(),
        extra_verifiers=extra, platform="linux-x86_64",
        toolchain=TOOLCHAIN,
    )


def _eval(mode, required, extra=ALL_PASS, env=None):
    return _evaluate(mode, required, extra=extra, env=env)["receipt"]


def test_happy_path_full_reward_valid_episode():
    r = _eval("ok", ["citations", "patch", "tests", "identity"])
    assert r["status"] == R.STATUS_OK
    assert r["validity"] == R.VALID
    assert abs(r["reward"] - 1.0) < 1e-9
    assert r["episode_id"].startswith("episode-")
    assert r["verification"]["citations"] == R.PASS
    assert r["verification"]["native"] == R.NOT_APPLICABLE
    assert r["outputs"]["finding_id"].startswith("finding-")
    assert r["outputs"]["patch_id"].startswith("patch-")


def test_bad_patch_fails_patch_and_hits_floor():
    r = _eval("badpatch", ["citations", "patch", "tests", "identity"])
    assert r["verification"]["patch"] == R.FAIL
    assert r["status"] == R.STATUS_OK
    assert abs(r["reward"] - 0.25) < 1e-9  # §7 patch floor


def test_policy_violation_is_invalid_episode():
    # No ambient `PATH` in this env either -- see the note in `_task`; the
    # runner strips a caller `PATH` (R1) but `task-` hashes it anyway.
    r = _eval("ok", ["citations", "patch"], env={"TRAAVIIS_STUB_MODE": "ok"})
    # constrain writable to src/ so root outputs violate
    # (re-run with a tighter policy)
    task = _task(["citations", "patch"])
    task["agent_run_policy"]["writable_paths"] = ["src/"]
    r = E.eval_one(task, CONTENT, AGENT, REWARD_SPEC,
                   snapshot=_snapshot(),
                   extra_verifiers=ALL_PASS, platform="linux-x86_64")
    assert r["status"] == R.STATUS_INVALID
    assert r["validity"] == R.INVALID
    assert r["reward"] == 0.0


def test_required_deferred_verifier_is_invalid_config():
    # Require identity but do NOT inject it -> defaults not_applicable -> invalid.
    r = _eval("ok", ["citations", "patch", "identity"], extra={})
    assert r["status"] == R.STATUS_INVALID
    assert r["reward"] is None


def test_required_verifier_without_implementation_version_is_invalid_config():
    # A required signal whose wired verifier declares no implementation version
    # (.version) can only ever fall back to not_applicable -> invalid config (F4),
    # distinct from the unwired case above.
    def _versionless_tests(context):
        return VerifierResult(R.PASS)  # deliberately carries no .version
    r = _eval("ok", ["citations", "patch", "tests"],
              extra={"tests": _versionless_tests})
    assert r["status"] == R.STATUS_INVALID
    assert r["reward"] is None


def test_episode_identity_stable_across_reruns():
    a = _eval("ok", ["citations", "patch", "tests", "identity"])
    b = _eval("ok", ["citations", "patch", "tests", "identity"])
    assert a["episode_id"] == b["episode_id"]


def test_ambient_environment_does_not_reach_identity():
    """This battery's identities are properties of the fixture, not of the host.

    `test_episode_identity_stable_across_reruns` above cannot see this: both of
    its reruns read the *same* ambient environment, so a fixture that copies the
    host into the task agrees with itself and passes. The failure only appears
    across two hosts -- or, on a box whose `PATH` carries a per-session
    directory, across two sessions -- which is where it cost `test_kernel`'s K27
    a multi-session investigation before the leak was found rather than the
    "later slice moved the id" it looked like.

    So perturb the input instead of repeating the run. Two host inputs are
    closed here and both are checked:

    - the ambient `PATH`, which `runner._seal_env` *discards* under R1 (so it
      never reached the agent) while `canonicalize_task` hashed it;
    - the interpreter basename, which `runner._normalize_command` *keeps* under
      R4, so `python3` and `python3.11` disagree on `trace-` and therefore on
      `episode-`.

    Both directions matter: R1 and R2 are right, and it was the fixtures that
    contradicted them. This is the check a fixture reading the environment
    cannot pass, and it is what makes pinning a literal `episode-` in this file
    safe later.
    """
    required = ["citations", "patch", "tests", "identity"]
    before_task = I.task_id(_task(required))
    before_episode = _eval("ok", required)["episode_id"]

    saved = os.environ.get("PATH")
    os.environ["PATH"] = ("/nonexistent/local-agent-mode-sessions"
                          "/00000000-1111-2222-3333-444444444444/probe")
    try:
        assert I.task_id(_task(required)) == before_task, \
            "the ambient environment reached the task identity"
        assert _eval("ok", required)["episode_id"] == before_episode, \
            "the ambient environment reached the episode identity"
    finally:
        if saved is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved

    # And the agent's argv basename -- the half a `PATH` perturbation cannot
    # reach -- is named by this fixture rather than by whoever launched it.
    # This cannot be written as an invariance: R4 *keeps* the basename on
    # purpose, so running the same Python as `python3.11` genuinely must move
    # `trace-`. The law is that the fixture, not the host, picks which name that
    # is. When `_stable_interpreter` took its documented symlink-less fallback
    # there is no fixture-chosen name to check, and that platform is the one
    # case this file cannot close.
    if INTERPRETER != sys.executable:
        assert os.path.basename(INTERPRETER) == "python3", \
            "the agent argv basename is host-determined, so episode- is too"


def test_toolchain_change_moves_episode():
    a = _eval("ok", ["citations", "patch", "tests", "identity"])
    task = _task(["citations", "patch", "tests", "identity"])
    b = E.eval_one(task, CONTENT, AGENT, REWARD_SPEC,
                   snapshot=_snapshot(),
                   extra_verifiers=ALL_PASS, platform="linux-x86_64",
                   toolchain={"profile": "cpython-3.11",
                              "resolved": {"python": {"version": "3.11.9"}}})
    assert a["episode_id"] != b["episode_id"]


def test_nonzero_exit_is_error_and_null_reward():
    # A non-allowed exit code is substrate failure: error / invalid / null reward,
    # never a false fail (exit-code semantics ruling).
    r = _eval("nonzero", ["citations", "patch", "tests", "identity"])
    assert r["status"] == R.STATUS_ERROR
    assert r["reward"] is None
    assert r["validity"] == R.INVALID


def test_malformed_json_result_never_crashes():
    # A result.json that parses to a JSON list (not an object) must not crash the
    # pipeline (blocker 7): it yields an empty finding scored as fail.
    r = _eval("listresult", ["citations", "patch"])
    assert r["status"] == R.STATUS_OK
    assert r["verification"]["citations"] == R.FAIL
    assert r["episode_id"].startswith("episode-")


# --- An unhashable submission is scored, not escalated -----------------------
#
# The candidate's own bytes reach the identity spine through `_finding_artifact`
# (`summary` and `citations` come verbatim out of its `result.json`, and
# `finding_id` is inside `identity._EPISODE_IDENTITY_KEYS`). Some of those bytes
# cannot be hashed, and plain `json.loads` produces them. The three laws below
# are the difference between that being a fact about the candidate and being a
# way out of the evaluation.

def test_an_unhashable_finding_is_scored_not_escalated():
    """A candidate cannot crash its own evaluation to avoid a bad score.

    `result.json` here is well-formed JSON — it parses — but it carries a lone
    surrogate and a `NaN`, so the finding cannot be canonicalized. That used to
    escape `eval_one` as an untyped `UnicodeEncodeError`: no receipt, no episode
    bundle, no reward, nothing for `compare` to read. A crashed run is not a bad
    run, it is an absent one, which is precisely what a candidate facing a bad
    score would want.

    §10a already says what this is: *a malformed agent output is a `fail`, never
    an `error`.* So the episode completes and the submission scores, and the
    routing is the point — `status == ok` (exit 0, a valid episode with a bad
    score), never `error` (exit 2, "the substrate could not answer").
    """
    r = _eval("unhashable", ["citations", "patch", "tests", "identity"])
    assert r["status"] == R.STATUS_OK, r          # not error: not the substrate
    assert r["validity"] == R.VALID
    assert r["verification"]["citations"] == R.FAIL
    assert r["verification"]["finding_completeness"] == R.FAIL
    assert r["reward"] is not None, "an unhashable submission escaped scoring"
    assert abs(r["reward"] - 0.25) < 1e-9         # the citations-fail cap
    assert r["episode_id"].startswith("episode-")
    assert r["outputs"]["finding_id"].startswith("finding-")


def test_an_unhashable_finding_scores_exactly_what_an_empty_one_scores():
    """Collapse is all-or-nothing, so garbage is not a way to launder an answer.

    The alternative fix — drop the citation that will not hash and keep the
    rest — pays the candidate: one good citation plus one unhashable one would
    lose the bad one and could then *pass* `citations`. Here the unhashable
    submission mints the same `finding-` as a result that was never an object at
    all, so the best it can do is what submitting nothing does.
    """
    unhashable = _eval("unhashable", ["citations", "patch"])
    nothing = _eval("listresult", ["citations", "patch"])
    assert unhashable["outputs"]["finding_id"] == nothing["outputs"]["finding_id"]
    assert unhashable["verification"]["citations"] == \
        nothing["verification"]["citations"] == R.FAIL


def test_a_hashable_finding_is_untouched_by_the_unhashable_guard():
    """The guard runs only where the spine refused. Nothing else moved.

    Stated as a law because the guard's whole claim is that it changes behaviour
    on exactly the inputs that used to raise: a well-formed finding must still
    mint the id it always minted, computed here from the same document
    `_finding_artifact` builds rather than pinned as a literal (a pinned hash
    would move with the fixture and stop being about the guard).
    """
    result = {"finding": {"summary": "spec/one.md line 2 contradicts src/mod.py",
                          "citations": [{"path": "spec/one.md", "start_line": 2,
                                         "end_line": 2, "quote": "beta"}]}}
    document = {
        "finding_version": "residency.finding.v1",
        "claims": [{"statement": result["finding"]["summary"],
                    "citations": result["finding"]["citations"]}],
    }
    got, want = E._finding_artifact(result)["finding_id"], I.finding_id(document)
    assert got == want, "the guard moved a well-formed finding: %s != %s" % (got, want)


# --- ...and the same hack one layer earlier ---------------------------------
#
# The three laws above close the route through `_finding_artifact`: bytes that
# `json.loads` accepted and the identity spine refused. This one closes the
# route through `runner.run_agent`, where `json.loads` refuses the bytes itself
# and the run dies before any finding is built. The submission never reaches
# `_finding_artifact`, so the guard above cannot see it; the outcome the
# candidate buys is identical (exit 1, no receipt, nothing persisted) and so is
# the ruling that closes it (§10a: a malformed agent output is a `fail`).

def test_a_pathologically_nested_result_is_scored_not_escalated():
    """A candidate cannot crash the *decoder* to avoid being scored either.

    `result.json` here is valid RFC 8259 JSON with no syntax error in it —
    200 000 nested arrays, 400 kB of ordinary bytes any agent can write. It does
    not parse: `json.loads` raises `RecursionError`, a `RuntimeError`, which the
    runner's `(ValueError, UnicodeDecodeError)` clause did not catch. Measured
    before the fix through the committed `residency-demo` bundle: `trvs eval-one`
    exited 1 with a traceback, printed no receipt, and wrote nothing under
    `--output`.

    The routing is the point, exactly as for the unhashable case: `status == ok`
    (a valid episode carrying a bad score), never `error` (exit 2, "the substrate
    could not answer"). A JSON document too deep for the decoder is the agent's
    answer, not the substrate being unavailable.
    """
    r = _eval("deepresult", ["citations", "patch", "tests", "identity"])
    assert r["status"] == R.STATUS_OK, r          # not error: not the substrate
    assert r["validity"] == R.VALID
    assert r["verification"]["citations"] == R.FAIL
    assert r["verification"]["finding_completeness"] == R.FAIL
    assert r["reward"] is not None, "an undecodable submission escaped scoring"
    assert abs(r["reward"] - 0.25) < 1e-9         # the citations-fail cap
    assert r["episode_id"].startswith("episode-")
    assert r["outputs"]["finding_id"].startswith("finding-")


def test_a_pathologically_nested_result_scores_what_an_empty_one_scores():
    """One undecodable-submission price, shared with every other malformed one.

    `listresult` (parses, wrong type), `unhashable` (parses, cannot be sealed)
    and `deepresult` (does not parse at all) are three different failures of the
    same contract, and they must all mint the same empty `finding-`. If the new
    route collapsed to anything else it would be a distinguishable state, and a
    distinguishable state is a lever: submitting garbage would say something
    about the candidate that submitting nothing does not.
    """
    deep = _eval("deepresult", ["citations", "patch"])
    nothing = _eval("listresult", ["citations", "patch"])
    unhashable = _eval("unhashable", ["citations", "patch"])
    assert deep["outputs"]["finding_id"] == nothing["outputs"]["finding_id"] \
        == unhashable["outputs"]["finding_id"]
    assert deep["verification"]["citations"] == \
        nothing["verification"]["citations"] == R.FAIL


# --- ...and the same hack through the evaluator's own observation ------------
#
# The five laws above close both routes through the candidate's *submission*.
# This one closes the route through the evaluator's *rescan*: `runner._scan`
# walks the workspace after the run, and a POSIX filename is bytes, so a file the
# agent created as `b"evil\xff.txt"` comes back surrogate-escaped and lands in
# `files_created_digest`. The candidate has to write no malformed byte at all —
# one `open()` call, and `run_agent` raised before it returned.

def test_an_undecodable_filename_is_scored_not_escalated():
    """A candidate cannot crash its own evaluation by naming a file, either.

    The `badname` stub submits the *same valid answer* the `ok` stub does and
    additionally creates one file whose name is not UTF-8. Measured before the
    fix, through the committed `residency-demo` bundle: `trvs eval-one` exited 1
    with `IdentityError [CANONICAL_ENCODING]`, printed no receipt and wrote
    nothing under `--output`.

    The routing follows §10a exactly as for the two submission routes: the
    process ran, exited 0, and produced everything the evaluator asked for, so
    nothing about the substrate was unavailable. `status == ok`, never `error`.
    """
    r = _eval("badname", ["citations", "patch", "tests", "identity"])
    assert r["status"] == R.STATUS_OK, r
    assert r["validity"] == R.VALID
    assert r["reward"] is not None, "an undecodable filename escaped scoring"
    assert r["episode_id"].startswith("episode-")


def test_an_undecodable_filename_costs_the_answer_nothing_and_hides_nothing():
    """The score is untouched; the evidence is not. Both halves are the law.

    Unlike the three malformed-submission routes, this one does not collapse to
    anything: the agent's *answer* was perfectly good and nothing about a badly
    named scratch file makes it worse, so the reward must be exactly the `ok`
    reward. But the episode is not the same episode — the file is recorded, so
    the trace's `files_created_digest` differs, and `trace_id` is inside
    `identity._EPISODE_IDENTITY_KEYS`. An `episode-` that did *not* move would
    mean the evidence had quietly dropped the file, which is the hack this fix
    is trading against: a candidate writing files the record does not show.
    """
    required = ["citations", "patch", "tests", "identity"]
    ok = _eval("ok", required)
    bad = _eval("badname", required)
    assert bad["reward"] == ok["reward"]
    assert bad["verification"] == ok["verification"]
    assert bad["outputs"] == ok["outputs"]          # same finding, same patch
    assert bad["episode_id"] != ok["episode_id"], \
        "the badly named file left no trace in the episode identity"


def test_an_identity_code_does_not_say_whose_fault_it_is():
    """Why `_finding_artifact` catches `IdentityError` by class, not by `code`.

    The collapse handler absorbs every `ValueError`, which includes every typed
    refusal `identity.py` has and every one it will ever have. The tempting
    narrowing is to catch only an enumerated set of `code`s, so an
    evaluator-side refusal surfaces as a bug instead of being scored as
    somebody's bad finding.

    It cannot be done, and this is the disproof: `CANONICAL_ENCODING` is raised
    both by a candidate's lone surrogate inside a finding *and* by the
    evaluator's own workspace inventory meeting a non-UTF-8 filename — the very
    defect fixed in `runner._evidence_name`. One code, two blame-holders. A
    `code` names the law that was broken, never who broke it, so a code
    allowlist could not have partitioned even the case that motivated it; and it
    would fail *open* — a future code not on the list escapes `eval_one`, which
    is the crashed-episode hack all over again.

    Attribution comes instead from the narrowness of the guarded input, which
    the second half asserts: the handler collapses a finding built from the
    candidate's own bytes, and it is reached, so it is not dead code.
    """
    from_finding = from_inventory = None
    try:
        I.canonical_bytes({"claims": [{"statement": "\ud800"}]})
    except I.IdentityError as exc:
        from_finding = exc.code
    try:                                   # the shape `runner._scan` used to emit
        I.canonical_bytes({"evil\udcff.txt": "sha256:00"})
    except I.IdentityError as exc:
        from_inventory = exc.code
    assert from_finding == from_inventory == I.CANONICAL_ENCODING, \
        (from_finding, from_inventory)

    # The guard is live: a candidate-owned surrogate still reaches it and still
    # collapses, so the class catch is doing work rather than sitting unused.
    collapsed = E._finding_artifact({"finding": {"summary": "\ud800",
                                                 "citations": []}})
    assert collapsed == E._finding_artifact({})


def test_the_receipt_does_not_say_why_a_finding_is_empty():
    """The docstring used to claim more than the receipt delivers.

    All four ways to end up with an empty finding — never an object, a lone
    surrogate, a `NaN`, an undecodable document — mint the *byte-identical*
    empty `finding-`, and the receipt carries no other slot that could hold the
    difference (every key it has is inside `identity._EPISODE_IDENTITY_KEYS`, so
    adding one would move every episode id ever minted). That indistinguishability
    is required by `test_..._scores_what_an_empty_one_scores` above: a
    distinguishable state is a lever.

    So the prose was wrong, not the behaviour. What actually survives is
    *evidence*: `trace.result_file_digest` pins the exact bytes the agent wrote,
    and a missing file digests as the empty string. Available to an auditor,
    unavailable to anything scoring.
    """
    kinds = {mode: _evaluate(mode, ["citations", "patch"])
             for mode in ("listresult", "unhashable", "deepresult", "nooutput")}
    ids = {r["receipt"]["outputs"]["finding_id"] for r in kinds.values()}
    assert len(ids) == 1, ids                       # the receipt cannot tell them apart

    # The receipt seals only `trace_id`; the digest lives in the trace evidence
    # the bundle persists, which is `evaluate`'s second half.
    digests = {mode: r["artifacts"]["trace"]["events"][0]["result_file_digest"]
               for mode, r in kinds.items()}
    assert len(set(digests.values())) == len(digests), digests
    empty = "sha256:" + hashlib.sha256(b"").hexdigest()
    assert digests["nooutput"] == empty             # "wrote nothing" is its own digest


def test_patched_tests_regression_hits_040_cap():
    # Wire the REAL tests verifier: baseline passes the check, the ok patch changes
    # "return 1" -> "return 2" so the patched tree regresses -> tests fail -> 0.40.
    # Deliberately the real `sys.executable`, NOT `INTERPRETER`. A `test_plan`
    # argv is hashed into `task-` verbatim -- `canonicalize_task` drops only
    # `task_id`, and nothing basenames it the way `runner._normalize_command`
    # (R4) basenames the *agent* argv. `INTERPRETER` lives under a per-run
    # `mkdtemp`, so routing this through it would make `task-` differ on every
    # invocation: strictly worse than a path that is at least fixed per host.
    # This law pins a reward, not an id, so neither is load-bearing here -- but
    # a `test_plan` is the one place in this file a host-stable `task-` would
    # need a fixed-location toolchain rather than the symlink above.
    check = [sys.executable, "-c",
             "import sys;sys.exit(0 if open('src/mod.py').read().strip()=="
             "'return 1' else 1)"]
    task = _task(["citations", "patch", "tests", "identity"])
    task["test_plan"] = {"commands": [{"argv": check, "cwd": "."}]}
    r = E.eval_one(
        task, CONTENT, AGENT, REWARD_SPEC,
        snapshot=_snapshot(),
        extra_verifiers={"tests": SV.tests_verifier, "identity": _pass_identity},
        platform="linux-x86_64", toolchain=TOOLCHAIN,
    )
    assert r["verification"]["tests"] == R.FAIL
    assert r["status"] == R.STATUS_OK
    assert abs(r["reward"] - 0.40) < 1e-9  # tests fail cap


def test_false_declared_snapshot_id_is_rejected():
    snap = _snapshot()
    snap["snapshot_id"] = "snap-fixture"  # a lie
    try:
        E.eval_one(_task(["patch"]), CONTENT, AGENT, REWARD_SPEC,
                   snapshot=snap,
                   extra_verifiers=ALL_PASS)
    except ADM.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a false snapshot_id")


def test_content_snapshot_mismatch_is_rejected():
    tampered = dict(CONTENT, **{"src/mod.py": "return 42\n"})
    try:
        E.eval_one(_task(["patch"]), tampered, AGENT, REWARD_SPEC,
                   snapshot=_snapshot(),
                   extra_verifiers=ALL_PASS)
    except ADM.AdmissionError:
        return
    raise AssertionError("expected AdmissionError when content != sealed subject")


def test_task_referencing_wrong_valid_reward_is_rejected():
    # A task whose reward_id is itself a valid rew-… but names a DIFFERENT reward
    # than the one supplied must be rejected (cross-bind), never scored against it.
    other_reward = dict(REWARD_SPEC, aggregation="weighted")  # a different valid spec
    task = _task(["patch"])
    task["reward_id"] = I.reward_id(other_reward)  # valid id, wrong reference
    task["task_id"] = I.task_id(task)
    try:
        E.eval_one(task, CONTENT, AGENT, REWARD_SPEC,
                   snapshot=_snapshot(),
                   extra_verifiers=ALL_PASS)
    except ADM.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a mis-referenced reward_id")


def test_task_referencing_wrong_valid_snapshot_is_rejected():
    # A task whose subject.snapshot_id is a valid snap-… for a DIFFERENT subject
    # than the one supplied must be rejected (cross-bind).
    other = {"snapshot_version": S.SNAPSHOT_VERSION,
             "files": {"spec/one.md": _content_hash("different\n")},
             "exclusions": [], "binary_paths": [], "file_modes": {},
             "base_revision": None, "visible_config": {}}
    other["snapshot_id"] = I.snapshot_id(other)
    task = _task(["patch"])
    task["subject"] = {"snapshot_id": other["snapshot_id"]}  # valid id, wrong subject
    task["task_id"] = I.task_id(task)
    try:
        E.eval_one(task, CONTENT, AGENT, REWARD_SPEC,
                   snapshot=_snapshot(),
                   extra_verifiers=ALL_PASS)
    except ADM.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a mis-referenced snapshot_id")


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
