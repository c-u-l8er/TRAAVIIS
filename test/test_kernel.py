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

K19-K28 close a defect the first eighteen could not see. `finalize` checked
`state == "started"` and wrote `"finalized"` in two separate statements with the
whole verifier plan in between, so two concurrent callers both passed the check
and both ran the plan -- which for a Residency task means executing a
candidate's test suite twice. Both received the *same receipt*, so nothing
downstream looked wrong; the identity did not move and only the work doubled. A
sequential battery cannot reach it, and a server accepting concurrent requests
reaches it immediately. These laws force the interleaving instead of hoping for
it, and assert both directions of the fix: one claimant per session (K19-K21,
K24), a refusal for the loser and for a close attempted mid-flight (K20, K22), a
terminal failure state (K23), and -- the part a lock is always tempted to break
-- that linearizing *one* session did not serialize the kernel (K25, K26).

**On what needs an engine.** The kernel is substrate-neutral and its lifecycle
laws are proven against the deterministic stub agent with injected verifiers, so
almost every law needs no Forge checkout -- including K17, whose refusals are
reached before any substrate is opened. Only K14 and K27, which are genuinely
claims about a *packed environment*, pack a real template and SKIP when no
engine is locatable.

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
import time

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


def _packed_package():
    """A real packed `residency-repair` package, scaffolded and packed once.

    Two laws are genuinely about a *packed environment* (K14, K27) and packing
    costs a Forge lower. The tree is read-only for both -- each law evaluates
    into its own output directory -- so building it once is sharing an input,
    not sharing state.
    """
    if "pkg" not in _TMP:
        eng = _engine_or_skip()
        from traaviis import pack as P, scaffold as SC
        env = os.path.join(_tmp(), "packed-env")
        out = os.path.join(_tmp(), "packed-pkg")
        for p in (env, out):
            shutil.rmtree(p, ignore_errors=True)
        SC.materialize(TEMPLATE, env)
        P.pack(env, out, engine=eng)
        _TMP["pkg"] = out
    return _TMP["pkg"]


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
#: The agent used against a *packed* environment (K14, K27). The stub above
#: answers the injected one-task kernel; this one produces a real patch against
#: the `residency-repair` template, and is the same fixture `test_eval_split`
#: drives, so a divergence here would be a divergence from the shipped path.
RESIDENCY_AGENT = [sys.executable,
                   os.path.join(HERE, "fixtures", "residency_agent.py")]


def _kernel(task=None, extra=ALL_PASS):
    """A one-task Residency kernel over the stub subject."""
    return K.local_kernel(
        task or _task(), CONTENT, REWARD_SPEC, snapshot=_snapshot(),
        extra_verifiers=extra, platform="linux-x86_64", toolchain=TOOLCHAIN)


def _canonical(obj):
    return I.canonical_bytes(obj)


def _kernel_error_codes(source):
    """Every code a `KernelError(...)` in `source` is actually raised with."""
    codes = set()
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name != "KernelError":
            continue
        first = node.args[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), \
            "a KernelError code must be a literal, not a computed value"
        codes.add(first.value)
    return sorted(codes)


def _law_names():
    """Every law defined in this module, by name.

    Read from `globals()` rather than from a hand-kept list, so a law that is
    deleted or renamed makes the completeness checks (K18, K28) fail instead of
    silently shrinking the battery.
    """
    return sorted(k for k, v in globals().items()
                  if k.startswith("test_k") and callable(v))


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
    out = _packed_package()

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
        report = ES.eval_split(out, "all", RESIDENCY_AGENT)
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

    # Structurally: the lock is only ever taken around session-table operations
    # and state transitions -- six blocks, in `open_sessions`, `session`,
    # `start`, `_claim_finalize`, `_release_finalize` and `close`. None of them
    # may contain admission, scoring, or a subprocess (K26 proves the same thing
    # dynamically, by holding the work open and taking the lock from outside).
    source = inspect.getsource(K.ResidencyKernelV1)
    assert source.count("with self._lock:") == 6, source.count("with self._lock:")
    for forbidden in ("_admit_episode", "_finish_episode", "_invalid_config_run",
                      "run_agent"):
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
    numbers = sorted(int(k.split("_")[1][1:]) for k in _law_names())
    assert numbers == list(range(1, 29)), numbers

    # No new rung. `identity.py` gained nothing, and the ladder is unchanged.
    src = inspect.getsource(I)
    assert "session" not in src.lower()
    assert "kernel" not in src.lower()

    # No CLI verb of its own. `trvs serve --ors` has since shipped, so the
    # original form of this clause -- "the command line cannot reach the kernel
    # yet" -- expired the moment a transport existed. What must not expire is
    # *how* it is reached: the CLI drives an adapter, and the adapter drives the
    # kernel. A command that constructed a kernel itself would make the CLI a
    # second transport, and the two would drift.
    from traaviis import cli
    cli_src = inspect.getsource(cli)
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


# ------------------------------------------------- K19-K28: linearization
# The kernel's lock is short by design, but a short lock is not the same as an
# atomic transition. `finalize` used to read `state == "started"` in one
# statement and write `"finalized"` in a later one, with the entire verifier
# plan in between. Two callers could both pass the check before either wrote,
# and the second was not refused -- it re-ran the plan, which for a Residency
# task can mean executing a candidate's test suite a second time. Both callers
# received the *same receipt*, which is precisely why it hides: the identity
# does not move and the work doubles. Sequential tests cannot see it; a server
# accepting concurrent requests makes it reachable from outside.
#
# These laws force the interleaving rather than hoping for it. `_finish_episode`
# is held open so a second caller is *guaranteed* to arrive mid-flight, which
# makes the race deterministic in both directions: it would fail every run
# against the old code and pass every run against the new.


class _HeldScoring(object):
    """Hold `_finish_episode` open, and count how many callers reach it.

    The counter is the point. "Exactly one finalization succeeded" is a weaker
    statement than "the scoring work ran once" -- the old code satisfied the
    first (both callers got the same receipt) while violating the second.
    """

    def __init__(self, party=1, fail=None):
        self.calls = []
        self.entered = threading.Semaphore(0)
        self.release = threading.Event()
        #: When >1, callers rendezvous *inside* the scoring work, so the law
        #: fails if the kernel serialized them instead of overlapping them.
        self.barrier = threading.Barrier(party) if party > 1 else None
        self.fail = fail
        self._real = None

    def __enter__(self):
        self._real = K._finish_episode

        def wrapped(plan, run, **kw):
            self.calls.append(plan["task_id"])
            self.entered.release()
            if self.barrier is not None:
                self.barrier.wait(60)
            self.release.wait(60)
            if self.fail is not None:
                raise self.fail
            return self._real(plan, run, **kw)

        K._finish_episode = wrapped
        return self

    def __exit__(self, *exc):
        K._finish_episode = self._real
        self.release.set()
        return False

    def await_entry(self, timeout=60):
        assert self.entered.acquire(timeout=timeout), \
            "no caller reached the scoring work"


def _in_thread(fn):
    """Run `fn` on a thread; return (thread, outcome-dict)."""
    out = {}

    def body():
        try:
            out["value"] = fn()
        except BaseException as exc:  # pragma: no cover - asserted by callers
            out["error"] = exc

    t = threading.Thread(target=body)
    t.daemon = True
    t.start()
    return t, out


def _await(predicate, what, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for %s" % what)


def test_k19_two_simultaneous_finalizations_produce_exactly_one_claimant():
    """The forced race: two callers, one session, one execution of the plan.

    Both threads pass a barrier and then race into `finalize`. Whoever claims
    first enters the scoring work and is held there; the other arrives while the
    session is `finalizing` and must be refused. Against the pre-patch kernel
    both would enter, both would score, and both would return the same receipt.
    """
    k = _kernel()
    sid = k.start(k.list_tasks()[0])
    session = k.session(sid)
    run = RUN.run_agent(AGENT, session.content, session.policy)

    gate = threading.Barrier(2)
    with _HeldScoring() as held:
        def contend():
            gate.wait(30)
            return k.finalize(sid, run)

        t1, a = _in_thread(contend)
        t2, b = _in_thread(contend)
        held.await_entry()
        _await(lambda: "error" in a or "error" in b, "the losing call to refuse")
        held.release.set()
        t1.join(60)
        t2.join(60)

    assert not t1.is_alive() and not t2.is_alive()
    assert len(held.calls) == 1, \
        "the verifier plan ran %d times for one session" % len(held.calls)

    outcomes = [a, b]
    winners = [o for o in outcomes if "value" in o]
    losers = [o for o in outcomes if "error" in o]
    assert len(winners) == 1, outcomes
    assert len(losers) == 1, outcomes
    assert winners[0]["value"]["receipt"]["status"] == R.STATUS_OK
    assert k.session(sid).state == K.SESSION_FINALIZED
    assert k.session(sid).result is winners[0]["value"]
    k.close(sid)


def test_k20_the_losing_call_is_refused_by_name():
    """The loser gets a typed refusal, not a second receipt.

    `KERNEL_SESSION_STATE` carrying `state = finalizing` says exactly what
    happened: the session exists, it is not available, and somebody else has it.
    The refusal must also stay catchable as the plain `AdmissionError` the split
    and the CLI already handle, or a busy session would abort a run instead of
    being recorded as one failed episode.
    """
    from traaviis import admission as _adm

    k = _kernel()
    sid = k.start(k.list_tasks()[0])
    session = k.session(sid)
    run = RUN.run_agent(AGENT, session.content, session.policy)

    with _HeldScoring() as held:
        t, out = _in_thread(lambda: k.finalize(sid, run))
        held.await_entry()

        assert k.session(sid).state == K.SESSION_FINALIZING
        ex = _refuses(lambda: k.finalize(sid, run), "KERNEL_SESSION_STATE",
                      "a session another caller is finalizing")
        assert ex.detail["state"] == K.SESSION_FINALIZING
        assert ex.detail["session_id"] == sid
        assert isinstance(ex, _adm.AdmissionError)
        assert isinstance(ex, AdmissionError)

        held.release.set()
        t.join(60)

    assert "error" not in out, out.get("error")
    assert len(held.calls) == 1
    k.close(sid)


def test_k21_the_scoring_work_runs_at_most_once_per_session():
    """Across every route to a second finalization, the plan executes once.

    Concurrent, then sequential-after-success, then sequential-after-close:
    three different ways to ask twice, one execution.
    """
    k = _kernel()
    sid = k.start(k.list_tasks()[0])
    session = k.session(sid)
    run = RUN.run_agent(AGENT, session.content, session.policy)

    with _HeldScoring() as held:
        t, out = _in_thread(lambda: k.finalize(sid, run))
        held.await_entry()
        _refuses(lambda: k.finalize(sid, run), "KERNEL_SESSION_STATE",
                 "mid-flight")
        held.release.set()
        t.join(60)
        assert "error" not in out, out.get("error")

        # After success.
        _refuses(lambda: k.finalize(sid, run), "KERNEL_SESSION_STATE",
                 "already finalized")
        # After close: the handle names nothing at all.
        assert k.close(sid) is True
        _refuses(lambda: k.finalize(sid, run), "KERNEL_SESSION_UNKNOWN",
                 "closed session")

    assert len(held.calls) == 1, held.calls


def test_k22_closing_a_finalizing_session_is_refused():
    """`close` is idempotent, not a way to cancel work already in flight.

    Removing a `finalizing` session from the table would not stop the verifiers
    -- it would only make the result unattributable, and would let a third
    caller `start` past a test run still executing. So it is the one state
    `close` refuses, and the refusal is by name.
    """
    k = _kernel()
    sid = k.start(k.list_tasks()[0])
    session = k.session(sid)
    run = RUN.run_agent(AGENT, session.content, session.policy)

    with _HeldScoring() as held:
        t, out = _in_thread(lambda: k.finalize(sid, run))
        held.await_entry()

        ex = _refuses(lambda: k.close(sid), "KERNEL_SESSION_BUSY",
                      "closing a session mid-finalization")
        assert ex.detail["state"] == K.SESSION_FINALIZING
        assert k.open_sessions() == [sid], "the refusal must not have removed it"

        held.release.set()
        t.join(60)

    assert "error" not in out, out.get("error")
    # Once it is no longer in flight, the same call succeeds.
    assert k.close(sid) is True
    assert k.open_sessions() == []
    assert k.close(sid) is False, "closing an absent handle stays idempotent"


def test_k23_a_failed_finalization_leaves_a_terminal_session():
    """A raise inside scoring is recorded, not swallowed and not rolled back.

    The session stays in the table so a local caller can see *which* session
    failed, in a state that says so. Rolling it back to `started` would be the
    dangerous choice: the verifier plan may already have executed a test suite,
    written files, or launched a process.
    """
    k = _kernel()
    sid = k.start(k.list_tasks()[0])
    session = k.session(sid)
    run = RUN.run_agent(AGENT, session.content, session.policy)

    boom = RuntimeError("verifier exploded")
    with _HeldScoring(fail=boom) as held:
        held.release.set()
        try:
            k.finalize(sid, run)
        except RuntimeError as exc:
            assert exc is boom
        else:
            raise AssertionError("a failing finalization returned a run")

    assert len(held.calls) == 1
    assert k.session(sid).state == K.SESSION_FINALIZE_FAILED
    assert k.session(sid).result is None
    assert k.open_sessions() == [sid], "a failed session is not auto-released"
    assert k.session(sid).describe()["state"] == K.SESSION_FINALIZE_FAILED
    assert k.close(sid) is True


def test_k24_a_failed_finalization_cannot_be_retried():
    """`finalize_failed` is terminal on that session; the recovery is a new one.

    Retrying in place would re-execute effects that may already have happened.
    Starting again is honest, because a second session is a second episode and
    says so.
    """
    k = _kernel()
    task_id = k.list_tasks()[0]
    sid = k.start(task_id)
    session = k.session(sid)
    run = RUN.run_agent(AGENT, session.content, session.policy)

    with _HeldScoring(fail=RuntimeError("verifier exploded")) as held:
        held.release.set()
        try:
            k.finalize(sid, run)
        except RuntimeError:
            pass
        ex = _refuses(lambda: k.finalize(sid, run), "KERNEL_SESSION_STATE",
                      "retrying a failed finalization")
        assert ex.detail["state"] == K.SESSION_FINALIZE_FAILED
        assert len(held.calls) == 1, "a retry re-entered the scoring work"

    # The kernel itself is undamaged: a fresh session over the same task scores.
    sid2 = k.start(task_id)
    s2 = k.session(sid2)
    ok = k.finalize(sid2, RUN.run_agent(AGENT, s2.content, s2.policy))
    assert ok["receipt"]["status"] == R.STATUS_OK
    assert k.close(sid) is True
    assert k.close(sid2) is True


def test_k25_two_different_sessions_may_finalize_concurrently():
    """Linearizing one session must not serialize the kernel.

    The rendezvous is *inside* the scoring work: both sessions have to be there
    at the same time for the barrier to clear. A kernel that took a lock around
    finalization would deadlock this law rather than merely slow it down.
    """
    k = _kernel()
    task_id = k.list_tasks()[0]
    a, b = k.start(task_id), k.start(task_id)
    runs = {sid: RUN.run_agent(AGENT, k.session(sid).content,
                               k.session(sid).policy) for sid in (a, b)}

    with _HeldScoring(party=2) as held:
        held.release.set()
        t1, o1 = _in_thread(lambda: k.finalize(a, runs[a]))
        t2, o2 = _in_thread(lambda: k.finalize(b, runs[b]))
        t1.join(90)
        t2.join(90)

    assert not t1.is_alive() and not t2.is_alive(), \
        "two sessions could not be finalized at the same time"
    assert "error" not in o1, o1.get("error")
    assert "error" not in o2, o2.get("error")
    assert len(held.calls) == 2
    assert _canonical(o1["value"]["receipt"]) == _canonical(o2["value"]["receipt"])
    assert k.session(a).state == K.SESSION_FINALIZED
    assert k.session(b).state == K.SESSION_FINALIZED
    k.close(a)
    k.close(b)


def test_k26_no_lock_is_held_while_verifiers_execute():
    """Proven dynamically, not by reading the source.

    While one session is inside the scoring work, the kernel's lock is free and
    every table operation still answers: another session can be started, listed,
    read and finalized to completion. K16 makes the same claim lexically; this
    one makes it while the claim is actually under load.
    """
    k = _kernel()
    task_id = k.list_tasks()[0]
    sid = k.start(task_id)
    session = k.session(sid)
    run = RUN.run_agent(AGENT, session.content, session.policy)

    with _HeldScoring() as held:
        t, out = _in_thread(lambda: k.finalize(sid, run))
        held.await_entry()

        assert k._lock.acquire(blocking=False) is True, \
            "the lock is held across the verifier plan"
        k._lock.release()
        assert k.session(sid).state == K.SESSION_FINALIZING
        assert sid in k.open_sessions()

        # A whole second episode completes while the first is mid-flight. Its
        # own scoring is the *real* one -- the hold is per-call, and this call
        # goes through the same held wrapper, so release it first.
        held.release.set()
        other = k.start(task_id)
        s2 = k.session(other)
        run2 = k.finalize(other, RUN.run_agent(AGENT, s2.content, s2.policy))
        assert run2["receipt"]["status"] == R.STATUS_OK
        k.close(other)

        t.join(60)

    assert "error" not in out, out.get("error")
    assert _canonical(out["value"]["receipt"]) == _canonical(run2["receipt"])
    k.close(sid)


#: The `episode-…` this fixture minted *before* the linearization patch,
#: recomputed from the shipped `TRAAVIIS_EPISODE_KERNEL_CLOSURE` packet. Every
#: input to it is fixture-determined -- the declared toolchain, the platform
#: string, the stub agent's deterministic output, the injected verifier versions
#: -- so it is a host-independent constant, and pinning it is the one check that
#: a *later* slice cannot satisfy by moving both sides of a comparison.
PRE_LINEARIZATION_EPISODE_ID = (
    "episode-7265e90a5680080bc0d5f995681b96360a214652f82771d52ff70336fa9e26c5")


def test_k27_local_receipts_are_unmoved_by_the_linearization():
    """A concurrency patch may not move a single byte of a serial result.

    Four independent derivations must agree with each other *and* with the id
    the pre-patch kernel minted: the adapter, the receipt-only wrapper, a
    hand-driven session, and -- when an engine is present -- a real `eval_split`
    over a packed environment, run twice.
    """
    task = _task()
    common = dict(snapshot=_snapshot(), extra_verifiers=ALL_PASS,
                  platform="linux-x86_64", toolchain=TOOLCHAIN)

    via_evaluate = E.evaluate(task, CONTENT, AGENT, REWARD_SPEC, **common)["receipt"]
    via_eval_one = E.eval_one(task, CONTENT, AGENT, REWARD_SPEC, **common)

    k = _kernel(task)
    sid = k.start(k.list_tasks()[0])
    s = k.session(sid)
    hand = k.finalize(sid, RUN.run_agent(AGENT, s.content, s.policy))["receipt"]
    k.close(sid)

    assert _canonical(via_evaluate) == _canonical(via_eval_one) == _canonical(hand)
    assert via_evaluate["episode_id"] == PRE_LINEARIZATION_EPISODE_ID, (
        "the linearization moved the episode identity: %s"
        % via_evaluate["episode_id"])

    # And the split path, which is the one a server will share a kernel with.
    package = _packed_package()
    first = ES.eval_split(package, "all", RESIDENCY_AGENT,
                          output=os.path.join(_tmp(), "k27-a"))
    second = ES.eval_split(package, "all", RESIDENCY_AGENT,
                           output=os.path.join(_tmp(), "k27-b"))
    assert first["episodes"] == second["episodes"]
    assert first["totals"] == second["totals"]
    assert first == second


def test_k28_the_linearization_added_no_identity_and_no_surface():
    """Completeness: K1-K28 are all present, and the patch is transport-only.

    A concurrency fix that quietly grew a rung, a CLI verb or a receipt field
    would be a feature wearing a bug fix's name. The lifecycle is the frozen
    four states; `closed` is deliberately *not* among them, because a closed
    session is forgotten rather than retained.
    """
    numbers = sorted(int(k.split("_")[1][1:]) for k in _law_names())
    assert numbers == list(range(1, 29)), numbers

    assert K.SESSION_STATES == ("started", "finalizing", "finalized",
                                "finalize_failed")
    assert "closed" not in K.SESSION_STATES
    assert K.SESSION_CLOSEABLE == ("started", "finalized", "finalize_failed")
    assert K.SESSION_FINALIZING not in K.SESSION_CLOSEABLE
    assert K.OPERATIONS == ("list_tasks", "start", "observe", "step", "reset",
                            "finalize", "close"), "the seven did not change"

    # No new rung, no new CLI surface -- restated because this patch is exactly
    # the kind that would be tempted to add one.
    assert "session" not in inspect.getsource(I).lower()
    from traaviis import cli
    assert "kernel" not in inspect.getsource(cli).lower()

    # The refusal vocabulary grew by exactly one code, and it is a lifecycle
    # refusal rather than an admission one. The codes are read from the
    # `KernelError(...)` constructions themselves rather than from a text scan,
    # because `KERNEL_VERSION` is a *name* in this module and matches any
    # plausible `KERNEL_[A-Z_]+` pattern -- the same parse-don't-grep correction
    # K10, K12 and K18 already needed.
    codes = _kernel_error_codes(inspect.getsource(K))
    assert "KERNEL_SESSION_BUSY" in codes
    assert codes == [
        "KERNEL_OPERATION_UNSUPPORTED", "KERNEL_REWARD_UNRESOLVED",
        "KERNEL_RUN_RESULT_MISSING", "KERNEL_RUN_RESULT_UNEXPECTED",
        "KERNEL_SESSION_BUSY", "KERNEL_SESSION_STATE", "KERNEL_SESSION_UNKNOWN",
        "KERNEL_SUBSTRATE_UNSUPPORTED", "KERNEL_TASK_UNIDENTIFIED",
        "KERNEL_TASK_UNKNOWN",
    ], codes

    # The public surface of `evalone` still did not change.
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
