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
      basename) so ``trace-…`` is host-independent. Every ``relpath`` in those
      maps is the *canonical evidence name* of the file (``_evidence_name``), not
      the raw string ``os.walk`` returned — see that function for why the two can
      differ and why the difference is the agent's doing.
"""

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from fnmatch import fnmatch
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import boundedjson as _bjson, identity
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


#: Marker for the canonical evidence name of a file whose real name is not UTF-8.
#:
#: A leading ``/`` is deliberate and load-bearing twice over. ``_scan`` builds
#: every name with ``os.path.relpath(abspath, root)``, which for a path under
#: ``root`` can never begin with ``/`` — so no real file can ever collide with an
#: escaped one, which is what makes ``_evidence_name`` injective. And
#: ``paths.safe_relposix`` *rejects* a leading ``/``, so if one of these names is
#: ever mistaken for a path to open, the mistake is a loud ``PathError`` rather
#: than a read of some other file.
_UNDECODABLE_NAME_PREFIX = "/undecodable-name.v1:"


def _evidence_name(rel: str) -> str:
    """The canonical evidence name of one scanned path.

    A POSIX filename is **bytes**, not text. ``os.walk`` hands them back as a
    ``str`` decoded with ``surrogateescape`` (PEP 383), so a file the agent
    created as ``b"evil\\xff.txt"`` arrives here as ``"evil\\udcff.txt"`` — a
    ``str`` holding a lone surrogate, which is precisely what
    ``identity.canonical_bytes`` refuses (``CANONICAL_ENCODING``). That name goes
    into ``files_created`` and thence into ``files_created_digest``, so before
    this function existed **one line in an agent** —
    ``open(b'evil\\xff.txt','wb').write(b'x')`` — raised an unhandled
    ``IdentityError`` out of ``run_agent``, *before it returned*. No result, no
    receipt, no bundle, no score: the candidate deleted its own grade. It is the
    same reward hack as the two closed in ``evalone._finding_artifact`` and in
    the result-decode handler below, and strictly the easiest of the three — the
    agent does not have to write a single malformed byte, and here it does not
    even have to submit a bad answer.

    **This is not the same kind of input as those two, and it does not get the
    same remedy.** A finding is the candidate's *submission*: refusing to read a
    malformed submission costs the candidate its claim, so collapsing it to the
    empty finding is honest. This map is the evaluator's *own observation* of
    what the run did to the workspace. An observation that drops what it could
    not name is a false record, and false in the direction that pays the
    candidate. Two remedies were rejected for exactly that reason:

    * **Collapse the whole map** (the ``_finding_artifact`` answer). One badly
      named file would erase the evidence for every file the run created — and
      ``policy_violations`` is computed from these maps, so the write-escape gate
      would go blind at the same moment. That is a *better* hack than the crash
      it replaces, because the episode now completes and looks clean.
    * **Drop just the offending entry.** Then a candidate can write files the
      evidence does not record, including outside ``writable_paths``: a crash
      (loud, no score) becomes a silent clean pass. Strictly worse.

    So the name is **escaped losslessly** instead: the inventory still records
    that something was created, at a name that is exactly recoverable
    (``bytes.fromhex`` of the suffix) and can be hashed. The two properties that
    make this safe are stated as laws in ``test_runner.py``:

    * **Identity on every encodable name.** The escape is applied only where
      ``str.encode`` refuses, so every workspace that worked before mints exactly
      the digests it minted before. No existing id moves — by construction, not
      by luck over a corpus.
    * **Injective.** Hex is injective on bytes and the prefix is unreachable for
      a real relative path, so two distinct workspaces can never share one
      ``trace-``.

    One consequence, stated rather than hidden: ``writable_paths`` globs are
    matched against this canonical name, so a policy of ``["*.txt"]`` does not
    admit ``b"evil\\xff.txt"`` the way it admits ``evil.txt``. That is the safe
    direction for a gate — a name that cannot be written down is treated as
    *not* matching the permission rather than as invisible — and it keeps one
    rule over one name, so a replayer checking the persisted violation list
    against the persisted policy reaches the same verdict this run did.
    """
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        # ``os.fsencode`` is the exact inverse of the ``os.fsdecode`` ``os.walk``
        # applied, so this recovers the filename's real bytes rather than a
        # re-encoding of them under some other assumption.
        return _UNDECODABLE_NAME_PREFIX + os.fsencode(rel).hex()
    return rel


def _scan(root: str) -> Dict[str, dict]:
    """``{canonical evidence name: {hash, mode, os_path}}`` for every file under ``root``.

    ``os_path`` is the concrete host path the entry was read from. It is carried
    because the canonical name is not always openable (see ``_evidence_name``),
    and it is never hashed: callers project ``hash`` / ``mode`` out and the
    digests are taken over those projections alone.
    """
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
            out[_evidence_name(rel)] = {
                "hash": digest, "mode": f"{mode:04o}", "os_path": abspath}
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
                result_obj = _bjson.load_json_bounded(result_bytes)
            except _bjson.BoundedJsonError:
                # malformed → orchestrator scores as fail.
                #
                # These are the candidate's own bytes, and §10a already rules on
                # them: *a missing or malformed agent output is a `fail`, never
                # an `error`.* So every way this decode can refuse the bytes has
                # to land here, not escape as an untyped crash — a crashed run
                # persists no receipt, and `batch`/`compare` refuse a pair they
                # cannot read, so a candidate that crashes its evaluator has
                # erased the bad score rather than earned a good one.
                #
                # The clause used to name only `ValueError` and
                # `UnicodeDecodeError`, which is the whole of what a
                # *hand-written* malformed file raises but not the whole of what
                # `json.loads` raises. It now names one type, because the
                # enumeration below belongs in `boundedjson` -- stated once --
                # rather than once per reader, which is how three readers came
                # to hold three different versions of it:
                #
                #   ValueError          `json.JSONDecodeError` — a syntax error;
                #                       also the >4300-digit integer refusal
                #                       (CVE-2020-10735), which is why a huge
                #                       numeric literal was already covered.
                #   UnicodeDecodeError  non-UTF-8 bytes out of `.decode`. It is a
                #                       `ValueError` subclass and so is already
                #                       implied, but it is named because it comes
                #                       from a different call than the rest.
                #   RecursionError      a *legal* document nested past the
                #                       decoder's stack. This is the one that was
                #                       missing: it is a `RuntimeError`, so the
                #                       old clause let it through. Measured: a
                #                       200 000-deep array is 400 kB of ordinary
                #                       bytes any agent can write, and before
                #                       this line it killed `eval-one` outright
                #                       (exit 1, no receipt, nothing persisted).
                #
                # Deliberately NOT caught here:
                #
                #   TypeError    `json.loads` raises it only for an argument that
                #                is not `str`/`bytes`. The argument is the result
                #                of `.decode`, so it is always a `str`: the only
                #                way to reach it is a bug in this function, and a
                #                bug in the evaluator must not be scored as
                #                somebody's bad finding.
                #   MemoryError  an out-of-memory parse is *not* classified here,
                #                on purpose. Two reasons. It is not attributable:
                #                whether a given document exhausts memory is a
                #                fact about the host, so catching it would score
                #                the same submission `fail` on one machine and
                #                let it through on another — a host-dependent
                #                reward is worse than a visible crash. And it
                #                would not close anything anyway: `fh.read()`
                #                above has already loaded the whole file, so an
                #                oversized `result.json` dies before this `try`
                #                is entered. The real closure for size is a
                #                declared byte bound on the result file (a fact
                #                about the bytes, identical on every host), which
                #                is a policy change, not a handler.
                #
                # Like `evalone._finding_artifact`, this offers the bytes to the
                # parser and honours its refusal rather than re-deriving what
                # JSON depth this host can take. A pre-parse depth check would be
                # a second, drifting copy of the decoder's domain.
                result_obj = None

        patch_text: Optional[str] = None
        pp = safe_join(root, patch_path)
        if os.path.isfile(pp):
            with open(pp, "rb") as fh:
                patch_bytes = fh.read()
            # A candidate patch is a unified diff over UTF-8 text; decode strictly.
            # Invalid UTF-8 is not a valid diff — reject it (no patch) rather than
            # lossily replacing bytes, which would seal a patch that never existed
            # and could not be re-attested on replay (GPT-5.6 strict-decode ruling).
            try:
                patch_text = patch_bytes.decode("utf-8")
            except UnicodeDecodeError:
                patch_text = None

        # Keyed by the canonical evidence name, opened by the concrete host path
        # ``_scan`` already read it from. Re-deriving the path from the key (the
        # old ``safe_join(root, rel)``) cannot work for an escaped name and was
        # never necessary: every key here came from this function's own walk
        # under ``root``, so there is nothing to re-validate, and reading the
        # recorded path guarantees the bytes reported are the bytes hashed.
        workspace_after = {}
        for rel, entry in after.items():
            with open(entry["os_path"], "rb") as fh:
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
