"""Laws for `EpisodeKernelV1` -- the substrate-neutral episode kernel.

The kernel was extracted *before* any transport exists, which is the only order
in which the extraction means anything: a kernel written after a server is a
description of what the server needed, and a kernel written before one is a
statement about what an episode is that the server then has to translate into.
So almost every law here is a statement about a way that statement could quietly
become false.

- the interface could grow or shrink silently, so a client could not know what
  it is talking to (K1, K2);
- the three interactive operations could be implemented as polite no-ops, so a
  client stepping a one-shot substrate would be told its actions applied when
  nothing applied (K3);
- a session id could drift into looking like -- or being used as -- an artifact
  id, adding a ninth rung to a closed ladder by accident (K4, K5);
- a session could outlive its process handle, so a finalized episode could be
  finalized again, or a released id could still name something (K6, K7);
- `finalize` could reconcile a missing or an unexpected run result instead of
  refusing it, sealing evidence its own preflight says does not exist (K8);
- the extraction could move a receipt by a byte, which would silently
  invalidate every episode identity ever minted (K10, K11);
- the subprocess boundary could acquire a second door, so "the kernel does not
  launch things" would stop being checkable (K12);
- the invalid-config path could start running an agent it was told not to (K13);
- a split could open one kernel per task, making "one kernel = one admitted
  environment" a claim about N objects that happen to agree (K14);
- the kernel could hold a lock across a session lifetime, serializing a future
  server down to one episode at a time (K15, K16);
- a substrate with no episode semantics could get a half-built kernel instead of
  a refusal (K17).

**On what needs an engine.** The kernel is substrate-neutral and its lifecycle
laws are proven against the deterministic stub agent with injected verifiers, so
every law but one needs no Forge checkout -- including K17, whose refusals are
reached before any substrate is opened. Only K14, which is genuinely a claim
about a *packed environment*, packs a real template and SKIPs when no engine is
locatable.

Run directly:      python3 test/test_kernel.py
Run under pytest:  pytest test/test_kernel.py
"""

import ast
import hashlib
import inspect
import json
import os
import shutil
import sys
import tempfile
import textwrap
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import engine as _engine  # noqa: E402
from traaviis import episode_bundle as EB  # noqa: E402
from traaviis import evalone as E  # noqa: E402
from traaviis import evalsplit as ES  # noqa: E402
from traaviis import identity as I  # noqa: E402
from traaviis import kernel as K  # noqa: E402
from traaviis import reward as R  # noqa: E402
from traaviis import runner as RUN  # noqa: E402
from traaviis import snapshot as S  # noqa: E402
from traaviis.substrates import AdmissionError  # noqa: E402
from traaviis.vcontext import VerifierResult  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = os.path.join(HERE, "fixtures", "stub_agent.py")
TEMPLATE = "residency-repair"


class Skip(Exception):
    pass


# ---------------------------------------------------------------- fixtures
_TMP = {}


def _tmp():
    if "d" not in _TMP:
        _TMP["d"] = tempfile.mkdtemp(prefix="trvs-kernel-law-")
    return _TMP["d"]


def _engine_or_skip():
    eng = _engine.try_load()
    if eng is None:
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    return eng


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


def _task(required=("citations", "patch", "tests", "identity"), mode="ok"):
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
            "environment": {"TRAAVIIS_STUB_MODE": mode,
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
AGENT = [sys.executable, STUB]


def _kernel(task=None, extra=ALL_PASS):
    """A one-task Residency kernel over the stub subject."""
    return K.local_kernel(
        task or _task(), CONTENT, REWARD_SPEC, snapshot=_snapshot(),
        extra_verifiers=extra, platform="linux-x86_64", toolchain=TOOLCHAIN)


def _canonical(obj):
    return I.canonical_bytes(obj)


def _run_agent_calls(source):
    """Every *call* of ``run_agent`` in ``source``, as (lineno, dotted name).

    Parsed, not grepped. A textual scan cannot tell a call from a docstring
    sentence naming the seam, and the law here is about calls -- prose saying
    "this drives ``runner.run_agent``" documents the boundary, it is not a
    second door through it.
    """
    found = []
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "run_agent":
            base = fn.value
            prefix = base.id + "." if isinstance(base, ast.Name) else ""
            found.append((node.lineno, prefix + "run_agent"))
        elif isinstance(fn, ast.Name) and fn.id == "run_agent":
            found.append((node.lineno, "run_agent"))
    return sorted(found)


def _identifiers(source):
    """Every identifier a module actually *references* -- names and attributes.

    Parsed for the same reason as `_run_agent_calls`: a module is allowed to
    name the seams it deliberately does not cross. Prose saying "a receipt is
    built by exactly one piece of code (``build_receipt_v1``)" is the statement
    of the boundary; a reference to it would be the crossing.
    """
    used = set()
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.alias):
            used.add(node.name.split(".")[-1])
            if node.asname:
                used.add(node.asname)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            used.add(node.name)
    return used


def _refuses(fn, code, what):
    try:
        fn()
    except AdmissionError as ex:
        assert getattr(ex, "code", None) == code, "%s: expected %s, got %s (%s)" % (
            what, code, getattr(ex, "code", None), ex)
        return ex
    raise AssertionError("%s: expected %s, got no refusal" % (what, code))


# ------------------------------------------------------------------- laws
def test_k1_the_interface_is_the_frozen_seven_and_the_base_refuses_all_of_them():
    """`EpisodeKernelV1` states the ruled operation set and implements none of it.

    A base class that quietly implemented an operation would make "this substrate
    supports X" a property of which method someone remembered to leave alone. Here
    support is a *declaration*: `supported_operations` is the whole of it, and the
    base refuses everything so a subclass that forgets to override cannot pass for
    one that meant to.
    """
    assert K.OPERATIONS == ("list_tasks", "start", "observe", "step", "reset",
                            "finalize", "close")
    assert K.INTERACTIVE_OPERATIONS == ("observe", "step", "reset")
    assert K.KERNEL_VERSION == "traaviis.episode-kernel.v1"

    base = K.EpisodeKernelV1()
    assert base.supported_operations == ()
    calls = {
        "list_tasks": lambda: base.list_tasks(),
        "start": lambda: base.start("t"),
        "observe": lambda: base.observe("s"),
        "step": lambda: base.step("s", {}),
        "reset": lambda: base.reset("s"),
        "finalize": lambda: base.finalize("s", None),
        "close": lambda: base.close("s"),
    }
    assert sorted(calls) == sorted(K.OPERATIONS)
    for op, call in sorted(calls.items()):
        ex = _refuses(call, "KERNEL_OPERATION_UNSUPPORTED", "base." + op)
        assert ex.detail["operation"] == op

    # Every operation the base declares is a real method, and there are no
    # others: the interface cannot grow by accident.
    for op in K.OPERATIONS:
        assert callable(getattr(K.EpisodeKernelV1, op)), op
    described = K.EpisodeKernelV1().describe()
    assert sorted(described["operations"]) == sorted(K.OPERATIONS)
    assert set(described["operations"].values()) == {False}


def test_k2_residency_supports_start_and_finalize_and_refuses_the_rest():
    """Residency v1 is one-shot: it starts, it finalizes, it does not step."""
    k = _kernel()
    assert k.substrate_profile == "residency.repository.v1"
    assert k.supported_operations == ("list_tasks", "start", "finalize", "close")

    for op in ("list_tasks", "start", "finalize", "close"):
        assert k.supports(op), op
    for op in K.INTERACTIVE_OPERATIONS:
        assert not k.supports(op), op

    d = k.describe()
    assert d["kernel_version"] == K.KERNEL_VERSION
    assert d["operations"] == {"list_tasks": True, "start": True, "finalize": True,
                               "close": True, "observe": False, "step": False,
                               "reset": False}
    assert d["tasks"] == k.list_tasks() and len(d["tasks"]) == 1
    assert d["open_sessions"] == []


def test_k3_the_interactive_operations_are_refusals_never_no_ops():
    """`observe` / `step` / `reset` raise. They do not succeed quietly.

    This is the law the whole refusal exists for. A no-op `step` returns
    something that looks like success, and a client that steps a Residency
    episode would be told its action was applied to a world that has no steps to
    apply it to. A protocol whose operations silently do nothing is worse than
    one that has none, because the second cannot be believed by mistake.
    """
    k = _kernel()
    sid = k.start(k.list_tasks()[0])
    try:
        for op, call in (("observe", lambda: k.observe(sid)),
                         ("step", lambda: k.step(sid, {"action": "noop"})),
                         ("reset", lambda: k.reset(sid))):
            ex = _refuses(call, "KERNEL_OPERATION_UNSUPPORTED", op)
            assert ex.detail["operation"] == op
            assert ex.detail["substrate_profile"] == "residency.repository.v1"
            assert "finalize" in ex.detail["supported"]
            assert op in str(ex) and "residency.repository.v1" in str(ex)
    finally:
        k.close(sid)

    # And structurally: the Residency kernel does not override them at all, so
    # there is no body that could ever be softened into a no-op.
    for op in K.INTERACTIVE_OPERATIONS:
        assert op not in K.ResidencyKernelV1.__dict__, op
        assert getattr(K.ResidencyKernelV1, op) is getattr(K.EpisodeKernelV1, op)


def test_k4_a_session_id_is_an_ephemeral_handle_not_an_artifact_id():
    """Minted from randomness, not content. Two starts of one task differ.

    Every rung of the identity ladder is `<name>-<64 hex>` derived from canonical
    bytes, so two derivations of the same input *must* agree. A session id must
    do the opposite -- if it were reproducible from the task it would be an
    identity, and the ladder would have grown a ninth rung nobody ruled.
    """
    k = _kernel()
    task_id = k.list_tasks()[0]
    a, b = k.start(task_id), k.start(task_id)
    try:
        assert a != b, "two sessions over one task must not share a handle"
        for sid in (a, b):
            assert sid.startswith(K.SESSION_PREFIX)
            body = sid[len(K.SESSION_PREFIX):]
            assert len(body) == 32 and int(body, 16) >= 0
            # Not any content address: no ladder prefix, and not 64 hex.
            for prefix in ("snap-", "rew-", "task-", "trace-", "episode-",
                           "env-", "bundle-", "finding-", "patch-", "sem-"):
                assert not sid.startswith(prefix)
        # `session-` is not, and must not become, an identity prefix.
        assert "session-" not in inspect.getsource(I)
    finally:
        k.close(a)
        k.close(b)


def test_k5_a_session_id_is_never_persisted_into_an_episode():
    """No receipt, artifact, trace or written bundle carries the handle."""
    k = _kernel()
    task_id = k.list_tasks()[0]
    sid = k.start(task_id)
    session = k.session(sid)
    run_result = RUN.run_agent(AGENT, session.content, session.policy)
    run = k.finalize(sid, run_result)
    k.close(sid)

    receipt = run["receipt"]
    assert receipt["status"] == R.STATUS_OK
    blob = json.dumps(run, default=lambda o: o.decode("utf-8", "replace")
                      if isinstance(o, bytes) else repr(o))
    assert sid not in blob
    assert K.SESSION_PREFIX not in blob
    assert K.SESSION_PREFIX not in _canonical(receipt).decode("utf-8")
    assert "session_id" not in receipt

    dest = os.path.join(_tmp(), "k5-bundle")
    shutil.rmtree(dest, ignore_errors=True)
    path = EB.write_episode_bundle(
        run, task=_task(), reward_spec=REWARD_SPEC, snapshot=_snapshot(),
        content=CONTENT, dest_root=dest, extra_verifiers=ALL_PASS)
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            assert K.SESSION_PREFIX not in name
            with open(os.path.join(dirpath, name), "rb") as fh:
                assert K.SESSION_PREFIX.encode() not in fh.read(), name


def test_k6_a_session_is_ephemeral_and_close_is_idempotent():
    """`close` forgets the session; releasing twice is not an error."""
    k = _kernel()
    sid = k.start(k.list_tasks()[0])
    assert k.open_sessions() == [sid]
    assert k.session(sid).state == K.SESSION_STARTED

    assert k.close(sid) is True
    assert k.open_sessions() == []
    # Releasing a process handle twice frees nothing twice. An adapter's
    # `finally: close()` must not be able to raise over an already-released id
    # and mask the real error on its way out.
    assert k.close(sid) is False
    assert k.close("session-" + "0" * 32) is False

    _refuses(lambda: k.session(sid), "KERNEL_SESSION_UNKNOWN", "closed session")
    _refuses(lambda: k.finalize(sid, None), "KERNEL_SESSION_UNKNOWN",
             "finalize after close")


def test_k7_finalize_refuses_an_unknown_or_already_finalized_session():
    k = _kernel()
    _refuses(lambda: k.finalize("session-" + "f" * 32, None),
             "KERNEL_SESSION_UNKNOWN", "unknown session")

    sid = k.start(k.list_tasks()[0])
    session = k.session(sid)
    run_result = RUN.run_agent(AGENT, session.content, session.policy)
    first = k.finalize(sid, run_result)
    assert first["receipt"]["episode_id"].startswith("episode-")
    assert k.session(sid).state == K.SESSION_FINALIZED

    ex = _refuses(lambda: k.finalize(sid, run_result), "KERNEL_SESSION_STATE",
                  "double finalize")
    assert ex.detail["state"] == K.SESSION_FINALIZED
    k.close(sid)


def test_k8_finalize_refuses_a_missing_or_unexpected_run_result():
    """The two ways the adapter and the kernel could disagree about what ran."""
    k = _kernel()
    sid = k.start(k.list_tasks()[0])
    assert k.session(sid).runnable is True
    _refuses(lambda: k.finalize(sid, None), "KERNEL_RUN_RESULT_MISSING",
             "runnable session finalized with no run result")
    k.close(sid)

    # The mirror image: a session the config preflight refused to run, handed a
    # run result anyway. Scoring it would seal evidence the receipt's own
    # preflight says does not exist.
    bad = _kernel(task=_task(required=("tests",)), extra={})
    sid2 = bad.start(bad.list_tasks()[0])
    assert bad.session(sid2).runnable is False
    session = bad.session(sid2)
    smuggled = RUN.run_agent(AGENT, session.content, session.policy)
    _refuses(lambda: bad.finalize(sid2, smuggled), "KERNEL_RUN_RESULT_UNEXPECTED",
             "invalid-config session finalized with a run result")
    bad.close(sid2)


def test_k9_start_refuses_an_unknown_task_and_opens_no_session():
    k = _kernel()
    ex = _refuses(lambda: k.start("task-" + "0" * 64), "KERNEL_TASK_UNKNOWN",
                  "unknown task")
    assert ex.detail["known"] == k.list_tasks()
    assert k.open_sessions() == [], "a refused start must leave no session"


def test_k10_every_path_to_an_episode_produces_the_identical_receipt():
    """The ruled byte-for-byte law, from three independent directions.

    `evaluate` is now the local command adapter rather than the pipeline. If the
    extraction moved a receipt by one byte, every `episode-…` ever minted would
    be silently invalidated -- so the three ways of reaching one must agree
    exactly: the adapter, the receipt-only wrapper, and an explicit hand-driven
    `start` → `run_agent` → `finalize`.
    """
    task = _task()
    common = dict(snapshot=_snapshot(), extra_verifiers=ALL_PASS,
                  platform="linux-x86_64", toolchain=TOOLCHAIN)

    via_evaluate = E.evaluate(task, CONTENT, AGENT, REWARD_SPEC, **common)["receipt"]
    via_eval_one = E.eval_one(task, CONTENT, AGENT, REWARD_SPEC, **common)

    k = _kernel(task)
    sid = k.start(k.list_tasks()[0])
    session = k.session(sid)
    assert session.task_id == k.list_tasks()[0]
    assert session.substrate_profile == "residency.repository.v1"
    hand_driven = k.finalize(
        sid, RUN.run_agent(AGENT, session.content, session.policy))["receipt"]
    k.close(sid)

    assert _canonical(via_evaluate) == _canonical(via_eval_one)
    assert _canonical(via_evaluate) == _canonical(hand_driven)
    assert via_evaluate["episode_id"] == hand_driven["episode_id"]
    assert via_evaluate["status"] == R.STATUS_OK

    # And the adapter really is an adapter: it launches nothing itself.
    assert _run_agent_calls(inspect.getsource(E.evaluate)) == []


def test_k11_a_kernel_episode_replays_to_the_identical_receipt():
    """An independent re-derivation: the replay path this slice did not touch.

    `verify_episode_bundle` rebuilds a *fresh* receipt from the saved evidence
    through the same `build_receipt_v1`, with no kernel involved at any point.
    That it comes back byte-identical is the strongest available statement that
    the kernel phases reassemble into exactly the pipeline they were cut from.
    """
    k = _kernel()
    sid = k.start(k.list_tasks()[0])
    session = k.session(sid)
    run = k.finalize(sid, RUN.run_agent(AGENT, session.content, session.policy))
    k.close(sid)

    dest = os.path.join(_tmp(), "k11-bundle")
    shutil.rmtree(dest, ignore_errors=True)
    path = EB.write_episode_bundle(
        run, task=_task(), reward_spec=REWARD_SPEC, snapshot=_snapshot(),
        content=CONTENT, dest_root=dest, extra_verifiers=ALL_PASS)

    report = EB.verify_episode_bundle(path, extra_verifiers=ALL_PASS)
    assert report["ok"] is True, report
    assert report["episode_id"] == run["receipt"]["episode_id"]
    assert os.path.basename(path) == run["receipt"]["episode_id"]


def test_k12_the_subprocess_boundary_has_exactly_one_door():
    """`runner.run_agent` is called from exactly one place: `kernel.run_episode`.

    "The kernel does not launch anything" is only checkable while that stays
    true. A second call site -- a convenience path in the CLI, a shortcut in the
    split -- would mean an episode could be produced without passing through a
    session, and the kernel would describe some episodes rather than all of them.
    """
    pkg = os.path.join(REPO, "traaviis")
    callers = []
    for name in sorted(os.listdir(pkg)):
        if not name.endswith(".py") or name == "runner.py":
            continue
        with open(os.path.join(pkg, name)) as fh:
            source = fh.read()
        for lineno, dotted in _run_agent_calls(source):
            callers.append((name, lineno, dotted))
    assert len(callers) == 1, callers
    assert callers[0][0] == "kernel.py", callers
    assert callers[0][2] == "runner.run_agent", callers
    assert len(_run_agent_calls(inspect.getsource(K.run_episode))) == 1


def test_k13_an_invalid_config_session_opens_runs_nothing_and_still_scores():
    """F4 is an outcome, not an exception. The session is real; the agent is not.

    A required signal no wired verifier can resolve is an invalid *configuration*
    -- a scored result (`status = invalid`, `reward = None`) that must never
    compete in post-run precedence. Refusing to open the session would have
    turned that scored outcome into a crash, which is a different claim about
    the task.
    """
    k = _kernel(task=_task(required=("tests",)), extra={})
    sid = k.start(k.list_tasks()[0])
    session = k.session(sid)
    assert session.runnable is False
    assert session.describe()["runnable"] is False
    assert session.describe()["state"] == K.SESSION_STARTED
    assert set(session.describe()) == {
        "session_id", "task_id", "kernel_version", "substrate_profile",
        "state", "runnable"}

    launched = []
    real = RUN.run_agent
    RUN.run_agent = lambda *a, **kw: launched.append(a) or real(*a, **kw)
    try:
        run = k.finalize(sid, None)
    finally:
        RUN.run_agent = real
    k.close(sid)

    assert launched == [], "an invalid-config session must launch no agent"
    receipt = run["receipt"]
    assert run["artifacts"] is None
    assert receipt["status"] == R.STATUS_INVALID
    assert receipt["reward"] is None
    assert receipt["validity"] == R.INVALID
    assert receipt["trace_id"] is None
    assert receipt["verification"] == {"tests": R.NOT_APPLICABLE}

    # Byte-identical to what the adapter returns for the same inputs.
    adapter = E.evaluate(_task(required=("tests",)), CONTENT, AGENT, REWARD_SPEC,
                         snapshot=_snapshot(), extra_verifiers={},
                         platform="linux-x86_64", toolchain=TOOLCHAIN)
    assert _canonical(adapter["receipt"]) == _canonical(receipt)
    assert adapter["artifacts"] is None


def test_k14_one_kernel_serves_one_admitted_environment():
    """A split opens exactly one kernel and one session per task.

    "One kernel = one admitted environment" is the process model the ruling
    fixed. A kernel per task would mean the environment was admitted N times and
    the shared-registry, shared-engine guarantee would be a claim about N objects
    that merely happen to agree today.
    """
    eng = _engine_or_skip()
    from traaviis import pack as P, scaffold as SC

    env = os.path.join(_tmp(), "k14-env")
    out = os.path.join(_tmp(), "k14-pkg")
    for p in (env, out):
        shutil.rmtree(p, ignore_errors=True)
    SC.materialize(TEMPLATE, env)
    P.pack(env, out, engine=eng)

    built = []
    real_env_kernel = K.environment_kernel

    def spy(*args, **kwargs):
        k = real_env_kernel(*args, **kwargs)
        built.append(k)
        return k

    starts = []
    real_start = K.ResidencyKernelV1.start

    def spy_start(self, task_id):
        sid = real_start(self, task_id)
        starts.append((id(self), task_id, sid))
        return sid

    # `evalsplit` imports the kernel lazily inside the function body, so
    # rebinding the attribute on the module object is what the split will see;
    # there is no import-time alias to clear.
    K.environment_kernel = spy
    K.ResidencyKernelV1.start = spy_start
    try:
        report = ES.eval_split(
            out, "all",
            [sys.executable, os.path.join(HERE, "fixtures",
                                          "residency_agent.py")])
    finally:
        K.environment_kernel = real_env_kernel
        K.ResidencyKernelV1.start = real_start

    assert len(built) == 1, "a split must open exactly one kernel"
    kernel = built[0]
    assert kernel.substrate_profile == "residency.repository.v1"
    assert kernel.list_tasks() == sorted(
        e["task_id"] for e in _tasks_of(out))
    assert kernel.open_sessions() == [], "every session must be closed"

    # One session per task in the split, all against that one kernel object.
    assert len(starts) == report["totals"]["tasks"]
    assert {s[0] for s in starts} == {id(kernel)}
    assert len({s[2] for s in starts}) == len(starts), "session ids must differ"
    assert report["evaluation_version"] == ES.EVALUATION_VERSION
    assert report["totals"]["tasks"] >= 1


def _tasks_of(package):
    with open(os.path.join(package, "environment.json")) as fh:
        return json.load(fh)["tasks"]


def test_k15_many_ephemeral_sessions_may_be_open_at_once():
    """Sessions interleave. Opening one does not close, block or move another."""
    k = _kernel()
    task_id = k.list_tasks()[0]

    a = k.start(task_id)
    b = k.start(task_id)
    c = k.start(task_id)
    assert sorted(k.open_sessions()) == sorted([a, b, c])

    runs = {}
    for sid in (a, b, c):
        s = k.session(sid)
        runs[sid] = RUN.run_agent(AGENT, s.content, s.policy)

    # Finalize out of order: a session is a handle, not a queue position.
    r_c = k.finalize(c, runs[c])["receipt"]
    r_a = k.finalize(a, runs[a])["receipt"]
    r_b = k.finalize(b, runs[b])["receipt"]
    for sid in (a, b, c):
        k.close(sid)
    assert k.open_sessions() == []

    # Same subject, same task, same deterministic agent → same episode.
    assert _canonical(r_a) == _canonical(r_b) == _canonical(r_c)

    # And identical to a strictly serial run through the same kernel.
    sid = k.start(task_id)
    s = k.session(sid)
    serial = k.finalize(sid, RUN.run_agent(AGENT, s.content, s.policy))["receipt"]
    k.close(sid)
    assert _canonical(serial) == _canonical(r_a)


def test_k16_no_process_wide_lock_is_held_over_a_session_lifetime():
    """The kernel's lock guards the session table, never a running episode.

    A kernel that locked for the duration of a run would serialize a future
    server down to one episode at a time, and would deadlock the moment a session
    outlived the request that opened it.
    """
    k = _kernel()
    task_id = k.list_tasks()[0]
    sid = k.start(task_id)

    # With a session open and un-finalized, the lock is free.
    assert k._lock.acquire(blocking=False) is True
    k._lock.release()

    # And a second thread can complete a whole episode while the first session
    # is still open.
    done = {}

    def other():
        try:
            s2 = k.start(task_id)
            sess = k.session(s2)
            done["receipt"] = k.finalize(
                s2, RUN.run_agent(AGENT, sess.content, sess.policy))["receipt"]
            k.close(s2)
        except Exception as exc:  # pragma: no cover - surfaced by the assert
            done["error"] = "%s: %s" % (type(exc).__name__, exc)

    t = threading.Thread(target=other)
    t.start()
    t.join(120)
    assert not t.is_alive(), "a concurrent session must not block on an open one"
    assert "error" not in done, done.get("error")
    assert done["receipt"]["status"] == R.STATUS_OK

    s = k.session(sid)
    mine = k.finalize(sid, RUN.run_agent(AGENT, s.content, s.policy))["receipt"]
    k.close(sid)
    assert _canonical(mine) == _canonical(done["receipt"])

    # Structurally: the lock is only ever taken around session-table operations.
    source = inspect.getsource(K.ResidencyKernelV1)
    assert source.count("with self._lock:") == 4, source.count("with self._lock:")
    for forbidden in ("_admit_episode", "_finish_episode", "run_agent"):
        for block in source.split("with self._lock:")[1:]:
            head = block.split("\n\n")[0]
            assert forbidden not in head, forbidden


def test_k17_a_substrate_with_no_episode_semantics_is_refused_by_name():
    """`trvm.world.v1` packs and reopens. It does not get a half-built kernel."""
    _refuses(
        lambda: K.environment_kernel(
            {"substrate_profile": "trvm.world.v1"}, {"tasks": {}, "rewards": {}},
            {}, {}),
        "KERNEL_SUBSTRATE_UNSUPPORTED", "world substrate")

    # A task whose reward is not in the package is refused at construction,
    # before any session exists -- the same discipline the rest of the pipeline
    # follows.
    _refuses(
        lambda: K.environment_kernel(
            {"substrate_profile": "residency.repository.v1"},
            {"tasks": {"task-x": {"reward_id": "rew-nope"}}, "rewards": {}},
            {}, {}),
        "KERNEL_REWARD_UNRESOLVED", "dangling reward reference")

    # And `evalsplit` still refuses the same substrate earlier, by name.
    assert ES._EVALUABLE == ("residency.repository.v1",)


def test_k18_the_ladder_the_cli_and_the_earlier_laws_are_untouched():
    """A completeness check: this slice adds no rung, no command, no field.

    The kernel is an extraction. If it added an identity prefix, a CLI verb, or a
    receipt field, it would be a feature wearing an extraction's name.
    """
    laws = sorted(k for k in globals()
                  if k.startswith("test_k") and callable(globals()[k]))
    numbers = sorted(int(k.split("_")[1][1:]) for k in laws)
    assert numbers == list(range(1, 19)), numbers

    # No new rung. `identity.py` gained nothing, and the ladder is unchanged.
    src = inspect.getsource(I)
    assert "session" not in src.lower()
    assert "kernel" not in src.lower()

    # No new CLI verb: the kernel is not reachable from the command line yet, by
    # ruling -- `trvs serve --ors` is the next slice, not this one.
    from traaviis import cli
    cli_src = inspect.getsource(cli)
    assert "serve" not in cli_src or "--ors" not in cli_src
    assert "EpisodeKernelV1" not in cli_src
    assert "kernel" not in cli_src.lower()

    # The kernel is where the lifecycle lives and `evalone` is where the episode
    # lives; neither may take over the other.
    kernel_names = _identifiers(inspect.getsource(K))
    assert "_assemble_receipt" not in kernel_names
    assert "build_receipt_v1" not in kernel_names
    for name in ("_admit_episode", "_invalid_config_run", "_finish_episode"):
        assert hasattr(E, name), name

    # The public surface of `evalone` did not change.
    assert E.__all__ == [
        "eval_one", "evaluate", "build_receipt_v1",
        "EPISODE_VERSION", "EVALUATION_RUN_VERSION", "VERIFIER_EVIDENCE_VERSION",
        "UnsupportedPolicyError",
    ]


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
