"""Content-addressed identity for TRAAVIIS evaluation artifacts.

Pure canonicalization + hashing. **No I/O, no subprocess, no verifier
execution, no workspace mutation.** This module is the frozen identity spine
that `trvs eval-one` builds on; it is implemented and tested against the
mutation laws *before* any runner exists, exactly as the WRL identity spine was.

Each artifact id is

    <prefix>-<sha256(canonical_bytes)>

where `canonical_bytes` is a deterministic UTF-8 JSON serialization (sorted
object keys, minimal separators) of the artifact with its own id field removed
and any volatile / non-identity metadata excluded, per the frozen contracts in
`RFC_TRAAVIIS_ARTIFACTS.md` (§1–§4) and `RFC_EVIDENCE_RESIDENCY.md` (§4–§9).

Canonicalization rule of thumb: **maps are order-independent** (object keys are
sorted, so reordering an unordered map never moves an id); **lists preserve
their given order** (the producer is responsible for canonical list ordering).

    | function                | id prefix   | self-field dropped | other exclusions                        |
    | ----------------------- | ----------- | ------------------ | --------------------------------------- |
    | snapshot_id             | `snap-`     | snapshot_id        | —                                       |
    | finding_id              | `finding-`  | finding_id         | —                                       |
    | patch_id                | `patch-`    | patch_id           | line endings normalized to LF           |
    | trace_id                | `trace-`    | trace_id           | volatile events + timed logs (§5a)      |
    | reward_id               | `rew-`      | reward_id          | —                                       |
    | task_id                 | `task-`     | task_id            | —                                       |
    | episode_id              | `episode-`  | episode_id         | everything outside the identity allowlist (volatile timing/PID/path) |
"""

import hashlib
import json
from typing import Any, Mapping

__all__ = [
    "canonical_bytes",
    "canonicalize_snapshot", "snapshot_id",
    "canonicalize_finding", "finding_id",
    "canonicalize_patch", "patch_id",
    "canonicalize_trace", "trace_id",
    "canonicalize_reward", "reward_id",
    "canonicalize_task", "task_id",
    "canonicalize_episode", "episode_id",
]


def canonical_bytes(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON: sorted object keys, no insignificant whitespace."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _id(prefix: str, canon: bytes) -> str:
    return prefix + "-" + hashlib.sha256(canon).hexdigest()


def _drop(d: Mapping[str, Any], *keys: str) -> dict:
    return {k: v for k, v in d.items() if k not in keys}


# --- SnapshotV1 → snap- (RFC Evidence Residency §4) --------------------------
# `snap-` seals only the subject: repository file content-hashes, normalized
# relative paths, file modes, exclusions, base revision, task-visible config.
# `files` is a map, so file order never moves the snapshot; a byte change lands
# in a file's content-hash and does move it.

def canonicalize_snapshot(snapshot: Mapping[str, Any]) -> bytes:
    return canonical_bytes(_drop(snapshot, "snapshot_id"))


def snapshot_id(snapshot: Mapping[str, Any]) -> str:
    return _id("snap", canonicalize_snapshot(snapshot))


# --- FindingV1 → finding- (RFC Evidence Residency §5) ------------------------
# Structured claims + citations. Citation object *keys* are sorted (key order is
# cosmetic); a changed span or quote is a semantic change and moves finding-.

def canonicalize_finding(finding: Mapping[str, Any]) -> bytes:
    return canonical_bytes(_drop(finding, "finding_id"))


def finding_id(finding: Mapping[str, Any]) -> str:
    return _id("finding", canonicalize_finding(finding))


# --- PatchV1 → patch- (RFC Evidence Residency §5a) ---------------------------
# A unified diff. The frozen canonical rule normalizes line endings to LF so a
# CRLF/LF difference in the same diff is not a semantic change; the diff text
# itself is otherwise byte-significant.

def _normalize_diff(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonicalize_patch(patch: Mapping[str, Any]) -> bytes:
    p = _drop(patch, "patch_id")
    if isinstance(p.get("diff"), str):
        p["diff"] = _normalize_diff(p["diff"])
    return canonical_bytes(p)


def patch_id(patch: Mapping[str, Any]) -> str:
    return _id("patch", canonicalize_patch(patch))


# --- TraceV1 → trace- (RFC Evidence Residency §5a) ---------------------------
# The canonical trace records deterministic events only. Volatile execution
# metadata (wall-clock timestamps, host paths, human-readable timed logs) is
# projected out and never enters trace-.
_TRACE_EVENT_KEYS = (
    "command", "cwd", "environment_keys", "exit_code",
    "stdout_digest", "stderr_digest",
    "files_created_digest", "files_modified_digest", "files_deleted_digest",
    "file_modes_changed_digest", "result_file_digest", "policy_violations_digest",
)


def canonicalize_trace(trace: Mapping[str, Any]) -> bytes:
    events = [
        {k: e[k] for k in _TRACE_EVENT_KEYS if k in e}
        for e in trace.get("events", [])
    ]
    return canonical_bytes({
        "trace_version": trace.get("trace_version"),
        "events": events,
    })


def trace_id(trace: Mapping[str, Any]) -> str:
    return _id("trace", canonicalize_trace(trace))


# --- RewardSpecV1 → rew- (RFC Artifacts §2) ----------------------------------
# Signals are a keyed map, so signal-map order never moves rew-; renaming,
# rebinding, adding, removing, or reweighting a signal does move it.

def canonicalize_reward(reward: Mapping[str, Any]) -> bytes:
    return canonical_bytes(_drop(reward, "reward_id"))


def reward_id(reward: Mapping[str, Any]) -> str:
    return _id("rew", canonicalize_reward(reward))


# --- TaskSpecV1 → task- (RFC Artifacts §3) -----------------------------------
# Identity includes the instructions and the agent run policy; changing either
# moves task-. The referenced reward_id is part of the task, so rebinding the
# reward also moves task-.

def canonicalize_task(task: Mapping[str, Any]) -> bytes:
    return canonical_bytes(_drop(task, "task_id"))


def task_id(task: Mapping[str, Any]) -> str:
    return _id("task", canonicalize_task(task))


# --- EpisodeReceiptV1 → episode- (RFC Artifacts §4) --------------------------
# The whole receipt is hashed *except* its own id and all volatile metadata.
# We use an explicit identity allowlist: any field outside it (wall-clock
# timestamps, absolute paths, transient PIDs, display formatting, host-specific
# log locations) is excluded by construction. Canonical execution_facts
# (resolved toolchain versions, normalized platform, exit codes, timeout state,
# output-truncation state) and verifier_versions ARE inside the allowlist, so a
# toolchain / platform / exit-code / verifier-version change moves episode-.
_EPISODE_IDENTITY_KEYS = (
    "episode_version", "substrate_profile", "task_id", "reward_id", "subject",
    "trace_id", "outputs", "verification", "verifier_versions", "reward",
    "status", "validity", "replayability", "execution_facts",
)


def canonicalize_episode(receipt: Mapping[str, Any]) -> bytes:
    return canonical_bytes({
        k: receipt[k] for k in _EPISODE_IDENTITY_KEYS if k in receipt
    })


def episode_id(receipt: Mapping[str, Any]) -> str:
    return _id("episode", canonicalize_episode(receipt))
