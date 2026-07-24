"""Strict, evidence-grade unified-diff applier over in-memory file content.

Pure, no I/O, no subprocess. Applies a canonical v1 unified diff
(``RFC_EVIDENCE_RESIDENCY.md`` §5a) to a mapping of ``{relpath: text}`` and returns
a **new** mapping. Evidence-grade means **exact, fully-admitted, no fuzz**: any
inadmissible header, unsafe path, count mismatch, or context mismatch raises
``PatchError`` — the ``patch`` verifier turns that into a clean ``fail``, never a
silent or partial apply.

v1 admission law (all enforced):

- File headers must be exactly ``--- a/<path>`` / ``+++ b/<path>`` (or ``/dev/null``
  for create/delete). No trailing timestamps or other metadata after the path.
- Every path is a **safe relative POSIX path** (``traaviis.paths``): no absolute
  path, no ``..`` segment.
- For a modify, the ``a/`` and ``b/`` paths must be the same file — **rename-style
  old≠new is rejected in v1**.
- Each hunk header ``@@ -o,ol +n,nl @@`` must have ``ol`` = context+removed and
  ``nl`` = context+added lines — both counts are validated.
- A **delete** (``+++ /dev/null``) must consume every source line exactly and leave
  nothing.
- A file may appear in **at most one** section (duplicate sections rejected).

Supported operations: modify, create (``--- /dev/null``), delete (``+++ /dev/null``).
The ``\\ No newline at end of file`` marker is honored so the trailing-newline state
is exact.
"""

from typing import Dict, List, Mapping, Tuple

from .paths import PathError, safe_relposix

__all__ = ["PatchError", "apply_unified_diff"]

_DEV_NULL = "/dev/null"


class PatchError(Exception):
    """A patch was inadmissible or did not apply exactly."""


def _split(content: str) -> Tuple[List[str], bool]:
    ended_nl = content.endswith("\n")
    lines = content.split("\n")
    if ended_nl:
        lines = lines[:-1]  # drop the empty element after the final newline
    return lines, ended_nl


def _join(lines: List[str], ended_nl: bool) -> str:
    s = "\n".join(lines)
    if ended_nl and lines:
        s += "\n"
    return s


def _parse_file_header(line: str, marker: str, side: str) -> str:
    """Parse a ``--- a/path`` / ``+++ b/path`` header into a safe relpath.

    ``marker`` is ``"--- "`` or ``"+++ "``; ``side`` is ``"a/"`` or ``"b/"``. Returns
    ``/dev/null`` for the null device, else the validated relpath. Rejects trailing
    metadata (timestamps), a missing ``a/``/``b/`` prefix, and unsafe paths.
    """
    rest = line[len(marker):]
    if rest == _DEV_NULL:
        return _DEV_NULL
    # No trailing timestamp / metadata: a tab or trailing whitespace is metadata.
    if "\t" in rest or rest != rest.strip():
        raise PatchError(f"header carries metadata/timestamp: {line!r}")
    if not rest.startswith(side):
        raise PatchError(f"header missing {side!r} prefix: {line!r}")
    path = rest[len(side):]
    try:
        return safe_relposix(path)
    except PathError as exc:
        raise PatchError(f"inadmissible patch path: {exc}") from exc


def _parse_hunk_header(line: str) -> Tuple[int, int, int, int]:
    """``@@ -o,ol +n,nl @@`` → ``(old_start, old_len, new_start, new_len)``.

    A missing length defaults to 1 (unified-diff convention).
    """
    try:
        parts = line.split(" ")
        old_part = parts[1]  # "-o,ol"
        new_part = parts[2]  # "+n,nl"
        if not old_part.startswith("-") or not new_part.startswith("+"):
            raise ValueError
        old_part = old_part[1:]
        new_part = new_part[1:]

        def _pair(s: str) -> Tuple[int, int]:
            if "," in s:
                a, b = s.split(",", 1)
                return int(a), int(b)
            return int(s), 1

        old_start, old_len = _pair(old_part)
        new_start, new_len = _pair(new_part)
        return old_start, old_len, new_start, new_len
    except (IndexError, ValueError):
        raise PatchError(f"malformed hunk header: {line!r}")


def apply_unified_diff(files: Mapping[str, str], diff: str) -> Dict[str, str]:
    """Apply ``diff`` to ``files`` and return a new ``{relpath: text}`` mapping.

    Raises ``PatchError`` on any inadmissible or inexact application.
    """
    result: Dict[str, str] = dict(files)
    seen_targets: set = set()
    lines = diff.split("\n")
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if not line.startswith("--- "):
            i += 1
            continue
        if i + 1 >= n or not lines[i + 1].startswith("+++ "):
            raise PatchError("'---' header not followed by '+++'")
        old_path = _parse_file_header(lines[i], "--- ", "a/")
        new_path = _parse_file_header(lines[i + 1], "+++ ", "b/")
        i += 2

        creating = old_path == _DEV_NULL
        deleting = new_path == _DEV_NULL
        if creating and deleting:
            raise PatchError("both sides /dev/null")
        if not creating and not deleting and old_path != new_path:
            raise PatchError(
                f"rename not supported in v1: {old_path!r} -> {new_path!r}"
            )
        target = old_path if deleting else new_path

        if target in seen_targets:
            raise PatchError(f"duplicate file section for {target!r}")
        seen_targets.add(target)

        if creating:
            if new_path in result:
                raise PatchError(f"create target already exists: {new_path!r}")
            cur_lines: List[str] = []
            cur_nl = True
        else:
            if old_path not in result:
                raise PatchError(f"patch target not found: {old_path!r}")
            cur_lines, cur_nl = _split(result[old_path])

        out: List[str] = []
        cursor = 0  # 0-based index into cur_lines already consumed
        new_ended_nl = cur_nl
        saw_hunk = False

        while i < n and lines[i].startswith("@@"):
            saw_hunk = True
            old_start, old_len, _new_start, new_len = _parse_hunk_header(lines[i])
            i += 1
            hunk_old_idx = old_start - 1 if not creating else 0
            if hunk_old_idx < cursor:
                raise PatchError("overlapping or out-of-order hunk")
            out.extend(cur_lines[cursor:hunk_old_idx])
            cursor = hunk_old_idx

            n_ctx = n_rem = n_add = 0
            prev_tag = " "
            while i < n and lines[i] and lines[i][0] in " -+\\":
                body = lines[i]
                if body.startswith("\\"):  # "\ No newline at end of file"
                    if prev_tag in " +":
                        new_ended_nl = False
                    i += 1
                    continue
                tag, text = body[0], body[1:]
                prev_tag = tag
                if tag == " ":
                    if cursor >= len(cur_lines) or cur_lines[cursor] != text:
                        raise PatchError(
                            f"context mismatch in {target!r} at line {cursor + 1}"
                        )
                    out.append(text)
                    cursor += 1
                    n_ctx += 1
                elif tag == "-":
                    if cursor >= len(cur_lines) or cur_lines[cursor] != text:
                        raise PatchError(
                            f"removed line mismatch in {target!r} at line {cursor + 1}"
                        )
                    cursor += 1
                    n_rem += 1
                elif tag == "+":
                    out.append(text)
                    n_add += 1
                i += 1

            # Both declared hunk lengths must match what the body actually carried.
            if n_ctx + n_rem != old_len:
                raise PatchError(
                    f"old hunk length mismatch in {target!r}: "
                    f"declared {old_len}, body {n_ctx + n_rem}"
                )
            if n_ctx + n_add != new_len:
                raise PatchError(
                    f"new hunk length mismatch in {target!r}: "
                    f"declared {new_len}, body {n_ctx + n_add}"
                )

        if not saw_hunk:
            raise PatchError(f"file header for {target!r} carried no hunk")

        out.extend(cur_lines[cursor:])  # tail context after the last hunk

        if deleting:
            if cursor != len(cur_lines) or out:
                raise PatchError(f"delete did not consume all of {old_path!r}")
            result.pop(old_path, None)
        else:
            result[target] = _join(out, new_ended_nl)

    return result
