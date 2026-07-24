"""Battery for the ``trvs eval-one`` CLI wiring (traaviis.cli.cmd_eval_one).

Drives the public command against the committed ``residency-demo`` task bundle and
the deterministic stub agent. Pins the CLI admission seam end-to-end: the bundle
loads + admits, the receipt renders, ``--json`` emits a valid episode, exit codes
map to episode status (ok=0 / invalid=1 / error=2), and every admission law is
rejected *before* a wrong receipt can be sealed —

  * a missing agent / bundle is a clean exit 2;
  * a tampered subject file is rejected;
  * a task referencing a different (valid) reward or snapshot is rejected;
  * a run policy the trusted-local runner cannot honor (``network:"disabled"``)
    is rejected at preflight;
  * a required signal with no wired verifier is invalid config;
  * agent flags after ``--`` reach the agent unchanged;
  * a declared-binary subject admits byte-exactly;
  * a file-mode mismatch is rejected;
  * a ``bundle.json`` manifest reference that escapes the bundle is rejected.

The full pipeline itself is covered by test_evalone; this pins the CLI seam.

Runs with pytest, or standalone: ``python3 test/test_cli_evalone.py``.
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import cli  # noqa: E402
from traaviis import identity as I  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUNDLE = os.path.join(REPO, "examples", "eval-one", "residency-demo")
STUB = os.path.join(HERE, "fixtures", "stub_agent.py")
AGENT = [sys.executable, STUB]  # absolute exe → no PATH needed under the sealed env


def _run(argv):
    """Invoke the CLI, returning (exit_code, stdout_text)."""
    buf = io.StringIO()
    code = 0
    try:
        with redirect_stdout(buf):
            cli.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    return code, buf.getvalue()


def _eval_argv(bundle, *extra):
    return ["eval-one", bundle, "--agent", *AGENT, *extra]


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _copy_bundle(tmp):
    """Copy the committed demo bundle into ``tmp`` and return the new bundle dir."""
    dst = os.path.join(tmp, "residency-demo")
    shutil.copytree(BUNDLE, dst)
    return dst


def _reseal_task_ids(dst, snapshot_id=None, reward_id=None):
    """Re-point the task's references, then recompute its ``task_id`` so the only
    admission law under test is the reference binding (not a stale ``task_id``)."""
    task_p = os.path.join(dst, "task.json")
    task = _load(task_p)
    if snapshot_id is not None:
        task["subject"]["snapshot_id"] = snapshot_id
    if reward_id is not None:
        task["reward_id"] = reward_id
    task.pop("task_id", None)
    task["task_id"] = I.task_id(task)
    _dump(task_p, task)
    return task


def test_happy_bundle_renders_valid_episode():
    code, out = _run(_eval_argv(BUNDLE, "--platform", "linux-x86_64"))
    assert code == 0
    assert "status" in out and "ok" in out
    assert "episode-" in out
    assert "snap-" in out


def test_json_receipt_is_a_valid_episode():
    code, out = _run(_eval_argv(BUNDLE, "--json"))
    assert code == 0
    receipt = json.loads(out)
    assert receipt["episode_version"] == "traaviis.episode.v1"
    assert receipt["status"] == "ok"
    assert receipt["validity"] == "valid"
    assert abs(receipt["reward"] - 0.55) < 1e-9  # citations+patch+finding
    assert receipt["verification"]["citations"] == "pass"
    assert receipt["verification"]["tests"] == "not_applicable"
    assert receipt["episode_id"].startswith("episode-")


def test_missing_agent_is_clean_exit_2():
    code, _ = _run(["eval-one", BUNDLE])
    assert code == 2


def test_missing_bundle_is_clean_exit_2():
    code, _ = _run(["eval-one", "/no/such/bundle", "--agent", *AGENT])
    assert code == 2


def test_tampered_subject_is_rejected():
    # Copy the bundle, corrupt one subject byte: the sealed snapshot no longer
    # binds the content, so admission rejects it (exit 2), never a false receipt.
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = os.path.join(tmp, "residency-demo")
        shutil.copytree(BUNDLE, dst)
        mod = os.path.join(dst, "subject", "src", "mod.py")
        with open(mod, "w", encoding="utf-8") as fh:
            fh.write("return 42\n")  # not the sealed subject
        code, _ = _run(_eval_argv(dst))
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_json_exit_code_tracks_status():
    # The --json path must still return the status-mapped exit code, not always 0.
    code, out = _run(_eval_argv(BUNDLE, "--json"))
    receipt = json.loads(out)
    assert cli._EVAL_EXIT[receipt["status"]] == code


def test_task_referencing_wrong_valid_reward_is_rejected():
    # The task names a DIFFERENT valid rew-… than the reward the bundle ships;
    # cross-bind must reject it before the agent runs (exit 2), never score it.
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        other_reward = dict(_load(os.path.join(dst, "reward.json")),
                            aggregation="weighted")  # a different valid spec
        other_reward.pop("reward_id", None)
        _reseal_task_ids(dst, reward_id=I.reward_id(other_reward))
        code, _ = _run(_eval_argv(dst))
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_task_referencing_wrong_valid_snapshot_is_rejected():
    # The task names a DIFFERENT valid snap-… than the snapshot the bundle seals.
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        other_snap = {"snapshot_version": "residency.snapshot.v1",
                      "files": {"spec/one.md": "sha256:" + hashlib.sha256(
                          b"different\n").hexdigest()},
                      "exclusions": [], "binary_paths": [], "file_modes": {},
                      "base_revision": None, "visible_config": {}}
        _reseal_task_ids(dst, snapshot_id=I.snapshot_id(other_snap))
        code, _ = _run(_eval_argv(dst))
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_network_disabled_policy_is_rejected():
    # The trusted-local runner is not a sandbox; a task demanding network:"disabled"
    # is refused at preflight (exit 2), never sealed with a false sandbox label.
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        task_p = os.path.join(dst, "task.json")
        task = _load(task_p)
        task["agent_run_policy"]["network"] = "disabled"
        task.pop("task_id", None)
        task["task_id"] = I.task_id(task)  # so only the policy law is under test
        _dump(task_p, task)
        code, _ = _run(_eval_argv(dst))
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_required_signal_without_wired_verifier_is_invalid_config():
    # Require "tests" but ship no test_plan → no verifier is wired → invalid config
    # (F4): the receipt is invalid (exit 1), not a fabricated pass/fail.
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        task_p = os.path.join(dst, "task.json")
        task = _load(task_p)
        plan = task["verifier_plan"]
        plan["required"] = sorted(set(plan["required"]) | {"tests"})
        plan["not_applicable"] = [s for s in plan["not_applicable"] if s != "tests"]
        task.pop("task_id", None)
        task["task_id"] = I.task_id(task)
        _dump(task_p, task)
        code, out = _run(_eval_argv(dst, "--json"))
        assert code == 1
        assert json.loads(out)["status"] == "invalid"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# A deterministic agent that produces the demo's valid outputs ONLY when it
# receives an exact dashed-flag tail — so a passing episode proves the tail after
# `--` reached the agent unchanged (byte-for-byte argv), and a wrong tail fails.
_ARGV_AGENT = r'''
import json, sys
if sys.argv[1:] != ["--model", "foo", "--temperature", "0"]:
    sys.exit(7)
with open("candidate.patch", "w") as fh:
    fh.write("--- a/src/mod.py\n+++ b/src/mod.py\n"
             "@@ -1,1 +1,1 @@\n-return 1\n+return 2\n")
with open("result.json", "w") as fh:
    json.dump({"format": "traaviis.agent-result.v1",
               "finding": {"summary": "spec/one.md line 2 contradicts src/mod.py",
                           "citations": [{"path": "spec/one.md", "start_line": 2,
                                          "end_line": 2, "quote": "beta"}]},
               "patch_path": "candidate.patch"}, fh)
sys.exit(0)
'''


def test_agent_flags_after_dashdash_reach_agent_unchanged():
    tmp = tempfile.mkdtemp(prefix="traaviis-agent-")
    try:
        agent = os.path.join(tmp, "argv_agent.py")
        with open(agent, "w", encoding="utf-8") as fh:
            fh.write(_ARGV_AGENT)
        base = ["eval-one", BUNDLE, "--platform", "linux-x86_64", "--",
                sys.executable, agent]
        # exact tail passes through unchanged → the agent emits valid outputs → ok.
        code, _ = _run(base + ["--model", "foo", "--temperature", "0"])
        assert code == 0
        # a different tail reaches the agent too (it exits 7) → substrate error → 2,
        # proving the tail is delivered verbatim, not silently normalized/dropped.
        code2, _ = _run(base + ["--model", "bar"])
        assert code2 == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_declared_binary_subject_admits_byte_exactly():
    # A snapshot that declares a file binary admits its exact bytes (incl. non-UTF-8)
    # without LF-mangling; the episode runs to a valid receipt.
    blob = b"\x00\x01\x02\xff\r\nnot text\r"  # CR/LF + NUL/0xFF: only valid as binary
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        with open(os.path.join(dst, "subject", "data.bin"), "wb") as fh:
            fh.write(blob)
        snap_p = os.path.join(dst, "snapshot.json")
        snap = _load(snap_p)
        snap["files"]["data.bin"] = "sha256:" + hashlib.sha256(blob).hexdigest()
        snap["binary_paths"] = ["data.bin"]
        snap["file_modes"]["data.bin"] = "0644"
        snap.pop("snapshot_id", None)
        snap["snapshot_id"] = I.snapshot_id(snap)
        _dump(snap_p, snap)
        _reseal_task_ids(dst, snapshot_id=snap["snapshot_id"])
        code, out = _run(_eval_argv(dst, "--json"))
        assert code == 0
        assert json.loads(out)["status"] == "ok"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_file_mode_mismatch_is_rejected():
    # The sealed mode is 0644; make the on-disk file 0755 → admission rejects the
    # subject (exit 2) before anything runs.
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        os.chmod(os.path.join(dst, "subject", "src", "mod.py"), 0o755)
        code, _ = _run(_eval_argv(dst))
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bundle_manifest_traversal_is_rejected():
    # A bundle.json reference that escapes the bundle must be refused at load,
    # never opened (exit 2).
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        manifest_p = os.path.join(dst, "bundle.json")
        manifest = _load(manifest_p)
        manifest["snapshot"] = "../../../etc/passwd"  # traversal
        _dump(manifest_p, manifest)
        code, _ = _run(_eval_argv(dst))
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_bundle_manifest_is_rejected():
    # The public preview requires an explicit bundle.json; a versioned bundle is
    # never inferred from directory convention. Removing it → exit 2.
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        os.remove(os.path.join(dst, "bundle.json"))
        code, _ = _run(_eval_argv(dst))
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bundle_member_file_symlink_escape_is_rejected():
    # A lexically-clean manifest ref that points OUTSIDE the bundle through a
    # symlink (snapshot.json → an external file) must be refused, never opened.
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        outside = os.path.join(tmp, "outside.json")
        shutil.move(os.path.join(dst, "snapshot.json"), outside)
        os.symlink(outside, os.path.join(dst, "snapshot.json"))
        code, _ = _run(_eval_argv(dst))
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bundle_subject_root_symlink_escape_is_rejected():
    # The subject root itself may not be a symlink to an external directory.
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        outside = os.path.join(tmp, "outside-subject")
        shutil.move(os.path.join(dst, "subject"), outside)
        os.symlink(outside, os.path.join(dst, "subject"), target_is_directory=True)
        code, _ = _run(_eval_argv(dst))
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bundle_intermediate_directory_symlink_is_rejected():
    # An intermediate directory symlink in a manifest ref is refused even when its
    # target stays inside the bundle — the resolved path is not the lexical one.
    tmp = tempfile.mkdtemp(prefix="traaviis-bundle-")
    try:
        dst = _copy_bundle(tmp)
        real_task = os.path.join(dst, "task.json")
        holder = os.path.join(dst, "holder")
        os.makedirs(holder)
        shutil.copy(real_task, os.path.join(holder, "task.json"))
        os.symlink(holder, os.path.join(dst, "viasym"), target_is_directory=True)
        manifest_p = os.path.join(dst, "bundle.json")
        manifest = _load(manifest_p)
        manifest["task"] = "viasym/task.json"  # in-bundle target, but via a symlink
        _dump(manifest_p, manifest)
        code, _ = _run(_eval_argv(dst))
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
