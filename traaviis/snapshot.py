"""Build a frozen ``SnapshotV1`` subject from a repository directory.

Reads a working tree and seals it into the canonical subject artifact defined in
``RFC_EVIDENCE_RESIDENCY.md`` §4 (``SnapshotV1`` → ``snap-…``) under the canonical
evidence rules of §5a. **Read-only**: this module never mutates the tree, never
shells out, and never runs a verifier. Its output feeds ``identity.snapshot_id``.

Sealed (§4): repository bytes, normalized relative POSIX paths, file modes,
exclusions, base revision (if the caller supplies one), and task-visible config.
Not sealed: timestamps and absolute machine paths — they would make the snapshot
machine-specific without changing its meaning.

Canonical evidence rules applied here (§5a):

- **Paths** are UTF-8 relative POSIX (``/`` separator on every platform); no
  absolute paths and no ``..`` ever appear.
- **Symlinks are excluded in v1** (neither followed nor sealed).
- **Line endings**: text files are normalized to LF before hashing; a path the
  caller declares binary is hashed byte-exact.
- **Content hash** is ``sha256:<hexdigest>`` of the (normalized) bytes.

Under-frozen edge flagged for GPT-5.6:

  S1  A file is *declared binary* by explicit ``binary_paths`` input (relative
      POSIX paths); everything else is text and LF-normalized. No content sniffing.
      Per GPT-5.6: the sorted ``binary_paths`` are **sealed into the snapshot**
      (not merely used transiently by the builder), so the normalization rule that
      produced each content-hash is itself part of ``snap-…``.

  S2  ``base_revision`` and ``visible_config`` are caller-supplied so this module
      stays subprocess-free; the orchestrator resolves the VCS revision.
"""

import hashlib
import os
import stat
from fnmatch import fnmatch
from typing import Iterable, Mapping, Optional

from . import identity
from .paths import PathError

__all__ = ["build_snapshot", "SNAPSHOT_VERSION"]

SNAPSHOT_VERSION = "residency.snapshot.v1"


def _check_encodable(rel: str, root: str) -> None:
    """Refuse a subject file whose **name** is not valid UTF-8.

    A POSIX filename is bytes; ``os.walk`` decodes it with ``surrogateescape``
    (PEP 383), so a file named ``b"evil\\xff.txt"`` arrives as a ``str`` holding
    a lone surrogate. Left alone it lands in ``snapshot["files"]`` and
    ``identity.canonical_bytes`` refuses it — correctly, but three frames down
    and saying only *"the key at $ contains U+DCFF at offset 4"*, which does not
    tell an operator which file to rename.

    ``runner._scan`` meets the same byte sequence and does **not** refuse it: it
    escapes the name losslessly and carries on. The difference is not an
    inconsistency, it is the difference between the two inputs:

    * **Who authored it.** The runner is inventorying what the *candidate under
      evaluation* did to a workspace, and a candidate that can crash the
      evaluator can delete its own grade — so that path must complete and score
      no matter what the agent writes. A subject tree is *operator-authored
      input*. An operator gains nothing by crashing their own snapshot build;
      they get no evaluation at all, so there is no incentive to refuse away.

    * **What the name is later used for.** A trace digest's keys are read by
      humans and by replay, and never opened. A snapshot's ``files`` keys become
      the keys of the ``content`` map, which ``runner._materialize`` and
      ``episode_bundle``'s closure check both push through
      ``paths.safe_relposix`` — which rejects a leading ``/``. So the runner's
      escape, applied here, would seal a subject that admits cleanly and then
      cannot be laid down by any runner or declared by any bundle. Escaping
      would convert a loud failure into a late and much more confusing one.

    §5a already fixes the domain — *paths are UTF-8 relative POSIX* — and already
    excludes symlinks on the same grounds: some filesystem objects are not
    sealable in v1. This is one of them. So the refusal stands; what changes is
    that it happens at the boundary the operator controls and names the file.
    """
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        raw = os.fsencode(rel)
        raise PathError(
            "subject file name is not valid UTF-8 and cannot be sealed: %s "
            "(raw bytes %s) under %s. Canonical evidence paths are UTF-8 "
            "relative POSIX (RFC_EVIDENCE_RESIDENCY.md §5a); a name that has no "
            "UTF-8 form has no content address, and escaping it would seal a "
            "subject no runner could materialize. Rename or exclude the file."
            % (raw.decode("utf-8", "backslashreplace"), raw.hex(), root)
        ) from None


def _normalize_lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _excluded(relpath: str, exclusions: Iterable[str]) -> bool:
    # Match the full POSIX relpath and each of its directory prefixes, so a glob
    # like "**/__pycache__/**" or "build/*" drops a whole subtree.
    for pattern in exclusions:
        if fnmatch(relpath, pattern):
            return True
    return False


def build_snapshot(
    root: str,
    *,
    exclusions: Iterable[str] = (),
    binary_paths: Iterable[str] = (),
    base_revision: Optional[str] = None,
    visible_config: Optional[Mapping[str, object]] = None,
) -> dict:
    """Seal ``root`` into a ``SnapshotV1`` dict (with its ``snapshot_id`` set).

    ``exclusions`` are POSIX globs matched against each relative path.
    ``binary_paths`` are relative POSIX paths hashed byte-exact (S1); all other
    files are LF-normalized. ``base_revision`` / ``visible_config`` are sealed
    verbatim (S2).

    Raises ``paths.PathError`` if a file that is *not* excluded has a name that
    is not valid UTF-8 — see ``_check_encodable`` for why this refuses where
    ``runner._scan`` escapes. Exclusions are applied first, so a tree with an
    unsealable name in an already-excluded subtree still seals.
    """
    exclusions = list(exclusions)
    binary = set(binary_paths)
    files: dict = {}
    file_modes: dict = {}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Do not descend into symlinked directories (v1: symlinks excluded).
        dirnames[:] = [
            d for d in dirnames
            if not os.path.islink(os.path.join(dirpath, d))
        ]
        for name in filenames:
            abspath = os.path.join(dirpath, name)
            if os.path.islink(abspath):
                continue  # v1: symlinks excluded
            rel = os.path.relpath(abspath, root).replace(os.sep, "/")
            if _excluded(rel, exclusions):
                continue  # an unsealable name can still be excluded, so check after
            _check_encodable(rel, root)
            with open(abspath, "rb") as fh:
                data = fh.read()
            if rel not in binary:
                data = _normalize_lf(data)
            files[rel] = _content_hash(data)
            mode = stat.S_IMODE(os.lstat(abspath).st_mode)
            file_modes[rel] = f"{mode:04o}"

    snapshot = {
        "snapshot_version": SNAPSHOT_VERSION,
        "files": files,
        "exclusions": sorted(exclusions),  # producer owns list order → canonical
        "binary_paths": sorted(binary),    # S1: sealed, not merely transient
        "file_modes": file_modes,
        "base_revision": base_revision,
        "visible_config": dict(visible_config or {}),
    }
    snapshot["snapshot_id"] = identity.snapshot_id(snapshot)
    return snapshot
