"""Safe relative-POSIX path validation, used at every path boundary.

One rule, enforced everywhere (``RFC_EVIDENCE_RESIDENCY.md`` §5a): every path that
crosses into the evaluation — snapshot files, ``result_path`` / ``patch_path``,
``writable_paths``, citation paths, unified-diff ``a/``/``b/`` paths, test-command
``cwd``, and identity-policy source paths — is a **relative POSIX path with no
absolute component and no ``..`` segment**. This closes the traversal gap where
``os.path.join(root, absolute_or_dotdot)`` escapes the sealed workspace.

``safe_relposix`` returns the normalized path (collapsing ``.`` segments and any
``\\`` separators to ``/``) or raises ``PathError``. ``is_safe_relposix`` is the
boolean form. Neither touches the filesystem — this is pure string law.
"""

import posixpath
from typing import Iterable, List

__all__ = ["PathError", "safe_relposix", "is_safe_relposix", "safe_join"]


class PathError(Exception):
    """A path was absolute, escaped the root, or was otherwise inadmissible."""


def safe_relposix(path: str, *, allow_empty: bool = False) -> str:
    """Return ``path`` as a normalized safe relative POSIX path, or raise.

    Rejects: non-strings, Windows/absolute drive forms, a leading ``/`` (POSIX
    absolute), any ``..`` segment, and (unless ``allow_empty``) the empty / pure
    ``.`` path. Backslashes are treated as separators and normalized to ``/``.
    """
    if not isinstance(path, str):
        raise PathError(f"path is not a string: {path!r}")
    raw = path.replace("\\", "/")
    if raw == "" or raw == ".":
        if allow_empty:
            return "."
        raise PathError("empty path is not allowed here")
    if raw.startswith("/"):
        raise PathError(f"absolute path is not allowed: {path!r}")
    # Windows drive / UNC forms ("C:/...", "//host/...") are absolute too.
    if len(raw) >= 2 and raw[1] == ":":
        raise PathError(f"absolute (drive) path is not allowed: {path!r}")
    segments: List[str] = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue  # collapse redundant separators and '.' segments
        if seg == "..":
            raise PathError(f"'..' segment is not allowed: {path!r}")
        segments.append(seg)
    if not segments:
        if allow_empty:
            return "."
        raise PathError(f"path resolves to empty: {path!r}")
    norm = posixpath.join(*segments)
    return norm


def is_safe_relposix(path: str, *, allow_empty: bool = False) -> bool:
    try:
        safe_relposix(path, allow_empty=allow_empty)
        return True
    except PathError:
        return False


def safe_join(root: str, relpath: str) -> str:
    """Join ``relpath`` under ``root`` on the host FS after validating it is safe.

    ``relpath`` is validated with ``safe_relposix`` first, so the result can never
    escape ``root``. Returns a host-native path.
    """
    import os

    rel = safe_relposix(relpath)
    return os.path.join(root, rel.replace("/", os.sep))


def all_safe(paths: Iterable[str]) -> bool:
    return all(is_safe_relposix(p) for p in paths)
