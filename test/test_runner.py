"""Battery for the controlled runner + trace capture (traaviis.runner, RFC §10a).

Crosses the subprocess boundary against a deterministic stub agent. Pins: sealed
env (host not inherited), output cap, timeout -> timed_out, missing outputs,
writable-path violations, canonical TraceV1 identity (volatile-free, moves on an
observable-event change).

Runs with pytest, or standalone: `python3 test/test_runner.py`.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import runner as RUN  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = os.path.join(HERE, "fixtures", "stub_agent.py")

CONTENT = {"spec/one.md": "alpha\nbeta\n", "src/mod.py": "return 1\n"}

#: A caller-supplied ``PATH`` that this fixture *owns*.
#:
#: These policies deliberately declare a ``PATH`` — several laws below are about
#: R1 stripping it, and a policy that never supplies one cannot witness a strip.
#: But the value used to be ``os.environ["PATH"]``, and reading the host for a
#: value the runner is guaranteed to throw away buys nothing and couples the
#: battery to whoever ran it. That coupling is what moved ``test_kernel``'s
#: pinned ``episode-`` across sessions: there the same ambient read landed in a
#: task's ``agent_run_policy``, and ``agent_run_policy`` is inside ``task-``,
#: which is inside ``episode-``.
#:
#: It could not do that *here* — measured, not assumed: ``run_agent`` mints only
#: ``trace-``, and the trace records ``sorted(sealed_env.keys())`` **after**
#: ``_seal_env`` has dropped ``PATH``, so this file's ``trace-`` was already
#: byte-identical under the real host ``PATH`` and under a bogus one
#: (``trace-ff754732…`` both ways). Nothing here was failing, and nothing here
#: is being fixed. What changes is that the fixture no longer reads an input it
#: does not use, so the R1 laws below say what they mean: the value below is
#: never seen by any child, by construction rather than by luck.
CALLER_PATH = "/fixture-supplied/never-reaches-the-agent/bin"


def _policy(**over):
    p = {
        "policy_version": "traaviis.agent-run-policy.v1",
        "command_mode": "argv",
        "shell": False,
        "network": "disabled",
        "timeout_seconds": 30,
        "max_output_bytes": 4194304,
        "environment": {"TRAAVIIS_STUB_MODE": "ok", "PATH": CALLER_PATH},
        "writable_paths": ["."],
        "result_path": "result.json",
        "patch_path": "candidate.patch",
    }
    p.update(over)
    return p


def _run(mode="ok", **over):
    env = {"TRAAVIIS_STUB_MODE": mode, "PATH": CALLER_PATH}
    over.setdefault("environment", env)
    return RUN.run_agent([sys.executable, STUB], CONTENT, _policy(**over))


def test_ok_run_produces_result_and_patch():
    r = _run("ok")
    assert r["exit_code"] == 0
    assert r["timed_out"] is False
    assert r["result"]["format"] == "traaviis.agent-result.v1"
    assert "candidate.patch" in r["files_created"]
    assert r["patch_text"].startswith("--- a/src/mod.py")


def test_trace_is_sealed_and_has_id():
    r = _run("ok")
    tr = r["trace"]
    assert tr["trace_version"] == RUN.TRACE_VERSION
    assert tr["trace_id"].startswith("trace-")
    ev = tr["events"][0]
    assert ev["cwd"] == "."
    assert ev["exit_code"] == 0
    assert "TRAAVIIS_STUB_MODE" in ev["environment_keys"]


def test_trace_moves_on_observable_change():
    ok = _run("ok")["trace"]["trace_id"]
    bad = _run("badpatch")["trace"]["trace_id"]  # different created-files digest
    assert ok != bad


def test_missing_outputs_are_none():
    r = _run("nooutput")
    assert r["result"] is None
    assert r["patch_text"] is None


def test_timeout_flags_timed_out():
    r = _run("timeout", timeout_seconds=1)
    assert r["timed_out"] is True
    assert r["exit_code"] is None


def test_host_env_not_inherited():
    # A host var absent from the sealed map must not reach the child, AND a
    # caller-supplied PATH is stripped (R1): PATH is owned by the toolchain
    # resolver, never the caller. The stub runs from an absolute argv so it needs
    # no PATH, and the only surviving sealed key is TRAAVIIS_STUB_MODE.
    r = _run("ok")
    keys = set(r["trace"]["events"][0]["environment_keys"])
    assert keys == {"TRAAVIIS_STUB_MODE"}


def test_caller_path_cannot_move_the_trace_id():
    """R1 at the identity level: a stripped key may not reach ``trace-``.

    ``test_host_env_not_inherited`` above asserts the strip one level down, on
    ``environment_keys``. This asserts the consequence that actually matters:
    two runs whose policies differ *only* in a caller-supplied ``PATH`` — one
    absent, two with different values — must mint the same ``trace-``.

    It is here because the identity/host coupling that cost ``test_kernel``'s
    K27 a multi-session investigation entered through exactly this door: an
    ``agent_run_policy`` carrying ``os.environ["PATH"]``. ``run_agent`` mints no
    ``task-``, so that leak could never reach an id from *this* battery — the
    reason is a property of ``_seal_env``, though, not of the fixture, so it is
    worth one cheap law rather than a comment. If a future ``PATH`` ever
    survives sealing, this fails here, where the runner is, instead of surfacing
    as a moved receipt in a battery three layers up.
    """
    base = _run("ok", environment={"TRAAVIIS_STUB_MODE": "ok"})
    one = _run("ok", environment={"TRAAVIIS_STUB_MODE": "ok",
                                  "PATH": "/one/bin"})
    two = _run("ok", environment={"TRAAVIIS_STUB_MODE": "ok",
                                  "PATH": "/a/totally/different/two/bin:/x"})
    assert base["trace"]["trace_id"] == one["trace"]["trace_id"] == \
        two["trace"]["trace_id"], "a caller-supplied PATH moved trace-"
    # ...and the reason it cannot: PATH never enters the sealed map at all.
    for r in (base, one, two):
        assert "PATH" not in r["trace"]["events"][0]["environment_keys"]


def test_output_cap_truncates():
    # A tiny cap should mark truncation if the child emits anything; the ok stub
    # is silent, so force output via a one-liner that prints.
    printer = "import sys; sys.stdout.write('x'*100)"
    r = RUN.run_agent(
        [sys.executable, "-c", printer], CONTENT,
        _policy(max_output_bytes=10, environment={"PATH": CALLER_PATH}),
    )
    assert r["output_truncated"] is True
    assert len(r["stdout"]) == 10


def test_writable_path_violation_reported():
    r = _run("escape")
    # stub writes ../escape.txt; that path is outside the workspace tree so it is
    # not scanned as a created file, but a restrictive writable set on an in-tree
    # write is the real law — assert the mechanism via an in-tree case:
    r2 = RUN.run_agent(
        [sys.executable, STUB], CONTENT,
        _policy(writable_paths=["src/"],
                environment={"TRAAVIIS_STUB_MODE": "ok", "PATH": CALLER_PATH}),
    )
    # result.json + candidate.patch are written at root, outside "src/"
    assert "result.json" in r2["policy_violations"]
    assert "candidate.patch" in r2["policy_violations"]


def test_deletion_moves_trace():
    # A run that deletes a sealed file must be observable: the files_deleted digest
    # enters trace-, so the trace id differs from a no-delete run over the same tree.
    ok = _run("ok")["trace"]["trace_id"]
    deleter = [sys.executable, "-c", "import os; os.remove('src/mod.py')"]
    r = RUN.run_agent(deleter, CONTENT,
                      _policy(environment={"X": "1"}))
    assert "src/mod.py" in r["files_deleted"]
    assert r["trace"]["trace_id"] != ok


def test_command_normalized_absolute_executable_not_in_trace():
    # The absolute interpreter path is machine-specific; the canonical trace must
    # record only its basename so trace- is host-independent (R4).
    r = _run("ok")
    cmd = r["trace"]["events"][0]["command"]
    assert cmd[0] == os.path.basename(sys.executable)
    assert sys.executable not in cmd
    assert not any(os.path.isabs(tok) for tok in cmd)


# --- A result the decoder cannot decode is `None`, never a crash -------------
#
# `run_agent` reads the candidate's `result.json` and hands the orchestrator a
# parsed object or `None`; the clause that produces the `None` is the seam where
# "the agent wrote nonsense" is separated from "the evaluator broke". §10a rules
# that the first is a `fail`, so anything that reaches this clause and escapes
# instead of being absorbed is a way for the evaluated agent to delete its own
# evaluation.

def test_a_pathologically_nested_result_is_none_not_a_crash():
    """A legal-but-undecodable `result.json` yields `None`, and the run completes.

    The bytes here are valid RFC 8259 — a nested array, no syntax error anywhere
    — but `json.loads` cannot decode them: it raises `RecursionError`, which is a
    `RuntimeError`, so the guard's original `(ValueError, UnicodeDecodeError)`
    did not catch it. Measured before the fix, through the committed
    `residency-demo` bundle: `trvs eval-one` died with a traceback, exit 1, no
    receipt on stdout and nothing under `--output`. A crashed run is not a bad
    run, it is an absent one — `compare` refuses a pair it cannot read, so the
    bad score simply never existed.

    The law's own precondition is asserted rather than assumed. On CPython 3.12+
    the decoder's bound is the real C stack measured at call time, not
    `sys.getrecursionlimit()`, so a host with more stack could decode the
    fixture's depth and quietly turn this into a test of nothing. The check runs
    against the bytes the agent actually wrote (out of `result_bytes`), not a
    constant copied from the fixture, so the two cannot drift.

    It used to read those bytes out of `workspace_after`, a second in-memory
    copy of the whole post-run workspace that `run_agent` built for no consumer
    but this line. 9D removed it: an unbounded decode of every
    candidate-controlled file, retained after it had already been hashed, is the
    same unbounded read this law's own subject is about. `result_bytes` is the
    one entry that was ever wanted, and it is the bounded copy the trace's
    `result_file_digest` is taken over — so the law now checks the bytes that
    were *sealed*, which is strictly the better subject.
    """
    r = _run("deepresult")
    raw = r["result_bytes"]
    try:
        json.loads(raw)
    except RecursionError:
        pass  # the precondition holds: this host cannot decode those bytes
    else:
        raise AssertionError(
            "this host decoded %d bytes of nested JSON; raise `_DEEP` in "
            "test/fixtures/stub_agent.py or this law asserts nothing" % len(raw))

    assert r["exit_code"] == 0        # the agent itself succeeded
    assert r["timed_out"] is False
    assert r["result"] is None        # undecodable -> None, scored as fail
    # Everything else the run observed is still observed: the undecodable file is
    # one output among several, not a poison pill for the whole capture.
    assert r["patch_text"].startswith("--- a/src/mod.py")
    assert "result.json" in r["files_created"]
    assert r["trace"]["trace_id"].startswith("trace-")


def test_an_undecodable_result_is_indistinguishable_from_no_result():
    """`None` means one thing, so garbage cannot be a third state to exploit.

    `nooutput` writes no `result.json` at all and `deepresult` writes one that
    cannot be decoded. Both must reach the orchestrator as the same `None`: if
    an undecodable result were its own state, a downstream branch could treat it
    as something other than the missing output §10a says it is.
    """
    assert _run("deepresult")["result"] is _run("nooutput")["result"] is None


# --- A filename the evaluator cannot write down --------------------------------
#
# The two laws above close routes through the candidate's *submission* — bytes it
# wrote into `result.json`. This one closes a route through the evaluator's own
# *observation*: `_scan` walks the workspace after the run, and a POSIX filename
# is bytes, so `os.walk` hands back a surrogate-escaped `str` for a name that is
# not UTF-8. That name went straight into `files_created` and into
# `files_created_digest`, where `identity.canonical_bytes` refused it and the
# refusal escaped `run_agent` *before it returned*. Same erased-grade outcome as
# the two above, one line of agent code, and no malformed byte required.

def _badname_key(mapping):
    """The one escaped key in ``mapping``, asserting the fixture reproduced.

    On a filesystem that enforces UTF-8 names the stub's `open` raises and
    `badname` degrades to `ok`; this is where that platform finds out, rather
    than silently running a law about nothing.
    """
    keys = [k for k in mapping if k.startswith(RUN._UNDECODABLE_NAME_PREFIX)]
    assert len(keys) == 1, (
        "expected exactly one escaped name, got %r -- if this host's filesystem "
        "rejects non-UTF-8 filenames the `badname` mode cannot reproduce here"
        % (sorted(mapping),))
    return keys[0]


def test_an_undecodable_filename_is_recorded_not_a_crash():
    """An agent cannot crash its evaluator by naming a file.

    The stub writes `b"evil\\xff.txt"` and then submits the *same valid answer*
    the `ok` mode submits — nothing about its output is malformed. Measured
    before the fix, through the committed `residency-demo` bundle: `trvs
    eval-one` exited 1 with `IdentityError [CANONICAL_ENCODING] ... the key at $
    contains U+DCFF at offset 4`, printed no receipt and wrote nothing under
    `--output`.

    The run must complete and the file must appear in the inventory. Both halves
    are the law: completing without recording it would be the *other* hack — a
    candidate writing files the evidence does not know about.
    """
    r = _run("badname")
    assert r["exit_code"] == 0
    assert r["timed_out"] is False
    key = _badname_key(r["files_created"])
    # Everything else the run did is still recorded alongside it.
    assert {"result.json", "candidate.patch"} <= set(r["files_created"])
    assert r["trace"]["trace_id"].startswith("trace-")
    assert r["result"]["format"] == "traaviis.agent-result.v1"
    # ...and the record is lossless: the real bytes come back exactly.
    raw = bytes.fromhex(key[len(RUN._UNDECODABLE_NAME_PREFIX):])
    assert raw == b"evil\xff.txt", raw


def test_an_undecodable_filename_still_trips_the_write_escape_gate():
    """Recording it is not enough — the tamper gate has to be able to see it.

    `policy_violations` is derived from the same maps, so the remedy that merely
    *dropped* the offending entry would have taken the write-escape observation
    (R3/E2) out with it: a candidate could write anywhere it liked as long as it
    chose a name the evaluator could not spell. That is strictly worse than the
    crash it would have replaced, because the episode then completes and looks
    clean. Here the escaped name is outside `writable_paths` and is reported.
    """
    r = RUN.run_agent(
        [sys.executable, STUB], CONTENT,
        _policy(writable_paths=["src/"],
                environment={"TRAAVIIS_STUB_MODE": "badname"}),
    )
    key = _badname_key(r["files_created"])
    assert key in r["policy_violations"], r["policy_violations"]


def test_the_escape_is_the_identity_on_every_encodable_name():
    """No existing id moves, by construction rather than by luck over a corpus.

    `_evidence_name` is only allowed to differ from its input where `str.encode`
    refuses. Checked two ways: directly, over names chosen to sit near the
    interesting edges (astral planes, combining marks, a literal backslash-x
    sequence, a `%`), and at the identity level, by pinning that a normal run's
    `trace-` is unchanged.
    """
    for name in ("src/mod.py", "spec/one.md", "a b/c.txt", "µ/é.txt",
                 "\U0001F600.txt", "é.md", "evil\\xff.txt", "100%.txt",
                 "/leading-slash-is-not-a-relpath", ""):
        assert RUN._evidence_name(name) == name, name
    # The literal-backslash name above is the collision a `backslashreplace`
    # rendering would have created: it is encodable, so it keeps its own name,
    # while the undecodable one is escaped into a namespace it cannot reach.
    assert RUN._evidence_name("evil\udcff.txt") != "evil\\xff.txt"


def test_an_escaped_name_can_never_collide_with_a_real_one():
    """Injectivity, which is what stops two workspaces sharing one `trace-`.

    Two independent reasons, both asserted: hex is injective on bytes, and the
    escaped form starts with `/`, which `os.path.relpath` cannot produce for a
    path under the root — so it is unreachable for a real file. The second is
    also why `paths.safe_relposix` rejects it, which turns "someone opened an
    evidence name as a path" into a loud error rather than a read of the wrong
    file.
    """
    from traaviis.paths import PathError, safe_relposix

    a = RUN._evidence_name("evil\udcff.txt")
    b = RUN._evidence_name("evil\udcfe.txt")
    assert a != b, "two distinct names collapsed onto one evidence name"
    assert a.startswith("/")
    try:
        safe_relposix(a)
    except PathError:
        pass
    else:
        raise AssertionError("an escaped evidence name was accepted as a path")


def test_a_snapshot_refuses_the_same_name_the_runner_escapes():
    """The other half of the ruling: an operator's tree is not a candidate's.

    `snapshot.build_snapshot` meets the identical byte sequence and *refuses*
    it, and the asymmetry is deliberate. The runner is inventorying what the
    agent under evaluation did, and an agent that can crash the evaluator can
    delete its own grade — so that path must complete no matter what. A subject
    tree is operator-authored input: nobody gains by crashing their own snapshot
    build. And a snapshot's `files` keys become `content` keys, which
    `runner._materialize` pushes through `safe_relposix` — so the runner's
    escape, applied there, would seal a subject that admits cleanly and then
    cannot be laid down.

    What the fix buys is the message. Before it the operator got
    `IdentityError ... the key at $ contains U+DCFF at offset 4`, which names no
    file; now the refusal names the path and the raw bytes. This law lives in
    the runner battery on purpose — it is the *contrast* that is being pinned,
    and splitting the two halves across two files is how they drift apart.
    """
    import tempfile

    from traaviis import snapshot as SNAP
    from traaviis.paths import PathError

    root = tempfile.mkdtemp(prefix="trvs-badname-snap-")
    with open(os.path.join(root, "ok.md"), "wb") as fh:
        fh.write(b"fine\n")
    with open(os.path.join(root.encode(), b"evil\xff.txt"), "wb") as fh:
        fh.write(b"x")

    try:
        SNAP.build_snapshot(root)
    except PathError as exc:
        assert "evil" in str(exc) and "6576696cff2e747874" in str(exc), exc
    else:
        raise AssertionError(
            "build_snapshot sealed a name that is not UTF-8 -- or this host's "
            "filesystem rejected the name and the law tested nothing")

    # ...and an *excluded* unsealable name is not a refusal: exclusions run
    # first, so a tree that never intended to seal the file still seals.
    snap = SNAP.build_snapshot(root, exclusions=["evil*"])
    assert set(snap["files"]) == {"ok.md"}


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
