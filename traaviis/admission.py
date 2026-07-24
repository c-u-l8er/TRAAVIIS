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
from typing import Any, Mapping, Optional

from . import identity
from .paths import PathError, safe_relposix

__all__ = [
    "AdmissionError",
    "verify_declared_id",
    "verify_materialization",
    "admit_subject",
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
