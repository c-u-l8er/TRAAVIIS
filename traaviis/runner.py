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
- ``environment`` is a **sealed key→value map**; host values (``PATH``, ``HOME``,
  ``LANG``…) are **not** inherited — only the sealed keys with their fixed values
  are exported.
- ``result_path`` / ``patch_path`` are read after exit; a missing required output
  is a ``fail`` for the corresponding verifier (handled by the orchestrator),
  never an ``error``.

Under-frozen engine edges — implemented defensively and **flagged for GPT-5.6**:

  R1  Sandbox ``PATH`` / ``toolchain_profile`` resolution. §10a says the runner
      constructs ``PATH`` from the ``toolchain_profile`` and never inherits the
      host ``PATH``. v1 here: if the sealed ``environment`` supplies ``PATH`` it
      is used verbatim; otherwise ``PATH`` is left unset (the agent must use
      absolute argv). A real toolchain-profile resolver is deferred.
  R2  ``network: disabled`` is recorded and passed through but **not** hard-
      enforced (no namespace/sandbox). Enforcement mechanism is deferred.
  R3  Writable-path enforcement: a write outside ``writable_paths`` is reported
      as ``policy_violations`` for the orchestrator to turn into an invalid
      episode; this module does not itself fail the run.
  R4  Trace digest construction: ``files_created_digest`` /
      ``files_modified_digest`` are sha256 over canonical JSON of
      ``{relpath: content_hash}``; ``result_file_digest`` is sha256 of the raw
      result bytes. This canonicalization is what enters ``trace-…``.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from fnmatch import fnmatch
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import identity

__all__ = ["run_agent", "RunResult", "TRACE_VERSION"]

TRACE_VERSION = "residency.trace.v1"


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_json(obj: Any) -> str:
    return _sha256_bytes(identity.canonical_bytes(obj))


def _materialize(content: Mapping[str, str], root: str) -> None:
    for rel, text in content.items():
        path = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        data = text.encode("utf-8") if isinstance(text, str) else text
        with open(path, "wb") as fh:
            fh.write(data)


def _scan(root: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            abspath = os.path.join(dirpath, name)
            if os.path.islink(abspath):
                continue
            rel = os.path.relpath(abspath, root).replace(os.sep, "/")
            with open(abspath, "rb") as fh:
                out[rel] = _sha256_bytes(fh.read())
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
    sealed_env = {str(k): str(v) for k, v in dict(policy.get("environment", {})).items()}
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

        output_truncated = len(stdout) > max_out or len(stderr) > max_out
        stdout, stderr = stdout[:max_out], stderr[:max_out]

        after = _scan(root)
        created = {p: h for p, h in after.items() if p not in before}
        modified = {p: h for p, h in after.items()
                    if p in before and before[p] != h}
        violations = sorted(
            p for p in list(created) + list(modified)
            if not _writable_ok(p, writable)
        )

        result_obj: Optional[dict] = None
        rp = os.path.join(root, result_path.replace("/", os.sep))
        result_bytes = b""
        if os.path.isfile(rp):
            with open(rp, "rb") as fh:
                result_bytes = fh.read()
            try:
                result_obj = json.loads(result_bytes.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                result_obj = None  # malformed → orchestrator scores as fail

        patch_text: Optional[str] = None
        pp = os.path.join(root, patch_path.replace("/", os.sep))
        if os.path.isfile(pp):
            with open(pp, "rb") as fh:
                patch_text = fh.read().decode("utf-8", errors="replace")

        workspace_after = {}
        for rel in after:
            with open(os.path.join(root, rel.replace("/", os.sep)), "rb") as fh:
                workspace_after[rel] = fh.read().decode("utf-8", errors="replace")

        event = {
            "command": list(agent_command),
            "cwd": ".",
            "environment_keys": sorted(sealed_env.keys()),
            "exit_code": exit_code,
            "stdout_digest": _sha256_bytes(stdout),
            "stderr_digest": _sha256_bytes(stderr),
            "files_created_digest": _sha256_json(created),
            "files_modified_digest": _sha256_json(modified),
            "result_file_digest": _sha256_bytes(result_bytes),
        }
        trace = {"trace_version": TRACE_VERSION, "events": [event]}
        trace["trace_id"] = identity.trace_id(trace)

        return RunResult(
            exit_code=exit_code,
            timed_out=timed_out,
            output_truncated=output_truncated,
            stdout=stdout,
            stderr=stderr,
            result=result_obj,
            patch_text=patch_text,
            files_created=created,
            files_modified=modified,
            policy_violations=violations,
            workspace_after=workspace_after,
            trace=trace,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
