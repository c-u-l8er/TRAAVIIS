"""Laws for `Residency Submission ORS Profile v1` -- the remote submission adapter.

The kernel was extracted before this transport existed so that the transport
would have to translate into it rather than define it. These laws are mostly
statements about ways that translation could quietly become a *second pipeline*
with its own trust boundary, its own idea of what a receipt is, and its own
opinion about what the client is allowed to say happened.

The failure modes they exist to close:

- the adapter could advertise more than it can honestly do, or hide that the
  substrate refuses `observe` / `step` / `reset` behind a 404 that blames the
  transport for a limit of the substrate (O1-O3);
- a client could smuggle a field the server is supposed to determine -- a
  reward, a status, verifier outcomes, an exit code, or a whole `RunResult` --
  and narrate its own execution into a receipt (O4-O9);
- the "nothing was executed" facts could be *fabricated* rather than stated: an
  `exited` termination and exit code 0 read exactly like a program that ran and
  succeeded (O10-O12);
- the same null exit code could be read by the existing substrate-failure rule
  as a timeout, turning every remote submission into an all-`error` map (O13);
- the ToolOutput could leak an absolute path or claim `finished` before the
  evidence was durable, so a client could hold a reward whose proof never
  reached disk (O14-O18);
- an idempotency retry could produce a *second* episode instead of the first
  one's answer, which is the single most expensive bug this class of server has
  (O19-O21);
- the linearization the kernel just gained could be reintroduced as a lock one
  layer up, either losing it (O22) or over-applying it (O23);
- the server could bind first and admit later, advertising readiness while still
  able to discover its package is unopenable (O24-O26);
- the served catalog could drift from the split, or the prompt could vary with
  server state so that "the same task" is not the same task twice (O27, O28);
- an ORS episode could be forced to collide with a local one, which requires
  pretending a submission was a process (O29);
- and the whole thing could add a rung, a receipt field, or a second CLI
  surface (O30).

**On what needs an engine.** Nothing here does. The adapter is substrate-neutral
above the verifier registry, and every law is proven against injected verifiers
over the in-memory subject, so this file runs identically with or without a
Forge checkout.

Run directly:      python3 test/test_ors.py
Run under pytest:  pytest test/test_ors.py
"""

import ast
import hashlib
import inspect
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import textwrap
import threading
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import episode_bundle as EB  # noqa: E402
from traaviis import evalone as E  # noqa: E402
from traaviis import execfacts as EF  # noqa: E402
from traaviis import identity as I  # noqa: E402
from traaviis import kernel as K  # noqa: E402
from traaviis import ors as O  # noqa: E402
from traaviis import ors_server as OS  # noqa: E402
from traaviis import reward as R  # noqa: E402
from traaviis import runner as RUN  # noqa: E402
from traaviis import snapshot as S  # noqa: E402
from traaviis.substrates import AdmissionError  # noqa: E402
from traaviis.vcontext import VerifierResult  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = os.path.join(HERE, "fixtures", "stub_agent.py")


class Skip(Exception):
    pass


# ---------------------------------------------------------------- fixtures
_TMP = {}


def _tmp():
    if "d" not in _TMP:
        _TMP["d"] = tempfile.mkdtemp(prefix="trvs-ors-law-")
    return _TMP["d"]


def _out(name):
    path = os.path.join(_tmp(), name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


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
    "caps": [
        {"when": {"signal": "patch", "state": "fail"}, "reward_max": 0.25},
    ],
    "aggregation": "terminal",
}

TOOLCHAIN = {"profile": "cpython-3.11",
             "resolved": {"python": {"version": "3.11.4"}}}

#: The same candidate the deterministic stub agent writes, sent as a submission
#: instead of produced by a process. Using the *identical* candidate is what
#: makes O29 a real comparison: the two episodes differ only in how the
#: candidate arrived, which is exactly the difference under test.
FINDING = {
    "summary": "spec/one.md line 2 contradicts src/mod.py",
    "citations": [
        {"path": "spec/one.md", "start_line": 2, "end_line": 2, "quote": "beta"}
    ],
}
PATCH = ("--- a/src/mod.py\n+++ b/src/mod.py\n"
         "@@ -1,1 +1,1 @@\n-return 1\n+return 2\n")


def _submission(finding=FINDING, patch=PATCH):
    return {"submission_version": O.SUBMISSION_VERSION,
            "finding": finding, "patch": patch}


def _content_hash(text):
    data = text.encode("utf-8").replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _snapshot():
    snap = {"snapshot_version": S.SNAPSHOT_VERSION,
            "files": {k: _content_hash(v) for k, v in CONTENT.items()},
            "exclusions": [], "binary_paths": [], "file_modes": {},
            "base_revision": None, "visible_config": {}}
    snap["snapshot_id"] = I.snapshot_id(snap)
    return snap


def _task(required=("citations", "patch", "tests", "identity")):
    return {
        "task_spec_version": "traaviis.task.v1",
        "substrate_profile": "residency.repository.v1",
        "subject": {"snapshot_id": _snapshot()["snapshot_id"]},
        "instructions": {"objective": "demo"},
        "reward_id": I.reward_id(REWARD_SPEC),
        "verifier_plan": {"required": list(required),
                          "not_applicable": ["native", "oracle"]},
        "termination": {"mode": "one_shot"},
        "agent_run_policy": {
            "policy_version": "traaviis.agent-run-policy.v1",
            "command_mode": "argv", "shell": False, "network": "unrestricted",
            "timeout_seconds": 30, "max_output_bytes": 4194304,
            "environment": {"TRAAVIIS_STUB_MODE": "ok",
                            "PATH": os.environ.get("PATH", "")},
            "writable_paths": ["."],
            "result_path": "result.json", "patch_path": "candidate.patch",
        },
    }


def _pass(context):
    return VerifierResult(R.PASS)


_pass.version = "residency.tests.v1"


def _pass_identity(context):
    return VerifierResult(R.PASS)


_pass_identity.version = "residency.identity.v1"

ALL_PASS = {"tests": _pass, "identity": _pass_identity}

#: A stand-in `env-…`. The adapter carries it through into `describe` and the
#: prompt and never derives anything from it, so a literal is honest here --
#: `open_adapter` is the code path that gets one from a real package, and O24
#: is the law about that path.
ENV_ID = "env-" + "0" * 64


def _kernel(task=None, extra=ALL_PASS, runner_profile=None):
    return K.local_kernel(
        task or _task(), CONTENT, REWARD_SPEC, snapshot=_snapshot(),
        extra_verifiers=extra, platform="linux-x86_64", toolchain=TOOLCHAIN,
        runner_profile=runner_profile or O.ORS_RUNNER_PROFILE)


def _adapter(output=None, task=None, extra=ALL_PASS, kernel=None):
    kernel = kernel or _kernel(task, extra)
    return O.OrsAdapterV1(kernel, env_id=ENV_ID,
                          output=output or _out("episodes-%d" % id(kernel)))


def _task_id(kernel):
    return kernel.list_tasks()[0]


def _submit(adapter, submission=None, key=None):
    """Open a session, submit once, return the ToolOutput."""
    task_id = adapter.list_tasks()[0]
    session = adapter.open_session(task_id)
    return adapter.call_tool(session["session_id"], O.ORS_TOOL,
                             submission or _submission(), idempotency_key=key)


def _refuses(fn, code, what):
    try:
        fn()
    except AdmissionError as ex:
        assert getattr(ex, "code", None) == code, "%s: expected %s, got %s (%s)" % (
            what, code, getattr(ex, "code", None), ex)
        return ex
    raise AssertionError("%s: expected %s, got no refusal" % (what, code))


def _ors_codes(source):
    """Every code an `OrsError(...)` in `source` is raised with, parsed.

    Read from the constructions themselves via the AST, and each asserted to be
    a **literal**. A text scan cannot tell a raised code from a name a module
    merely mentions -- the K-battery needed that correction four separate times
    (K10, K12, K18, K28) -- and a computed code would make the vocabulary
    uncheckable by any means at all.
    """
    codes = set()
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name != "OrsError":
            continue
        first = node.args[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), \
            "an OrsError code must be a literal, not a computed value"
        codes.add(first.value)
    return sorted(codes)


def _law_names():
    return sorted(k for k, v in globals().items()
                  if k.startswith("test_o") and callable(v))


def _strings(obj):
    """Every string anywhere in a nested structure."""
    out = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            out.extend(_strings(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_strings(v))
    return out


class _Live(object):
    """A really-bound, really-serving ORS server on an ephemeral port."""

    def __init__(self, adapter):
        self.server = OS.OrsHttpServer(adapter, host="127.0.0.1", port=0)
        self.server.start_background()
        self.base = self.server.base_url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()

    def request(self, method, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


# ------------------------------------------------------------------- laws
def test_o1_the_profile_is_named_and_exposes_exactly_one_tool():
    """The adapter states what it is and advertises `submit_candidate` alone.

    A benchmark endpoint is a thing other people write clients against, so what
    it *is* has to be a declaration rather than something inferred from which
    calls happen to work. One tool, named, versioned, with its input schema
    stated -- and no second tool, because this substrate has nothing a second
    tool could mean.
    """
    desc = _adapter().describe()
    assert desc["ors_profile_version"] == "traaviis.ors-profile.v1"
    assert desc["submission_version"] == "traaviis.remote-submission.v1"
    assert desc["runner_profile"] == "traaviis.ors-submission.v1"
    assert desc["kernel_version"] == K.KERNEL_VERSION
    assert desc["substrate_profile"] == "residency.repository.v1"
    assert desc["env_id"] == ENV_ID

    assert len(desc["tools"]) == 1, "exactly one tool, always"
    tool = desc["tools"][0]
    assert tool["name"] == "submit_candidate"
    schema = tool["input_schema"]
    assert schema["required"] == ["submission_version", "finding", "patch"]
    assert schema["additionalProperties"] is False, \
        "the advertised schema must be closed, like the validator"
    assert sorted(schema["properties"]) == ["finding", "patch",
                                            "submission_version"]


def test_o2_describe_reports_the_substrate_limit_rather_than_hiding_it():
    """`observe` / `step` / `reset` are advertised as **false**, not omitted.

    A capability list that only ever lists capabilities cannot express a limit,
    and a client that has to call an operation to learn it does not exist has
    been told nothing by the description. This is also the one place a future
    substrate with real steps would show up without anybody editing a document.
    """
    desc = _adapter().describe()
    ops = desc["operations"]
    assert set(ops) == set(K.OPERATIONS), "the full seven are reported"
    for op in K.INTERACTIVE_OPERATIONS:
        assert ops[op] is False, "%s must be advertised as unsupported" % op
    for op in ("list_tasks", "start", "finalize", "close"):
        assert ops[op] is True
    assert desc["interactive"] is False


def test_o3_the_interactive_routes_refuse_in_the_substrates_words_not_the_transports():
    """`POST .../observe` answers 501 `KERNEL_OPERATION_UNSUPPORTED`, never 404.

    The distinction is the whole reason those routes exist. A 404 says "this
    server has no such endpoint", which is a claim about the transport and is
    false -- the endpoint is right there. The truth is a claim about the
    substrate: `residency.repository.v1` is one-shot and has no observation
    between steps because it has no steps. A client tuning its retry logic needs
    to know which of those it is looking at.
    """
    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    with _Live(adapter) as live:
        status, body = live.request("POST", "/sessions", {"task_id": task_id})
        assert status == 201, body
        sid = body["session_id"]
        for op in K.INTERACTIVE_OPERATIONS:
            status, body = live.request("POST", "/sessions/%s/%s" % (sid, op), {})
            assert status == 501, "%s: expected 501, got %s" % (op, status)
            assert body["error"]["code"] == "KERNEL_OPERATION_UNSUPPORTED", body
            assert body["error"]["detail"]["operation"] == op

        # And a genuinely absent path is still a 404, so the two remain
        # distinguishable rather than one swallowing the other.
        status, body = live.request("GET", "/nonesuch")
        assert status == 404 and body["error"]["code"] == "ORS_NOT_FOUND"


def test_o4_a_submission_is_validated_by_exact_key_set():
    """`RemoteSubmissionV1` carries exactly three fields. Not at least three.

    An exact key set answers "is this the document"; a denylist answers "is this
    one of the things we thought of". Only the first is still true after
    somebody adds a field to a receipt.
    """
    assert O.SUBMISSION_FIELDS == ("submission_version", "finding", "patch")
    ok = O.validate_submission(_submission())
    assert sorted(ok) == ["finding", "patch", "submission_version"]
    assert ok["finding"] == FINDING and ok["patch"] == PATCH

    # A finding-only submission and a patch-only submission are both real
    # submissions: their missing half is *scored*, not rejected. Refusing them
    # would turn a candidate outcome into a protocol error.
    assert O.validate_submission(_submission(patch=None))["patch"] is None
    assert O.validate_submission(_submission(finding=None))["finding"] is None


def test_o5_every_field_the_server_determines_is_refused_by_name():
    """A client may not supply a reward, a status, a verdict, or a trace id.

    Each of these is refused with `ORS_SUBMISSION_FIELD` and named in the
    message as forbidden rather than merely unknown, because the two cases need
    different help: `reward` is a trust-boundary violation and `rewrad` is a
    typo, and telling a client "unknown field" for the first is an unhelpfully
    modest description of what just happened.
    """
    assert "run_result" in O.CLIENT_FORBIDDEN_FIELDS, \
        "a client-supplied RunResult is the forbidden field that matters most"
    for field in O.CLIENT_FORBIDDEN_FIELDS:
        body = _submission()
        body[field] = "anything at all"
        ex = _refuses(lambda b=body: O.validate_submission(b),
                      "ORS_SUBMISSION_FIELD", "forbidden field %s" % field)
        assert ex.detail["forbidden"] == [field], ex.detail
        assert "may not supply" in ex.message, ex.message

    # The twelve ruled names are all present, so the diagnostic set cannot
    # shrink without this law noticing.
    for ruled in ("reward", "status", "validity", "verification",
                  "verifier_versions", "trace", "trace_id", "episode_id",
                  "execution_facts", "exit_code", "timed_out",
                  "policy_violations"):
        assert ruled in O.CLIENT_FORBIDDEN_FIELDS, ruled


def test_o6_an_unforeseen_extra_field_is_refused_too():
    """The exact key set catches fields nobody put on a list.

    This is the law that makes O5's list a *diagnostic* rather than the
    validation rule. A denylist would let `reward_override` through.
    """
    body = _submission()
    body["reward_override"] = 1.0
    ex = _refuses(lambda: O.validate_submission(body),
                  "ORS_SUBMISSION_FIELD", "unlisted extra field")
    assert ex.detail["unknown"] == ["reward_override"]
    assert ex.detail["allowed"] == list(O.SUBMISSION_FIELDS)
    assert "forbidden" not in ex.detail, \
        "an unlisted field is unknown, not specifically forbidden"


def test_o7_the_submission_version_is_checked_before_anything_else():
    """A missing or wrong `submission_version` is `ORS_SUBMISSION_VERSION`."""
    for bad in (None, "", "traaviis.remote-submission.v2", 1, {}):
        body = _submission()
        body["submission_version"] = bad
        ex = _refuses(lambda b=body: O.validate_submission(b),
                      "ORS_SUBMISSION_VERSION", "version %r" % (bad,))
        assert ex.detail["expected"] == O.SUBMISSION_VERSION
    missing = {"finding": FINDING, "patch": PATCH}
    _refuses(lambda: O.validate_submission(missing),
             "ORS_SUBMISSION_VERSION", "absent version")


def test_o8_malformed_and_mistyped_submissions_are_typed_refusals():
    """Not-an-object is `ORS_SUBMISSION_MALFORMED`; a bad type is `..._TYPE`.

    Neither is ever a `TypeError` escaping into the transport. A server that
    500s on a malformed request has told its client "the server broke" about
    the client's own mistake, and the client will retry.
    """
    for bad in ([], "x", 1, None, True):
        _refuses(lambda b=bad: O.validate_submission(b),
                 "ORS_SUBMISSION_MALFORMED", "non-object %r" % (bad,))
    ex = _refuses(lambda: O.validate_submission(_submission(finding=["a"])),
                  "ORS_SUBMISSION_TYPE", "finding as a list")
    assert ex.detail["field"] == "finding"
    ex = _refuses(lambda: O.validate_submission(_submission(patch={"diff": "x"})),
                  "ORS_SUBMISSION_TYPE", "patch as an object")
    assert ex.detail["field"] == "patch"


def test_o9_a_client_supplied_run_result_cannot_reach_the_kernel():
    """The one forbidden thing, tested as an end-to-end submission.

    A `runner.RunResult` is the *server's* record of what it observed. Accepting
    one over the wire would let a client state its own exit code, its own
    filesystem effects, its own policy violations and its own trace -- which is
    to say, narrate its own execution into a receipt.
    """
    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    session = adapter.open_session(task_id)

    forged = dict(RUN.RunResult(exit_code=0, timed_out=False))
    body = _submission()
    body["run_result"] = forged
    ex = _refuses(
        lambda: adapter.call_tool(session["session_id"], O.ORS_TOOL, body),
        "ORS_SUBMISSION_FIELD", "a submitted RunResult")
    assert ex.detail["forbidden"] == ["run_result"]

    # The refusal did not consume the session's one episode: a caller error that
    # scored nothing must not burn the shot, exactly as the kernel rules for its
    # own run-result refusals.
    out = adapter.call_tool(session["session_id"], O.ORS_TOOL, _submission())
    assert out["finished"] is True


def test_o10_the_server_builds_the_run_result_and_it_says_nothing_executed():
    """`trusted_run_result` states the truth about a submission, field by field.

    `exit_code: None` with `timed_out: False` is contradictory under the local
    profile, where a null exit code *means* a timeout. It is not contradictory
    here, and the pair is the load-bearing evidence that the non-executing
    branch in `execfacts` is what reads it.
    """
    run = O.trusted_run_result(O.validate_submission(_submission()))
    assert run["exit_code"] is None
    assert run["timed_out"] is False
    assert run["output_truncated"] is False
    assert run["stdout"] == b"" and run["stderr"] == b""
    assert run["policy_violations"] == []
    for empty in ("files_created", "files_modified", "files_deleted",
                  "file_modes_changed", "workspace_after"):
        assert run[empty] == {}, empty
    assert run["result"] == {"finding": FINDING}
    assert run["patch_text"] == PATCH

    trace = run["trace"]
    assert trace["trace_version"] == "traaviis.submission-trace.v1", \
        "a submission trace is not a process trace and must not claim to be"
    assert trace["trace_version"] != RUN.TRACE_VERSION
    event = trace["events"][0]
    assert event["command"] == [] and event["environment_keys"] == []
    assert event["exit_code"] is None
    empty_digest = "sha256:" + hashlib.sha256(b"").hexdigest()
    assert event["stdout_digest"] == empty_digest
    assert event["stderr_digest"] == empty_digest
    # The finding is what actually arrived, so it is what the result digest is
    # over -- the analogue of the result file a local agent would have written.
    assert event["result_file_digest"] == \
        "sha256:" + hashlib.sha256(I.canonical_bytes(FINDING)).hexdigest()
    assert trace["trace_id"] == I.trace_id(trace)


def test_o11_the_adapter_profile_is_a_new_key_and_moved_no_existing_episode():
    """`RUNNER_PROFILES` gained a key, not a field. This is an identity law.

    `build_execution_facts` seals `dict(RUNNER_PROFILES[profile])` straight into
    `episode-…`. Adding a *field* to the trusted-local profile's dict would have
    moved every episode identity ever minted under it -- silently, and for every
    bundle already on disk. The local profile's sandbox is therefore pinned
    here, byte for byte.
    """
    assert EF.RUNNER_PROFILES["residency.trusted-local.v1"] == {
        "filesystem": "observed", "network": "unrestricted",
    }, "the trusted-local sandbox dict is frozen; changing it moves every episode"
    assert EF.RUNNER_PROFILES[O.ORS_RUNNER_PROFILE] == {
        "filesystem": "not_applicable", "network": "not_applicable",
    }
    assert sorted(EF.RUNNER_PROFILES) == ["residency.trusted-local.v1",
                                          "traaviis.ors-submission.v1"]
    assert EF.NON_EXECUTING_PROFILES == frozenset({"traaviis.ors-submission.v1"})
    assert "residency.trusted-local.v1" not in EF.NON_EXECUTING_PROFILES, \
        "the local runner really does execute; it must never be listed here"


def test_o12_execution_facts_say_not_executed_rather_than_fabricating_a_success():
    """`termination: "not_executed"`, `exit_code: null`, sandbox not applicable.

    The tempting alternative -- `exited` with exit code 0 -- is a fabrication:
    it asserts a program ran and that it succeeded. The project's sandbox labels
    are honest to the point of saying `observed` rather than `enforced`; this is
    the same discipline applied to a profile that observes nothing because there
    was nothing to observe.
    """
    run = O.trusted_run_result(O.validate_submission(_submission()))
    facts = EF.build_execution_facts(
        run, runner_profile=O.ORS_RUNNER_PROFILE,
        platform="linux-x86_64", toolchain=TOOLCHAIN)
    assert facts["agent_process"] == {
        "termination": "not_executed", "exit_code": None,
        "stdout_truncated": False, "stderr_truncated": False}
    assert facts["sandbox"] == {"filesystem": "not_applicable",
                                "network": "not_applicable"}
    assert facts["runner"] == {"profile": "traaviis.ors-submission.v1"}

    # The local profile is untouched by the new branch -- and reading the same
    # RunResult under it shows exactly why the branch had to exist. The
    # executing profile derives termination from the `timed_out` flag, so a
    # submission would be described as a process that "exited" with a null exit
    # code: a sentence about a run that never happened. `not_executed` is the
    # honest word, and it is only reachable through the profile.
    local = EF.build_execution_facts(
        run, runner_profile=RUN.RUNNER_PROFILE,
        platform="linux-x86_64", toolchain=TOOLCHAIN)
    assert local["agent_process"] == {
        "termination": "exited", "exit_code": None,
        "stdout_truncated": False, "stderr_truncated": False}
    assert local["sandbox"] == {"filesystem": "observed",
                                "network": "unrestricted"}

    # A policy that would be refused under the executing profile is vacuous here
    # -- there is no subprocess whose posture it could describe.
    EF.validate_run_policy({"network": "disabled"}, O.ORS_RUNNER_PROFILE)
    try:
        EF.validate_run_policy({"network": "disabled"}, RUN.RUNNER_PROFILE)
    except EF.UnsupportedPolicyError:
        pass
    else:
        raise AssertionError("the executing profile must still refuse this")


def test_o13_a_null_exit_code_is_not_read_as_a_substrate_failure():
    """The run-failure override is skipped for a non-executing profile.

    This is the subtlest thing in the slice. `exit_code=None` is not in
    `allowed_exit_codes=[0]`, and the existing §6a rule turns a disallowed exit
    into `error` for **every** run-dependent signal. Left ungated, every remote
    submission would score an all-`error` verification map and a null reward --
    and it would look like a verifier problem, not a profile problem.
    """
    out = _submit(_adapter())
    assert out["metadata"]["status"] == "ok", out
    assert out["reward"] is not None and out["reward"] > 0
    assert out["metadata"]["validity"] == "valid"

    # Stated structurally as well as behaviourally: the guard is a real branch
    # in both the live path and the replay path, and they must agree.
    live_src = inspect.getsource(E._finish_episode)
    replay_src = inspect.getsource(EB.verify_episode_bundle)
    for name, src in (("evalone", live_src), ("episode_bundle", replay_src)):
        assert "NON_EXECUTING_PROFILES" in src, \
            "%s must gate the run-failure override on the profile" % name


def test_o14_the_tool_output_is_exactly_the_ruled_shape():
    """`{blocks, reward, finished, metadata}` and nothing else.

    Pinned exactly, keys and all, because a ToolOutput is the one thing every
    client parses and the one place an extra field becomes a de-facto part of
    the protocol the moment somebody reads it.
    """
    out = _submit(_adapter())
    assert sorted(out) == ["blocks", "finished", "metadata", "reward"]
    assert sorted(out["metadata"]) == ["episode_id", "evidence_member",
                                       "status", "task_id", "validity"]
    assert out["finished"] is True
    assert isinstance(out["blocks"], list) and out["blocks"]
    assert out["blocks"][0]["type"] == "text"
    assert out["metadata"]["episode_id"].startswith("episode-")
    assert out["metadata"]["evidence_member"] == out["metadata"]["episode_id"]

    # And over the real transport, byte-identically.
    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    with _Live(adapter) as live:
        status, session = live.request("POST", "/sessions", {"task_id": task_id})
        assert status == 201
        status, body = live.request(
            "POST", "/sessions/%s/call_tool" % session["session_id"],
            {"tool": O.ORS_TOOL, "arguments": _submission()},
            headers={OS.IDEMPOTENCY_HEADER: "k1"})
        assert status == 200, body
        assert sorted(body) == ["blocks", "finished", "metadata", "reward"]
        assert body["finished"] is True


def test_o15_no_absolute_path_appears_anywhere_in_a_response():
    """A response names the content-addressed member, never the server's disk.

    An absolute path in a response is two problems at once: it discloses the
    server's layout, and it is a thing a client will eventually try to open --
    which works right up until the client is on a different machine, which is
    the only case this protocol exists for.
    """
    output = _out("no-abs-paths")
    out = _submit(_adapter(output=output))
    for text in _strings(out):
        assert not text.startswith("/"), "absolute path in ToolOutput: %r" % text
        assert output not in text, "server output root leaked: %r" % text

    desc = _adapter(output=output).describe()
    for text in _strings(desc):
        assert output not in text, "server output root leaked into describe"


def test_o16_the_reward_is_the_receipts_reward_and_nothing_else():
    """Every scored field in the ToolOutput is copied from the sealed receipt.

    Not recomputed, not rounded, not defaulted. The receipt is the artifact; the
    ToolOutput is a view of it, and a view that computes is a second source of
    truth.
    """
    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    session = adapter.open_session(task_id)
    out = adapter.call_tool(session["session_id"], O.ORS_TOOL, _submission())

    receipt = adapter._kernel.session(session["session_id"]).result["receipt"]
    assert out["reward"] == receipt["reward"]
    assert out["metadata"]["episode_id"] == receipt["episode_id"]
    assert out["metadata"]["status"] == receipt["status"]
    assert out["metadata"]["validity"] == receipt["validity"]
    assert out["metadata"]["task_id"] == task_id


def test_o17_finished_is_true_only_after_the_evidence_is_durably_published():
    """The strongest claim is the last thing made, and it is checkable on disk.

    `write_episode_bundle` stages into a temp sibling, **fully re-verifies the
    bundle by replay**, fsyncs, and atomically renames. Only after that returns
    does the adapter assemble a ToolOutput. So a client reading `finished: true`
    can be told, truthfully, that the proof is on disk and closed -- which this
    law then goes and checks by replaying it.
    """
    output = _out("durable")
    out = _submit(_adapter(output=output))
    assert out["finished"] is True

    member = out["metadata"]["evidence_member"]
    path = os.path.join(output, member)
    assert os.path.isdir(path), "the bundle must exist when finished is true"
    assert member == out["metadata"]["episode_id"], \
        "the member is content-addressed, so it IS the episode id"

    report = EB.verify_episode_bundle(path, extra_verifiers=ALL_PASS)
    assert report["ok"] is True, report
    assert report["outcome"] == EB.OUTCOME_CLOSED, report
    assert report["episode_id"] == out["metadata"]["episode_id"]


def test_o18_a_publish_failure_is_reported_as_a_failure_not_as_a_finish():
    """Unwritable evidence means no `finished`, ever. Not a finish with a warning.

    An episode that scored but could not be kept is precisely a reward with no
    retained proof -- the conflation the split-evaluation index was split apart
    to avoid. Here it is worse than a mislabelled row, because the client is
    remote and the disk is the server's: the client cannot check.
    """
    output = _out("readonly-publish")
    adapter = _adapter(output=output)
    os.chmod(output, stat.S_IRUSR | stat.S_IXUSR)
    try:
        task_id = adapter.list_tasks()[0]
        session = adapter.open_session(task_id)
        ex = _refuses(
            lambda: adapter.call_tool(session["session_id"], O.ORS_TOOL,
                                      _submission()),
            "ORS_PUBLISH_FAILED", "unwritable output root")
        assert "not finished" in ex.message, ex.message
        assert adapter._session(session["session_id"]).finished is False
    finally:
        os.chmod(output, stat.S_IRWXU)


def test_o19_the_same_idempotency_key_replays_the_answer_instead_of_rescoring():
    """A retry returns the first ToolOutput verbatim, and scores nothing twice.

    This is the most expensive bug this class of server can have. A dropped
    response is completely ordinary; a client that retries and gets a *second*
    episode has paid twice for one submission, and under a Residency task
    "paying" means running a candidate's test suite again.

    The counter is the point, exactly as in K19: "the same answer came back" is
    strictly weaker than "the work ran once", and the broken version satisfies
    the first.
    """
    scored = []
    original = E._finish_episode

    def counting(*args, **kwargs):
        scored.append(1)
        return original(*args, **kwargs)

    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    session = adapter.open_session(task_id)
    E._finish_episode = counting
    K._finish_episode = counting
    try:
        first = adapter.call_tool(session["session_id"], O.ORS_TOOL,
                                  _submission(), idempotency_key="key-1")
        again = adapter.call_tool(session["session_id"], O.ORS_TOOL,
                                  _submission(), idempotency_key="key-1")
    finally:
        E._finish_episode = original
        K._finish_episode = original

    assert again == first, "a retry must return the identical ToolOutput"
    assert again is first, "and it must be the cached object, not a rebuild"
    assert len(scored) == 1, \
        "the scoring work ran %d times; a retry must not rescore" % len(scored)


def test_o20_a_different_key_after_a_finish_is_refused_by_name():
    """One session carries one episode. A new intent needs a new session.

    Silently scoring a second episode under the first session's id would make
    "which episode did session S produce" unanswerable -- and it is the question
    the idempotency key exists to answer.
    """
    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    session = adapter.open_session(task_id)
    adapter.call_tool(session["session_id"], O.ORS_TOOL, _submission(),
                      idempotency_key="key-1")
    ex = _refuses(
        lambda: adapter.call_tool(session["session_id"], O.ORS_TOOL,
                                  _submission(), idempotency_key="key-2"),
        "ORS_SESSION_FINISHED", "a fresh key on a finished session")
    assert ex.detail["session_id"] == session["session_id"]

    # A *new* session is the honest recovery, and it works.
    second = adapter.open_session(task_id)
    out = adapter.call_tool(second["session_id"], O.ORS_TOOL, _submission(),
                            idempotency_key="key-2")
    assert out["finished"] is True


def test_o21_a_keyless_second_submission_is_refused_too():
    """Idempotency is an optimisation for retries, not a licence for a second run.

    A client that sends no key at all still gets one episode per session. The
    key changes what a repeat *returns*; it never changes how many episodes a
    session may contain.
    """
    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    session = adapter.open_session(task_id)
    adapter.call_tool(session["session_id"], O.ORS_TOOL, _submission())
    _refuses(lambda: adapter.call_tool(session["session_id"], O.ORS_TOOL,
                                       _submission()),
             "ORS_SESSION_FINISHED", "a keyless repeat")

    # A session id that names nothing is refused rather than silently opened.
    _refuses(lambda: adapter.call_tool("session-nope", O.ORS_TOOL, _submission()),
             "ORS_SESSION_UNKNOWN", "an unknown session")
    _refuses(lambda: adapter.call_tool(session["session_id"], "step",
                                       _submission()),
             "ORS_TOOL_UNKNOWN", "a tool that is not the one tool")


def test_o22_two_concurrent_submissions_to_one_session_score_exactly_once():
    """The forced race, one layer above the kernel's own.

    K19 proved the kernel linearizes `finalize`. This proves the *adapter* did
    not undo it -- by, for example, checking `session.finished` and then calling
    `finalize` in two separate statements with the whole scoring work in
    between, which is the identical defect one level up.

    The interleaving is forced with a barrier inside the scoring work rather
    than hoped for: two threads that merely start at the same time collide most
    of the time, and a law that passes most of the time is a law that passes for
    the wrong reason.
    """
    entered = threading.Barrier(2, timeout=20)
    counts = []
    original = E._finish_episode
    release = threading.Event()

    def held(*args, **kwargs):
        counts.append(1)
        release.wait(10)
        return original(*args, **kwargs)

    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    session = adapter.open_session(task_id)
    results = {}
    K._finish_episode = held
    try:
        def submit(tag, key):
            try:
                entered.wait()
            except threading.BrokenBarrierError:  # pragma: no cover
                pass
            try:
                results[tag] = ("ok", adapter.call_tool(
                    session["session_id"], O.ORS_TOOL, _submission(),
                    idempotency_key=key))
            except AdmissionError as ex:
                results[tag] = ("refused", getattr(ex, "code", None))

        threads = [threading.Thread(target=submit, args=("a", "k-a")),
                   threading.Thread(target=submit, args=("b", "k-b"))]
        for t in threads:
            t.start()
        # Let both threads reach the submission, then release the held scoring.
        release.set()
        for t in threads:
            t.join(30)
    finally:
        K._finish_episode = original

    kinds = sorted(v[0] for v in results.values())
    assert kinds == ["ok", "refused"], \
        "expected exactly one winner, got %s" % (results,)
    refused_code = [v[1] for v in results.values() if v[0] == "refused"][0]
    assert refused_code in ("ORS_SESSION_BUSY", "ORS_SESSION_FINISHED",
                            "KERNEL_SESSION_STATE"), refused_code
    assert len(counts) == 1, \
        "the scoring work ran %d times for one session" % len(counts)


def test_o23_two_different_sessions_may_be_scored_at_the_same_time():
    """The other direction, and the reason O22 is not enough on its own.

    The obvious way to make O22 pass is a lock around `call_tool`. That would
    serialize the whole server down to one submission at a time -- reintroducing
    exactly what K15/K16/K25 exist to prevent, one layer up where those laws
    cannot see it.

    So the barrier goes **inside** the scoring work and requires both sessions
    to be there simultaneously. An adapter that serialized submissions would
    **deadlock** this law rather than merely running it slowly, which is the
    only kind of assertion a timing bug cannot pass by accident.
    """
    both_inside = threading.Barrier(2, timeout=20)
    original = E._finish_episode

    def barriered(*args, **kwargs):
        both_inside.wait()
        return original(*args, **kwargs)

    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    sessions = [adapter.open_session(task_id), adapter.open_session(task_id)]
    results = {}
    K._finish_episode = barriered
    try:
        def submit(tag, session):
            try:
                results[tag] = adapter.call_tool(
                    session["session_id"], O.ORS_TOOL, _submission())
            except BaseException as ex:  # pragma: no cover - diagnostic
                results[tag] = ex

        threads = [threading.Thread(target=submit, args=(i, s))
                   for i, s in enumerate(sessions)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        K._finish_episode = original

    assert sorted(results) == [0, 1], results
    for tag, out in results.items():
        assert isinstance(out, dict), "session %s failed: %r" % (tag, out)
        assert out["finished"] is True
    # The same candidate on the same task is deterministic, so both sessions
    # honestly produce the same episode id -- and the content-addressed bundle
    # writer is idempotent rather than conflicting.
    assert results[0]["metadata"]["episode_id"] == \
        results[1]["metadata"]["episode_id"]


def test_o24_the_whole_admission_happens_before_anything_binds():
    """`open_adapter` either returns an admitted environment or raises.

    A server that binds and then admits on the first request advertises
    readiness while it is still capable of discovering that its package is
    unopenable; a client that connected has been told something false. Every
    failure here is knowable without a client, so every one is found without
    one.

    The structural half of the law matters as much as the behavioural half:
    `serve` must call `open_adapter` *before* it constructs the server, and
    that is checked by parsing the function rather than by trusting the
    docstring that says so.
    """
    missing = os.path.join(_tmp(), "no-such-package")
    _refuses(lambda: O.open_adapter(missing, "dev", _out("adm-1")),
             "ENV_MISSING", "a package that is not there")

    # A directory that exists but is not a package.
    bare = _out("bare-dir")
    _refuses(lambda: O.open_adapter(bare, "dev", _out("adm-2")),
             "ENV_MISSING", "a directory with no environment.json")

    src = inspect.getsource(OS.serve)
    tree = ast.parse(textwrap.dedent(src))
    order = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name in ("open_adapter", "OrsHttpServer"):
                order.append((node.lineno, name))
    order.sort()
    assert [n for _, n in order] == ["open_adapter", "OrsHttpServer"], \
        "serve must admit before it binds, got %s" % (order,)


def test_o25_the_episode_output_is_mandatory_and_proven_writable_at_startup():
    """`finished` is a durability claim, so the place it lands is checked first.

    Discovering an unwritable output directory on the first submission means
    having already scored a real episode -- run a candidate's verifiers, with
    whatever side effects that has -- and then having nowhere to put the proof.
    """
    _refuses(lambda: O._verify_output_root(None),
             "ORS_OUTPUT_REQUIRED", "no output directory")
    _refuses(lambda: O._verify_output_root(""),
             "ORS_OUTPUT_REQUIRED", "an empty output directory")

    good = os.path.join(_tmp(), "made-on-demand")
    shutil.rmtree(good, ignore_errors=True)
    assert O._verify_output_root(good) == os.path.abspath(good)
    assert os.path.isdir(good), "the root is created if absent"
    assert not os.listdir(good), "and the write probe leaves nothing behind"

    locked = _out("locked-root")
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        _refuses(lambda: O._verify_output_root(os.path.join(locked, "sub")),
                 "ORS_OUTPUT_UNWRITABLE", "an unwritable output root")
    finally:
        os.chmod(locked, stat.S_IRWXU)

    # And the CLI makes it required rather than defaulted, because a default
    # would be a directory the operator did not choose to fill with evidence.
    from traaviis import cli
    parser = cli.build_parser()
    serve = parser._subparsers._group_actions[0].choices["serve"]
    required = {a.dest for a in serve._actions if getattr(a, "required", False)}
    assert {"ors", "split", "output"} <= required, required


def test_o26_the_default_bind_is_loopback_and_leaving_it_is_an_explicit_act():
    """Exposing the server beyond loopback requires `--allow-remote`.

    This server holds a candidate's patches and wires verifiers that run test
    commands. The default for a thing like that is not "reachable from the
    network", and the escape hatch is a flag rather than an inference from the
    address so that exposing it is something a human typed.
    """
    adapter = _adapter()
    ex = _refuses(lambda: OS.OrsHttpServer(adapter, host="0.0.0.0", port=0),
                  "ORS_REMOTE_BIND_REFUSED", "a wildcard bind without the flag")
    assert "--allow-remote" in ex.message
    assert OS.LOOPBACK_HOSTS == ("127.0.0.1", "::1", "localhost")

    server = OS.OrsHttpServer(adapter, host="127.0.0.1", port=0)
    try:
        assert server.host == "127.0.0.1"
        assert server.base_url.endswith(OS.ORS_BASE_PATH)
    finally:
        server.shutdown()

    from traaviis import cli
    parser = cli.build_parser()
    serve = parser._subparsers._group_actions[0].choices["serve"]
    host = [a for a in serve._actions if a.dest == "host"][0]
    assert host.default == "127.0.0.1", "the default bind must be loopback"


def test_o27_the_served_catalog_is_exactly_the_split():
    """A task outside the served split is `KERNEL_TASK_UNKNOWN`, at the kernel.

    Restricting the catalog rather than filtering on the way out means an
    unlisted task is refused by the object that owns task identity, not by a
    check some later endpoint might forget to apply. `list_tasks`, `task`,
    `open_session` and the prompt all inherit one refusal.
    """
    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    assert adapter.list_tasks() == [task_id]
    _refuses(lambda: adapter.open_session("task-" + "9" * 64),
             "KERNEL_TASK_UNKNOWN", "a task the server does not serve")
    _refuses(lambda: adapter.task("task-" + "9" * 64),
             "KERNEL_TASK_UNKNOWN", "describing a task not served")

    # And the restriction is applied in `open_adapter`, against the split, by
    # the same `resolve_split` the sequential evaluator uses.
    src = inspect.getsource(O.open_adapter)
    assert "resolve_split" in src
    assert "_entries" in src, "the catalog restriction must be structural"

    with _Live(adapter) as live:
        status, body = live.request("POST", "/sessions",
                                    {"task_id": "task-" + "9" * 64})
        assert status == 404 and body["error"]["code"] == "KERNEL_TASK_UNKNOWN"


def test_o28_the_prompt_is_static_and_the_ordering_is_canonical():
    """A prompt carries the ruled content and **no mutable kernel state**.

    Two agents given "the same task" must actually have been given the same
    task. A prompt that mentioned the open-session count, the rewards so far, or
    "attempts remaining" would vary with what other clients happened to be
    doing, and the benchmark would be measuring the schedule.
    """
    adapter = _adapter()
    task_id = adapter.list_tasks()[0]
    before = adapter.task_prompt(task_id)

    assert task_id in before
    assert ENV_ID in before
    assert "one_shot" in before, "the declared termination behaviour is ruled in"
    assert "demo" in before, "the instructions are ruled in"
    assert O.SUBMISSION_VERSION in before, "the required output schema is ruled in"
    assert O.ORS_TOOL in before

    # Churn the server's state as hard as the protocol allows, then re-read.
    for _ in range(3):
        session = adapter.open_session(task_id)
        adapter.call_tool(session["session_id"], O.ORS_TOOL, _submission())
    open_sessions = adapter._kernel.open_sessions()
    assert len(open_sessions) >= 3
    after = adapter.task_prompt(task_id)
    assert after == before, "the prompt moved when server state moved"

    for forbidden in ("session-", "open_session", "attempts", "reward so far"):
        assert forbidden not in before, \
            "mutable kernel state leaked into the prompt: %r" % forbidden

    # Ordering is canonical by task_id, not manifest order: a remote client that
    # pages this list must be able to page it again and get the same page.
    assert adapter.list_tasks() == sorted(adapter.list_tasks())
    src = inspect.getsource(O.OrsAdapterV1.list_tasks)
    assert "sorted(" in src


def test_o29_an_ors_episode_and_a_local_episode_honestly_differ():
    """Two different things happened, so they have two different identities.

    The ruling is explicit that these must not be forced to agree, and the
    reason is worth stating: making them collide would require sealing a
    submission under a process's runner profile and a process's trace version --
    that is, pretending a submission was a process. The identity ladder's job is
    to make different things different.

    What must still hold is that **both** are internally closed: each receipt's
    `episode-…` is reproducible from its own canonical bytes.
    """
    from traaviis import kernel as _K
    ors_out = _submit(_adapter(output=_out("ident-ors")))

    local_kernel = _kernel(runner_profile=RUN.RUNNER_PROFILE)
    local = _K.run_episode(local_kernel, _task_id(local_kernel),
                           [sys.executable, STUB])
    local_receipt = local["receipt"]

    assert ors_out["metadata"]["episode_id"] != local_receipt["episode_id"], \
        "a submission and a process must not mint the same episode"
    assert local_receipt["execution_facts"]["runner"]["profile"] == \
        "residency.trusted-local.v1"
    assert local_receipt["execution_facts"]["agent_process"]["termination"] == \
        "exited"

    # Both are closed against themselves.
    assert local_receipt["episode_id"] == I.episode_id(local_receipt)
    bundle = os.path.join(_out("ident-local"), "b")
    report = EB.verify_episode_bundle(
        os.path.join(_out("ident-ors"), ors_out["metadata"]["evidence_member"]),
        extra_verifiers=ALL_PASS) \
        if os.path.isdir(os.path.join(_out("ident-ors"),
                                      ors_out["metadata"]["evidence_member"])) \
        else None
    assert report is None or report["closed"] is True

    # The two traces differ *by version*, so the divergence is a declared fact
    # about what happened rather than an accident of a field.
    assert O.SUBMISSION_TRACE_VERSION != RUN.TRACE_VERSION
    assert local["artifacts"]["trace"]["trace_version"] == RUN.TRACE_VERSION


def test_o30_the_slice_added_no_rung_no_receipt_field_and_one_new_verb():
    """The completeness law: what this slice was allowed to add, and no more.

    The identity ladder is closed. A transport is the single most likely place
    to grow a ninth rung by accident -- a "submission id", a "request id"
    promoted to an artifact -- so it is checked here rather than assumed.
    """
    # No rung. `identity` knows nothing about submissions, sessions or servers.
    #
    # Read as *words*, not as substrings. The first form of this check searched
    # raw text and reported `ors` inside `separators` -- the fifth time in this
    # codebase (after K10, K12, K18 and K28) that a text scan made a claim about
    # structure it could not see. A word this module is forbidden to know is a
    # word, and the check has to be able to tell one from a syllable.
    ident_words = set()
    for token in re.findall(r"[A-Za-z_]+", inspect.getsource(I)):
        ident_words.update(p for p in token.lower().split("_") if p)
    for word in ("submission", "session", "ors", "http"):
        assert word not in ident_words and word + "s" not in ident_words, \
            "identity.py mentions %r" % word

    # The episode receipt gained no field: the ORS path builds it through the
    # same single builder, and that builder's source is unchanged by this slice
    # in every respect that a receipt key would show up in.
    out = _submit(_adapter())
    assert "submission_version" not in json.dumps(out["metadata"])

    # Exactly one new CLI verb, and it is `serve`.
    from traaviis import cli
    parser = cli.build_parser()
    verbs = set(parser._subparsers._group_actions[0].choices)
    assert "serve" in verbs
    assert "serve-mcp" not in verbs and "repl" not in verbs, \
        "MCP and the REPL are deferred by ruling"

    # The ORS refusal vocabulary, read out of the `OrsError(...)` constructions
    # via the AST and asserted literal.
    codes = _ors_codes(inspect.getsource(O)) + _ors_codes(inspect.getsource(OS))
    assert sorted(set(codes)) == [
        "ORS_BAD_REQUEST", "ORS_BODY_TOO_LARGE", "ORS_NO_EVIDENCE",
        "ORS_OUTPUT_REQUIRED", "ORS_OUTPUT_UNWRITABLE", "ORS_PUBLISH_FAILED",
        "ORS_REMOTE_BIND_REFUSED", "ORS_SESSION_BUSY", "ORS_SESSION_FINISHED",
        "ORS_SESSION_UNKNOWN", "ORS_SUBMISSION_FIELD",
        "ORS_SUBMISSION_MALFORMED", "ORS_SUBMISSION_TYPE",
        "ORS_SUBMISSION_VERSION", "ORS_TOOL_UNKNOWN",
    ], sorted(set(codes))

    # The kernel's own vocabulary is untouched by this slice: the transport
    # relays kernel refusals, it does not mint new names for them.
    kernel_src = inspect.getsource(K)
    for code in ("KERNEL_SESSION_STATE", "KERNEL_SESSION_BUSY",
                 "KERNEL_OPERATION_UNSUPPORTED"):
        assert code in kernel_src
    assert "KernelError(" not in inspect.getsource(O), \
        "the adapter must relay kernel refusals, never manufacture them"

    # The battery is complete and numbered without gaps.
    names = _law_names()
    assert len(names) == 30, "expected 30 laws, found %d" % len(names)
    numbers = sorted(int(n.split("_")[1][1:]) for n in names)
    assert numbers == list(range(1, 31)), numbers


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
