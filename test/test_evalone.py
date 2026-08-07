"""End-to-end battery for the eval-one orchestrator (traaviis.evalone, RFC §10).

Drives the full one-shot pipeline against the deterministic stub agent: snapshot
-> controlled run -> trace -> finding/patch -> verifiers -> reward -> episode
receipt. Pins the happy path (valid full-reward episode), a bad-patch fail with
the §7 floor, a tampered (policy-violation) invalid episode, a required-but-
deferred verifier producing invalid-config, and episode-identity stability vs.
movement.

V1-V9 close the fourth route into the grade-erasure class: a verifier that raises
(or returns a malformed result) is classified as `error` evidence and the episode
is emitted and reopenable, instead of escaping uncaught and leaving nothing
behind. B1/B4/B5 pin the identity-versioning cutover.

Runs with pytest, or standalone: `python3 test/test_evalone.py`.
"""

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping as Mapping_ABC

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


# --------------------------------------------------------------------------- #
# B1 identity versioning: the minting default, and the cost of moving it       #
# --------------------------------------------------------------------------- #

REPO = os.path.dirname(HERE)
GOLDEN_EPISODE = os.path.join(
    REPO, "examples", "eval-one", "episodes",
    "episode-42d0bb07e5f83e9e57518bf5cd3717e2a1e3aa45aa5821e36ac19206a3d73299")


def _isolated_traaviis(*edits):
    """A private copy of `traaviis/` with literal source edits, importable.

    Same instrument as `test_canonical.isolated_package`, duplicated rather than
    imported because these two batteries do not import each other and a test
    helper shared across files is a coupling that outlives the reason for it.
    Returns `(package module, cleanup callable)`.
    """
    import shutil
    _COUNTER[0] += 1
    name = "traaviis_cutover_%d" % _COUNTER[0]
    root = tempfile.mkdtemp(prefix="trvs-cutover-")
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
        package = __import__(
            name, fromlist=["episode_bundle", "evalone", "wiring"])
        for submodule in ("episode_bundle", "evalone", "wiring"):
            __import__(name + "." + submodule)
    except Exception:
        cleanup()
        raise
    return package, cleanup


_COUNTER = [0]


class Skip(Exception):
    """A law whose substrate is not present in this environment.

    Same shape as `test_canonical.Skip` and `test_kernel.Skip`. It exists here
    because the two cutover laws replay the golden episode, which scored an
    `identity` signal, and that signal needs the Forge engine. With no engine
    the replay answers `not_applicable` where the receipt says `pass` and the
    bundle reports a mismatch -- a true statement about a runtime that cannot
    answer, and not a fact about identity versioning.
    """


def _engine_backed_verifiers_or_skip(wiring_module):
    """`_episode_verifiers`, refusing to pretend when the engine is absent.

    `accept_packet.py` runs the battery twice, once with `TRVS_FORGE_DIR` unset
    in a temporary extraction with no TRVM checkout above it. That is a real
    absence, and G5 requires every engine-dependent law to skip rather than
    fail -- *and* requires at least one to skip, on the grounds that a law which
    sails through an absent engine was never testing it.
    """
    wired = _episode_verifiers(wiring_module)
    if "identity" not in wired:
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    return wired


def _episode_verifiers(wiring_module):
    """The verifier set a replay is offered, built without going through the CLI.

    ``cmd_verify_episode`` offers everything the runtime has, because a replay
    does not know which signals an episode scored until it opens the bundle.
    That set is built here from ``wiring.default_registry`` directly rather than
    through ``cli._wire_episode_verifiers``: a law that reaches through a CLI
    helper to obtain a runtime dependency is testing the command layer's wiring
    as a side effect, and it breaks the next time that helper is refactored.
    Same registry, same implementations, one fewer thing depended on.
    """
    registry = wiring_module.default_registry(None)
    return {signal: registry.get(signal) for signal in registry.available()}


def test_b1_the_minting_default_is_v1_and_the_golden_episode_closes():
    """The cutover decision, pinned where it is taken.

    `EPISODE_VERSION` governs **newly minted** receipts only; legacy receipts
    declare what they were minted with and verify under it forever. This law
    pins that the default is still `v1` and that the shipped golden episode --
    the one whose id is quoted in memos and shipped inside both `dist/` packets
    -- still closes with every signal answered and reward 1.0.

    Both halves matter. The default being v1 is a decision; the golden episode
    closing is the evidence that the decision costs nothing to the artifacts
    that already exist.
    """
    if not os.path.isdir(GOLDEN_EPISODE):
        return                                  # not in this checkout
    from traaviis import episode_bundle as EB
    from traaviis import wiring
    assert E.EPISODE_VERSION == "traaviis.episode.v1"
    assert I.EPISODE_SCHEMES[E.EPISODE_VERSION] == I.SCHEME_LEGACY

    report = EB.verify_episode_bundle(
        GOLDEN_EPISODE,
        extra_verifiers=_engine_backed_verifiers_or_skip(wiring))
    assert report["outcome"] == EB.OUTCOME_CLOSED, report.get("checks")
    assert report["episode_id"] == os.path.basename(GOLDEN_EPISODE)
    assert report["checks"]["reward"]["ok"], report["checks"]["reward"]
    assert report["checks"]["receipt"]["ok"], report["checks"]["receipt"]
    signals = report["checks"]["signals"]
    assert len(signals) == 7, sorted(signals)
    assert all(entry["ok"] for entry in signals.values()), signals


def test_b4_a_v1_bundle_still_closes_under_a_v2_minting_default():
    """**The assertion the whole cutover rests on: replay follows the document.**

    `build_receipt_v1` is shared by live evaluation and by verification replay --
    `episode_bundle.verify_episode_bundle` rebuilds the receipt through it and
    requires byte equality with the stored one. So the version replay stamps
    decides whether a sealed bundle still closes. It must be the version the
    *stored receipt declares*, never the version this build happens to mint
    today, and `episode_bundle.py` passes exactly that.

    This law flips `EPISODE_VERSION` to v2 in an isolated copy of the package --
    the cutover, performed -- and requires the shipped golden v1 bundle to close
    anyway, with its id unmoved, while new episodes under that same build are
    sealed under RFC 8785. That is the property: **the minting default and the
    verification of history are independent.**

    Non-vacuity is by source-level deletion, pointed at the one argument that
    makes it true: a second isolated copy has the same flip *and* the
    `episode_version=` argument removed from the `build_receipt_v1` call, and
    under it the golden bundle must fail with "derived receipt differs from
    stored". If it closes anyway, this law is measuring nothing and says so.

    **Superseded form, recorded rather than replaced.** ~~`test_b4_flipping_the_
    default_alone_breaks_every_sealed_episode`~~ -- an earlier version of this
    law asserted the *defect*: that flipping `EPISODE_VERSION` alone made every
    sealed v1 bundle stop closing. That was true when written, because the
    argument below did not yet exist; it named the prerequisite for the cutover
    while the prerequisite was still outstanding. It inverted the moment
    `episode_bundle.py` started passing the stored version, and a law that
    pins the presence of a bug either fails on the day the bug is fixed or,
    worse, gets "repaired" later by someone who does not realise it had flipped
    sides. So the body was rewritten to assert the property rather than the
    defect, with the deletion proof aimed at the fix. Same shape as C18 in
    `test_canonical.py`, which had asserted the *absence* of a domain check and
    had to be rewritten two-sided rather than deleted when checks were added.

    What survives the supersession, and is the reason to keep reading the old
    claim: the failure it described is real and is the worst shape this product
    can produce -- a verifier reporting a mismatch on evidence nobody touched,
    because it re-derived under its own current defaults instead of under the
    document's. The deletion half below reproduces exactly that, on purpose.
    """
    if not os.path.isdir(GOLDEN_EPISODE):
        return
    flip = ('EPISODE_VERSION = "traaviis.episode.v1"',
            'EPISODE_VERSION = "traaviis.episode.v2"')
    drop_argument = (
        "episode_bundle.py",
        '            episode_version=receipt.get("episode_version"),\n',
        "")

    # (a) the cutover, performed: a v1 bundle must still close under a v2 default
    cutover, cleanup = _isolated_traaviis(("evalone.py",) + flip)
    try:
        assert cutover.evalone.EPISODE_VERSION == "traaviis.episode.v2", \
            "the edit did not apply; this law would prove nothing"
        report = cutover.episode_bundle.verify_episode_bundle(
            GOLDEN_EPISODE, extra_verifiers=_engine_backed_verifiers_or_skip(cutover.wiring))
        assert report["outcome"] == cutover.episode_bundle.OUTCOME_CLOSED, \
            report.get("checks")
        assert report["checks"]["receipt"]["ok"], report["checks"]["receipt"]
        assert report["episode_id"] == os.path.basename(GOLDEN_EPISODE), \
            "the v1 episode's id moved under a v2 minting default"
        # and a *new* episode under that build really is sealed under the JCS
        # profile. Deliberately a *literal*, not `identity.SCHEME_RFC8785` --
        # comparing the constant to itself would assert nothing, and the point
        # here is that v2 maps to the JCS scheme at all. The literal moved once
        # already: `rfc8785-v1` overclaimed unrestricted RFC 8785 numerics, and
        # the profile was renamed when the admitted numeric domain was closed to
        # safe integers. That rename is identity-moving, which is pinned on its
        # own by `test_canonical.py`'s C52; this law only pins the mapping.
        assert cutover.evalone.identity.EPISODE_SCHEMES[
            cutover.evalone.EPISODE_VERSION] == "JCS_CLOSED_NUMBER_PROFILE_V1"
    finally:
        cleanup()

    # (b) the same flip with the one argument deleted from the source
    blind, cleanup = _isolated_traaviis(("evalone.py",) + flip, drop_argument)
    try:
        report = blind.episode_bundle.verify_episode_bundle(
            GOLDEN_EPISODE, extra_verifiers=_engine_backed_verifiers_or_skip(blind.wiring))
        assert report["outcome"] != blind.episode_bundle.OUTCOME_CLOSED, \
            "removing `episode_version=` from the replay did NOT break the " \
            "golden bundle -- (a) is therefore not measuring that argument, " \
            "and this law proves nothing as written"
        assert not report["checks"]["receipt"]["ok"], report["checks"]["receipt"]
    finally:
        cleanup()


def test_b5_a_v2_receipt_is_minted_shaped_and_reproducible():
    """The builder emits a well-formed v2 receipt, through the real path.

    Not a hand-written dict: `_assemble_receipt` is the single point where a
    receipt is shaped, so a v2 receipt has to come out of it or the shape is
    untested. Checks that the `canonicalization` field appears only for the
    scheme that requires it, that the id is reproducible, and that the grammar
    is byte-for-byte the same shape as v1's.
    """
    parts = dict(
        substrate_profile="residency.repository.v1",
        task_id="task-" + "0" * 64, reward_id="rew-" + "1" * 64,
        snapshot_id="snap-" + "2" * 64, trace_id="trace-" + "3" * 64,
        outputs={"finding_id": None, "patch_id": None},
        verification={"tests": "pass"}, verification_evidence={},
        verifier_versions={}, execution_facts={},
        score={"reward": 1.0, "status": "pass", "validity": "valid"},
    )
    v1 = E._assemble_receipt(**parts)
    v2 = E._assemble_receipt(episode_version="traaviis.episode.v2", **parts)

    assert v1["episode_version"] == "traaviis.episode.v1"
    assert "canonicalization" not in v1, \
        "a v1 receipt grew a field no v1 receipt on disk has"
    assert v2["episode_version"] == "traaviis.episode.v2"
    assert v2["canonicalization"] == I.SCHEME_RFC8785
    assert list(v2)[:2] == ["episode_version", "canonicalization"]

    for receipt in (v1, v2):
        prefix, _, body = receipt["episode_id"].partition("-")
        assert prefix == "episode" and len(body) == 64
        assert all(c in "0123456789abcdef" for c in body)
        without = {k: v for k, v in receipt.items() if k != "episode_id"}
        assert I.episode_id(without) == receipt["episode_id"], "not reproducible"
    assert v1["episode_id"] != v2["episode_id"]

    # an unknown version is refused at the seal, not stamped and forgotten
    try:
        E._assemble_receipt(episode_version="traaviis.episode.v7", **parts)
    except I.IdentityError as ex:
        assert ex.code == I.EPISODE_SCHEME_UNKNOWN, ex.code
    else:
        raise AssertionError("the builder minted a receipt under an unknown scheme")


# --------------------------------------------------------------------------- #
# V1-V10: a verifier that fails must not erase the episode                     #
# --------------------------------------------------------------------------- #
#
# The fourth route into the grade-erasure class. The first three were the
# candidate's own bytes reaching the identity spine (a malformed citation, a
# 200 000-deep result, a non-UTF-8 filename the evaluator's own rescan picked
# up); each was closed by *classifying* the input instead of letting the refusal
# escape `eval_one`. This one is the checker rather than the candidate: before
# `evalone.resolve_signal`, `{sig: verifier(context) for sig in signal_ids}` had
# no exception boundary and none upstream, so a verifier that raised took the
# whole episode with it -- no receipt, no bundle, nothing persisted -- and
# `batch`/`compare` issue a pair refusal when nothing was persisted. A bad score
# that never existed cannot be argued with.
#
# The acceptance bar, in one sentence: a verifier can throw, EVERY OTHER
# VERIFIER STILL REPORTS, the episode becomes error/invalid with reward null,
# and the evidence bundle reopens to the same derived receipt.

_ALL_SIGNALS = ["citations", "patch", "tests", "identity", "finding_completeness"]
_SCORED_REQUIRED = ["citations", "patch", "tests", "identity"]


class VerifierBoom(Exception):
    """A verifier-owned failure with a stable, qualified type name.

    Defined at module scope on purpose: `_qualified_exception_type` reads
    `__module__` + `__qualname__`, and a class defined inside a function body
    would still be qualified but would read as `<locals>`-suffixed noise in the
    sealed evidence. Nothing about it is host-dependent either way.
    """


def _boom_tests(context):
    raise VerifierBoom("the checker fell over")


def _value_error_tests(context):
    raise ValueError("a builtin, to pin the bare-name spelling")


def _memory_error_tests(context):
    raise MemoryError()


def _interrupt_tests(context):
    raise KeyboardInterrupt()


def _not_a_result_tests(context):
    return "pass"                      # a bare string: not a VerifierResult


def _unknown_state_tests(context):
    return VerifierResult("probably")  # a VerifierResult, but not one of the four


def _detail_not_mapping_tests(context):
    return VerifierResult(R.PASS, ["not", "a", "mapping"])


def _unhashable_detail(VR):
    """A verifier returning a *valid* state with a detail the spine refuses.

    Parameterised by the `VerifierResult` class so the same factory works inside
    an isolated package copy, where `traaviis_probe_N.vcontext.VerifierResult` is
    a different class object and `isinstance` against this process's would fail
    for the wrong reason.

    This is the violation that is not merely hygiene. `NaN` is a legal Python
    float and an ordinary thing for a verifier to put in a detail; it reaches
    `_evidence_ref` -> `identity.canonical_bytes`, which refuses it
    (`CANONICAL_NON_FINITE`) -- **inside `build_receipt_v1`, after the seam and
    outside every guard.** Without the protocol check it erases the episode
    exactly as a raising verifier did, one function later.
    """
    def verifier(context):
        return VR(R.PASS, {"score": float("nan")})
    verifier.version = "residency.tests.v1"
    return verifier


for _fn in (_boom_tests, _value_error_tests, _memory_error_tests, _interrupt_tests,
            _not_a_result_tests, _unknown_state_tests, _detail_not_mapping_tests):
    # A *required* signal demands a wired verifier carrying an implementation
    # version, or F4 preflight refuses the config and the agent never runs. These
    # are wired and versioned; what they are not is well-behaved. That is the
    # whole point: the failures below are post-preflight, which is precisely the
    # window in which nothing used to survive.
    _fn.version = "residency.tests.v1"


def _run_with(extra, mode="ok", required=None):
    """One episode with a chosen verifier set. Returns `(EvaluationRunV1, task)`.

    Returns the task too because writing a bundle needs the *same* task object
    the receipt was sealed against, and `_evaluate` above builds one internally.
    """
    task = _task(list(_SCORED_REQUIRED if required is None else required))
    task["agent_run_policy"]["environment"]["TRAAVIIS_STUB_MODE"] = mode
    run = E.evaluate(task, CONTENT, AGENT, REWARD_SPEC, snapshot=_snapshot(),
                     extra_verifiers=extra, platform="linux-x86_64",
                     toolchain=TOOLCHAIN)
    return run, task


def _bundle(run, task, extra, name):
    """Persist the run and re-verify it. Returns `(path, report)`.

    `write_episode_bundle` *itself* stages the tree, runs the full
    `verify_episode_bundle` over it, and refuses to publish anything that does
    not close -- so merely getting a path back is already half the round-trip
    proof. The explicit re-verification afterwards is the other half: it opens
    the published directory cold, exactly as a third party would.
    """
    from traaviis import episode_bundle as EB
    root = os.path.join(_tmp(), "bundles", name)
    path = EB.write_episode_bundle(
        run, task=task, reward_spec=REWARD_SPEC, snapshot=_snapshot(),
        content=CONTENT, dest_root=root, extra_verifiers=extra)
    return path, EB.verify_episode_bundle(path, extra_verifiers=extra)


def test_v1_a_raising_verifier_leaves_every_other_verifier_reporting():
    """The acceptance bar's first two clauses, on the real pipeline.

    `tests` raises. The episode still exists, and the four verifiers that did not
    raise still say what they found -- `citations`, `patch` and
    `finding_completeness` from the pure module, `identity` from an injected
    stand-in. A boundary that caught the exception but abandoned the remaining
    signals would satisfy "no crash" and still be wrong: it would delete evidence
    about a *candidate* in order to describe a fault in the *evaluator*, and it
    would leave `reward.score`'s totality precondition violated. TAP's `Bail
    out!` is that convention, and it is the wrong one for a rubric.
    """
    run, _task_doc = _run_with({"tests": _boom_tests, "identity": _pass_identity})
    r = run["receipt"]

    assert r["verification"]["tests"] == R.ERROR
    for sig in ("citations", "patch", "finding_completeness", "identity"):
        assert r["verification"][sig] == R.PASS, (sig, r["verification"])
    assert r["verification"]["native"] == R.NOT_APPLICABLE

    # ...and the episode is error/invalid with a null reward (reward.score F2).
    assert r["status"] == R.STATUS_ERROR
    assert r["validity"] == R.INVALID
    assert r["reward"] is None, "an error episode must score None, never 0"

    # The receipt was emitted, the trace retained, the outputs still named.
    assert r["episode_id"].startswith("episode-")
    assert r["trace_id"].startswith("trace-")
    assert r["outputs"]["finding_id"].startswith("finding-")
    assert r["outputs"]["patch_id"].startswith("patch-")
    assert run["artifacts"] is not None
    assert run["artifacts"]["trace"]["trace_id"] == r["trace_id"]

    # Every declared signal sealed evidence, including the one that failed.
    evidence = run["artifacts"]["verifier_evidence"]
    assert sorted(evidence) == sorted(_ALL_SIGNALS)
    assert sorted(r["verification_evidence"]) == sorted(_ALL_SIGNALS)

    detail = evidence["tests"]["detail"]
    assert detail["error_origin"] == E.ERROR_ORIGIN_VERIFIER_EXCEPTION
    assert detail["error_code"] == E.ERROR_CODE_VERIFIER_RAISED
    assert detail["exception_type"] == E._qualified_exception_type(VerifierBoom)
    assert detail["verifier_implementation"] == "residency.tests.v1"


def test_v2_a_builtin_exception_seals_its_bare_name():
    """`ValueError`, not `builtins.ValueError`.

    The spelling is the one JUnit XML has used for `<error type=…>` since Ant
    ("the full class name of the exception") and SARIF for `exception.kind`, with
    the builtins module elided because prefixing every ordinary exception with
    `builtins.` carries no information and would differ across Python 2/3
    spellings of the same module.
    """
    run, _t = _run_with({"tests": _value_error_tests, "identity": _pass_identity})
    detail = run["artifacts"]["verifier_evidence"]["tests"]["detail"]
    assert detail["exception_type"] == "ValueError"
    assert "." not in detail["exception_type"]
    # and a package-defined exception keeps its module path
    assert "." in E._qualified_exception_type(E.UnsupportedPolicyError)


def test_v3_a_raise_and_a_malformed_return_are_different_facts():
    """`verifier_exception` and `verifier_protocol` must not collapse.

    They are different failures with different remedies: a raise means the
    checker broke *during* its procedure, a malformed return means it broke its
    *contract* while believing it had succeeded. The second is the more dangerous
    of the two -- one of its shapes (`detail_not_canonical`) would have killed
    receipt construction downstream, and another (`unknown_state`) would have
    reached `reward.score` as an unscoreable map.

    Pinned three ways: different origin, different code, and different sealed
    evidence digests, so the distinction survives into `episode-…` and is not
    merely a nicer log line.
    """
    raised, _t = _run_with({"tests": _boom_tests, "identity": _pass_identity})

    seen = {}
    for label, verifier in (
        ("not_a_verifier_result", _not_a_result_tests),
        ("unknown_state", _unknown_state_tests),
        ("detail_not_mapping", _detail_not_mapping_tests),
        ("detail_not_canonical", _unhashable_detail(VerifierResult)),
    ):
        run, _t2 = _run_with({"tests": verifier, "identity": _pass_identity})
        detail = run["artifacts"]["verifier_evidence"]["tests"]["detail"]
        assert run["receipt"]["verification"]["tests"] == R.ERROR, label
        assert run["receipt"]["status"] == R.STATUS_ERROR, label
        assert run["receipt"]["reward"] is None, label
        assert detail["error_origin"] == E.ERROR_ORIGIN_VERIFIER_PROTOCOL, label
        assert detail["error_code"] == E.ERROR_CODE_INVALID_VERIFIER_RESULT, label
        assert detail["violation"] == label, (label, detail)
        # ...and the siblings answered even here
        assert run["receipt"]["verification"]["citations"] == R.PASS, label
        seen[label] = run["receipt"]["verification_evidence"]["tests"]["digest"]

    assert sorted(seen) == sorted(E.RESULT_VIOLATIONS), \
        "every named violation must be reachable, or the enumeration is fiction"

    raised_detail = raised["artifacts"]["verifier_evidence"]["tests"]["detail"]
    assert raised_detail["error_origin"] != E.ERROR_ORIGIN_VERIFIER_PROTOCOL
    assert raised_detail["error_code"] != E.ERROR_CODE_INVALID_VERIFIER_RESULT
    raised_digest = raised["receipt"]["verification_evidence"]["tests"]["digest"]
    assert raised_digest not in seen.values()
    # the four violations are distinguishable from each other, not just from a raise
    assert len(set(seen.values())) == 4, seen


def test_v4_no_message_no_path_and_no_traceback_reaches_identity():
    """Identity hygiene, proved by moving the message and watching the id hold.

    An exception message is host- and run-dependent -- it routinely carries
    absolute paths, temp-directory names, pids and object addresses -- so sealing
    one would make `episode-…` a function of the machine. Two episodes are run
    whose verifiers differ ONLY in the text they raise, and the two receipts must
    be byte-identical. That is a stronger statement than "the string does not
    appear in the receipt", because it also rules out any digest of the message
    having been folded in.

    This is why no *sanitized-message digest* was added, though the option was
    open. A digest is only worth sealing if it is provably host-independent, and
    that proof cannot be given: the set of host-dependent substrings an arbitrary
    exception message can carry is open-ended, so any sanitizer is a heuristic,
    and a heuristic that misses one case moves an episode id nondeterministically
    -- the exact failure the hygiene rule exists to prevent. Prior art does not
    rescue it either: SARIF keeps `exception.stack`, `threadId` and `timeUtc` on
    the *notification*, structurally outside the `result` that gets
    fingerprinted, and nothing published normalizes error text for digesting. The
    full message and traceback go where SARIF puts them -- see V5.
    """
    import uuid

    def _noisy(token):
        def verifier(context):
            raise VerifierBoom(
                "failed at %s (pid %d) token=%s"
                % (os.path.abspath(__file__), os.getpid(), token))
        verifier.version = "residency.tests.v1"
        return verifier

    secret_a = uuid.uuid4().hex
    secret_b = uuid.uuid4().hex
    a, _t1 = _run_with({"tests": _noisy(secret_a), "identity": _pass_identity})
    b, _t2 = _run_with({"tests": _noisy(secret_b), "identity": _pass_identity})

    assert a["receipt"]["episode_id"] == b["receipt"]["episode_id"], \
        "the exception message moved the episode id"
    assert I.canonicalize_episode(a["receipt"]) == I.canonicalize_episode(b["receipt"])
    assert (a["artifacts"]["verifier_evidence"]["tests"]
            == b["artifacts"]["verifier_evidence"]["tests"])

    # ...and, separately, none of the volatile text is anywhere in what is sealed.
    sealed = json.dumps([a["receipt"], a["artifacts"]["verifier_evidence"]])
    for volatile in (secret_a, os.path.abspath(__file__), str(os.getpid()),
                     "Traceback", "the checker fell over"):
        assert volatile not in sealed, volatile

    # What IS sealed is exactly four stable facts, and nothing else.
    assert sorted(a["artifacts"]["verifier_evidence"]["tests"]["detail"]) == [
        "error_code", "error_origin", "exception_type", "verifier_implementation"]


def test_v5_the_operator_gets_the_traceback_and_the_bundle_does_not():
    """Full diagnostics for a human, zero of them on disk.

    SARIF's placement, adopted: the volatile parts of a failure live on the
    notification, never on the result. Here they live in
    `artifacts["verifier_diagnostics"]`, which `episode_bundle._populate` does not
    write (and `_verify_tree_closure` would reject if something did, since the
    manifest must be the complete closure). So the operator can debug and the
    identity cannot move -- the split is enforced by two different mechanisms,
    not by remembering.
    """
    run, task_doc = _run_with({"tests": _boom_tests, "identity": _pass_identity})
    diag = run["artifacts"]["verifier_diagnostics"]
    assert sorted(diag) == ["tests"], "only the failing signal is diagnosed"
    assert diag["tests"]["error_origin"] == E.ERROR_ORIGIN_VERIFIER_EXCEPTION
    assert diag["tests"]["message"] == "the checker fell over"
    assert "Traceback (most recent call last)" in diag["tests"]["traceback"]
    assert "_boom_tests" in diag["tests"]["traceback"]

    path, _report = _bundle(run, task_doc, {"tests": _boom_tests,
                                            "identity": _pass_identity}, "v5")
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            with open(os.path.join(dirpath, name), "rb") as fh:
                blob = fh.read()
            assert b"Traceback" not in blob, os.path.join(dirpath, name)
            assert b"the checker fell over" not in blob, os.path.join(dirpath, name)

    # A clean episode carries an empty map rather than no key: a consumer should
    # not have to distinguish "no verifier failed" from "this build is older".
    clean, _t = _run_with(ALL_PASS)
    assert clean["artifacts"]["verifier_diagnostics"] == {}


def test_v6_the_evidence_bundle_reopens_to_the_same_derived_receipt():
    """**The acceptance bar's last clause.** Round-trip, cold, through the real bundle.

    `verify_episode_bundle` re-derives the whole receipt from the saved evidence
    through the same `build_receipt_v1` the live run used and requires byte
    equality -- so a replay that classified the raise differently, or that let it
    escape, cannot pass here. It could not even have got this far:
    `write_episode_bundle` verifies the *staged* tree before publishing, so an
    error episode whose replay crashed would be unpersistable, which is the
    grade-erasure defect one layer further down.

    That is why `episode_bundle` calls `evalone.resolve_signal` rather than
    carrying its own `v(context)`: one classification rule, two callers, no
    second copy to drift.
    """
    from traaviis import episode_bundle as EB
    extra = {"tests": _boom_tests, "identity": _pass_identity}
    run, task_doc = _run_with(extra)
    path, report = _bundle(run, task_doc, extra, "v6")

    assert report["outcome"] == EB.OUTCOME_CLOSED, report["checks"]
    assert report["episode_id"] == run["receipt"]["episode_id"]
    assert report["checks"]["receipt"]["ok"], report["checks"]["receipt"]
    assert report["checks"]["reward"]["ok"], report["checks"]["reward"]
    assert report["checks"]["episode_id"]["ok"], report["checks"]["episode_id"]

    entry = report["checks"]["signals"]["tests"]
    assert entry["replayed"] == R.ERROR and entry["receipt"] == R.ERROR
    assert entry["evidence_match"] is True, "the error evidence did not re-derive"
    assert all(e["ok"] for e in report["checks"]["signals"].values()), \
        report["checks"]["signals"]

    # The saved evidence file on disk really is the error evidence, and it is what
    # the receipt's digest pins.
    with open(os.path.join(path, "evidence", "verifiers", "tests.json"),
              encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["state"] == R.ERROR
    assert saved["detail"]["error_code"] == E.ERROR_CODE_VERIFIER_RAISED
    assert (E._evidence_ref(saved)["digest"]
            == run["receipt"]["verification_evidence"]["tests"]["digest"])

    # ...and the same holds for a malformed *return*, not only for a raise.
    extra2 = {"tests": _unhashable_detail(VerifierResult), "identity": _pass_identity}
    run2, task2 = _run_with(extra2)
    _p2, report2 = _bundle(run2, task2, extra2, "v6b")
    assert report2["outcome"] == EB.OUTCOME_CLOSED, report2["checks"]
    assert report2["checks"]["signals"]["tests"]["evidence_match"] is True


def test_v7_memory_error_is_caught_and_keyboard_interrupt_is_not():
    """The `MemoryError` ruling, and the `BaseException` line, in one law.

    **Divergence from precedent, recorded.** `runner._read_result`,
    `batch.load_candidate_set` and `bundle.read_manifest` all deliberately EXCLUDE
    `MemoryError` from their narrow clauses, on the grounds that whether a
    document exhausts memory is a fact about the host, so catching it would score
    the same submission differently on different machines. That argument turns on
    *what the catch produces*: there, a caught refusal becomes the empty finding,
    which the verifiers score `fail` -- a number. Here it produces `error`, which
    is this system's existing word for "the host could not answer" and which
    `reward.score` gives `reward = None`, never `0`; downstream aggregation drops
    `None` rather than averaging it in. So the same submission does not score
    differently on two machines -- it scores on one and declines to score on the
    other, which is true. The engine already does exactly that for a timeout, also
    a host fact, also sealed as `error`, in the same function.

    The precedent's second leg ("it would close nothing anyway": `fh.read()` had
    already loaded the file) also inverts. Here it closes the hole this change
    exists to close, and the hole is candidate-reachable: a verifier walks the
    finding, the patch and the patched tree, all built from bytes the candidate
    chose.

    `KeyboardInterrupt` is the other side of the line and stays uncaught.
    Interruption must interrupt; an operator pressing ^C must not silently mint
    and persist an `error` episode in which a verifier is recorded as having had
    an opinion.
    """
    run, _t = _run_with({"tests": _memory_error_tests, "identity": _pass_identity})
    r = run["receipt"]
    assert r["verification"]["tests"] == R.ERROR
    assert r["status"] == R.STATUS_ERROR and r["reward"] is None
    assert r["verification"]["citations"] == R.PASS
    detail = run["artifacts"]["verifier_evidence"]["tests"]["detail"]
    assert detail["exception_type"] == "MemoryError"
    assert detail["error_code"] == E.ERROR_CODE_VERIFIER_RAISED

    try:
        _run_with({"tests": _interrupt_tests, "identity": _pass_identity})
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError(
            "KeyboardInterrupt was swallowed; shutdown lost its meaning")


def test_v8_a_raising_verifier_is_not_an_invalid_configuration():
    """Ruled disposition, checked against the code rather than assumed.

    "A **required** signal with no wired implementation before execution is an
    invalid configuration; do not run" -- and `_required_config_error` implements
    exactly that and no more: it asks whether a verifier is wired and whether it
    declares an implementation version. A wired, versioned verifier that will
    later raise passes preflight, the agent runs, and the failure is a *runtime*
    error episode, not an invalid-config refusal. The two must not be confused:
    invalid config produces no artifacts at all (`artifacts is None`, nothing to
    persist), while an error episode produces the full evidence set. Only the
    second is reopenable, and only the second is what a raise should yield.
    """
    run, _t = _run_with({"tests": _boom_tests, "identity": _pass_identity})
    assert run["receipt"]["status"] == R.STATUS_ERROR
    assert run["receipt"]["status"] != R.STATUS_INVALID
    assert run["artifacts"] is not None
    assert run["receipt"]["execution_facts"] is not None

    # ...and the genuinely invalid config still refuses, for contrast.
    unwired, _t2 = _run_with({"identity": _pass_identity},
                             required=["citations", "patch", "tests"])
    assert unwired["receipt"]["status"] == R.STATUS_INVALID
    assert unwired["artifacts"] is None


def test_v9_the_laws_go_red_when_the_guard_is_deleted_from_the_source():
    """Non-vacuity, by source-level deletion in isolated copies of the package.

    Four probes, each removing exactly one thing and asserting the law that
    claims it inverts. Deletion rather than monkeypatching, because a patched
    module proves the test can be made to fail, not that the shipped source is
    what makes it pass.
    """
    import shutil  # noqa: F401  (kept beside _isolated_traaviis's own import)

    def _episode(pkg, extra, required=None):
        task = _task(list(_SCORED_REQUIRED if required is None else required))
        return pkg.evalone.evaluate(
            task, CONTENT, AGENT, REWARD_SPEC, snapshot=_snapshot(),
            extra_verifiers=extra, platform="linux-x86_64", toolchain=TOOLCHAIN)

    # (a) the boundary itself. `except ()` is a legal empty tuple that matches
    #     nothing, so the try/except stays syntactically intact and semantically
    #     absent -- the pre-fix behaviour exactly: the raise escapes `evaluate`,
    #     there is no receipt, and the grade is erased.
    no_boundary = (
        "evalone.py",
        "    except Exception as exc:  # noqa: BLE001 -- NOT BaseException; "
        "see the docstring",
        "    except () as exc:  # noqa: BLE001 -- NOT BaseException; "
        "see the docstring")
    pkg, cleanup = _isolated_traaviis(no_boundary)
    try:
        try:
            _episode(pkg, {"tests": _boom_tests, "identity": _pass_identity})
        except VerifierBoom:
            pass
        else:
            raise AssertionError(
                "the verifier exception did NOT escape with the boundary "
                "deleted -- V1 is measuring something other than that boundary")
    finally:
        cleanup()

    # (b) the siblings. Stop the loop at the first error and the verification map
    #     is no longer total, which `reward.score` refuses outright -- so
    #     "continue resolving the remaining signals" is load-bearing, not tidy.
    stop_early = (
        "evalone.py",
        "        results[sig] = resolve_signal(sig, wired, context, "
        "diagnostics=diagnostics)\n",
        "        results[sig] = resolve_signal(sig, wired, context, "
        "diagnostics=diagnostics)\n"
        "        if results[sig].state == reward.ERROR:\n"
        "            break\n")
    pkg, cleanup = _isolated_traaviis(stop_early)
    try:
        try:
            out = _episode(pkg, {"tests": _boom_tests, "identity": _pass_identity})
        except ValueError:
            pass                      # reward.score refused the partial map
        else:
            missing = sorted(set(_ALL_SIGNALS) - set(out["receipt"]["verification"]))
            raise AssertionError(
                "abandoning the loop after the first error cost nothing visible "
                "(missing: %s) -- V1's sibling clause proves nothing" % missing)
    finally:
        cleanup()

    # (c) the protocol check. A NaN detail is a valid state with an unsealable
    #     payload; without the check it reaches `identity.canonical_bytes` inside
    #     `build_receipt_v1`, one function past every guard.
    no_protocol = ("evalone.py",
                   "    violation = _result_violation(result)",
                   "    violation = None")
    pkg, cleanup = _isolated_traaviis(no_protocol)
    try:
        try:
            _episode(pkg, {"tests": _unhashable_detail(pkg.evalone.VerifierResult),
                           "identity": _pass_identity})
        except pkg.identity.IdentityError as exc:
            assert exc.code == pkg.identity.CANONICAL_NON_FINITE, exc.code
        else:
            raise AssertionError(
                "an unsealable verifier detail did NOT erase the episode with "
                "the protocol check deleted -- V3 proves nothing")
    finally:
        cleanup()

    # (d) the two origins must stay two. Collapse the constant and V3's first
    #     assertion inverts.
    collapse = ("evalone.py",
                'ERROR_ORIGIN_VERIFIER_PROTOCOL = "verifier_protocol"',
                'ERROR_ORIGIN_VERIFIER_PROTOCOL = "verifier_exception"')
    pkg, cleanup = _isolated_traaviis(collapse)
    try:
        raised = _episode(pkg, {"tests": _boom_tests, "identity": _pass_identity})
        returned = _episode(pkg, {"tests": _not_a_result_tests,
                                  "identity": _pass_identity})
        a = raised["artifacts"]["verifier_evidence"]["tests"]["detail"]["error_origin"]
        b = returned["artifacts"]["verifier_evidence"]["tests"]["detail"]["error_origin"]
        assert a == b, "the collapse did not apply; (d) proves nothing"
    finally:
        cleanup()

    # And with everything restored, each of those inverts back.
    def _origin(verifier):
        run, _t = _run_with({"tests": verifier, "identity": _pass_identity})
        return run

    raised = _origin(_boom_tests)
    returned = _origin(_not_a_result_tests)
    assert sorted(raised["receipt"]["verification"]) == \
        sorted(_ALL_SIGNALS + ["native", "oracle"])          # (a) + (b) restored
    for run in (raised, returned):
        assert run["receipt"]["episode_id"].startswith("episode-")   # (c) restored
    assert (raised["artifacts"]["verifier_evidence"]["tests"]["detail"]["error_origin"]
            != returned["artifacts"]["verifier_evidence"]["tests"]["detail"][
                "error_origin"])                                     # (d) restored


# --------------------------------------------------------- V10-V14: route 5
# V1-V9 close "a verifier that raises erases the episode". They do not close
# "the *report about* the verifier that raised erases the episode", and until
# now that was open: the message and traceback were built as ARGUMENTS to
# `_verifier_error`, and Python evaluates arguments before entering a function.
# So `_verifier_error`'s ordering -- sealed detail first, diagnostics second --
# was already lost by the time control arrived, and a verifier raising with a
# candidate-derived enormous message, a chain thousands of links deep, or a
# `__str__` that allocates until the host says no took the episode down from
# inside the handler written to prevent exactly that.


class HostileBoom(Exception):
    """A verifier failure carrying a candidate-sized payload.

    Module scope for the same reason as `VerifierBoom`: `__qualname__` is sealed
    and a locally-defined class reads as `<locals>`-suffixed noise.
    """


_STR_CALLS = [0]


class CountingBoom(Exception):
    """Records every `str()` taken of it, so "was this work done?" is measurable.

    The instrument for V13(a). Whether the diagnostic path *ran* is otherwise
    invisible from outside -- both the old and the new code produce the same
    `error` verdict, and that is the point: the defect was in when the work
    happened, not in what it concluded.
    """

    def __str__(self):
        _STR_CALLS[0] += 1
        return "counted"


def _hostile_message_tests(context):
    """5 MiB of message. Not adversarial in kind -- a verifier that interpolates
    a candidate's file into its own error text writes this by accident."""
    raise HostileBoom("x" * (5 * 1024 * 1024))


def _deep_chain_tests(context):
    """20 000 chained exceptions.

    Built by assigning `__context__` rather than by nesting 20 000 `try` blocks,
    which would hit the recursion limit long before it hit the point. The links
    are indistinguishable from the ones the interpreter sets implicitly -- every
    `raise` inside an `except` sets exactly this attribute -- so a verifier
    looping over candidate-supplied data reaches this shape without meaning to.
    An earlier version of this fixture *did* loop over try/except and produced a
    chain of length **two**, because the interpreter clears the handled exception
    when the `except` block exits; it passed the link-limit assertion by having
    no links to limit, which is the shape of vacuity this file exists to catch.
    """
    top = HostileBoom("top")
    cur = top
    for i in range(20000):
        nxt = HostileBoom("link %d" % i)
        cur.__context__ = nxt
        cur = nxt
    raise top


def _cyclic_chain_tests(context):
    """A chain that is a cycle -- the one shape a depth limit alone still walks
    to the end of, because there is no end."""
    a = HostileBoom("a")
    b = HostileBoom("b")
    a.__cause__ = b
    b.__cause__ = a
    raise a


def _counting_tests(context):
    raise CountingBoom()


for _fn in (_hostile_message_tests, _deep_chain_tests, _cyclic_chain_tests,
            _counting_tests):
    _fn.version = "residency.tests.v1"


def test_v10_a_hostile_exception_still_yields_a_persisted_error_episode():
    """**Route 5's acceptance bar.** The reporter is inside the boundary too.

    Three shapes of hostile failure -- an enormous message, a 20 000-link cause
    chain, and a cycle in that chain -- and for each one the whole pipeline must
    finish: a receipt, a total verification map, sealed error evidence, a written
    bundle, and a cold re-verification that closes. Before this change each of
    them was a crash inside the `except` clause, which is the same outcome as no
    boundary at all: nothing persisted, `batch`/`compare` refuse the pair, the
    failing grade never existed.

    The sealed detail is asserted to be **exactly** the four stable keys, the same
    four V4 pins. That is the load-bearing half of "diagnostics cannot move an
    id": no matter how large or strange the failure, the bytes that reach
    `episode-…` are the same four facts a well-behaved failure produces.
    """
    from traaviis import episode_bundle as EB
    import time

    for label, verifier in (("message", _hostile_message_tests),
                            ("chain", _deep_chain_tests),
                            ("cycle", _cyclic_chain_tests)):
        started = time.monotonic()
        extra = {"tests": verifier, "identity": _pass_identity}
        run, task_doc = _run_with(extra)
        elapsed = time.monotonic() - started

        r = run["receipt"]
        assert r["verification"]["tests"] == R.ERROR, label
        assert sorted(r["verification"]) == sorted(_ALL_SIGNALS
                                                   + ["native", "oracle"]), label
        assert r["status"] == R.STATUS_ERROR and r["reward"] is None, label
        assert r["episode_id"].startswith("episode-"), label

        detail = run["artifacts"]["verifier_evidence"]["tests"]["detail"]
        assert detail["error_origin"] == E.ERROR_ORIGIN_VERIFIER_EXCEPTION, label
        assert detail["error_code"] == E.ERROR_CODE_VERIFIER_RAISED, label
        assert detail["exception_type"] == E._qualified_exception_type(
            HostileBoom), label
        assert sorted(detail) == ["error_code", "error_origin", "exception_type",
                                  "verifier_implementation"], (label, detail)

        # ...and it is publishable and reopenable, which is the clause a crash
        # inside the handler removed entirely.
        _path, report = _bundle(run, task_doc, extra, "v10-" + label)
        assert report["outcome"] == EB.OUTCOME_CLOSED, (label, report["checks"])
        assert report["episode_id"] == r["episode_id"], label
        assert report["checks"]["signals"]["tests"]["evidence_match"] is True, label

        # A bound, not a benchmark: the point is that none of the three walks
        # something unbounded. Generous enough that a loaded host does not make
        # this law a flake, tight enough that an unbounded walk cannot pass it.
        assert elapsed < 60.0, (label, elapsed)


def test_v11_the_diagnostic_budget_is_a_cap_and_not_a_hope():
    """Message + traceback ≤ 64 KiB, and the walk is bounded in work.

    Two different claims, and the second is the one that needed the rewrite.
    Truncating output after the fact would satisfy the first and leave the second
    open: `traceback.format_exception` walks the entire `__cause__`/`__context__`
    chain eagerly and only then returns a list to join, so the cost is paid in
    full before the first byte can be discarded. The walk in `_bounded_traceback`
    is bounded at both ends -- `_DIAGNOSTIC_CHAIN_LIMIT` links, and stop when the
    budget is spent -- and refuses to follow a link it has already seen.

    An ordinary failure is checked too, and must come through **untouched**. A cap
    that also mangles the everyday case has traded one defect for another; V5
    asserts the operator gets a usable traceback and that must stay true.
    """
    def _diag(verifier):
        ctx = E.VerifierContextV1(task={}, snapshot={}, original_content={},
                                  run={})
        out = {}
        result = E.resolve_signal("tests", verifier, ctx, diagnostics=out)
        assert result.state == R.ERROR
        return out["tests"]

    for label, verifier in (("message", _hostile_message_tests),
                            ("chain", _deep_chain_tests),
                            ("cycle", _cyclic_chain_tests)):
        rec = _diag(verifier)
        total = (len(rec["message"].encode("utf-8"))
                 + len(rec["traceback"].encode("utf-8")))
        assert total <= E._DIAGNOSTIC_BUDGET_BYTES, (label, total)

    # The oversized message really was cut, and says so rather than looking short.
    big = _diag(_hostile_message_tests)
    assert big["message"].endswith(E._TRUNCATION_MARK)
    assert len(big["message"]) < 5 * 1024 * 1024

    # The deep chain was cut at the link limit, not merely at the byte budget:
    # count the separators the walker emits between links.
    deep = _diag(_deep_chain_tests)
    links = deep["traceback"].count(E._CHAIN_LINK_SEP) + 1
    assert links <= E._DIAGNOSTIC_CHAIN_LIMIT, links
    assert E._CHAIN_TRUNCATION_MARK in deep["traceback"] \
        or deep["traceback"].endswith(E._TRUNCATION_MARK)

    # The cycle terminated at all, which a naive walk does not.
    cyclic = _diag(_cyclic_chain_tests)
    assert cyclic["traceback"].count(E._CHAIN_LINK_SEP) + 1 \
        <= E._DIAGNOSTIC_CHAIN_LIMIT

    # ...and the ordinary case is not collateral damage.
    plain = _diag(_boom_tests)
    assert plain["message"] == "the checker fell over"
    assert E._TRUNCATION_MARK not in plain["message"]
    assert E._TRUNCATION_MARK not in plain["traceback"]
    assert "Traceback (most recent call last)" in plain["traceback"]
    assert "_boom_tests" in plain["traceback"]


def test_v12_a_diagnostic_that_fails_is_dropped_and_moves_no_byte():
    """**The identity clause of route 5, proved by breaking diagnostics entirely.**

    The requirement is not "diagnostics are usually fine". It is that diagnostic
    generation cannot affect verification state, reward, receipt, episode id or
    bundle publication *at all* -- so the way to prove it is to make diagnostic
    generation fail outright and watch nothing else move.

    Two runs, identical except that the second cannot produce a diagnostic. Their
    receipts must be byte-identical, and the failing one must simply have no entry
    for the signal: a whole record or none, never a half-written one. That is a
    stronger statement than "the traceback is not in the receipt", because it also
    rules out any *length* or *presence* of a diagnostic having been folded in.
    """
    extra = {"tests": _boom_tests, "identity": _pass_identity}
    good, _t = _run_with(extra)

    saved = E._exception_diagnostics

    def _broken(exc):
        raise MemoryError("the host could not build a diagnostic")

    try:
        E._exception_diagnostics = _broken
        broken, _t2 = _run_with(extra)
    finally:
        E._exception_diagnostics = saved

    assert broken["artifacts"]["verifier_diagnostics"] == {}, \
        "a failed diagnostic must be absent, not partial"
    assert good["artifacts"]["verifier_diagnostics"]["tests"]["message"] \
        == "the checker fell over", "the working run must still diagnose"

    assert (I.canonical_bytes(broken["receipt"])
            == I.canonical_bytes(good["receipt"])), \
        "a failed diagnostic moved bytes inside the receipt"
    assert broken["receipt"]["episode_id"] == good["receipt"]["episode_id"]
    assert broken["receipt"]["status"] == R.STATUS_ERROR
    assert (broken["artifacts"]["verifier_evidence"]
            == good["artifacts"]["verifier_evidence"])


def test_v13_the_reporter_does_its_expensive_work_behind_the_guard():
    """Non-vacuity for route 5, by source-level deletion in isolated copies.

    Four probes. **None of them is a crash probe, deliberately.** The natural
    proof -- "restore the eager arguments and watch a huge message erase the
    episode" -- can only be made to fail by exhausting the host's memory, which
    would make this law's verdict a property of the machine running it. That is
    the exact host-dependence V4 and V7 refuse elsewhere, and a law that OOMs a
    developer's box to prove a point is not a law. So each probe asserts a
    *measurable consequence* of the shipped structure instead:

      (a) the work is not done when nobody will read it -- `diagnostics=None`
          must not so much as `str()` the exception. Under the old eager
          arguments it always did, unconditionally.
      (b) the nested guard is what keeps a failed diagnostic from escaping.
      (c) the byte budget is what bounds the output.
      (d) the chain limit is what bounds the walk.
    """
    def _episode(pkg, extra, required=None):
        task = _task(list(_SCORED_REQUIRED if required is None else required))
        return pkg.evalone.evaluate(
            task, CONTENT, AGENT, REWARD_SPEC, snapshot=_snapshot(),
            extra_verifiers=extra, platform="linux-x86_64", toolchain=TOOLCHAIN)

    def _ctx(pkg):
        return pkg.vcontext.VerifierContextV1(
            task={}, snapshot={}, original_content={}, run={})

    # (a) The thunk. With `diagnostics=None` the shipped seam never asks for a
    #     message; the eager form built one every time, before `_verifier_error`
    #     was even entered.
    _STR_CALLS[0] = 0
    E.resolve_signal("tests", _counting_tests,
                     E.VerifierContextV1(task={}, snapshot={},
                                         original_content={}, run={}),
                     diagnostics=None)
    assert _STR_CALLS[0] == 0, \
        "the shipped seam stringified an exception nobody asked about"

    eager = (
        "evalone.py",
        "            lambda: _exception_diagnostics(exc),",
        "            {\"message\": _safe_str(exc), \"traceback\": \"\".join("
        "traceback.format_exception(type(exc), exc, exc.__traceback__))},")
    pkg, cleanup = _isolated_traaviis(eager)
    try:
        _STR_CALLS[0] = 0
        pkg.evalone.resolve_signal("tests", _counting_tests, _ctx(pkg),
                                   diagnostics=None)
        assert _STR_CALLS[0] > 0, \
            "the eager form did not apply; (a) proves nothing"
    finally:
        cleanup()

    # (b) The nested guard. Diagnostics are forced to fail in both copies; only
    #     the guard differs, so the guard is the only thing the outcome can be
    #     attributed to.
    force_failure = (
        "evalone.py",
        "    message = _clip(_safe_str(exc), _DIAGNOSTIC_MESSAGE_BYTES)",
        "    raise MemoryError('forced')\n"
        "    message = _clip(_safe_str(exc), _DIAGNOSTIC_MESSAGE_BYTES)")
    no_guard = (
        "evalone.py",
        "        except Exception:  # noqa: BLE001 -- deliberately total; "
        "see the docstring",
        "        except ():  # noqa: BLE001 -- deliberately total; "
        "see the docstring")

    pkg, cleanup = _isolated_traaviis(force_failure)
    try:
        out = _episode(pkg, {"tests": _boom_tests, "identity": _pass_identity})
        assert out["receipt"]["episode_id"].startswith("episode-")
        # `tests`, not the whole map: `_pass_identity` returns *this* process's
        # `VerifierResult`, which the copy's `isinstance` check correctly refuses,
        # so `identity` carries a protocol diagnostic of its own. See
        # `_raising_state` for the same trap in its sharper form.
        assert "tests" not in out["artifacts"]["verifier_diagnostics"], \
            "the forced failure did not apply; (b) proves nothing"
    finally:
        cleanup()

    pkg, cleanup = _isolated_traaviis(force_failure, no_guard)
    try:
        try:
            _episode(pkg, {"tests": _boom_tests, "identity": _pass_identity})
        except MemoryError:
            pass                      # the reporter erased the episode: the hole
        else:
            raise AssertionError(
                "a failing diagnostic did NOT erase the episode with the nested "
                "guard deleted -- V12 is measuring something else")
    finally:
        cleanup()

    # (c) The byte budget.
    raise_cap = ("evalone.py",
                 "_DIAGNOSTIC_BUDGET_BYTES = 64 * 1024",
                 "_DIAGNOSTIC_BUDGET_BYTES = 64 * 1024 * 1024")
    raise_msg = ("evalone.py",
                 "_DIAGNOSTIC_MESSAGE_BYTES = 8 * 1024",
                 "_DIAGNOSTIC_MESSAGE_BYTES = 8 * 1024 * 1024")
    pkg, cleanup = _isolated_traaviis(raise_cap, raise_msg)
    try:
        out = {}
        pkg.evalone.resolve_signal("tests", _hostile_message_tests, _ctx(pkg),
                                   diagnostics=out)
        assert len(out["tests"]["message"]) > 64 * 1024, \
            "the cap was not what held the message down; (c) proves nothing"
    finally:
        cleanup()

    # (d) The chain limit. Raise both it and the budget, and the walk gets longer
    #     -- so the limit, not the budget alone, is what stops it.
    raise_links = ("evalone.py",
                   "_DIAGNOSTIC_CHAIN_LIMIT = 8",
                   "_DIAGNOSTIC_CHAIN_LIMIT = 500")
    pkg, cleanup = _isolated_traaviis(raise_cap, raise_links)
    try:
        out = {}
        pkg.evalone.resolve_signal("tests", _deep_chain_tests, _ctx(pkg),
                                   diagnostics=out)
        links = out["tests"]["traceback"].count(E._CHAIN_LINK_SEP) + 1
        assert links > E._DIAGNOSTIC_CHAIN_LIMIT, \
            "raising the link limit changed nothing; (d) proves nothing"
    finally:
        cleanup()

    # Restored, each inverts back.
    _STR_CALLS[0] = 0
    E.resolve_signal("tests", _counting_tests,
                     E.VerifierContextV1(task={}, snapshot={},
                                         original_content={}, run={}),
                     diagnostics=None)
    assert _STR_CALLS[0] == 0                                        # (a)
    live, _t = _run_with({"tests": _boom_tests, "identity": _pass_identity})
    assert live["artifacts"]["verifier_diagnostics"]["tests"]["message"]  # (b)
    out = {}
    E.resolve_signal("tests", _hostile_message_tests,
                     E.VerifierContextV1(task={}, snapshot={},
                                         original_content={}, run={}),
                     diagnostics=out)
    assert len(out["tests"]["message"]) <= 64 * 1024                 # (c)
    assert out["tests"]["traceback"].count(E._CHAIN_LINK_SEP) + 1 \
        <= E._DIAGNOSTIC_CHAIN_LIMIT                                 # (d)


class _RaisingHash(object):
    """A verifier state that cannot be compared against the four frozen ones.

    `reward.STATES` is a frozenset, so `state not in STATES` hashes first. This
    makes that hash raise, which used to propagate out of `_result_violation` --
    a function that runs *outside* the reporting seam, because it is what decides
    whether the seam is entered.
    """

    def __hash__(self):
        raise RuntimeError("this state refuses to be classified")


class _ExhaustingMapping(Mapping_ABC):
    """A `detail` that is a real Mapping and still cannot be read.

    `MemoryError` specifically: the old clause named
    `(ValueError, TypeError, RecursionError)` and let this one through, straight
    into `build_receipt_v1` and out of every guard.
    """

    def __iter__(self):
        raise MemoryError("the host could not enumerate this detail")

    def __len__(self):
        return 1

    def __getitem__(self, key):
        raise MemoryError("the host could not read this detail")


def _raising_state(VR):
    """Parameterised by the `VerifierResult` class, for the same reason
    `_unhashable_detail` is: inside an isolated package copy
    `traaviis_cutover_N.vcontext.VerifierResult` is a *different class object*,
    and a result built from this process's class fails the first clause
    (`not_a_verifier_result`) before the clause under test is ever reached. That
    is a real trap -- it silently turns a deletion probe into a test of clause
    one -- and it cost this law a debugging round."""
    def verifier(context):
        return VR(_RaisingHash(), {})
    verifier.version = "residency.tests.v1"
    return verifier


def _exhausting_detail(VR):
    def verifier(context):
        return VR(R.PASS, _ExhaustingMapping())
    verifier.version = "residency.tests.v1"
    return verifier


def test_v14_a_return_that_cannot_be_classified_is_still_classified():
    """`_result_violation` is total, clause by clause.

    It runs *before* `_verifier_error` and outside every guard -- it is the
    function that decides whether the guarded seam is entered -- so a clause that
    raises escapes exactly as the old reporter did. The returned object belongs to
    the verifier, and none of its attribute reads, `__hash__`es or mapping
    protocol are ours to trust.

    A check that cannot be *completed* returns the violation it was testing for.
    That is not a fudge: an object whose state cannot be compared has, for every
    purpose this system has, an `unknown_state`, and a detail the canonicalizer
    cannot get through is `detail_not_canonical` whether it refused or died
    trying. No new violation string was minted, so `RESULT_VIOLATIONS` is
    unchanged, `coverage`'s vocabulary is untouched, and no sealed evidence
    document can gain a value it did not have.
    """
    from traaviis import episode_bundle as EB

    for label, verifier, violation in (
            ("raising-state", _raising_state(VerifierResult), "unknown_state"),
            ("exhausting-detail", _exhausting_detail(VerifierResult),
             "detail_not_canonical")):
        extra = {"tests": verifier, "identity": _pass_identity}
        run, task_doc = _run_with(extra)
        detail = run["artifacts"]["verifier_evidence"]["tests"]["detail"]
        assert run["receipt"]["verification"]["tests"] == R.ERROR, label
        assert detail["error_origin"] == E.ERROR_ORIGIN_VERIFIER_PROTOCOL, label
        assert detail["error_code"] == E.ERROR_CODE_INVALID_VERIFIER_RESULT, label
        assert detail["violation"] == violation, (label, detail)
        assert detail["violation"] in E.RESULT_VIOLATIONS, label
        _p, report = _bundle(run, task_doc, extra, "v14-" + label)
        assert report["outcome"] == EB.OUTCOME_CLOSED, (label, report["checks"])

    # Non-vacuity: narrow the canonical clause back to the three names it used to
    # catch and the `MemoryError` detail escapes again.
    narrow = ("evalone.py",
              "    except Exception:  # noqa: BLE001 -- see the docstring\n"
              "        # Formerly `(ValueError, TypeError, RecursionError)`",
              "    except (ValueError, TypeError, RecursionError):\n"
              "        # Formerly `(ValueError, TypeError, RecursionError)`")
    pkg, cleanup = _isolated_traaviis(narrow)
    try:
        task = _task(list(_SCORED_REQUIRED))
        try:
            pkg.evalone.evaluate(
                task, CONTENT, AGENT, REWARD_SPEC, snapshot=_snapshot(),
                extra_verifiers={
                    "tests": _exhausting_detail(pkg.evalone.VerifierResult),
                    "identity": _pass_identity},
                platform="linux-x86_64", toolchain=TOOLCHAIN)
        except MemoryError:
            pass
        else:
            raise AssertionError(
                "a detail that exhausts memory did NOT escape the narrowed "
                "clause -- V14 is measuring something other than that clause")
    finally:
        cleanup()


# ----------------------------------------------------------- W1-W6: route 6
# V1-V14 close every way a verifier can *stop* badly. None of them closes a
# verifier that never stops. An infinite loop, a native deadlock, an `os._exit`
# or a segfault reaches no `except` clause: the evaluating process does not come
# back, nothing is persisted, and the failing grade is erased exactly as
# thoroughly as an uncaught exception erased it.
#
# The `tests` verifier never had this problem -- `run_command_set` has always run
# its commands through `subprocess.run(..., timeout=…)`, and a `TimeoutExpired`
# is `_INFRA_ERROR` -> `error`. The `identity` verifier did: it called
# `adapter.lower_source(patched[rel])` in this process, on a WRL source the
# *candidate* wrote, with no timeout anywhere on the path. These laws pin the
# worker that closes that asymmetry, and the policy that says which verifiers
# have a boundary and which are trusted without one.

_FAKE_FORGE_SOURCE = '''\
"""A Forge engine that misbehaves on demand, so route 6 is provable without one.

Deliberately NOT the real engine. Every hazard route 6 exists for -- a lowering
that never returns, one that spawns something that outlives it, one that exits
the process outright -- is a hazard the real engine must never exhibit, so a law
that waited for the real engine to hang would never run. This is also what keeps
W1-W3 engine-INDEPENDENT: they are about the boundary, not about Forge, and a
packet extracted with no TRVM checkout above it must still be able to prove them.
"""

ENGINE_API_VERSION = "1"
LOWER_RESULT_VERSION = "forge.lower-result.v1"
BENCH_VERSION = "0.0.0-fake"

import os
import subprocess
import sys
import time


def lower_source(source):
    if "@@SPAWN@@" in source:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"])
        with open(os.environ["TRVS_FAKE_FORGE_MARKER"], "w") as fh:
            fh.write(str(child.pid))
            fh.flush()
            os.fsync(fh.fileno())
    if "@@NOISE@@" in source:
        print("engine chatter that must not land on the response wire")
    if "@@HANG@@" in source:
        time.sleep(600)
    if "@@EXIT@@" in source:
        os._exit(9)
    return {"ok": True, "semantic_artifact_id": "sem-fake", "error": None}
'''


def _fake_forge_dir():
    """A directory `traaviis.engine` will accept, holding the engine above."""
    root = os.path.join(_tmp(), "fake-forge")
    if not os.path.isdir(root):
        os.makedirs(root)
        with open(os.path.join(root, "forge_api.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(_FAKE_FORGE_SOURCE)
    return root


def _marker_path(name):
    path = os.path.join(_tmp(), "marker-" + name)
    if os.path.exists(path):
        os.remove(path)
    os.environ["TRVS_FAKE_FORGE_MARKER"] = path
    return path


def _alive(pid):
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


def _reap(pid):
    try:
        os.kill(pid, 9)
    except (ProcessLookupError, OSError):
        pass


def test_w1_a_lowering_that_never_returns_is_killed_and_reported():
    """**Route 6's acceptance bar.** A hang becomes an outcome instead of an end.

    Three claims in one law, because they are three halves of the same boundary:

      * a source that makes the engine hang produces `ForgeTimeout` in bounded
        time, rather than never producing anything;
      * `ForgeTimeout` **is a** `ForgeUnavailable`, so every existing
        `except ForgeUnavailable` handler already reports it as `error` rather
        than silently not covering the case the change exists to cover;
      * an ordinary lowering still works, and still works when the engine prints
        on stdout -- the worker takes a duplicate of fd 1 before importing
        anything, so engine chatter cannot land in the middle of the response
        document and turn a good answer into an unparseable one.

    Engine-independent by construction: the misbehaving engine is written to a
    temp directory by this file. A law that waited for the real Forge to hang
    would never run, and the real Forge hanging is not the hypothesis -- a
    candidate-authored source making it hang is.
    """
    import time
    from traaviis import forge_adapter as FA

    fake = _fake_forge_dir()

    # An ordinary lowering: the boundary is not in the way of the normal case.
    ok = FA._lower_in_worker("plain source", fake, 60.0)
    assert ok.ok is True and ok.semantic_id == "sem-fake", ok

    # ...including one where the engine writes to stdout.
    noisy = FA._lower_in_worker("@@NOISE@@", fake, 60.0)
    assert noisy.ok is True and noisy.semantic_id == "sem-fake", noisy

    started = time.monotonic()
    try:
        FA._lower_in_worker("@@HANG@@", fake, 2.0)
    except FA.ForgeTimeout as exc:
        elapsed = time.monotonic() - started
        assert isinstance(exc, FA.ForgeUnavailable), \
            "a timeout that is not a ForgeUnavailable is a case no handler covers"
    else:
        raise AssertionError("a lowering that never returns returned")
    # Bounded, not benchmarked: 2s deadline plus the reap grace, with room for a
    # loaded host. The engine sleeps 600s, so an unbounded wait cannot pass.
    assert elapsed < 60.0, elapsed



def _reap_isolated_workers(package):
    """Kill any lowering worker left running out of an isolated package copy.

    Matched on the module name, which is unique per copy (`traaviis_cutover_N`),
    so this can never touch the real package's workers or another battery's.
    """
    import signal as _signal
    try:
        listing = os.listdir("/proc")
    except OSError:
        return
    for entry in listing:
        if not entry.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % entry, "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if package in cmdline and "forge_worker" in cmdline:
            try:
                os.kill(int(entry), _signal.SIGKILL)
            except OSError:
                pass


def test_w2_the_kill_reaches_the_grandchild():
    """`join(timeout)` is not a kill, and killing the child is not killing the tree.

    This repository has learned both halves already. `mcp_server.drain` documents
    the first at length: it stopped *waiting*, which is not the same as anything
    stopping, and only the workers being non-daemon threads kept the process
    honest. `tools/run_battery.py::_kill_tree` documents the second: killing only
    the direct child left grandchildren holding the pipes, so the battery went on
    waiting for output from a file it had already given up on. A lowering worker
    is exactly that shape -- an engine may shell out -- so it is started with
    `start_new_session=True` and the timeout signals the *group*.

    The misbehaving engine spawns a 600-second sleeper and records its pid before
    hanging. After the timeout that pid must be gone.

    Non-vacuity is the sharpest kind available here: an isolated copy with the
    containment removed must leave the same sleeper alive. Nothing else differs,
    so the boundary is the only thing the outcome can be attributed to. Since
    9E that boundary is a per-run cgroup rather than a process group -- see the
    comment on the deletion below for why the older probe had stopped proving
    anything.
    """
    from traaviis import forge_adapter as FA

    fake = _fake_forge_dir()
    marker = _marker_path("w2")
    try:
        FA._lower_in_worker("@@SPAWN@@ @@HANG@@", fake, 3.0)
    except FA.ForgeTimeout:
        pass
    else:
        raise AssertionError("the spawning hang did not time out")

    assert os.path.isfile(marker), "the engine never recorded a grandchild"
    with open(marker, encoding="utf-8") as fh:
        pid = int(fh.read().strip())
    # A reparented process takes a moment to disappear from the table.
    for _ in range(50):
        if not _alive(pid):
            break
        __import__("time").sleep(0.1)
    alive = _alive(pid)
    _reap(pid)
    assert not alive, "the grandchild outlived the kill (pid %d)" % pid

    # Non-vacuity: remove the containment and the grandchild survives.
    #
    # **This probe was retargeted twice, and the second time is the interesting
    # one.** 9D moved the kill out of this module into the shared execution
    # profile, so the edit stopped being about `forge_adapter.py`. Then 9E
    # showed that the thing being deleted -- `killpg` -- was never the mechanism
    # that closes this: a descendant can call `setsid()` and leave the group
    # before any signal addressed to it arrives, so deleting the group kill and
    # watching a *cooperative* grandchild die was proving something about a
    # boundary that did not hold anyway.
    #
    # What is deleted now is the cgroup discovery, which forces the copy down to
    # `traaviis.process-group.v1` -- exactly the boundary 9D shipped -- and the
    # group kill with it. The grandchild must then survive. That is the real
    # counterfactual: not "a weaker kill", but "the boundary 9D had".
    no_cgroup = ("containment.py",
                 "    base = _own_cgroup_path()\n"
                 "    if base is not None and os.path.isdir(base):",
                 "    base = None\n"
                 "    if base is not None and os.path.isdir(base):")
    no_kill = ("containment.py",
               '    if hasattr(os, "killpg"):',
               "    if False:  # hasattr(os, \"killpg\")")
    quick_reap = ("execlimits.py",
                  "REAP_GRACE_SECONDS = 30.0",
                  "REAP_GRACE_SECONDS = 3.0")
    pkg, cleanup = _isolated_traaviis(no_cgroup, no_kill, quick_reap)
    survivor = None
    try:
        marker = _marker_path("w2-probe")
        probe = __import__(pkg.__name__ + ".forge_adapter",
                           fromlist=["forge_adapter"])
        try:
            probe._lower_in_worker("@@SPAWN@@ @@HANG@@", fake, 3.0)
        except probe.ForgeTimeout:
            pass
        else:
            raise AssertionError("the probe did not time out; W2 proves nothing")
        with open(marker, encoding="utf-8") as fh:
            survivor = int(fh.read().strip())
        __import__("time").sleep(0.5)
        assert _alive(survivor), \
            "the grandchild died even with the group kill deleted -- W2 is " \
            "measuring something other than that kill"
    finally:
        if survivor is not None:
            _reap(survivor)
        # ...and the worker itself, not only the sleeper whose pid it recorded.
        # This probe *deliberately* runs with containment disabled -- that is
        # what it proves -- so nothing kills what it spawns except this block.
        # Measured: a hung `@@HANG@@` worker from the isolated copy outlived the
        # battery by three and a half minutes and held a run cgroup open the
        # whole time, which no sweep can remove while it is populated. A law
        # that demonstrates a boundary failing has to clean up after the failure
        # it demonstrated.
        _reap_isolated_workers(pkg.__name__)
        cleanup()


def test_w3_a_worker_that_exits_is_unavailable_and_never_a_verdict():
    """`os._exit` and a segfault reach no `except` clause. They still get an answer.

    In-process there was nothing to say about them: the evaluating interpreter
    was simply gone. Across a process boundary they are a return code, and a
    return code that is not zero means the engine did not answer -- which is
    `ForgeUnavailable`, i.e. `error`. Never `pass` (the identity would be claimed
    to hold on no evidence) and never `fail` (a candidate would be marked down for
    the evaluator's crash). A worker that dies is never evidence against the
    candidate.

    `ForgeUnavailable` but *not* `ForgeTimeout`: the two are distinguishable
    because they call for different remedies -- an engine that dies is a bug to
    fix, an engine that hangs is a deadline to reconsider -- and collapsing them
    would be the same mistake V3 refuses for the two error origins.
    """
    from traaviis import forge_adapter as FA

    fake = _fake_forge_dir()
    try:
        FA._lower_in_worker("@@EXIT@@", fake, 60.0)
    except FA.ForgeTimeout:
        raise AssertionError("an exiting worker was reported as a hang")
    except FA.ForgeUnavailable as exc:
        assert "9" in str(exc), str(exc)
    else:
        raise AssertionError("a worker that exited 9 produced a LowerResult")

    # An engine the worker cannot use: `status: "unavailable"` on the wire, and
    # `ForgeUnavailable` out of the parent -- the same answer as a dead worker,
    # reached without one dying.
    #
    # An *incompatible* engine rather than an absent one, deliberately.
    # `engine.try_load` treats an invalid `TRVS_FORGE_DIR` as a candidate that
    # did not match and searches on (unlike `engine.load`, which fails fast), so
    # pointing the worker at an empty directory inside this monorepo finds the
    # real sibling engine and lowers successfully. That is `engine.py`'s
    # behaviour, not this boundary's, and a law asserting otherwise would be
    # asserting the shape of the checkout it happens to run in.
    wrong = os.path.join(_tmp(), "wrong-api-forge")
    if not os.path.isdir(wrong):
        os.makedirs(wrong)
        with open(os.path.join(wrong, "forge_api.py"), "w",
                  encoding="utf-8") as fh:
            fh.write('ENGINE_API_VERSION = "999"\n'
                     'def lower_source(source):\n'
                     '    raise AssertionError("must never be called")\n')
    try:
        FA._lower_in_worker("plain", wrong, 60.0)
    except FA.ForgeUnavailable as exc:
        assert "Forge engine" in str(exc), str(exc)
    else:
        raise AssertionError("an incompatible engine produced a LowerResult")


class _HangingAdapter(object):
    """An adapter whose lowering was killed on its deadline."""

    version = "forge.identity.v1@api-1@lower-fake@engine-fake"

    def lower_source(self, source):
        from traaviis.forge_adapter import ForgeTimeout
        raise ForgeTimeout("the Forge lowering worker did not finish "
                           "and was killed after 120.0s on pid 12345")


def _timeout_identity(context):
    """The **real** identity verifier, given a real policy and a hung adapter.

    Not a stand-in for it: `SV.make_identity_verifier` is the shipped factory and
    this exercises its actual branch. The context is rebuilt with an
    `identity_policy` because `_task` above declares none, and with a patched tree
    containing the bound path -- the two things that get the verifier past its
    `not_applicable` and `no patched tree` exits and onto the lowering call.
    """
    import dataclasses

    task = dict(context.task)
    task["identity_policy"] = {
        "must_remain": {"world": {"path": "src/mod.py",
                                  "before_id": "sem-declared"}}}
    patched = dict(context.patched_content or {})
    patched.setdefault("src/mod.py", "return 1\n")
    ctx = dataclasses.replace(context, task=task, patched_content=patched)
    return SV.make_identity_verifier(_HangingAdapter())(ctx)


_timeout_identity.version = "residency.identity.v1"


def test_w4_a_hung_lowering_is_a_sealed_error_episode_not_a_lost_one():
    """The whole point, at the pipeline level: the grade survives the hang.

    The identity verifier's timeout branch seals a **stable code**, not a message.
    That distinction is not cosmetic: this detail enters
    `verification_evidence[identity]` and therefore `episode-…`, and a timeout
    message is the one kind of message guaranteed to be about the host -- a
    deadline, a pid, a duration. The adapter here raises with all three in its
    text, and none of them may appear anywhere in what is sealed.

    No committed id can move onto this branch, and the reason is stronger than
    "we checked": before the worker existed a hang produced no detail, because it
    produced no episode. There is nothing for the new bytes to have displaced.
    """
    from traaviis import episode_bundle as EB

    extra = {"tests": _pass_tests, "identity": _timeout_identity}
    run, task_doc = _run_with(extra)
    r = run["receipt"]

    assert r["verification"]["identity"] == R.ERROR, r["verification"]
    assert sorted(r["verification"]) == sorted(_ALL_SIGNALS
                                               + ["native", "oracle"])
    assert r["status"] == R.STATUS_ERROR and r["reward"] is None
    assert r["episode_id"].startswith("episode-")

    detail = run["artifacts"]["verifier_evidence"]["identity"]["detail"]
    from traaviis import forge_adapter as FA
    assert detail["error_code"] == FA.ERROR_CODE_FORGE_TIMEOUT
    assert detail["reason"] == "forge lowering timed out"
    assert detail["path"] == "src/mod.py"
    assert sorted(detail) == ["error_code", "path", "reason"], detail

    # Not one host byte from the exception text reached the seal.
    sealed = json.dumps([r, run["artifacts"]["verifier_evidence"]])
    for volatile in ("120.0", "12345", "pid", "was killed"):
        assert volatile not in sealed, volatile

    # ...and it publishes and reopens, which a hang never did.
    _path, report = _bundle(run, task_doc, extra, "w4")
    assert report["outcome"] == EB.OUTCOME_CLOSED, report["checks"]
    assert report["episode_id"] == r["episode_id"]
    assert report["checks"]["signals"]["identity"]["evidence_match"] is True


def test_w5_the_worker_lowers_to_exactly_the_id_the_engine_does():
    """**Zero ids moved**, measured against the engine rather than argued.

    The worker is a new *place* for the lowering to happen, not a new lowering.
    If it produced a different `SemanticArtifactID` for the same source, every
    sealed episode that scored `identity` would stop closing -- so this compares
    the in-process call the adapter used to make against the worker call it makes
    now, on the same source, and requires the same `sem-…`.

    The second half is the timeout's placement: `real_adapter(engine)` and
    `real_adapter(engine, timeout=…)` must declare the **same** `.version`, because
    that string enters `verifier_versions.identity` and thus `episode-…`. A
    host-resource knob in an identity is a knob that moves every id in the corpus
    for no semantic reason.

    Engine-dependent, so it **skips** rather than fails: `accept_packet.py`'s G5
    runs the battery once in an extraction with no TRVM checkout above it, and a
    law that sailed through an absent engine was never testing it.
    """
    from traaviis import engine as _engine
    from traaviis import forge_adapter as FA

    eng = _engine.try_load()
    if eng is None:
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    sources = [p for p in (
        os.path.join(REPO, "worlds", "alley.wrl"),
        os.path.join(REPO, "examples", "eval-one", "residency-forge",
                     "subject", "world", "frozen.wrl"),
    ) if os.path.isfile(p)]
    if not sources:
        raise Skip("no WRL source in this extraction to lower")

    adapter = FA.real_adapter(eng)
    for path in sources:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        in_process = eng.lower_source(source)
        via_worker = adapter.lower_source(source)
        assert bool(in_process.get("ok")) == via_worker.ok, path
        assert in_process.get("semantic_artifact_id") == via_worker.semantic_id, \
            "the worker lowered %s to a different id" % path

    assert FA.real_adapter(eng).version == FA.real_adapter(eng, timeout=1.5).version, \
        "the timeout reached the identity-bearing version string"


def test_w6_every_verifier_has_a_stated_isolation_and_the_source_matches():
    """The plugin question, answered in code rather than left open.

    A policy that lives only in a docstring drifts from the code the first time
    someone edits one and not the other, so `VERIFIER_ISOLATION_POLICY` is a map
    and this law checks the source against it. Four rows, and the fourth is the
    one that had to be *decided* rather than described:

        builtin_pure  in_process_trusted       this repository's own pure code
        tests         bounded_process_group    a deadline was not containment
        identity      bounded_worker_group     new in route 6, bounded in 9D
        external      worker_process_required  a precondition on the caller

    `external` is a requirement this package cannot enforce and says so:
    `extra_verifiers` is a plain mapping of callables, and `resolve_signal` cannot
    time out a call it is inside of. Leaving it unstated would have been the worse
    option -- a caller wiring a third-party verifier in-process would reopen route
    6 without ever being told the rule existed.

    **Two things about this law changed in 9D, and the second is the reason it
    is now written on the parse tree.**

    The `tests` row used to read `subprocess_timeout`, and this law used to
    prove it with `"subprocess.run(" in src and "timeout=timeout," in src`. Both
    the row and the proof were true about the *clock* and silent about
    everything else: `subprocess.run` buffers all output and applies its cap
    afterwards, and its `timeout=` kills the direct child, so a grandchild
    holding stdout outlives the deadline. A deadline is not a bounded
    process-tree and evidence-capture boundary, and the row says so now.

    The proof was also one of the twelve raw-text structural claims Law B
    (`test_boundedjson::J17`) registered as violations: it asserted the shape of
    an implementation by searching its source *text*, so a comment naming
    `subprocess.run(` satisfied it and a comment naming the wrong thing broke
    it. It is rewritten here on `ast`, where a call is a node and prose is not --
    which is the same instrument the `builtin_pure` half of this law has used
    since it was written, for the same reason.
    """
    import ast
    import inspect

    assert SV.VERIFIER_ISOLATION_POLICY == {
        "builtin_pure": "in_process_trusted",
        "tests": "bounded_process_group",
        "identity": "bounded_worker_group",
        "external": "worker_process_required",
    }

    def _calls(module):
        return {ast.unparse(n.func)
                for n in ast.walk(ast.parse(inspect.getsource(module)))
                if isinstance(n, ast.Call)}

    def _handlers(module):
        """Every `except` clause, in source order, as `(qualified name, lineno)`."""
        out = []
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                out.append((ast.unparse(node.type), node.lineno))
        return sorted(out, key=lambda pair: pair[1])

    # `tests`: the containment claim is a fact about `run_command_set`'s calls.
    # The bounded primitive is present and the unbounded ones are absent -- the
    # second half is the load-bearing one, because a module that called both
    # would satisfy any check that only looked for the good call.
    sv_calls = _calls(SV)
    assert "execlimits.run_bounded" in sv_calls
    assert "subprocess.run" not in sv_calls
    assert "subprocess.Popen" not in sv_calls

    # `identity`: the timeout is caught, and caught BEFORE the class it
    # subclasses -- an `except ForgeUnavailable` written first would swallow it
    # and the stable code would be unreachable. Read as handler nodes, so the
    # ordering claim is about `except` clauses rather than about the first place
    # two names happen to appear in the file.
    order = [name for name, _line in _handlers(SV)]
    assert "ForgeTimeout" in order and "ForgeUnavailable" in order
    assert order.index("ForgeTimeout") < order.index("ForgeUnavailable"), order

    # ...and the lowering really does leave this process, through the shared
    # bounded primitive rather than through a `Popen` of its own.
    from traaviis import forge_adapter as FA
    adapter_tree = ast.parse(inspect.getsource(FA))
    fa_calls = _calls(FA)
    assert "execlimits.run_bounded" in fa_calls
    assert "subprocess.Popen" not in fa_calls
    assert "_lower_in_worker" in fa_calls

    # The worker is launched out of THIS package copy, not a hard-coded name --
    # the defect the isolated-copy replay caught, and the reason B4 went red.
    # Asserted as an *assignment* whose value is the `__package__` name, and as
    # the argv the worker is actually spawned with, rather than as two substrings
    # that a comment could supply.
    assigned = {}
    for node in ast.walk(adapter_tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and \
                isinstance(node.targets[0], ast.Name):
            assigned[node.targets[0].id] = ast.unparse(node.value)
    assert assigned.get("_PACKAGE", "").startswith("__package__"), assigned.get("_PACKAGE")
    assert assigned.get("_WORKER_MODULE") == "_PACKAGE + '.forge_worker'", \
        assigned.get("_WORKER_MODULE")
    spawn_argv = [ast.unparse(n) for n in ast.walk(adapter_tree)
                  if isinstance(n, ast.List)
                  and any(isinstance(e, ast.Constant) and e.value == "-m"
                          for e in n.elts)]
    assert spawn_argv == ["[sys.executable, '-m', _WORKER_MODULE]"], spawn_argv

    # `builtin_pure`: the three pure verifiers spawn nothing, which is what makes
    # trusting them in-process a statement rather than a hope.
    #
    # Read from the *parse tree*, not from the text. `verifiers.py`'s own
    # docstring contains the word "subprocess" -- in a sentence promising there
    # is none -- so a substring check reports the promise as a violation. An
    # import is a node; prose is not.
    import ast
    from traaviis import verifiers as PV

    tree = ast.parse(inspect.getsource(PV))
    imported = set()
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            calls.add(ast.unparse(node.func))
    for forbidden in ("subprocess", "multiprocessing", "socket", "ctypes"):
        assert forbidden not in imported, forbidden
    for forbidden in ("os.system", "os.popen", "os.fork", "eval", "exec"):
        assert forbidden not in calls, forbidden


def test_w7_the_timeout_is_what_ends_the_hang():
    """Non-vacuity for W1, by source-level deletion, run where a hang is safe.

    The deletion here is the one probe in this file that **cannot** be run in
    process: removing the deadline means the call does not come back, so an
    in-process probe would hang the battery rather than fail it -- which is
    precisely the defect being demonstrated, and precisely why it needs a
    subprocess of its own to demonstrate it in.

    So the same driver is run twice against the same misbehaving engine, once
    against the shipped package and once against an isolated copy whose
    `communicate(..., timeout=timeout)` has had its deadline removed. The shipped
    one must report `TIMEOUT` and exit; the copy must be killed by the driver's
    own outer deadline. Both directions are asserted, because only the pair
    distinguishes "the deadline works" from "nothing was ever slow".
    """
    import subprocess as _sp
    import signal as _signal

    fake = _fake_forge_dir()
    driver = os.path.join(_tmp(), "w7_driver.py")
    with open(driver, "w", encoding="utf-8") as fh:
        fh.write(
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "mod = __import__(sys.argv[2] + '.forge_adapter',\n"
            "                 fromlist=['forge_adapter'])\n"
            "try:\n"
            "    mod._lower_in_worker('@@HANG@@', sys.argv[3], 2.0)\n"
            "except mod.ForgeTimeout:\n"
            "    print('TIMEOUT')\n"
            "else:\n"
            "    print('RETURNED')\n")

    def _drive(root, name, deadline):
        proc = _sp.Popen([sys.executable, driver, root, name, fake],
                         stdout=_sp.PIPE, stderr=_sp.PIPE, text=True,
                         start_new_session=True)
        try:
            out, _err = proc.communicate(timeout=deadline)
            return out.strip()
        except _sp.TimeoutExpired:
            try:
                os.killpg(proc.pid, _signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
            proc.communicate()
            return "HUNG"

    assert _drive(REPO, "traaviis", 60.0) == "TIMEOUT", \
        "the shipped deadline did not end the hang"

    # The deadline moved with the kill: `_lower_in_worker` no longer opens its
    # own `Popen` and calls `communicate(..., timeout=...)`, it calls
    # `execlimits.run_bounded`, whose supervisor loop holds the one deadline
    # every execution path in the package now shares. Deleting it there deletes
    # it for the lowering worker, which is what this probe needs.
    no_deadline = ("execlimits.py",
                   "        if deadline is not None and time.monotonic() >= deadline:",
                   "        if False:  # deadline deleted for W7")
    pkg, cleanup = _isolated_traaviis(no_deadline)
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(pkg.__file__)))
        assert _drive(root, pkg.__name__, 20.0) == "HUNG", \
            "the lowering came back with the deadline deleted -- W1 is " \
            "measuring something other than that deadline"
    finally:
        # The copy has **no deadline**, so its worker hangs by construction and
        # nothing in it will ever stop. `_drive` kills the driver's process
        # group, which does not reach a worker the driver spawned into its own.
        # Measured: two of these accumulated per full battery run and sat at 3
        # and 7 minutes old, forever. Same rule as W2 -- a law that demonstrates
        # a boundary failing has to clean up after the failure it demonstrated,
        # and here the boundary that fails is the deadline itself.
        _reap_isolated_workers(pkg.__name__)
        cleanup()


def _main():
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    failures = []
    skipped = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Skip as exc:
            # `tools/run_battery.py` counts a leading SKIP, and
            # `accept_packet.py` G5 requires engine-dependent laws to *notice*
            # the engine is gone rather than pass regardless. A law that
            # returned early instead would be counted as passing, which is the
            # fail-open shape this repository removed from three id guards.
            skipped += 1
            print(f"SKIP  {name} ({exc})")
        except AssertionError as exc:
            failures.append((name, exc))
            print(f"FAIL  {name}: {exc}")
    print(f"\n{len(tests) - len(failures) - skipped}/{len(tests)} passed"
          f", {skipped} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
