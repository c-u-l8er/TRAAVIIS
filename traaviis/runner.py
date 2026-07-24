"""Controlled one-shot agent runner + canonical ``TraceV1`` capture.

Enforces ``AgentRunPolicyV1`` (``RFC_EVIDENCE_RESIDENCY.md`` §10a) and records the
observable process events into a canonical ``TraceV1`` (§5a). This is the one
module in the identity/verifier core that crosses the **subprocess boundary**: it
materializes a controlled workspace, runs the agent argv with a *sealed*
environment and no shell, bounds time + output, and reads the declared result and
patch files after exit.

What is frozen and implemented exactly (§10a):

- ``command_mode: argv`` / ``shell: false`` — the command is an argv vector run
  without a shell; no shell interpolation.
- ``timeout_seconds`` / ``max_output_bytes`` are hard bounds; exceeding either
  sets ``timed_out`` / ``output_truncated`` and the affected verifier reports
  ``error`` (substrate unavailability), not ``fail``.
- ``environment`` is a **sealed key→value map**; host values (``HOME``, ``LANG``…)
  are **not** inherited — only the sealed keys with their fixed values are
  exported. A caller-supplied ``PATH`` is **stripped** (R1).
- ``result_path`` / ``patch_path`` are read after exit; a missing required output
  is a ``fail`` for the corresponding verifier (handled by the orchestrator),
  never an ``error``.

GPT-5.6 closure rulings implemented here:

  R1  ``PATH`` is owned by the ``toolchain_profile`` resolver, never the caller.
      The runner strips any caller ``PATH`` and injects only a resolver-supplied
      one. The prototype resolver returns ``None`` (no ``PATH``), so agents must
      use absolute argv — a real pinned-toolchain resolver is deferred.
  R2  The runner does **not** enforce a network sandbox. The honest profile
      ``residency.trusted-local.v1`` reports ``network: unrestricted`` (see
      ``execfacts``); it is only safe for a trusted deterministic subject.
  R3  Writable-path enforcement is **observation**, not blocking: a write outside
      ``writable_paths`` is recorded in ``policy_violations`` (and its digest in
      the trace); the orchestrator decides the episode verdict. True filesystem
      escapes (writes outside the workspace tree) are **not** detected in v1.
  R4  Trace digests are sha256 over canonical JSON of ``{relpath: content_hash}``
      for created/modified/deleted maps, ``{relpath: mode}`` for mode changes, and
      the sorted ``policy_violations`` list; ``result_file_digest`` is sha256 of
      the raw result bytes. The ``command`` is normalized (absolute argv tokens →
      basename) so ``trace-…`` is host-independent.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from fnmatch import fnmatch
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import identity
from .paths import safe_join

__all__ = ["run_agent", "RunResult", "TRACE_VERSION", "RUNNER_PROFILE"]

TRACE_VERSION = "residency.trace.v1"

# The one honest runner profile: writes are *observed* by rescan (not blocked),
# network is unrestricted. See ``execfacts.RUNNER_PROFILES``. A caller-supplied
# ``PATH`` is never trusted (R1) — the toolchain resolver owns ``PATH``.
RUNNER_PROFILE = "residency.trusted-local.v1"


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_json(obj: Any) -> str:
    return _sha256_bytes(identity.canonical_bytes(obj))


def _normalize_command(argv: Sequence[str]) -> List[str]:
    # Absolute argv tokens (the interpreter path, an absolute script path) are
    # machine-specific; the canonical trace records only their basename so
    # ``trace-…`` is host-independent (R4). Relative tokens and flags pass through.
    return [os.path.basename(str(t)) if os.path.isabs(str(t)) else str(t)
            for t in argv]


def _seal_env(policy: Mapping[str, Any]) -> Dict[str, str]:
    # R1: seal the caller's environment map but NEVER trust a caller ``PATH`` —
    # ``PATH`` is resolved from the ``toolchain_profile`` by a trusted resolver.
    # The prototype has no resolver, so ``PATH`` is simply unset and the agent
    # must use absolute argv. A real toolchain-profile resolver is deferred.
    env: Dict[str, str] = {}
    for k, v in dict(policy.get("environment", {})).items():
        if str(k) == "PATH":
            continue
        env[str(k)] = str(v)
    resolved = _resolve_toolchain_path(policy.get("toolchain_profile"))
    if resolved is not None:
        env["PATH"] = resolved
    return env


def _resolve_toolchain_path(toolchain_profile: Optional[str]) -> Optional[str]:
    # Deferred: a real resolver maps a toolchain profile to a controlled PATH of
    # pinned, digest-verified executables. Until it exists, return None (no PATH).
    return None


def _materialize(content: Mapping[str, str], root: str) -> None:
    for rel, text in content.items():
        path = safe_join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        data = text.encode("utf-8") if isinstance(text, str) else text
        with open(path, "wb") as fh:
            fh.write(data)


def _scan(root: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            abspath = os.path.join(dirpath, name)
            if os.path.islink(abspath):
                continue
            rel = os.path.relpath(abspath, root).replace(os.sep, "/")
            with open(abspath, "rb") as fh:
                digest = _sha256_bytes(fh.read())
            mode = stat.S_IMODE(os.lstat(abspath).st_mode)
            out[rel] = {"hash": digest, "mode": f"{mode:04o}"}
    return out


def _writable_ok(rel: str, writable: Sequence[str]) -> bool:
    for pattern in writable:
        if pattern in (".", "./") or fnmatch(rel, pattern):
            return True
        if pattern.endswith("/") and rel.startswith(pattern):
            return True
    return False


class RunResult(dict):
    """Structured result of a controlled agent run (a plain dict subclass)."""


def run_agent(
    agent_command: Sequence[str],
    content: Mapping[str, str],
    policy: Mapping[str, Any],
) -> RunResult:
    """Run ``agent_command`` over a controlled copy of ``content`` under ``policy``.

    Returns a ``RunResult`` with: ``exit_code``, ``timed_out``,
    ``output_truncated``, ``stdout``/``stderr`` (bytes, capped), ``result`` (parsed
    JSON or ``None``), ``patch_text`` (or ``None``), ``files_created`` /
    ``files_modified`` (``{relpath: content_hash}``), ``policy_violations``,
    ``workspace_after`` (``{relpath: text}``), and a canonical ``trace``
    (``TraceV1`` with its ``trace_id`` set).
    """
    timeout = policy.get("timeout_seconds")
    max_out = int(policy.get("max_output_bytes", 4 * 1024 * 1024))
    sealed_env = _seal_env(policy)
    result_path = policy.get("result_path", "result.json")
    patch_path = policy.get("patch_path", "candidate.patch")
    writable = list(policy.get("writable_paths", ["."]))

    root = tempfile.mkdtemp(prefix="traaviis-run-")
    try:
        _materialize(content, root)
        before = _scan(root)

        timed_out = False
        try:
            proc = subprocess.run(
                list(agent_command),
                cwd=root,
                env=sealed_env,  # sealed map only; host env not inherited (§10a)
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
            exit_code: Optional[int] = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = None
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""

        stdout_truncated = len(stdout) > max_out
        stderr_truncated = len(stderr) > max_out
        output_truncated = stdout_truncated or stderr_truncated
        stdout, stderr = stdout[:max_out], stderr[:max_out]

        after = _scan(root)
        created = {p: e["hash"] for p, e in after.items() if p not in before}
        modified = {p: e["hash"] for p, e in after.items()
                    if p in before and before[p]["hash"] != e["hash"]}
        deleted = {p: before[p]["hash"] for p in before if p not in after}
        modes_changed = {p: after[p]["mode"] for p in after
                         if p in before and before[p]["mode"] != after[p]["mode"]}
        violations = sorted(
            p for p in list(created) + list(modified) + list(deleted)
            if not _writable_ok(p, writable)
        )

        result_obj: Optional[Any] = None
        rp = safe_join(root, result_path)
        result_bytes = b""
        if os.path.isfile(rp):
            with open(rp, "rb") as fh:
                result_bytes = fh.read()
            try:
                result_obj = json.loads(result_bytes.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                result_obj = None  # malformed → orchestrator scores as fail

        patch_text: Optional[str] = None
        pp = safe_join(root, patch_path)
        if os.path.isfile(pp):
            with open(pp, "rb") as fh:
                patch_text = fh.read().decode("utf-8", errors="replace")

        workspace_after = {}
        for rel in after:
            with open(safe_join(root, rel), "rb") as fh:
                workspace_after[rel] = fh.read().decode("utf-8", errors="replace")

        event = {
            "command": _normalize_command(agent_command),
            "cwd": ".",
            "environment_keys": sorted(sealed_env.keys()),
            "exit_code": exit_code,
            "stdout_digest": _sha256_bytes(stdout),
            "stderr_digest": _sha256_bytes(stderr),
            "files_created_digest": _sha256_json(created),
            "files_modified_digest": _sha256_json(modified),
            "files_deleted_digest": _sha256_json(deleted),
            "file_modes_changed_digest": _sha256_json(modes_changed),
            "result_file_digest": _sha256_bytes(result_bytes),
            "policy_violations_digest": _sha256_json(violations),
        }
        trace = {"trace_version": TRACE_VERSION, "events": [event]}
        trace["trace_id"] = identity.trace_id(trace)

        return RunResult(
            exit_code=exit_code,
            timed_out=timed_out,
            output_truncated=output_truncated,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            stdout=stdout,
            stderr=stderr,
            result=result_obj,
            patch_text=patch_text,
            files_created=created,
            files_modified=modified,
            files_deleted=deleted,
            file_modes_changed=modes_changed,
            policy_violations=violations,
            workspace_after=workspace_after,
            trace=trace,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
