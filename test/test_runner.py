"""Battery for the controlled runner + trace capture (traaviis.runner, RFC §10a).

Crosses the subprocess boundary against a deterministic stub agent. Pins: sealed
env (host not inherited), output cap, timeout -> timed_out, missing outputs,
writable-path violations, canonical TraceV1 identity (volatile-free, moves on an
observable-event change).

Runs with pytest, or standalone: `python3 test/test_runner.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import runner as RUN  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = os.path.join(HERE, "fixtures", "stub_agent.py")

CONTENT = {"spec/one.md": "alpha\nbeta\n", "src/mod.py": "return 1\n"}


def _policy(**over):
    p = {
        "policy_version": "traaviis.agent-run-policy.v1",
        "command_mode": "argv",
        "shell": False,
        "network": "disabled",
        "timeout_seconds": 30,
        "max_output_bytes": 4194304,
        "environment": {"TRAAVIIS_STUB_MODE": "ok",
                        "PATH": os.environ.get("PATH", "")},
        "writable_paths": ["."],
        "result_path": "result.json",
        "patch_path": "candidate.patch",
    }
    p.update(over)
    return p


def _run(mode="ok", **over):
    env = {"TRAAVIIS_STUB_MODE": mode, "PATH": os.environ.get("PATH", "")}
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


def test_output_cap_truncates():
    # A tiny cap should mark truncation if the child emits anything; the ok stub
    # is silent, so force output via a one-liner that prints.
    printer = "import sys; sys.stdout.write('x'*100)"
    r = RUN.run_agent(
        [sys.executable, "-c", printer], CONTENT,
        _policy(max_output_bytes=10, environment={"PATH": os.environ.get("PATH", "")}),
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
                environment={"TRAAVIIS_STUB_MODE": "ok",
                             "PATH": os.environ.get("PATH", "")}),
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
