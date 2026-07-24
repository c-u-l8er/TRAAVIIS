"""Strict unified-diff applier over in-memory file content.

Pure, no I/O, no subprocess. Applies a canonical v1 unified diff
(``RFC_EVIDENCE_RESIDENCY.md`` §5a: relative paths, ``a/``/``b/`` prefixes, LF
line endings, no binary patches) to a mapping of ``{relpath: text}`` and returns
a **new** mapping. Evidence-grade means **exact context, no fuzz**: any context
mismatch, unknown target, or malformed hunk raises ``PatchError`` — the ``patch``
verifier turns that into a clean ``fail``, never a silent partial apply.

Supported v1 operations: modify, create (``--- /dev/null``), delete
(``+++ /dev/null``). The ``\\ No newline at end of file`` marker is honored so a
file's trailing-newline state is exact.
"""

from typing import Dict, List, Mapping, Tuple

__all__ = ["PatchError", "apply_unified_diff"]

_DEV_NULL = "/dev/null"


class PatchError(Exception):
    """A patch did not apply exactly (context mismatch / malformed / bad path)."""


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


def _strip_prefix(header: str) -> str:
    # "--- a/path" / "+++ b/path" -> "path"; "/dev/null" stays.
    path = header[4:].split("\t", 1)[0].strip()
    if path == _DEV_NULL:
        return _DEV_NULL
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _parse_hunk_header(line: str) -> Tuple[int, int]:
    # "@@ -old_start,old_len +new_start,new_len @@" -> (old_start, old_len)
    try:
        old_part = line.split(" ")[1]  # "-old_start,old_len"
        old_part = old_part[1:]  # drop '-'
        if "," in old_part:
            start_s, len_s = old_part.split(",", 1)
            return int(start_s), int(len_s)
        return int(old_part), 1
    except (IndexError, ValueError):
        raise PatchError(f"malformed hunk header: {line!r}")


def apply_unified_diff(files: Mapping[str, str], diff: str) -> Dict[str, str]:
    """Apply ``diff`` to ``files`` and return a new ``{relpath: text}`` mapping.

    Raises ``PatchError`` on any inexact application.
    """
    result: Dict[str, str] = dict(files)
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
        old_path = _strip_prefix(lines[i])
        new_path = _strip_prefix(lines[i + 1])
        i += 2

        creating = old_path == _DEV_NULL
        deleting = new_path == _DEV_NULL
        target = new_path if not deleting else old_path

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
            old_start, _old_len = _parse_hunk_header(lines[i])
            i += 1
            # Copy untouched context between the last hunk and this one.
            hunk_old_idx = old_start - 1 if not creating else 0
            if hunk_old_idx < cursor:
                raise PatchError("overlapping or out-of-order hunk")
            out.extend(cur_lines[cursor:hunk_old_idx])
            cursor = hunk_old_idx

            prev_tag = " "
            while i < n and lines[i] and lines[i][0] in " -+\\":
                body = lines[i]
                if body.startswith("\\"):  # "\ No newline at end of file"
                    # Applies to the side of the preceding line: a produced-side
                    # marker (after ' ' or '+') means the new file has no final LF.
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
                elif tag == "-":
                    if cursor >= len(cur_lines) or cur_lines[cursor] != text:
                        raise PatchError(
                            f"removed line mismatch in {target!r} at line {cursor + 1}"
                        )
                    cursor += 1
                elif tag == "+":
                    out.append(text)
                i += 1

        if not saw_hunk:
            raise PatchError(f"file header for {target!r} carried no hunk")

        out.extend(cur_lines[cursor:])  # tail context after the last hunk

        if deleting:
            if out and any(s for s in out):
                raise PatchError(f"delete left residual content in {old_path!r}")
            result.pop(old_path, None)
        else:
            result[target] = _join(out, new_ended_nl)

    return result
