"""Preflight admission: verify identity + bind the subject before any run.

The runner and orchestrator must never trust self-asserted state. Two laws close
the two biggest correctness gaps GPT-5.6 flagged:

  1. **Declared-id verification.** A ``task``/``reward``/``snapshot`` may carry its
     own ``*_id``. We recompute it and compare: declared-absent → compute and
     accept; declared-correct → accept; declared-mismatched → **reject before
     execution** (``AdmissionError``). An unverified self-asserted id never reaches
     an ``episode-…`` receipt.

  2. **Snapshot ↔ content binding.** ``eval_one`` receives the sealed ``snap-…``
     and the working ``content`` as independent inputs. Before running anything we
     prove ``content`` *is* the subject the snapshot sealed: identical path set,
     matching content-hash per path (under the snapshot's own LF/binary rule),
     matching file modes when present, and honored exclusions. A mismatch means the
     receipt would lie about its ``subject`` — so it is rejected here.

Pure: hashing + comparison only, no subprocess, no workspace mutation. Path
admission is delegated to ``traaviis.paths`` so a snapshot that smuggles an
absolute or ``..`` path is rejected too.
"""

import hashlib
import os
import stat
from fnmatch import fnmatch
from typing import Any, Mapping, Optional

from . import identity
from .paths import PathError, safe_join, safe_relposix

__all__ = [
    "AdmissionError",
    "verify_declared_id",
    "verify_materialization",
    "admit_subject",
    "materialize_subject",
    "cross_bind_task",
    "verify_subject_tree",
]


class AdmissionError(Exception):
    """A declared identity or a snapshot↔content binding failed preflight."""


def _normalize_lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def verify_declared_id(
    obj: Mapping[str, Any], id_field: str, compute,
) -> str:
    """Recompute ``obj[id_field]`` via ``compute(obj)`` and reconcile.

    Returns the verified id. Raises ``AdmissionError`` if a declared id is present
    but does not match the recomputed one. ``compute`` is one of the
    ``identity.*_id`` functions.
    """
    computed = compute(obj)
    declared = obj.get(id_field)
    if declared is not None and declared != computed:
        raise AdmissionError(
            f"declared {id_field}={declared!r} does not match computed {computed!r}"
        )
    return computed


def verify_materialization(
    snapshot: Mapping[str, Any],
    content: Mapping[str, str],
    *,
    binary_paths=(),
) -> None:
    """Prove ``content`` is exactly the subject sealed by ``snapshot``.

    ``content`` maps relpath → text (the working copy the runner will materialize).
    ``binary_paths`` are relpaths hashed byte-exact (must match how the snapshot was
    built). Raises ``AdmissionError`` on any path-set difference, hash mismatch, or
    unsafe path; raises nothing and returns ``None`` on an exact bind.
    """
    sealed_files = snapshot.get("files")
    if not isinstance(sealed_files, Mapping):
        raise AdmissionError("snapshot has no 'files' map")

    binary = set(binary_paths)

    # Every sealed path must be safe and present in content, and vice-versa.
    sealed_norm = {}
    for rel in sealed_files:
        try:
            norm = safe_relposix(rel)
        except PathError as exc:
            raise AdmissionError(f"snapshot path inadmissible: {exc}") from exc
        sealed_norm[norm] = sealed_files[rel]

    content_norm = {}
    for rel, text in content.items():
        try:
            norm = safe_relposix(rel)
        except PathError as exc:
            raise AdmissionError(f"content path inadmissible: {exc}") from exc
        content_norm[norm] = text

    missing = set(sealed_norm) - set(content_norm)
    extra = set(content_norm) - set(sealed_norm)
    if missing:
        raise AdmissionError(f"content missing sealed paths: {sorted(missing)}")
    if extra:
        raise AdmissionError(f"content has paths not in snapshot: {sorted(extra)}")

    for rel, sealed_hash in sealed_norm.items():
        text = content_norm[rel]
        data = text.encode("utf-8") if isinstance(text, str) else text
        if rel not in binary:
            data = _normalize_lf(data)
        got = _content_hash(data)
        if got != sealed_hash:
            raise AdmissionError(
                f"content hash mismatch for {rel!r}: sealed {sealed_hash}, got {got}"
            )


def cross_bind_task(
    task: Mapping[str, Any], reward_id: str, snapshot_id: str,
) -> None:
    """Bind the task to the *verified* reward + snapshot it references.

    Verifying each artifact's own declared id (``verify_declared_id``) only proves
    each is internally consistent — it does NOT prove the ``task`` references *these*
    artifacts. A task carrying a fabricated ``reward_id`` / ``subject.snapshot_id``
    (e.g. ``rew-000…`` / ``snap-000…``) would otherwise be accepted against an
    unrelated reward/snapshot and the receipt would misattribute its scoring and
    subject. Here we require the task's own references to equal the verified ids and
    reject before execution otherwise.
    """
    declared_reward = task.get("reward_id")
    if declared_reward != reward_id:
        raise AdmissionError(
            f"task.reward_id={declared_reward!r} does not bind the supplied "
            f"reward spec {reward_id!r}"
        )
    subject = task.get("subject")
    declared_snap = subject.get("snapshot_id") if isinstance(subject, Mapping) else None
    if declared_snap != snapshot_id:
        raise AdmissionError(
            f"task.subject.snapshot_id={declared_snap!r} does not bind the "
            f"supplied snapshot {snapshot_id!r}"
        )


def admit_subject(
    snapshot: Mapping[str, Any],
    content: Mapping[str, str],
    *,
    binary_paths=(),
) -> str:
    """Verify the snapshot's own id, then bind it to ``content``. Returns snap-id.

    The one preflight the orchestrator calls before it will run an agent against a
    subject: (1) the snapshot's declared ``snapshot_id`` (if any) is correct, and
    (2) ``content`` materializes exactly to that sealed subject.
    """
    snap_id = verify_declared_id(snapshot, "snapshot_id", identity.snapshot_id)
    verify_materialization(snapshot, content, binary_paths=binary_paths)
    return snap_id


def materialize_subject(
    snapshot: Mapping[str, Any],
    content: Mapping[str, str],
    root: str,
) -> None:
    """Write ``content`` into ``root`` restoring the snapshot's sealed file modes.

    The write-side twin of ``verify_subject_tree``: the runner's plain
    ``_materialize`` writes bytes with the process umask, dropping the sealed
    permission bits, so a ``0755`` executable in the subject would silently come
    back ``0644`` and fail re-admission. This restores each path's sealed
    ``file_modes`` entry after writing (defaulting to ``0644`` when a path has no
    sealed mode), so a materialized-then-re-admitted subject binds exactly.

    ``content`` values are UTF-8 ``str`` (LF-canonical) or ``bytes`` (declared
    binaries). Raises ``AdmissionError`` on an unsafe relpath.
    """
    file_modes = snapshot.get("file_modes", {}) or {}
    sealed_modes = {}
    for rel, mode in file_modes.items():
        try:
            sealed_modes[safe_relposix(rel)] = mode
        except PathError as exc:
            raise AdmissionError(f"snapshot mode path inadmissible: {exc}") from exc

    for rel, text in content.items():
        try:
            safe_rel = safe_relposix(rel)
        except PathError as exc:
            raise AdmissionError(f"content path inadmissible: {exc}") from exc
        path = safe_join(root, safe_rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        data = text.encode("utf-8") if isinstance(text, str) else text
        with open(path, "wb") as fh:
            fh.write(data)
        sealed = sealed_modes.get(safe_rel, "0644")
        os.chmod(path, int(sealed, 8))


def _excluded(relpath: str, exclusions) -> bool:
    for pattern in exclusions:
        if fnmatch(relpath, pattern):
            return True
    return False


def verify_subject_tree(
    snapshot: Mapping[str, Any], subject_root: str,
) -> dict:
    """Read + admit a subject *directory* on disk against ``snapshot``; return content.

    This is the tree-level admission the bundle loader uses. Beyond the byte binding
    of ``verify_materialization`` it enforces the full §5a canonical-evidence surface
    that only exists on disk:

      * **Declared binaries are loaded as bytes**, everything else as UTF-8 text. A
        non-UTF-8 file that is *not* declared binary is rejected (never silently
        mangled).
      * **No symlinks or special files.** v1 seals only regular files; a symlink or
        device/fifo in the subject tree is rejected, never followed.
      * **File modes must match** the sealed ``file_modes`` exactly.
      * **Exclusions are honored** — a path matching a sealed exclusion glob is
        skipped, not treated as an extra.

    Returns the ``{relpath: text|bytes}`` content map (binaries as ``bytes``) that
    binds exactly to the snapshot. Raises ``AdmissionError`` on any violation.
    """
    files = snapshot.get("files")
    if not isinstance(files, Mapping):
        raise AdmissionError("snapshot has no 'files' map")
    exclusions = list(snapshot.get("exclusions", []))
    binary = set(snapshot.get("binary_paths", ()))
    file_modes = snapshot.get("file_modes", {}) or {}

    content: dict = {}
    modes_on_disk: dict = {}

    for dirpath, dirnames, filenames in os.walk(subject_root, followlinks=False):
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                rel = os.path.relpath(
                    os.path.join(dirpath, d), subject_root).replace(os.sep, "/")
                raise AdmissionError(f"subject contains a symlinked directory: {rel!r}")
        for name in filenames:
            abspath = os.path.join(dirpath, name)
            rel = os.path.relpath(abspath, subject_root).replace(os.sep, "/")
            if os.path.islink(abspath):
                raise AdmissionError(f"subject contains a symlink: {rel!r}")
            if not os.path.isfile(abspath):
                raise AdmissionError(f"subject contains a non-regular file: {rel!r}")
            if _excluded(rel, exclusions):
                continue
            try:
                safe_rel = safe_relposix(rel)
            except PathError as exc:
                raise AdmissionError(f"subject path inadmissible: {exc}") from exc
            with open(abspath, "rb") as fh:
                data = fh.read()
            if safe_rel in binary:
                content[safe_rel] = data
            else:
                # The admitted materialization must be the *canonical* LF
                # representation, not merely something that hashes to it. A CRLF
                # and an LF subject seal the same snap-… (snapshot hashing
                # normalizes to LF); if we handed back the original line endings
                # the same snap-… could expose different bytes to the agent and
                # yield a different trace. Normalize first, then decode.
                try:
                    content[safe_rel] = _normalize_lf(data).decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise AdmissionError(
                        f"subject file {safe_rel!r} is not UTF-8 text; declare it "
                        f"binary in the snapshot: {exc}"
                    ) from exc
            mode = stat.S_IMODE(os.lstat(abspath).st_mode)
            modes_on_disk[safe_rel] = f"{mode:04o}"

    # Path set + content-hash binding (also rejects unsafe sealed paths).
    verify_materialization(snapshot, content, binary_paths=binary)

    # File modes must match the sealed modes exactly.
    for rel, sealed_mode in file_modes.items():
        try:
            norm = safe_relposix(rel)
        except PathError as exc:
            raise AdmissionError(f"snapshot mode path inadmissible: {exc}") from exc
        got = modes_on_disk.get(norm)
        if got != sealed_mode:
            raise AdmissionError(
                f"file mode mismatch for {norm!r}: sealed {sealed_mode}, got {got}"
            )

    return content
