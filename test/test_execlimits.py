"""Laws for `traaviis.execution-limits.v1` — the resource totality boundary (9D).

The defect this battery exists for is not an exception and cannot be caught. A
candidate that leaves one named pipe behind::

    mkfifo output.pipe        # then exit 0

made the evaluator's post-run scan block in `open()` **forever**, after the
agent had already exited. No verifier raised, no subprocess was still under a
timeout, no receipt was written, no episode was persisted. That is a seventh way
for a candidate to erase its own failing score, and it is the cleanest of the
seven: it requires no malformed bytes, no crafted JSON, and no bad answer.

The same seam had three more shapes, all of them the same mistake — a bound
applied *after* an unbounded read:

* `_scan` did `open(p, "rb").read()` on every entry, with no file-count, no
  per-file and no total bound. One sparse file, or a million small ones.
* `result.json` and `candidate.patch` were read whole and *then* offered to the
  8 MiB JSON boundary. `boundedjson`'s own docstring said so: the real closure
  for size is a declared byte bound on the file.
* `subprocess.run(stdout=PIPE)` buffers everything and caps afterwards, and its
  `timeout=` kills the direct child only — so a grandchild holding stdout
  outlives the deadline that was supposed to end the wait.

Nine laws are required by the ruling and each is named for what it proves:

    X1  a candidate-created FIFO does not hang
    X2  a candidate-created socket is refused
    X3  a single oversized workspace file is refused
    X4  too many files are refused
    X5  an oversized result is refused before a full read
    X6  an oversized patch is refused before a full read
    X7  infinite stdout is bounded without OOM
    X8  a grandchild holding stdout is killed and reaped
    X9  every refusal still emits a receipt

The rest pin the profile itself (X10-X13), the bounded worker IPC (X14-X16),
non-vacuity (X17-X19) -- remove the guard in an isolated copy of the package and
watch the law go red, because a law that would pass with the code deleted is not
evidence of anything -- X20, the one outcome this boundary must **not** absorb,
and X21, which exists because a duplicated guard made one of these very proofs
unsound.

**Every law here runs everywhere.** None needs a Forge engine and none skips.
`Skip` is defined and counted anyway, for the reason `test_boundedjson` gives:
a battery that cannot report a skip will one day report a skipped law as a
passing one.

Run directly:      python3 test/test_execlimits.py
Run under pytest:  pytest test/test_execlimits.py
"""
import ast
import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import execlimits as E  # noqa: E402
from traaviis import runner as RUN  # noqa: E402
from traaviis import forge_adapter as FA  # noqa: E402
from traaviis import forge_worker as FW  # noqa: E402

PKG = os.path.join(REPO, "traaviis")
HERE = os.path.dirname(os.path.abspath(__file__))
STUB = os.path.join(HERE, "fixtures", "stub_agent.py")

CONTENT = {"spec/one.md": "alpha\nbeta\n", "src/mod.py": "return 1\n"}

#: Generous, and deliberately not tight. These laws prove *termination*, not
#: speed: the failure they guard against is an evaluator that never comes back,
#: so the assertion that matters is "it returned at all". A tight budget would
#: convert a slow host into a red law, which is the host-dependent verdict this
#: repository refuses everywhere else.
TERMINATES_WITHIN = 120.0


class Skip(Exception):
    pass


def _policy(**over):
    p = {
        "policy_version": "traaviis.agent-run-policy.v1",
        "command_mode": "argv",
        "shell": False,
        "network": "disabled",
        "timeout_seconds": 60,
        "max_output_bytes": 4194304,
        "environment": {"TRAAVIIS_STUB_MODE": "ok"},
        "writable_paths": ["."],
        "result_path": "result.json",
        "patch_path": "candidate.patch",
    }
    p.update(over)
    return p


def _run(mode, **over):
    """One stub-agent run, with the wall clock it took to come back.

    The elapsed time is returned rather than asserted inside, because every law
    below wants to say something slightly different about it and a helper that
    made the assertion would hide which law was actually doing the work.
    """
    over.setdefault("environment", {"TRAAVIIS_STUB_MODE": mode})
    started = time.monotonic()
    result = RUN.run_agent([sys.executable, STUB], CONTENT, _policy(**over))
    return result, time.monotonic() - started


def _violations(result, reason):
    """The recorded refusals of one kind, as sealed."""
    prefix = RUN.RESOURCE_VIOLATION_PREFIX + reason
    return [v for v in result["resource_violations"] if v.startswith(prefix)]


# =============================================== X1-X9: the nine required laws

def test_x1_a_candidate_created_fifo_does_not_hang():
    """The seventh erasure route, closed.

    `mkfifo output.pipe` + `exit 0`. Before `execlimits`, this call did not
    return — not "returned an error", *did not return* — because `open()` on a
    FIFO with no writer blocks until one arrives and the only process that could
    have opened the other end had already exited.

    Three things are asserted, in increasing strength. It came back. It came
    back with the refusal named and sealed. And the episode is *worse* for the
    candidate than the failing score it was trying to escape: a non-empty
    `policy_violations` is what `evalone` reads as tampered, which is reward 0
    and `validity: invalid`, not an unscored run.
    """
    result, elapsed = _run("fifo")
    assert elapsed < TERMINATES_WITHIN, "the scan did not terminate"

    assert _violations(result, "irregular_file"), result["resource_violations"]
    assert any("output.pipe" in v for v in result["resource_violations"]), \
        "the refusal must name what it refused"

    # Sealed, not merely returned: the same list feeds `policy_violations`,
    # which is what the trace digests and what replay re-attests.
    assert set(result["resource_violations"]) <= set(result["policy_violations"])
    assert result["trace"]["trace_id"].startswith("trace-")
    assert result["trace"]["events"][0]["execution_limits_version"] == \
        E.EXECUTION_LIMITS_VERSION


def test_x2_a_candidate_created_socket_is_refused():
    """A bound AF_UNIX socket is the same route through a different file type.

    Named separately from the FIFO because they fail differently at the OS
    level — `open()` on a socket raises `ENXIO` rather than blocking — and a
    boundary that closed only the blocking one would be a boundary against a
    single syscall's behaviour rather than against a *class* of file.
    """
    result, elapsed = _run("socket")
    assert elapsed < TERMINATES_WITHIN
    assert _violations(result, "irregular_file"), result["resource_violations"]
    assert any("agent.sock" in v for v in result["resource_violations"])
    assert set(result["resource_violations"]) <= set(result["policy_violations"])


def test_x3_a_single_oversized_workspace_file_is_refused():
    """One 1 GiB sparse file does not become 1 GiB of resident memory.

    Sparse is the point: `truncate` costs the candidate nothing and costs an
    unbounded reader everything. The old `_scan` would have called `.read()` on
    it, and on a 16 GiB host with a 20 GiB file it would not have failed
    *gracefully* — the process would have died, which persists nothing.
    """
    result, elapsed = _run("hugefile")
    assert elapsed < TERMINATES_WITHIN
    assert _violations(result, "workspace_file_too_large"), \
        result["resource_violations"]
    assert any("payload.bin" in v for v in result["resource_violations"])
    assert set(result["resource_violations"]) <= set(result["policy_violations"])


def test_x4_too_many_files_are_refused():
    """The count bound, end to end and as a property of the scan itself.

    Two halves, because "refused" and "refused *without walking the rest*" are
    different claims. The end-to-end half crosses the real
    `MAX_WORKSPACE_FILES`; the direct half sets a small bound and checks that
    enumeration stops at the first proof — a directory of a million files must
    cost 10 001 `lstat` calls, not a million.
    """
    result, elapsed = _run("manyfiles")
    assert elapsed < TERMINATES_WITHIN
    assert _violations(result, "too_many_files"), result["resource_violations"]
    assert set(result["resource_violations"]) <= set(result["policy_violations"])

    root = tempfile.mkdtemp(prefix="traaviis-x4-")
    try:
        for i in range(20):
            with open(os.path.join(root, "f%02d" % i), "wb"):
                pass
        try:
            E.scan_tree(root, max_files=5)
        except E.ResourceLimitError as exc:
            assert exc.reason == "too_many_files"
            assert exc.detail["max_files"] == 5
        else:
            raise AssertionError("20 files passed a bound of 5")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_x5_an_oversized_result_is_refused_before_a_full_read():
    """A 1 GiB `result.json` is refused, and never allocated.

    `boundedjson.load_json_bounded` could not have closed this and says so: its
    8 MiB input bound is reached only *after* `fh.read()` has already produced
    the whole document. `runner.run_agent`'s own comment named the missing piece
    exactly — "a declared byte bound on the result file (a fact about the bytes,
    identical on every host)". This is that bound, and it is applied at the
    `stat` and re-proven by reading `max + 1` and no more.

    The result is `None`, which §10a already rules is a `fail` and never an
    `error`; the violation is additionally sealed, so the episode is invalid
    rather than merely unanswered.
    """
    result, elapsed = _run("hugeresult")
    assert elapsed < TERMINATES_WITHIN
    assert _violations(result, "result_too_large"), result["resource_violations"]
    assert result["result"] is None
    assert set(result["resource_violations"]) <= set(result["policy_violations"])


def test_x6_an_oversized_patch_is_refused_before_a_full_read():
    """The same bound on the other declared output.

    A patch is not parsed, so no JSON boundary was ever going to reach it: the
    only thing between a 1 GiB `candidate.patch` and the evaluator's memory was
    `fh.read()`.
    """
    result, elapsed = _run("hugepatch")
    assert elapsed < TERMINATES_WITHIN
    assert _violations(result, "patch_too_large"), result["resource_violations"]
    assert result["patch_text"] is None
    assert set(result["resource_violations"]) <= set(result["policy_violations"])


def test_x7_infinite_stdout_is_bounded_without_oom():
    """A child writing forever is capped *while* it writes, and then killed.

    Two claims, and the second is the one `subprocess.run` could not make. The
    retained bytes never exceed the cap — so memory is bounded by the profile
    rather than by how long the child ran. And the run *ends*: the group is
    killed on overflow instead of waiting out a timeout that a determined child
    would spend filling the host.

    The recorded `exit_code` is `None` on every host and every scheduling. That
    is deliberate and is asserted here: the supervisor might or might not notice
    the overflow before the child exits, but the *reader threads* set the flag on
    the bytes themselves, so the outcome is a fact about the output rather than
    about who won a race.
    """
    spew = ("import sys\n"
            "buf = 'x' * 65536\n"
            "while True:\n"
            "    sys.stdout.write(buf)\n")
    cap = 1 << 20
    started = time.monotonic()
    run = E.run_bounded([sys.executable, "-c", spew], timeout=TERMINATES_WITHIN,
                        max_stdout_bytes=cap, max_stderr_bytes=cap)
    elapsed = time.monotonic() - started

    assert elapsed < TERMINATES_WITHIN, "the overflow kill never fired"
    assert len(run["stdout"]) <= cap, len(run["stdout"])
    assert run["stdout_truncated"] is True
    assert run["output_overflow"] is True
    assert run["exit_code"] is None
    assert run["stdout_bytes_seen"] > cap, \
        "the run must record what the child emitted, not only what was kept"


def test_x8_a_grandchild_holding_stdout_is_killed_and_reaped():
    """The half of a timeout that `subprocess.run` never had.

    The child spawns a grandchild that inherits stdout, records its pid, and
    then sleeps past the deadline. Killing only the direct child leaves the
    grandchild holding the write end of the pipe, so a reader waiting for EOF
    waits forever — the hang the timeout existed to prevent, reached through a
    different door. `run_bounded` gives the child its own session and signals
    the whole group.

    The grandchild's death is checked against the OS, not inferred from the call
    returning. `os.kill(pid, 0)` is the question "does this process exist?" and
    `ProcessLookupError` is the OS answering no.
    """
    root = tempfile.mkdtemp(prefix="traaviis-x8-")
    try:
        script = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c',\n"
            "                      'import time; time.sleep(600)'])\n"
            "open('grandchild.pid', 'w').write(str(p.pid))\n"
            "time.sleep(600)\n")
        started = time.monotonic()
        run = E.run_bounded([sys.executable, "-c", script], cwd=root, timeout=3.0)
        elapsed = time.monotonic() - started

        assert elapsed < TERMINATES_WITHIN, "the collection never finished"
        assert run["timed_out"] is True
        assert run["exit_code"] is None

        pidfile = os.path.join(root, "grandchild.pid")
        assert os.path.isfile(pidfile), "the fixture never spawned a grandchild"
        with open(pidfile) as fh:
            pid = int(fh.read().strip())

        # The signal is asynchronous; give the OS a moment to reap, then ask it.
        for _ in range(200):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:  # pragma: no cover -- reaped and pid reused
                break
            time.sleep(0.05)
        else:
            raise AssertionError(
                "grandchild %d survived the group kill and still holds stdout"
                % pid)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_x9_every_refusal_still_emits_a_receipt():
    """No refusal in this profile costs the episode its evidence.

    This is the law the other eight exist to serve. A bound that turned a hang
    into a crash would have changed nothing: a crashed evaluator persists no
    receipt either, and `batch`/`compare` refuse a pair they cannot read, so the
    candidate would still have erased the score rather than earned it.

    So for every mode: a `RunResult` comes back, it carries a sealed
    `trace-…`, the refusal is in the list the trace digests, and the trace names
    the profile that refused. `trace_id` is recomputed from the returned trace
    rather than trusted, because a sealed id that does not match its own
    document is not evidence.
    """
    from traaviis import identity

    for mode, reason in (("fifo", "irregular_file"),
                         ("socket", "irregular_file"),
                         ("hugefile", "workspace_file_too_large"),
                         ("manyfiles", "too_many_files"),
                         ("hugeresult", "result_too_large"),
                         ("hugepatch", "patch_too_large")):
        result, _ = _run(mode)
        assert _violations(result, reason), (mode, result["resource_violations"])

        trace = result["trace"]
        assert trace["trace_id"] == identity.trace_id(trace), mode
        event = trace["events"][0]
        assert event["execution_limits_version"] == E.EXECUTION_LIMITS_VERSION, mode
        assert event["policy_violations_digest"] == \
            "sha256:" + __import__("hashlib").sha256(
                identity.canonical_bytes(result["policy_violations"])
            ).hexdigest(), mode
        # Tampered, i.e. reward 0 and invalid -- not unscored.
        assert result["policy_violations"], mode


# =========================================== X10-X13: the profile is declared

def test_x10_the_profile_is_frozen_and_machine_independent():
    """The numbers are constants, and `LIMITS` is exactly the constants.

    A bound is worth having only if it is the same number on every machine —
    `boundedjson` says this about its own bounds and the reasoning transfers
    unchanged. A limit derived from free memory or core count would score the
    same submission differently on two hosts.

    The map is checked against the module attributes rather than against a
    second copy of the numbers, so this law cannot be satisfied by editing a
    literal in the test.
    """
    assert E.EXECUTION_LIMITS_VERSION == "traaviis.execution-limits.v1"
    assert E.LIMITS["execution_limits_version"] == E.EXECUTION_LIMITS_VERSION
    assert E.LIMITS["regular_files_only"] is True

    for key, attr in (("max_workspace_files", "MAX_WORKSPACE_FILES"),
                      ("max_workspace_total_bytes", "MAX_WORKSPACE_TOTAL_BYTES"),
                      ("max_workspace_file_bytes", "MAX_WORKSPACE_FILE_BYTES"),
                      ("max_result_bytes", "MAX_RESULT_BYTES"),
                      ("max_patch_bytes", "MAX_PATCH_BYTES"),
                      ("max_stdout_bytes", "MAX_STDOUT_BYTES"),
                      ("max_stderr_bytes", "MAX_STDERR_BYTES"),
                      ("max_ipc_source_bytes", "MAX_IPC_SOURCE_BYTES"),
                      ("max_ipc_request_bytes", "MAX_IPC_REQUEST_BYTES"),
                      ("max_ipc_response_bytes", "MAX_IPC_RESPONSE_BYTES"),
                      ("max_ipc_diagnostic_bytes", "MAX_IPC_DIAGNOSTIC_BYTES"),
                      ("max_workspace_entries", "MAX_WORKSPACE_ENTRIES"),
                      ("max_workspace_directories", "MAX_WORKSPACE_DIRECTORIES"),
                      ("max_workspace_depth", "MAX_WORKSPACE_DEPTH"),
                      ("max_workspace_path_bytes", "MAX_WORKSPACE_PATH_BYTES"),
                      ("max_single_path_bytes", "MAX_SINGLE_PATH_BYTES")):
        value = getattr(E, attr)
        assert isinstance(value, int) and value > 0, attr
        assert E.LIMITS[key] == value, key

    # No entry in the map that is not a declared constant, and no constant
    # missing from the map: the map *is* the profile, not a summary of it.
    assert set(E.LIMITS) == {
        "execution_limits_version", "regular_files_only",
        "max_workspace_files", "max_workspace_total_bytes",
        "max_workspace_file_bytes", "max_result_bytes", "max_patch_bytes",
        "max_stdout_bytes", "max_stderr_bytes", "max_ipc_source_bytes",
        "max_ipc_request_bytes", "max_ipc_response_bytes",
        "max_ipc_diagnostic_bytes", "max_workspace_entries",
        "max_workspace_directories", "max_workspace_depth",
        "max_workspace_path_bytes", "max_single_path_bytes"}


def test_x11_one_profile_is_shared_by_every_execution_path():
    """Four callers, one primitive — checked on the parse tree, not the text.

    The ruling's requirement is a *shared* profile: agent runner, test verifier,
    Forge worker and any future external verifier under one set of numbers. A
    module that merely exported constants nobody imported would satisfy a prose
    reading of that and nothing else.

    Read structurally, so a docstring mentioning `run_bounded` cannot satisfy
    it. `subprocess.run` is separately asserted *absent* from the three modules
    that used to call it — the containment is worth nothing if one path keeps
    the unbounded primitive.
    """
    for module in (RUN, FA, FW):
        tree = ast.parse(inspect.getsource(module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add((alias.asname or alias.name).split(".")[0])
        assert "execlimits" in imported, \
            "%s does not import the shared profile" % module.__name__

    from traaviis import substrate_verifiers as SV
    for module in (RUN, FA, SV):
        tree = ast.parse(inspect.getsource(module))
        calls = {
            "%s.%s" % (n.func.value.id, n.func.attr)
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
        }
        assert "subprocess.run" not in calls, (
            "%s still spawns through the unbounded primitive" % module.__name__)
        assert "subprocess.Popen" not in calls, (
            "%s opens its own process instead of using run_bounded"
            % module.__name__)
        assert "execlimits.run_bounded" in calls, module.__name__


def test_x12_nothing_on_these_paths_reads_a_file_without_a_bound():
    """No argument-less `.read()` survives on the evidence-collection paths.

    The functional laws above prove the bounds fire on the inputs they were
    given. This one proves there is no *second* door: a bare `fh.read()` is an
    unbounded allocation whatever the caller intended, and every one of the four
    defects in this battery's docstring was literally that call.

    Located on the parse tree. `execlimits` itself is exempt at exactly one
    call — `stream.read(65536)` and `fh.read(SCAN_CHUNK_BYTES)` are bounded by
    their argument — which is why the check is "read with no argument" rather
    than "reads at all".
    """
    offenders = []
    for module in (RUN, E, FA, FW):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "read"
                    and not node.args):
                offenders.append("%s:%d" % (module.__name__, node.lineno))
    assert not offenders, (
        "unbounded .read() on an evidence-collection path: %s"
        % ", ".join(offenders))


def test_x13_a_refusal_is_typed_and_carries_its_reason():
    """Every refusal is one type, from a closed set of reasons, with the profile.

    The contract `boundedjson.REASONS` established: a caller maps refusals onto
    its own vocabulary without ever naming an exception class, because
    enumerating exception classes per site is the failure both modules exist to
    stop repeating. `ValueError` as the base is the same choice for the same
    reason — every reader in this tree already catches it, so a site that routes
    through here cannot produce an untyped crash even before its own handler is
    swept.
    """
    assert issubclass(E.ResourceLimitError, ValueError)
    assert len(set(E.REASONS)) == len(E.REASONS), "duplicate reason token"

    root = tempfile.mkdtemp(prefix="traaviis-x13-")
    try:
        os.mkfifo(os.path.join(root, "p"))
        try:
            E.scan_tree(root)
        except E.ResourceLimitError as exc:
            assert exc.reason in E.REASONS
            assert exc.detail["execution_limits_version"] == \
                E.EXECUTION_LIMITS_VERSION, \
                "a refusal must name the profile that refused"
            assert exc.detail["kind"] == "fifo"
        else:
            raise AssertionError("a FIFO passed the scan")

        # A symlink is **refused**, not skipped. 9D skipped it and said so; the
        # 9E ruling overturned that, and the argument is exact: snapshot
        # materialization cannot express a symlink, the workspace begins from
        # that sealed materialization, therefore any symlink present after the
        # run was created by the candidate. Omitting it would leave the trace
        # describing an incomplete filesystem state -- incomplete in the
        # direction that pays the candidate.
        os.remove(os.path.join(root, "p"))
        with open(os.path.join(root, "real"), "wb") as fh:
            fh.write(b"x")
        assert set(E.scan_tree(root)) == {"real"}
        os.symlink("real", os.path.join(root, "link"))
        try:
            E.scan_tree(root)
        except E.ResourceLimitError as exc:
            assert exc.reason == "symlink", exc.reason
            assert exc.detail["kind"] == "symlink"
        else:
            raise AssertionError("a candidate-created symlink passed the scan")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ============================================ X14-X16: the bounded worker IPC

def test_x14_the_forge_ipc_is_bounded_in_both_directions():
    """The envelope is internal; the payload is not, and the bounds say so.

    `forge_worker:main` and `forge_adapter:_lower_in_worker` were registered as
    *exempt* from the bounded-JSON boundary on the grounds that they parse an
    internal IPC envelope. The envelope is internal. Its payload is
    candidate-modified WRL on the way in and an engine-emitted diagnostic on the
    way back, so the exemption encoded the wrong trust judgment.

    Checked on the parse tree: both sites now call `load_json_bounded`, and
    neither calls `json.loads`.
    """
    for module in (FW, FA):
        tree = ast.parse(inspect.getsource(module))
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
        assert "load_json_bounded" in calls, \
            "%s does not parse through the boundary" % module.__name__
        assert "loads" not in calls, \
            "%s still holds a raw json.loads" % module.__name__

    assert E.MAX_IPC_SOURCE_BYTES < E.MAX_IPC_REQUEST_BYTES, \
        "the envelope bound must leave room for the payload it wraps"
    assert E.MAX_IPC_RESPONSE_BYTES < E.MAX_IPC_REQUEST_BYTES, \
        "a response is an id and a diagnostic; it has no reason to be larger"
    assert E.MAX_IPC_DIAGNOSTIC_BYTES <= E.MAX_IPC_RESPONSE_BYTES


def test_x15_an_oversized_wrl_source_never_starts_a_worker():
    """A source over the IPC bound is refused in the parent, before any spawn.

    The parent used to do an unbounded `json.dumps({"source": source})` on bytes
    the candidate wrote — a whole escaped copy of a candidate-controlled string,
    materialized in the process holding the episode. Refusing first means the
    cost is one `len` on bytes that were already resident.

    `ForgeSourceTooLarge` is deliberately **not** a `ForgeUnavailable` any more.
    9D made it one so the existing handlers would absorb it, and flagged the
    tension: `error` nulls the reward, so a candidate could unscore its identity
    signal by writing a large enough file. The 9E ruling drew the line at
    attribution -- a declared byte bound is a fact about the submission, not
    about the host -- so an oversized *patched* source is an identity `fail`. If
    it still subclassed `ForgeUnavailable` the old handlers would keep turning it
    back into `error`, whatever the docstring said, which is why the base class
    is asserted here rather than the behaviour alone.
    """
    assert not issubclass(FA.ForgeSourceTooLarge, FA.ForgeUnavailable)
    assert issubclass(FA.ForgeSourceTooLarge, Exception)
    assert FA.ERROR_CODE_FORGE_SOURCE_TOO_LARGE != FA.ERROR_CODE_FORGE_TIMEOUT, \
        "a size refusal and a hang are different facts and need different codes"

    oversized = "a" * (E.MAX_IPC_SOURCE_BYTES + 1)
    try:
        FA._encode_request(oversized)
    except FA.ForgeSourceTooLarge as exc:
        assert str(E.MAX_IPC_SOURCE_BYTES) in str(exc)
    else:
        raise AssertionError("an oversized source was encoded for the wire")

    # Exactly at the bound is admitted: an off-by-one here would refuse a legal
    # source, which is a denial of service dressed as a guard.
    assert FA._encode_request("a" * E.MAX_IPC_SOURCE_BYTES)


def test_x16_the_worker_refuses_an_oversized_request_and_bounds_its_answer():
    """Both directions, against the worker's own functions.

    `read_request` is given more than the request bound as a real stream, so the
    law measures what the worker would actually read rather than what it would
    be handed. `encode_response` is given an engine diagnostic far past the
    diagnostic bound and must still produce one in-bounds document — truncation
    marked, never a partial write, because a partial response is read back as a
    lowering that never took place.
    """
    import io

    huge = b'{"source": "' + b"a" * (E.MAX_IPC_REQUEST_BYTES + 64) + b'"}'
    try:
        FW.read_request(io.BytesIO(huge))
    except E.ResourceLimitError as exc:
        assert exc.reason in ("ipc_request_too_large", "ipc_source_too_large")
    else:
        raise AssertionError("an oversized request was accepted")

    # In-bounds envelope, out-of-bounds payload: the source bound is not the
    # envelope bound wearing a different name.
    source = "a" * (E.MAX_IPC_SOURCE_BYTES + 1)
    body = ('{"source": "%s"}' % source).encode("utf-8")
    assert len(body) <= E.MAX_IPC_REQUEST_BYTES
    try:
        FW.read_request(io.BytesIO(body))
    except E.ResourceLimitError as exc:
        assert exc.reason == "ipc_source_too_large"
    else:
        raise AssertionError("an oversized source was accepted")

    from traaviis import boundedjson as B

    encoded = FW.encode_response({"status": "ok", "ok": False,
                                  "error": "x" * (E.MAX_IPC_DIAGNOSTIC_BYTES * 4)})
    assert len(encoded) <= E.MAX_IPC_RESPONSE_BYTES
    parsed = B.load_json_bounded(encoded)
    assert parsed["status"] == "ok" and parsed["ok"] is False
    assert "truncated" in parsed["error"], \
        "a cut diagnostic must say it was cut"


# =============================================== X17-X19: non-vacuity by proof

def _isolated_package(*edits):
    """A private copy of `traaviis/` with literal source edits, importable.

    The same non-vacuity instrument `test_boundedjson._isolated_package`,
    `test_canonical.isolated_package` and `test_evalone._isolated_traaviis` use.
    Duplicated rather than shared for the reason they all give: a battery that
    imported its own instrument from another battery would go red for a reason
    that has nothing to do with the law it states.
    """
    root = tempfile.mkdtemp(prefix="traaviis-x-iso-")
    name = "traaviis_execlimits_iso"
    shutil.copytree(PKG, os.path.join(root, name))
    for filename, old, new in edits:
        path = os.path.join(root, name, filename)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert old in src, "the deletion target is not in %s" % filename
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src.replace(old, new, 1))
    return root, name


def _run_in_isolation(root, name, body, timeout=180):
    """Run `body` (a source string) against the isolated copy, in a fresh process.

    A fresh interpreter, not an import: the real package is already in
    `sys.modules` and a same-process import of a copy would resolve back to it
    for every `from . import` inside the copy.
    """
    script = "import sys\nsys.path.insert(0, %r)\n%s" % (root, body)
    return subprocess.run([sys.executable, "-c", script], cwd=root,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)


def test_x17_deleting_the_regular_file_check_makes_the_fifo_hang_again():
    """Non-vacuity for X1, by source-level deletion.

    The type check is removed in an isolated copy and the FIFO scan is run under
    a deadline. It must **not** come back — which is the whole claim X1 makes,
    stated as its own negation. A law that would pass with the guard deleted is
    not evidence that the guard does anything.

    The subprocess is killed by its own timeout, and the assertion is on that
    timeout firing. `subprocess.run`'s timeout is adequate here for the one
    reason it is inadequate everywhere else in this package: the hung child is
    this battery's own fixture, it spawns nothing, and there is no grandchild to
    outlive the kill.
    """
    root, name = _isolated_package(
        ("execlimits.py",
         "        if not stat.S_ISREG(st.st_mode):\n"
         "            raise ResourceLimitError(",
         "        if False:\n"
         "            raise ResourceLimitError("),
        # ...and the `O_NONBLOCK` that keeps the *open* from blocking, so the
        # deletion reconstructs the pre-9D shape rather than a half of it.
        ("execlimits.py",
         "    flags = os.O_RDONLY | getattr(os, \"O_NONBLOCK\", 0)",
         "    flags = os.O_RDONLY"))
    try:
        body = (
            "import os, tempfile\n"
            "from %s import execlimits as E\n"
            "root = tempfile.mkdtemp()\n"
            "os.mkfifo(os.path.join(root, 'p'))\n"
            "E.scan_tree(root)\n"
            "print('RETURNED')\n" % name)
        try:
            # 45 seconds, not 180. `open()` on a FIFO with no writer blocks
            # until one arrives, and nothing in this fixture will ever open the
            # write end -- so the wait is unbounded and any deadline at all
            # decides it. A longer one would only make the battery slower at
            # proving the same thing.
            proc = _run_in_isolation(root, name, body, timeout=45)
        except subprocess.TimeoutExpired:
            return  # the guard is what stops the hang
        raise AssertionError(
            "the scan returned with the type check deleted (%s); this law and "
            "X1 are both vacuous" % proc.stdout.decode("utf-8", "replace")[:200])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_x18_deleting_the_size_bound_makes_the_sparse_file_resident():
    """Non-vacuity for X3/X5, by source-level deletion.

    With the per-file bound removed, the isolated copy must *accept* the sparse
    file rather than refuse it. Asserted as acceptance rather than as a crash:
    whether a 1 GiB allocation kills a given host is a fact about the host, and
    a law that depended on it would be the host-dependent verdict this
    repository refuses. What is host-independent is that the guard is the only
    thing saying no.
    """
    root, name = _isolated_package(
        ("execlimits.py",
         "                if st.st_size > max_file_bytes:",
         "                if False:"),
        ("execlimits.py",
         "        if size > max_bytes:\n"
         "            raise ResourceLimitError(",
         "        if False:\n"
         "            raise ResourceLimitError("))
    try:
        # 96 MiB: over the 64 MiB bound, and small enough that reading it is a
        # measurement rather than a hazard on any host that can run this suite.
        body = (
            "import os, tempfile\n"
            "from %s import execlimits as E\n"
            "root = tempfile.mkdtemp()\n"
            "with open(os.path.join(root, 'big'), 'wb') as fh:\n"
            "    fh.truncate(96 * 1024 * 1024)\n"
            "print('ACCEPTED' if E.scan_tree(root) else 'EMPTY')\n" % name)
        proc = _run_in_isolation(root, name, body)
        assert b"ACCEPTED" in proc.stdout, (
            "the oversized file was still refused with the bound deleted, so "
            "something other than the bound is refusing it: %s"
            % proc.stderr.decode("utf-8", "replace")[-400:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_x19_without_the_cgroup_the_setsid_escape_comes_back():
    """Non-vacuity for the containment laws, by source-level deletion.

    **This law was rewritten in 9E, because its old subject stopped being the
    mechanism.** It used to delete `killpg` and prove a grandchild survived. The
    ruling's finding is that `killpg` was never sufficient anyway: a descendant
    that calls `setsid()` leaves the process group before any signal addressed to
    that group arrives, so deleting the group kill and watching a *cooperative*
    grandchild die proved something about the wrong boundary.

    So the deletion is now of `cgroup_v2_available`'s discovery -- the isolated
    copy is forced down to `traaviis.process-group.v1`, which is exactly the
    boundary 9D shipped -- and the escape used is the one the ruling
    demonstrated: `setsid`. It must survive, and the run must still report
    `enforced: false`, so a reader can tell "nothing escaped" from "nothing was
    watching".

    The survivor is killed explicitly. A law that proves a process was *not*
    killed and then walks away would leak a sleeper on every green run.
    """
    root, name = _isolated_package(
        ("containment.py",
         "    base = _own_cgroup_path()\n"
         "    if base is not None and os.path.isdir(base):",
         "    base = None\n"
         "    if base is not None and os.path.isdir(base):"),
        ("containment.py",
         '    if hasattr(os, "killpg"):',
         "    if False:  # hasattr(os, \"killpg\")"),
        ("execlimits.py",
         "REAP_GRACE_SECONDS = 30.0",
         "REAP_GRACE_SECONDS = 3.0"))
    try:
        body = (
            "import os, sys, time\n"
            "from %s import execlimits as E\n"
            "script = (\"import subprocess, sys\\n\"\n"
            "          \"p = subprocess.Popen([sys.executable, '-c',\\n\"\n"
            "          \"                      'import time; time.sleep(600)'],\\n\"\n"
            "          \"                     start_new_session=True)\\n\"\n"
            "          \"open('gc.pid','w').write(str(p.pid))\\n\")\n"
            "run = E.run_bounded([sys.executable, '-c', script], cwd='.', timeout=60)\n"
            "time.sleep(1.0)\n"
            "pid = int(open('gc.pid').read())\n"
            "print('PROFILE', run['process_containment']['profile'])\n"
            "print('ENFORCED', run['process_containment']['certifiable'])\n"
            "try:\n"
            "    os.kill(pid, 0)\n"
            "    print('SURVIVED')\n"
            "    os.kill(pid, 9)\n"
            "except ProcessLookupError:\n"
            "    print('DIED')\n" % name)
        proc = _run_in_isolation(root, name, body, timeout=180)
        out = proc.stdout.decode("utf-8", "replace")
        # stderr is carried into every message below. The probe runs in a fresh
        # interpreter, so when it dies -- a renamed key, an import error -- the
        # only symptom here is *empty stdout*, and an assertion that showed only
        # that reports "the law failed" while hiding which line of the probe
        # broke. Measured: a `RunResult` key rename made this law go red with a
        # blank message, and the blank message is what cost the time.
        detail = "stdout=%r stderr=%s" % (
            out, proc.stderr.decode("utf-8", "replace")[-400:])
        assert "PROFILE traaviis.process-group.v1" in out, detail
        assert "ENFORCED False" in out, detail
        assert "SURVIVED" in out, (
            "the setsid descendant died without the cgroup, so the containment "
            "laws are measuring something other than the cgroup: %s" % detail)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_x21_the_group_kill_has_exactly_one_implementation():
    """A safety primitive with two copies is a probe that disables the wrong one.

    Found the hard way. `kill_process_group` was written out in `execlimits` and
    again inside `Containment.kill_all`. `test_evalone::W2`'s non-vacuity probe
    deleted the `killpg` in `execlimits`, forced the weak containment profile,
    and watched the grandchild die anyway -- because the *other* copy was still
    doing the group kill. The law then reported that the boundary worked. What
    it had measured was its own failure to turn the boundary off.

    That is worse than an ordinary duplicate: a second copy of a *guard* makes
    every deletion-based proof about that guard unsound, silently, in the
    direction that says everything is fine.

    So: exactly one `os.killpg` call in the whole package, and every other name
    for it resolves to the same function object. Located on the parse tree,
    because a comment quoting `os.killpg` is not a kill.
    """
    calls = []
    for name in sorted(os.listdir(PKG)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(PKG, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and \
                    ast.unparse(node.func) == "os.killpg":
                calls.append("%s:%d" % (name, node.lineno))
    assert len(calls) == 1, (
        "the group kill has %d implementations (%s); a deletion-based proof "
        "about it can disable one and measure the other"
        % (len(calls), ", ".join(calls)))
    assert calls[0].startswith("containment.py:"), calls

    from traaviis import containment as C
    assert E.kill_process_group is C.kill_process_group, \
        "execlimits re-implements the group kill instead of re-exporting it"


def test_x20_a_launch_failure_is_still_raised_and_never_absorbed():
    """The one outcome `run_bounded` reports that `run_agent` must re-raise.

    Containment is a good default and it is not a universal one. A spawn failure
    is not the candidate doing anything — it is `argv[0]` not existing on this
    host, i.e. an operator's typo — and the surrounding pipeline already has a
    ruled answer for it: `evalsplit._run_episode` catches `OSError` so that one
    bad argv costs one task rather than abandoning the split, and `batch` turns
    a candidate that never launched into a typed `EPISODE_UNAVAILABLE` refusal
    on every pair it appears in.

    Absorbing it inside `run_bounded` — which is what the first version of this
    change did — is silent and wrong in a specific direction: the candidate
    receives a *complete* `RunResult` describing a process that never existed,
    and is then compared against candidates that really ran. `test_batch::B14`
    caught it. This law states it where the boundary lives, so the next person
    to widen the containment sees why this case is outside it.

    Both halves are asserted, because only the pair says what the design is:
    `run_bounded` **reports** the failure (a verifier collecting evidence needs
    a fact, not an exception to classify), and `run_agent` **raises** it.
    """
    missing = os.path.join(tempfile.gettempdir(), "traaviis-no-such-agent-x20")
    assert not os.path.exists(missing), "the fixture's premise is false"

    run = E.run_bounded([missing])
    assert run["spawn_error"] == "FileNotFoundError", run["spawn_error"]
    assert isinstance(run["spawn_exception"], OSError)
    assert run["exit_code"] is None and run["stdout"] == b""

    try:
        RUN.run_agent([missing], CONTENT, _policy())
    except OSError as exc:
        assert isinstance(exc, FileNotFoundError), type(exc).__name__
    else:
        raise AssertionError(
            "run_agent absorbed a launch failure into a RunResult; a candidate "
            "that never started would be scored against ones that did")


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
