"""Mutation-law battery for the TRAAVIIS identity spine (traaviis.identity).

Pure identity only — no subprocess runner, no workspace mutation, no verifier
execution, no ORS/MCP. Each test pins one frozen mutation law from
RFC_TRAAVIIS_ARTIFACTS.md (§2–§4) and RFC_EVIDENCE_RESIDENCY.md (§4–§9):

    file order changes                    -> same snap
    repository byte changes               -> new  snap
    citation object key order changes     -> same finding
    citation span or quote changes        -> new  finding
    patch line-ending normalization       -> same patch
    patch content changes                 -> new  patch
    trace volatile timing/host changes    -> same trace
    trace observable event changes        -> new  trace
    signal map order changes              -> same rew
    signal rename/rebind/reweight         -> new  rew
    instruction changes                   -> new  task
    run-policy changes                    -> new  task
    volatile timing/PID/path changes      -> same episode
    toolchain/platform/exit-code changes  -> new  episode
    verifier-version changes              -> new  episode
    reward rescore                        -> new  episode, same trace

Runs with pytest, or standalone: `python3 test/test_identity.py`.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import identity as I  # noqa: E402


def dup(obj):
    """Independent deep copy via a JSON round-trip (fixtures are JSON data)."""
    return json.loads(json.dumps(obj))


def reordered(mapping):
    """Same key/value pairs, reversed insertion order (to prove order-independence)."""
    return {k: mapping[k] for k in reversed(list(mapping.keys()))}


# --------------------------------------------------------------------------- #
# Fixed fixtures                                                              #
# --------------------------------------------------------------------------- #

SNAPSHOT = {
    "snapshot_version": "residency.snapshot.v1",
    "snapshot_id": "snap-PLACEHOLDER",
    "files": {
        "spec/WRL_CORE_0.1.md": "sha256:aaa",
        "src/wrl_ir.py": "sha256:bbb",
        "README.md": "sha256:ccc",
    },
    "exclusions": ["**/__pycache__/**"],
    "file_modes": {"src/wrl_ir.py": "0644"},
    "base_revision": "1af09d6",
    "visible_config": {"python": "3.11"},
}

FINDING = {
    "finding_version": "residency.finding.v1",
    "finding_id": "finding-PLACEHOLDER",
    "claims": [
        {
            "statement": "Spec says X but impl does Y.",
            "citations": [
                {
                    "path": "spec/WRL_CORE_0.1.md",
                    "start_line": 120,
                    "end_line": 138,
                    "quote": "the world admits <= 1 sig-in per node",
                }
            ],
        }
    ],
}

PATCH = {
    "patch_version": "residency.patch.v1",
    "patch_id": "patch-PLACEHOLDER",
    "diff": "--- a/src/wrl_ir.py\n+++ b/src/wrl_ir.py\n@@ -1 +1 @@\n-old\n+new\n",
}

TRACE = {
    "trace_version": "residency.trace.v1",
    "trace_id": "trace-PLACEHOLDER",
    "events": [
        {
            "command": ["./stub-agent"],
            "cwd": ".",
            "environment_keys": ["LANG", "HOME"],
            "exit_code": 0,
            "stdout_digest": "sha256:out",
            "stderr_digest": "sha256:err",
            "files_created_digest": "sha256:c",
            "files_modified_digest": "sha256:m",
            "result_file_digest": "sha256:r",
        }
    ],
    # volatile — must be excluded from trace- identity:
    "started_at": "2026-07-23T20:00:00Z",
    "host_cwd": "/home/travis/tmp/ws",
    "timed_log": [{"t": 0.0, "line": "starting"}, {"t": 1.2, "line": "done"}],
}

REWARD = {
    "reward_spec_version": "traaviis.reward.v1",
    "reward_id": "rew-PLACEHOLDER",
    "substrate_profile": "residency.repository.v1",
    "signals": {
        "citations": {"verifier": "residency.citations.v1", "weight": 0.25},
        "patch": {"verifier": "residency.patch.v1", "weight": 0.20},
        "tests": {"verifier": "residency.tests.v1", "weight": 0.30},
        "identity": {"verifier": "residency.identity.v1", "weight": 0.15},
        "finding_completeness": {"verifier": "residency.finding.v1", "weight": 0.10},
    },
    "floors": [
        {"when": "patch_does_not_apply", "reward_max": 0.25},
        {"when": "citations_do_not_resolve", "reward_max": 0.25},
    ],
    "aggregation": "terminal",
}

TASK = {
    "task_spec_version": "traaviis.task.v1",
    "task_id": "task-PLACEHOLDER",
    "substrate_profile": "residency.repository.v1",
    "subject": {"snapshot_id": "snap-abc"},
    "instructions": {"objective": "Identify one WRL spec/impl inconsistency."},
    "reward_id": "rew-abc",
    "verifier_plan": {
        "required": ["citations", "patch", "tests", "identity"],
        "not_applicable": ["native", "oracle"],
    },
    "identity_policy": {
        "must_remain": ["demo_world_semantic_id"],
        "may_move": ["documentation"],
    },
    "termination": {"mode": "one_shot"},
    "agent_run_policy": {
        "policy_version": "traaviis.agent-run-policy.v1",
        "command_mode": "argv",
        "shell": False,
        "network": "disabled",
        "timeout_seconds": 900,
        "max_output_bytes": 4194304,
        "environment": {"LANG": "C.UTF-8", "HOME": "/sandbox/home"},
        "toolchain_profile": "residency.python-3.11.v1",
        "writable_paths": ["."],
        "result_path": "result.json",
        "patch_path": "candidate.patch",
    },
}

EPISODE = {
    "episode_id": "episode-PLACEHOLDER",
    "episode_version": "traaviis.episode.v1",
    "substrate_profile": "residency.repository.v1",
    "task_id": "task-abc",
    "reward_id": "rew-abc",
    "subject": {"snapshot_id": "snap-abc"},
    "trace_id": "trace-abc",
    "outputs": {"finding_id": "finding-abc", "patch_id": "patch-abc"},
    "verification": {
        "citations": "pass", "patch": "pass", "tests": "pass",
        "identity": "pass", "native": "not_applicable", "oracle": "not_applicable",
    },
    "verifier_versions": {
        "citations": "1", "patch": "1", "tests": "1", "identity": "1",
    },
    "reward": 1.0,
    "status": "completed",
    "validity": "valid",
    "replayability": "verification",
    "execution_facts": {
        "exit_code": 0,
        "platform": "linux-x86_64",
        "timed_out": False,
        "output_truncated": False,
        "toolchain": {"python": "3.11.4"},
    },
    # volatile — must be excluded from episode- identity:
    "created_at": "2026-07-23T20:00:00Z",
    "process_id": 12345,
    "log_path": "/tmp/run-12345/log.txt",
    "workspace_abs_path": "/home/travis/tmp/ws",
}


# --------------------------------------------------------------------------- #
# snapshot                                                                    #
# --------------------------------------------------------------------------- #

def test_snapshot_file_order_changes_same_snap():
    a = dup(SNAPSHOT)
    b = dup(SNAPSHOT)
    b["files"] = reordered(b["files"])
    assert I.snapshot_id(a) == I.snapshot_id(b)
    assert I.canonicalize_snapshot(a) == I.canonicalize_snapshot(b)


def test_snapshot_repository_byte_changes_new_snap():
    a = dup(SNAPSHOT)
    b = dup(SNAPSHOT)
    b["files"]["README.md"] = "sha256:CHANGED"
    assert I.snapshot_id(a) != I.snapshot_id(b)


def test_snapshot_id_ignores_self_field():
    a = dup(SNAPSHOT)
    b = dup(SNAPSHOT)
    b["snapshot_id"] = "snap-somethingelse"
    assert I.snapshot_id(a) == I.snapshot_id(b)


# --------------------------------------------------------------------------- #
# finding                                                                     #
# --------------------------------------------------------------------------- #

def test_finding_citation_key_order_changes_same_finding():
    a = dup(FINDING)
    b = dup(FINDING)
    b["claims"][0]["citations"][0] = reordered(b["claims"][0]["citations"][0])
    assert I.finding_id(a) == I.finding_id(b)


def test_finding_span_change_new_finding():
    a = dup(FINDING)
    b = dup(FINDING)
    b["claims"][0]["citations"][0]["end_line"] = 139
    assert I.finding_id(a) != I.finding_id(b)


def test_finding_quote_change_new_finding():
    a = dup(FINDING)
    b = dup(FINDING)
    b["claims"][0]["citations"][0]["quote"] = "a different quote"
    assert I.finding_id(a) != I.finding_id(b)


# --------------------------------------------------------------------------- #
# patch                                                                       #
# --------------------------------------------------------------------------- #

def test_patch_line_ending_normalization_same_patch():
    a = dup(PATCH)
    b = dup(PATCH)
    b["diff"] = b["diff"].replace("\n", "\r\n")
    assert I.patch_id(a) == I.patch_id(b)


def test_patch_content_change_new_patch():
    a = dup(PATCH)
    b = dup(PATCH)
    b["diff"] = b["diff"].replace("+new", "+other")
    assert I.patch_id(a) != I.patch_id(b)


# --------------------------------------------------------------------------- #
# trace                                                                       #
# --------------------------------------------------------------------------- #

def test_trace_volatile_timing_and_host_changes_same_trace():
    a = dup(TRACE)
    b = dup(TRACE)
    b["started_at"] = "2999-01-01T00:00:00Z"
    b["host_cwd"] = "/some/other/abs/path"
    b["timed_log"] = [{"t": 9.9, "line": "different volatile log"}]
    assert I.trace_id(a) == I.trace_id(b)
    assert I.canonicalize_trace(a) == I.canonicalize_trace(b)


def test_trace_observable_event_change_new_trace():
    a = dup(TRACE)
    b = dup(TRACE)
    b["events"][0]["exit_code"] = 1
    assert I.trace_id(a) != I.trace_id(b)


# --------------------------------------------------------------------------- #
# reward                                                                      #
# --------------------------------------------------------------------------- #

def test_reward_signal_map_order_changes_same_rew():
    a = dup(REWARD)
    b = dup(REWARD)
    b["signals"] = reordered(b["signals"])
    assert I.reward_id(a) == I.reward_id(b)
    assert I.canonicalize_reward(a) == I.canonicalize_reward(b)


def test_reward_signal_rename_new_rew():
    a = dup(REWARD)
    b = dup(REWARD)
    b["signals"]["citation_validity"] = b["signals"].pop("citations")
    assert I.reward_id(a) != I.reward_id(b)


def test_reward_signal_rebind_new_rew():
    a = dup(REWARD)
    b = dup(REWARD)
    b["signals"]["tests"]["verifier"] = "residency.tests.v2"
    assert I.reward_id(a) != I.reward_id(b)


def test_reward_signal_reweight_new_rew():
    a = dup(REWARD)
    b = dup(REWARD)
    b["signals"]["tests"]["weight"] = 0.35
    assert I.reward_id(a) != I.reward_id(b)


# --------------------------------------------------------------------------- #
# task                                                                        #
# --------------------------------------------------------------------------- #

def test_task_instruction_change_new_task():
    a = dup(TASK)
    b = dup(TASK)
    b["instructions"]["objective"] = "A different objective."
    assert I.task_id(a) != I.task_id(b)


def test_task_run_policy_change_new_task():
    a = dup(TASK)
    b = dup(TASK)
    b["agent_run_policy"]["timeout_seconds"] = 300
    assert I.task_id(a) != I.task_id(b)


def test_task_reward_rebind_new_task():
    a = dup(TASK)
    b = dup(TASK)
    b["reward_id"] = "rew-different"
    assert I.task_id(a) != I.task_id(b)


# --------------------------------------------------------------------------- #
# episode                                                                     #
# --------------------------------------------------------------------------- #

def test_episode_volatile_timing_pid_path_changes_same_episode():
    a = dup(EPISODE)
    b = dup(EPISODE)
    b["created_at"] = "2999-01-01T00:00:00Z"
    b["process_id"] = 99999
    b["log_path"] = "/var/log/elsewhere.txt"
    b["workspace_abs_path"] = "/some/other/ws"
    b["episode_id"] = "episode-whatever"
    assert I.episode_id(a) == I.episode_id(b)
    assert I.canonicalize_episode(a) == I.canonicalize_episode(b)


def test_episode_toolchain_change_new_episode():
    a = dup(EPISODE)
    b = dup(EPISODE)
    b["execution_facts"]["toolchain"]["python"] = "3.11.5"
    assert I.episode_id(a) != I.episode_id(b)


def test_episode_platform_change_new_episode():
    a = dup(EPISODE)
    b = dup(EPISODE)
    b["execution_facts"]["platform"] = "darwin-arm64"
    assert I.episode_id(a) != I.episode_id(b)


def test_episode_exit_code_change_new_episode():
    a = dup(EPISODE)
    b = dup(EPISODE)
    b["execution_facts"]["exit_code"] = 1
    assert I.episode_id(a) != I.episode_id(b)


def test_episode_verifier_version_change_new_episode():
    a = dup(EPISODE)
    b = dup(EPISODE)
    b["verifier_versions"]["tests"] = "2"
    assert I.episode_id(a) != I.episode_id(b)


def test_episode_rescore_new_episode_same_trace():
    a = dup(EPISODE)
    b = dup(EPISODE)
    # Re-score the SAME trace under a new rubric + recomputed reward/verification.
    b["reward_id"] = "rew-newrubric"
    b["reward"] = 0.7
    b["verification"]["tests"] = "fail"
    assert I.episode_id(a) != I.episode_id(b)
    # ...but the trace it references is invariant: behaviour did not change.
    assert a["trace_id"] == b["trace_id"]
    assert I.trace_id(TRACE) == I.trace_id(TRACE)


# --------------------------------------------------------------------------- #
# cross-cutting sanity                                                        #
# --------------------------------------------------------------------------- #

def test_id_prefixes_and_shape():
    cases = [
        (I.snapshot_id(SNAPSHOT), "snap-"),
        (I.finding_id(FINDING), "finding-"),
        (I.patch_id(PATCH), "patch-"),
        (I.trace_id(TRACE), "trace-"),
        (I.reward_id(REWARD), "rew-"),
        (I.task_id(TASK), "task-"),
        (I.episode_id(EPISODE), "episode-"),
    ]
    for value, prefix in cases:
        assert value.startswith(prefix)
        digest = value[len(prefix):]
        assert len(digest) == 64
        int(digest, 16)  # raises if not lowercase hex


def test_ids_are_deterministic():
    for fn, fx in [
        (I.snapshot_id, SNAPSHOT), (I.finding_id, FINDING), (I.patch_id, PATCH),
        (I.trace_id, TRACE), (I.reward_id, REWARD), (I.task_id, TASK),
        (I.episode_id, EPISODE),
    ]:
        assert fn(dup(fx)) == fn(dup(fx))


# --------------------------------------------------------------------------- #
# standalone runner (zero deps)                                               #
# --------------------------------------------------------------------------- #

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
