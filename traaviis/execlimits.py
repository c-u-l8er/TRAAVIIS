"""``traaviis.execution-limits.v1`` — one declared resource profile, enforced.

Why this module exists
======================
``evalone.resolve_signal`` contains a verifier that **stops**: a raise, or a
return the contract refuses, becomes ``error`` and the episode survives. Route 6
(``forge_adapter._lower_in_worker``) added containment against a verifier that
**never stops**. Neither closes the third shape, which is the one this module is
for: an evaluator that is *still running* and will never finish, or that dies of
the host rather than of a rule.

The clean exploit, and the reason the module is written at all::

    mkfifo output.pipe        # then exit 0

``runner._scan`` walked every non-symlink filename and did ``open(p, "rb").read()``.
A FIFO with no writer blocks in ``open`` forever. There is no verifier exception,
no subprocess still under a timeout, no receipt, no trace, and no persisted
failing episode — the agent has already exited, so nothing is left to kill. That
is a **seventh erasure route**, and it is broader than one call: candidate-
controlled filesystem and process output was collected with no enforceable bound
on file *type*, file *size*, file *count*, or *output volume*.

Three more forms of the same seam, all closed here:

* **Unbounded files.** ``_scan`` read every file whole. One 20 GiB file, one
  sparse file, or a million small ones exhausts the host, and a host that dies
  persists nothing.
* **Bounds applied after the read.** ``result.json`` and ``candidate.patch``
  went through ``fh.read()`` *before* the 8 MiB JSON boundary or the UTF-8
  decoder could refuse them. An 8 MiB bound does not help when a 20 GiB document
  must first be allocated to reach it.
* **Output capped after capture.** ``subprocess.run(stdout=PIPE)`` buffers
  everything the child emits and *then* ``max_output_bytes`` slices it. A
  candidate emitting output indefinitely exhausts memory before truncation
  happens. And ``timeout=`` kills the direct child only: a grandchild that
  inherited the pipe keeps it open, so the collection that was supposed to end
  goes on waiting.

The profile
===========
Frozen as `LIMITS`, and **declared rather than discovered** — the same
requirement `boundedjson` states for its own bounds. A bound is worth having
only if it is the same number on every machine; a limit derived from free memory
or core count would score the same submission differently on two hosts, which is
the host-dependent reward this repository has refused three times.

`EXECUTION_LIMITS_VERSION` names the whole set, so a refusal can say which
profile refused it without re-listing the numbers.

What is deliberately *not* done: the version is **not** added unconditionally to
``execution_facts`` or to the canonical trace event. Every run that stays inside
these bounds mints exactly the evidence it minted before — byte for byte, so no
sealed ``episode-…`` moves. A run that *hits* a bound carries
``execution_limits_version`` alongside the violation it hit, which is where a
replayer needs it and the only place the answer depends on it. That is not a
convenience: a run that hits one of these bounds previously produced **no**
episode at all (it hung, or the host killed it), so there is no id to move and
nothing to be compatible with.

What a violation is
===================
A resource-policy violation is a **deterministic persisted outcome**, never a
host OOM and never a hang. `ResourceLimitError` carries a stable `reason` from
`REASONS` and enough `detail` to say how far over the bound the subject was.
Callers turn it into evidence — `runner.run_agent` records it in
``resource_violations`` and still returns a `RunResult`, so the receipt exists
and the score is real.

`ResourceLimitError` subclasses `ValueError` for the reason
`boundedjson.BoundedJsonError` and `jcs.JcsError` both give: every reader in
this tree already catches `ValueError` at minimum, so a site that routes through
here cannot produce an untyped crash even before its own handler is swept.

Layering
========
This module imports **nothing from `traaviis`**. It sits below `runner`,
`substrate_verifiers` and `forge_adapter`, all three of which import it, which is
what makes "one profile, four call sites" true rather than aspirational.
"""

import errno
import hashlib
import os
import signal
import stat
import subprocess
import threading
import time

from . import containment

__all__ = [
    "EXECUTION_LIMITS_VERSION", "LIMITS", "REASONS", "ResourceLimitError",
    "MAX_WORKSPACE_FILES", "MAX_WORKSPACE_TOTAL_BYTES",
    "MAX_WORKSPACE_FILE_BYTES", "MAX_RESULT_BYTES", "MAX_PATCH_BYTES",
    "MAX_STDOUT_BYTES", "MAX_STDERR_BYTES",
    "MAX_IPC_SOURCE_BYTES", "MAX_IPC_REQUEST_BYTES", "MAX_IPC_RESPONSE_BYTES",
    "MAX_IPC_DIAGNOSTIC_BYTES",
    "MAX_WORKSPACE_ENTRIES", "MAX_WORKSPACE_DIRECTORIES", "MAX_WORKSPACE_DEPTH",
    "MAX_WORKSPACE_PATH_BYTES", "MAX_SINGLE_PATH_BYTES",
    "REAP_GRACE_SECONDS", "SCAN_CHUNK_BYTES",
    "kill_process_group", "read_file_bounded", "scan_tree", "BoundedRun",
    "run_bounded", "file_kind",
]


#: The name of this whole set of numbers. A refusal names the profile; it never
#: re-states the numbers, because two copies of a bound is how they drift.
EXECUTION_LIMITS_VERSION = "traaviis.execution-limits.v1"

#: Only ``S_ISREG`` entries are ever opened. A FIFO, socket, device or symlink
#: found where evidence is collected is refused, not skipped — see `scan_tree`.
REGULAR_FILES_ONLY = True

#: How many files one collected workspace may hold.
MAX_WORKSPACE_FILES = 10000
#: Total bytes across every file in one collected workspace. 256 MiB.
MAX_WORKSPACE_TOTAL_BYTES = 268435456
#: Bytes in any single collected workspace file. 64 MiB.
MAX_WORKSPACE_FILE_BYTES = 67108864
#: Bytes in the declared ``result_path``. Matches `boundedjson.MAX_INPUT_BYTES`
#: on purpose: the result is parsed there, and a file bound above the parser's
#: bound would only mean reading bytes that are certain to be refused.
MAX_RESULT_BYTES = 8388608
#: Bytes in the declared ``patch_path``. 8 MiB.
MAX_PATCH_BYTES = 8388608
#: Bytes of stdout retained from one child process. 4 MiB.
MAX_STDOUT_BYTES = 4194304
#: Bytes of stderr retained from one child process. 4 MiB.
MAX_STDERR_BYTES = 4194304

# --- enumeration bounds (9E) --------------------------------------------------
# 9D bounded *regular files* and nothing else, and that left enumeration itself
# unbounded: a million empty directories, or a million symlinks, crosses no
# file-count bound and still costs unbounded time and memory to walk. The cost of
# collection is per *entry*, so the bound has to be per entry.
#: Every filesystem entry under the workspace, of every type.
MAX_WORKSPACE_ENTRIES = 20000
#: Directories specifically.
MAX_WORKSPACE_DIRECTORIES = 2000
#: Nesting depth, so a 10 000-deep chain is refused at 33 rather than walked.
MAX_WORKSPACE_DEPTH = 32
#: Total UTF-8 bytes of every relative path name in the workspace. 1 MiB.
MAX_WORKSPACE_PATH_BYTES = 1048576
#: One relative path name. Comfortably above POSIX ``PATH_MAX`` for a workspace
#: -relative name, and far below what an adversary needs to make the sum matter.
MAX_SINGLE_PATH_BYTES = 4096

# --- the worker IPC sub-profile ---------------------------------------------
# Narrower than the general document profile, deliberately. `boundedjson`'s
# 8 MiB is the bound on a *document a candidate submits*; this is the bound on a
# *message between a process and its own worker*, whose payload happens to be
# candidate-influenced (the WRL source) on the way in and engine-influenced (a
# compile diagnostic) on the way back. Neither direction has any business being
# megabytes, and a limit set at the document profile's width would leave the IPC
# unbounded in every practical sense.
#: Bytes of WRL source one lowering request may carry. 1 MiB.
MAX_IPC_SOURCE_BYTES = 1048576
#: Bytes of the encoded request envelope. Source plus JSON escaping headroom.
MAX_IPC_REQUEST_BYTES = 2097152
#: Bytes of the encoded response. A response is an id and maybe a diagnostic.
MAX_IPC_RESPONSE_BYTES = 262144
#: Bytes of engine diagnostic carried in one response, and of worker stderr
#: retained by the parent. Equal to `boundedjson.MAX_DIAGNOSTIC_BYTES`.
MAX_IPC_DIAGNOSTIC_BYTES = 65536

#: After a group kill, how long to wait for the pipes to close. Bounded for the
#: reason `forge_adapter` already gives: an unbounded wait after a kill
#: reintroduces the hang the kill exists to end, in the one case (a grandchild
#: that survived) where it matters most.
REAP_GRACE_SECONDS = 30.0

#: Read granularity for streaming hashes. Chosen so the peak resident cost of
#: hashing a file is this constant rather than the file's size.
SCAN_CHUNK_BYTES = 1024 * 1024

#: How often the supervisor loop looks at a running child. Small enough that a
#: short command is not measurably slowed, large enough that a long one costs
#: nothing.
_POLL_SECONDS = 0.005


#: The machine-readable profile, as one object. Exposed so a law can assert the
#: numbers are declared in exactly one place, and so a future receipt can seal
#: it without any caller re-typing a constant.
LIMITS = {
    "execution_limits_version": EXECUTION_LIMITS_VERSION,
    "regular_files_only": REGULAR_FILES_ONLY,
    "max_workspace_files": MAX_WORKSPACE_FILES,
    "max_workspace_total_bytes": MAX_WORKSPACE_TOTAL_BYTES,
    "max_workspace_file_bytes": MAX_WORKSPACE_FILE_BYTES,
    "max_result_bytes": MAX_RESULT_BYTES,
    "max_patch_bytes": MAX_PATCH_BYTES,
    "max_stdout_bytes": MAX_STDOUT_BYTES,
    "max_stderr_bytes": MAX_STDERR_BYTES,
    "max_workspace_entries": MAX_WORKSPACE_ENTRIES,
    "max_workspace_directories": MAX_WORKSPACE_DIRECTORIES,
    "max_workspace_depth": MAX_WORKSPACE_DEPTH,
    "max_workspace_path_bytes": MAX_WORKSPACE_PATH_BYTES,
    "max_single_path_bytes": MAX_SINGLE_PATH_BYTES,
    "max_ipc_source_bytes": MAX_IPC_SOURCE_BYTES,
    "max_ipc_request_bytes": MAX_IPC_REQUEST_BYTES,
    "max_ipc_response_bytes": MAX_IPC_RESPONSE_BYTES,
    "max_ipc_diagnostic_bytes": MAX_IPC_DIAGNOSTIC_BYTES,
}


#: Every way this profile can refuse, as stable machine-readable tokens. A
#: caller maps these onto its own evidence vocabulary without ever naming an
#: exception class — the same contract `boundedjson.REASONS` offers, for the
#: same reason.
REASONS = (
    "irregular_file",            # a FIFO / socket / device where evidence is read
    "symlink",                   # a candidate-created symlink (9E: refused, not skipped)
    "unreadable_entry",          # an entry that could not be opened at all
    "too_many_entries",          # every-type entry count over MAX_WORKSPACE_ENTRIES
    "too_many_directories",      # directory count over MAX_WORKSPACE_DIRECTORIES
    "workspace_too_deep",        # nesting over MAX_WORKSPACE_DEPTH
    "path_too_long",             # one path over MAX_SINGLE_PATH_BYTES
    "workspace_paths_too_large", # all paths over MAX_WORKSPACE_PATH_BYTES
    "processes_escaped",         # candidate processes survived containment
    "containment_unavailable",   # no real process-tree containment on this host
    "stdout_too_large",          # agent stdout over the declared cap
    "stderr_too_large",          # agent stderr over the declared cap
    "too_many_files",            # workspace file count over MAX_WORKSPACE_FILES
    "workspace_file_too_large",  # one file over MAX_WORKSPACE_FILE_BYTES
    "workspace_too_large",       # total over MAX_WORKSPACE_TOTAL_BYTES
    "result_too_large",          # result_path over MAX_RESULT_BYTES
    "patch_too_large",           # patch_path over MAX_PATCH_BYTES
    "ipc_source_too_large",      # WRL source over MAX_IPC_SOURCE_BYTES
    "ipc_request_too_large",     # worker request over MAX_IPC_REQUEST_BYTES
    "ipc_response_too_large",    # worker response over MAX_IPC_RESPONSE_BYTES
)


class ResourceLimitError(ValueError):
    """A subject was refused by `EXECUTION_LIMITS_VERSION`.

    `reason` is one of `REASONS`; `detail` carries the numbers, so a refusal can
    say how far over a bound the subject was without the caller re-measuring.
    Subclasses `ValueError` — see the module docstring.
    """

    def __init__(self, reason, message, detail=None):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.detail = dict(detail or {})
        self.detail.setdefault("execution_limits_version",
                               EXECUTION_LIMITS_VERSION)

    def __str__(self):
        return self.message


# ------------------------------------------------------------- file typing
def file_kind(st_mode):
    """A stable one-word name for a stat mode's file type.

    Used only in refusal messages and evidence, so it must not vary with the
    host. Every branch is a POSIX file type, and the fallback is deliberately
    not ``"unknown"`` per-platform text.
    """
    if stat.S_ISREG(st_mode):
        return "regular"
    if stat.S_ISDIR(st_mode):
        return "directory"
    if stat.S_ISLNK(st_mode):
        return "symlink"
    if stat.S_ISFIFO(st_mode):
        return "fifo"
    if stat.S_ISSOCK(st_mode):
        return "socket"
    if stat.S_ISCHR(st_mode):
        return "character-device"
    if stat.S_ISBLK(st_mode):
        return "block-device"
    return "other"


# ------------------------------------------------------------ bounded reads
def _open_regular_at(dir_fd, name, subject):
    """A descriptor for one **regular** file, opened without following anything.

    The 9E closure for the time-of-check/time-of-use race. 9D did::

        lstat(path) -> verify regular -> open(path)

    which is three operations on a *name*, and a surviving candidate process
    could replace that name between the check and the open — with a FIFO (the
    seventh route, reopened), a symlink out of the workspace, a device, or a
    different, larger file. Containment is meant to make that impossible by
    emptying the box first; this is the second line of defence the ruling asked
    for, and it is the one that does not depend on containment being perfect.

    Three properties, and none of them is a check on a name:

    * ``O_NOFOLLOW`` — the final component is never traversed as a symlink. The
      kernel refuses with ``ELOOP`` instead of opening the target.
    * ``O_NONBLOCK`` — opening a FIFO with no writer *returns* instead of
      blocking, so even reaching one costs nothing. (Regular files ignore it.)
    * ``fstat`` on the **descriptor**, not ``lstat`` on the path. What is
      verified is the object now held open, so there is no window afterwards in
      which it could be swapped: a rename or replace changes what the *name*
      refers to, never what an open descriptor refers to.

    ``dir_fd`` makes the lookup relative to a directory that is itself already
    open, which closes the same race one level up — the path components above
    cannot be swapped underneath collection either.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise ResourceLimitError(
                "symlink",
                "refusing to read %s: it is a symlink. The subject is "
                "materialized from a sealed snapshot that cannot express one, "
                "so any symlink in the workspace was created by the candidate."
                % subject,
                {"subject": subject, "kind": "symlink"})
        # The open itself refused, and *why* is worth recovering rather than
        # collapsing. A bound AF_UNIX socket gives `ENXIO`, a device with no
        # driver `ENODEV`, and reporting either as a generic unreadable entry
        # would lose the one fact a reader needs: the candidate created a file
        # type that evidence collection does not read. `lstat` is consulted only
        # to *name* what was already refused -- the refusal is the open's, so
        # there is no check-then-use here to race.
        kind = None
        if dir_fd is not None:
            try:
                kind = file_kind(os.lstat(name, dir_fd=dir_fd).st_mode)
            except OSError:
                kind = None
        if kind is not None and kind != "regular":
            raise ResourceLimitError(
                "irregular_file",
                "refusing to read %s: it is a %s, not a regular file."
                % (subject, kind),
                {"subject": subject, "kind": kind})
        raise ResourceLimitError(
            "unreadable_entry",
            "refusing to read %s: %s" % (subject, exc),
            {"subject": subject, "errno": exc.errno})
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ResourceLimitError(
                "irregular_file",
                "refusing to read %s: it is a %s, not a regular file. Opening "
                "one can block forever (a FIFO with no writer) or never end (a "
                "character device), and evidence collection that can wait "
                "forever is a way to erase an episode."
                % (subject, file_kind(st.st_mode)),
                {"subject": subject, "kind": file_kind(st.st_mode)})
    except BaseException:
        os.close(fd)
        raise
    return fd, st


def _resolve_at(root_fd, relparts, subject):
    """Walk `relparts` from `root_fd`, one ``openat`` per component.

    Returns ``(dir_fd, final_name)`` with `dir_fd` **owned by the caller**.

    Component by component, each opened ``O_NOFOLLOW | O_DIRECTORY``, so no part
    of the path may be a symlink and no part may be swapped between one lookup
    and the next. Resolving the whole string in one ``open`` would re-introduce
    the race for every directory above the file.
    """
    dir_fd = os.dup(root_fd)
    try:
        for part in relparts[:-1]:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                nxt = os.open(part, flags, dir_fd=dir_fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.EMLINK):
                    raise ResourceLimitError(
                        "symlink",
                        "refusing to traverse %s: %r is a symlink"
                        % (subject, part),
                        {"subject": subject, "kind": "symlink"})
                raise ResourceLimitError(
                    "unreadable_entry",
                    "refusing to traverse %s: %s" % (subject, exc),
                    {"subject": subject, "errno": exc.errno})
            os.close(dir_fd)
            dir_fd = nxt
    except BaseException:
        os.close(dir_fd)
        raise
    return dir_fd, relparts[-1]


def _read_fd_bounded(fd, max_bytes, reason, subject):
    """At most ``max_bytes + 1`` bytes off an already-verified descriptor.

    One byte past the bound is exactly enough to *prove* the file is over it, and
    it is the whole difference between a refusal and an allocation: an oversized
    ``result.json`` is refused having read 8 MiB, not having read 20 GiB in order
    to discover it was too big.
    """
    chunks = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = os.read(fd, min(SCAN_CHUNK_BYTES, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise ResourceLimitError(
            reason,
            "refusing to read %s: it is over the bound of %d bytes"
            % (subject, max_bytes),
            {"subject": subject, "max_bytes": max_bytes})
    return data


def read_file_bounded(root, relpath, max_bytes, reason, subject=None):
    """The bytes of one regular file under `root`, or a refusal.

    `root` is a directory path and `relpath` is a ``/``-joined relative name
    inside it. The split is not cosmetic: the file is reached by ``openat`` from
    a descriptor for `root`, one component at a time, so nothing on the path can
    be swapped for a symlink or a FIFO while the read is being set up. 9D took a
    single joined path and did ``lstat`` then ``open``, which is a check on a
    name followed by a use of that name — the classic race, and the ruling was
    right that it lets the FIFO route back in.
    """
    subject = relpath if subject is None else subject
    parts = [p for p in relpath.split("/") if p not in ("", ".")]
    if not parts:
        raise ResourceLimitError(
            "unreadable_entry", "refusing to read an empty path",
            {"subject": subject})
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        dir_fd, name = _resolve_at(root_fd, parts, subject)
        try:
            fd, st = _open_regular_at(dir_fd, name, subject)
        finally:
            os.close(dir_fd)
        try:
            if st.st_size > max_bytes:
                raise ResourceLimitError(
                    reason,
                    "refusing to read %s: it is %d bytes and the bound is %d"
                    % (subject, st.st_size, max_bytes),
                    {"subject": subject, "size": st.st_size,
                     "max_bytes": max_bytes})
            return _read_fd_bounded(fd, max_bytes, reason, subject)
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)


def _hash_fd(fd, max_bytes, subject):
    """``(sha256 hex digest, size)`` off an already-verified descriptor.

    Peak memory is `SCAN_CHUNK_BYTES`, not the file's size. The bound is
    re-checked *while* streaming rather than only against the stat, because a
    file the candidate is still writing can pass the stat and then not stop.
    """
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = os.read(fd, SCAN_CHUNK_BYTES)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise ResourceLimitError(
                "workspace_file_too_large",
                "refusing to hash %s: it is over the bound of %d bytes"
                % (subject, max_bytes),
                {"subject": subject, "max_bytes": max_bytes})
        digest.update(chunk)
    return digest.hexdigest(), size


def scan_tree(root, name_of=None,
              max_files=MAX_WORKSPACE_FILES,
              max_file_bytes=MAX_WORKSPACE_FILE_BYTES,
              max_total_bytes=MAX_WORKSPACE_TOTAL_BYTES,
              max_entries=MAX_WORKSPACE_ENTRIES,
              max_directories=MAX_WORKSPACE_DIRECTORIES,
              max_depth=MAX_WORKSPACE_DEPTH,
              max_path_bytes=MAX_WORKSPACE_PATH_BYTES,
              max_single_path_bytes=MAX_SINGLE_PATH_BYTES):
    """``{name: {hash, mode, size, os_path}}`` for every regular file under `root`.

    `name_of` maps one ``/``-joined relative path to the caller's canonical
    evidence name (``runner._evidence_name``); the default is identity. It is
    applied here rather than by the caller so that the *count* this function
    bounds and the *keys* it returns are the same set — a caller that renamed
    afterwards could collapse two entries into one and quietly get under the
    bound.

    **Every entry is counted, not only the ones that get read.** 9D bounded
    regular files and nothing else, and the ruling showed the hole: a candidate
    can create a million empty *directories*, or a million symlinks, cross no
    file-count bound at all, and still make evidence collection take unbounded
    time and memory. Enumeration itself is the cost, so enumeration itself is
    what has to be bounded. Five separate dimensions, each refused at the first
    proof rather than after the walk:

    ``max_entries``          every name seen, of every type
    ``max_directories``      directories specifically
    ``max_depth``            nesting, so a 10 000-deep chain is refused at 33
    ``max_single_path_bytes``  one relative name
    ``max_path_bytes``       the sum of all of them

    **Symlinks are refused, not skipped** (9E ruling). 9D skipped them and said
    so; the ruling overturned that, and the argument is exact: snapshot
    materialization cannot express a symlink, the workspace begins from that
    sealed materialization, therefore **any symlink present after the run was
    created by the candidate**. Omitting it means the trace does not describe the
    complete candidate-created filesystem state — and an incomplete record is
    false in the direction that pays the candidate. The denial-of-service worry
    that motivated skipping does not apply to a v1 subject that could not have
    contained one to begin with.

    Traversal is by ``os.fwalk``, which hands back a **directory descriptor** for
    each level, and every file is opened relative to that descriptor with
    ``O_NOFOLLOW | O_NONBLOCK`` and verified by ``fstat``. See
    `_open_regular_at` for why a name-based check cannot be enough.
    """
    if name_of is None:
        def name_of(rel):
            return rel

    out = {}
    counts = {"files": 0, "entries": 0, "directories": 0, "path_bytes": 0}
    total = 0

    def _count_path(subject):
        encoded = len(subject.encode("utf-8", "surrogatepass"))
        if encoded > max_single_path_bytes:
            raise ResourceLimitError(
                "path_too_long",
                "refusing to collect the workspace: a path is %d bytes and the "
                "per-path bound is %d" % (encoded, max_single_path_bytes),
                {"size": encoded, "max_bytes": max_single_path_bytes})
        counts["path_bytes"] += encoded
        if counts["path_bytes"] > max_path_bytes:
            raise ResourceLimitError(
                "workspace_paths_too_large",
                "refusing to collect the workspace: its path names exceed %d "
                "bytes in total" % max_path_bytes,
                {"max_bytes": max_path_bytes})

    def _count_entry():
        counts["entries"] += 1
        if counts["entries"] > max_entries:
            raise ResourceLimitError(
                "too_many_entries",
                "refusing to collect the workspace: it holds more than %d "
                "filesystem entries" % max_entries,
                {"max_entries": max_entries})

    for dirpath, dirnames, filenames, dir_fd in os.fwalk(root,
                                                         follow_symlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        depth = 0 if rel_dir == "." else rel_dir.count("/") + 1
        if depth > max_depth:
            raise ResourceLimitError(
                "workspace_too_deep",
                "refusing to collect the workspace: it nests deeper than %d "
                "directories" % max_depth,
                {"max_depth": max_depth, "depth": depth})

        for name in dirnames:
            _count_entry()
            counts["directories"] += 1
            if counts["directories"] > max_directories:
                raise ResourceLimitError(
                    "too_many_directories",
                    "refusing to collect the workspace: it holds more than %d "
                    "directories" % max_directories,
                    {"max_directories": max_directories})
            child = name if rel_dir == "." else rel_dir + "/" + name
            _count_path(name_of(child))
            # A symlinked directory is refused here rather than pruned. `fwalk`
            # with `follow_symlinks=False` already declines to descend into it,
            # so skipping would be safe -- and silent, which is the half the
            # ruling overturned.
            try:
                st = os.lstat(name, dir_fd=dir_fd)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                raise ResourceLimitError(
                    "symlink",
                    "refusing to collect the workspace: %s is a symlink, and "
                    "the sealed subject cannot express one -- so the candidate "
                    "created it." % name_of(child),
                    {"subject": name_of(child), "kind": "symlink"})

        for name in sorted(filenames):
            _count_entry()
            rel = name if rel_dir == "." else rel_dir + "/" + name
            subject = name_of(rel)
            _count_path(subject)

            counts["files"] += 1
            if counts["files"] > max_files:
                raise ResourceLimitError(
                    "too_many_files",
                    "refusing to collect the workspace: it holds more than %d "
                    "files" % max_files,
                    {"max_files": max_files})

            fd, st = _open_regular_at(dir_fd, name, subject)
            try:
                if st.st_size > max_file_bytes:
                    raise ResourceLimitError(
                        "workspace_file_too_large",
                        "refusing to collect the workspace: %s is %d bytes and "
                        "the per-file bound is %d"
                        % (subject, st.st_size, max_file_bytes),
                        {"subject": subject, "size": st.st_size,
                         "max_bytes": max_file_bytes})
                if total + st.st_size > max_total_bytes:
                    raise ResourceLimitError(
                        "workspace_too_large",
                        "refusing to collect the workspace: it exceeds the "
                        "total bound of %d bytes" % max_total_bytes,
                        {"max_total_bytes": max_total_bytes})
                hexdigest, size = _hash_fd(fd, max_file_bytes, subject)
            finally:
                os.close(fd)

            total += size
            if total > max_total_bytes:
                raise ResourceLimitError(
                    "workspace_too_large",
                    "refusing to collect the workspace: it exceeds the total "
                    "bound of %d bytes" % max_total_bytes,
                    {"max_total_bytes": max_total_bytes})
            out[subject] = {
                "hash": "sha256:" + hexdigest,
                "mode": "%04o" % stat.S_IMODE(st.st_mode),
                "size": size,
                "os_path": os.path.join(dirpath, name),
            }
    return out


# --------------------------------------------------------- process boundary
#: The best-effort process-group kill, re-exported from `containment`.
#:
#: **An alias, not a second implementation.** It was written out here as well
#: until a non-vacuity probe deleted this copy, forced the weak containment
#: profile, and watched the grandchild die anyway -- because `Containment.kill_all`
#: held the other copy and was still doing the group kill. The law reported that
#: the boundary worked; what it had actually measured was that it had failed to
#: turn the boundary off. A duplicated safety primitive is exactly one deletion
#: target too many.
kill_process_group = containment.kill_process_group


class BoundedRun(dict):
    """What one bounded child process did (a plain dict subclass).

    Keys: ``exit_code`` (``None`` if it was killed), ``timed_out``,
    ``output_overflow``, ``stdout`` / ``stderr`` (bytes, at most the cap),
    ``stdout_truncated`` / ``stderr_truncated``, ``stdout_bytes_seen`` /
    ``stderr_bytes_seen`` (what the child actually emitted, which is how a
    reader can tell "exactly at the cap" from "far past it"),
    ``capture_abandoned`` (how many pipes a survivor still holds — nonzero means
    the capture is short for a reason, not because the child was quiet),
    ``spawn_error`` (the ``OSError`` type name when the process never started)
    and ``spawn_exception`` (that ``OSError`` itself).

    Both spawn fields exist because two callers need different things from the
    same event. ``run_command_set`` wants a *name* to put in evidence — a record
    saying ``FileNotFoundError`` is a fact about the fixture, and a host path in
    an exception message is not something that may enter a canonical record.
    ``runner.run_agent`` has to **re-raise**: its contract is that an agent whose
    ``argv[0]`` does not exist raises ``OSError``, and ``evalsplit._run_episode``
    catches exactly that so one caller's typo costs one task rather than the
    whole split. Returning the object as well as its name is what lets the
    second caller keep a promise the first one must not.
    """


def _drain(stream, cap, into, state, key, overflow):
    """Read `stream` to EOF, keeping at most `cap` bytes.

    Two properties, and the second is the one that matters. Memory is bounded by
    `cap` no matter what the child emits — bytes past the cap are counted and
    dropped, never accumulated. And the stream keeps being *read* after the cap
    is reached rather than being abandoned, so the child never blocks on a full
    pipe while the supervisor is trying to decide whether to kill it. Abandoning
    the pipe would turn an output overflow into a deadlock, which is the failure
    this whole module is about.

    **The stream is closed here, on the thread that owns it, and never by the
    supervisor.** That is not tidiness. `io.BufferedReader.close()` takes the
    buffer's lock, and this thread holds that lock while it is blocked in
    `read()` — so a supervisor closing the pipe after a kill blocks *forever* in
    exactly the case the kill exists for: a grandchild survived, still holds the
    write end, and no EOF is coming. Measured: with the group kill disabled, a
    supervisor-side `close()` hung a probe past 60 seconds having already
    decided the verdict. Closing from inside the reader means a live pipe is
    released the moment EOF arrives, and an abandoned one is simply left to the
    daemon thread rather than taking the supervisor down with it.
    """
    seen = 0
    buf = bytearray()
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            seen += len(chunk)
            if len(buf) < cap:
                buf += chunk[:cap - len(buf)]
            if seen > cap and not overflow.is_set():
                overflow.set()
    except (ValueError, OSError):
        # The pipe was closed under us. Whatever was read before that is still
        # what the child emitted.
        pass
    into[key] = bytes(buf)
    state[key] = seen
    try:
        stream.close()
    except (ValueError, OSError):
        pass


def _feed(stream, data):
    """Write one request to a child's stdin and close it.

    A worker that exits before reading gives ``EPIPE``, which is not an error
    here: the child's answer (or its absence) is the outcome, and a writer that
    raised would replace a structured "no response" with an untyped crash in a
    thread nobody is watching. Nothing is done with the exception because there
    is nothing to do with it — the supervisor is already watching the process
    and will report what it did.
    """
    try:
        if data:
            stream.write(data)
        stream.flush()
    except (BrokenPipeError, ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except (BrokenPipeError, ValueError, OSError):
            pass


def run_bounded(argv, *, cwd=None, env=None, timeout=None, input=None,
                max_stdout_bytes=MAX_STDOUT_BYTES,
                max_stderr_bytes=MAX_STDERR_BYTES,
                kill_on_overflow=True,
                reap_grace=REAP_GRACE_SECONDS):
    """Run `argv` under a process-tree bound and a *streaming* output cap.

    The one execution primitive in this package. ``subprocess.run`` is not used
    anywhere that touches candidate-influenced input, because it gets two things
    wrong that cannot be fixed by wrapping it:

    * it **buffers all output and caps afterwards**, so a child emitting
      indefinitely exhausts memory before any bound is applied;
    * its ``timeout=`` **kills the direct child only**, so a grandchild holding
      the pipe survives and the wait continues.

    Here the child is placed in a **per-run cgroup** before it can fork (see
    `containment`), reader threads keep memory at the cap while still draining
    the pipes, the supervisor kills on either a deadline or an overflow, and —
    the 9E closure — **everything the run started is killed after the direct
    child exits, on every path, including a clean exit 0**. ``input`` is fed by
    its own thread, so a child that never reads its stdin cannot deadlock a
    parent that is trying to write it.

    9D killed the process *group*, and only on a timeout or an overflow. Both
    halves were escapable, and the ruling demonstrated both: a candidate that
    spawns a worker and exits 0 was never cleaned up at all, and a descendant
    that calls ``setsid()`` leaves the group before any signal addressed to it
    arrives. A process cannot remove *itself* from its cgroup, ``cgroup.kill``
    is transitive,
    and ``cgroup.events``' ``populated`` flag is the kernel saying whether
    anything is still alive — so "nothing survived *inside the boundary*" becomes
    something measured rather than assumed.

    **That is a kill boundary and not containment**, and 9E called it
    containment. A candidate that can write the parent's ``cgroup.procs`` leaves
    the subtree entirely, and the kill never reaches it; see
    ``containment.CGROUP_KILL_OBSERVED_PROFILE`` and the capability vector that
    replaced the single ``enforced`` boolean.

    Returns a `BoundedRun`. It **does not raise** for anything the child did:
    a spawn failure, a timeout and an overflow are all outcomes, because a caller
    collecting evidence needs a fact to record rather than an exception to
    classify. `ResourceLimitError` is raised only for a request that is over
    bound *before* anything is spawned, which is the caller's own input.

    ``kill_on_overflow`` exists because "kill on overflow" and "truncate and let
    it finish" are different rulings for different subjects, and the difference
    should be visible at the call site rather than assumed. The default is to
    kill: a process that has already emitted more than the profile will retain is
    not producing evidence any more.
    """
    if input is not None and not isinstance(input, (bytes, bytearray)):
        raise TypeError("run_bounded input must be bytes, got %s"
                        % type(input).__name__)

    box = containment.open_containment()
    popen_kwargs = {}
    if os.name == "posix":
        # Still taken, and no longer the whole story. A session is what makes
        # `killpg` reach anything at all on a host with no cgroup v2, and on a
        # host with one it costs nothing and keeps the weaker profile honest.
        popen_kwargs["start_new_session"] = True

    status_read = status_write = None
    if box.wraps():
        # The bootstrap reports an exec failure down this pipe, and its write end
        # is close-on-exec — so EOF *is* the report that the exec succeeded. See
        # `containment.GUARD_SOURCE`.
        status_read, status_write = os.pipe()
        os.set_inheritable(status_write, True)
        popen_kwargs["pass_fds"] = (status_write,)
        spawn_argv = box.wrap(argv, status_write)
    else:
        spawn_argv = list(argv)

    try:
        proc = subprocess.Popen(
            spawn_argv, cwd=cwd, env=env, shell=False,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs)
    except OSError as exc:
        for fd in (status_read, status_write):
            if fd is not None:
                os.close(fd)
        box.kill_all()
        box.close()
        return BoundedRun(
            exit_code=None, timed_out=False, output_overflow=False,
            stdout=b"", stderr=b"", stdout_truncated=False,
            stderr_truncated=False, stdout_bytes_seen=0, stderr_bytes_seen=0,
            capture_abandoned=0, surviving_processes=0,
            process_containment=box.facts(), spawn_error=type(exc).__name__,
            spawn_exception=exc)

    captured = {}
    seen = {}
    overflow = threading.Event()
    threads = [
        threading.Thread(target=_drain,
                         args=(proc.stdout, max_stdout_bytes, captured, seen,
                               "stdout", overflow), daemon=True),
        threading.Thread(target=_drain,
                         args=(proc.stderr, max_stderr_bytes, captured, seen,
                               "stderr", overflow), daemon=True),
    ]
    if input is not None:
        threads.append(threading.Thread(target=_feed,
                                        args=(proc.stdin, bytes(input)),
                                        daemon=True))
    for thread in threads:
        thread.start()

    # Read the bootstrap's verdict *after* the readers are running. If the exec
    # succeeded this returns immediately (EOF at exec, because the write end is
    # close-on-exec); if it failed, the errno arrives and the guard is already
    # gone. Starting the readers first removes the one window where a fast,
    # loud command could fill a pipe nobody was draining yet.
    spawn_status = None
    if status_write is not None:
        os.close(status_write)
        try:
            raw = os.read(status_read, 32)
        except OSError:
            raw = b""
        os.close(status_read)
        spawn_status = containment.read_status(raw)

    if spawn_status is not None:
        # The command never ran: either it could not be placed in its cgroup, or
        # `execv` refused it. The second is the case that has to keep working —
        # `runner.run_agent`'s contract is that a nonexistent ``argv[0]`` raises
        # `FileNotFoundError`, and inserting a bootstrap between `Popen` and the
        # command would otherwise have turned that into a successful launch of a
        # program that exits 127. The status pipe is what preserves it.
        for thread in threads:
            thread.join(timeout=reap_grace)
        try:
            proc.wait(timeout=reap_grace)
        except subprocess.TimeoutExpired:
            pass
        box.kill_all(proc)
        facts = box.facts()
        box.close()
        exc = containment.status_error(spawn_status)
        return BoundedRun(
            exit_code=None, timed_out=False, output_overflow=False,
            stdout=b"", stderr=b"", stdout_truncated=False,
            stderr_truncated=False, stdout_bytes_seen=0, stderr_bytes_seen=0,
            capture_abandoned=0, surviving_processes=facts["remaining_processes_in_boundary"],
            process_containment=facts, spawn_error=type(exc).__name__,
            spawn_exception=exc)

    deadline = None if timeout is None else time.monotonic() + float(timeout)
    timed_out = False
    while True:
        if proc.poll() is not None:
            break
        if kill_on_overflow and overflow.is_set():
            kill_process_group(proc)
            break
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            kill_process_group(proc)
            break
        time.sleep(_POLL_SECONDS)

    try:
        proc.wait(timeout=reap_grace)
    except subprocess.TimeoutExpired:
        # A descendant survived and still holds the pipes. Stop waiting on the
        # process, not on the verdict: the verdict is already decided.
        try:
            proc.kill()
        except OSError:
            pass

    # **After the direct child is reaped, on every path.** Not only on timeout
    # and not only on overflow — a clean exit 0 is precisely the case 9D missed,
    # and it is the easier of the two escapes: spawn a worker, exit, keep
    # running. `kill_all` waits for the kernel to report `populated 0`, so what
    # comes back is a measurement of what survived rather than a hope.
    remaining = box.kill_all(proc)
    # `close` **before** `facts`, and the order is load-bearing rather than
    # tidy: teardown is what discovers how many cgroups the candidate created
    # inside its own, and a report taken first records a zero it has not yet
    # looked for. Measured: a candidate made `a/b/c` and the run reported
    # `nested_cgroups_removed: 0`. `close` only reads state captured at open
    # time, so calling it first costs the report nothing.
    box.close()
    containment_facts = box.facts()

    abandoned = 0
    for thread in threads:
        thread.join(timeout=reap_grace)
        if thread.is_alive():
            abandoned += 1
    # The streams are **not** closed here; each reader closes its own on the way
    # out (see `_drain`). A thread still alive at this point is one whose pipe a
    # survivor still holds — it is a daemon, so it costs the process nothing at
    # exit, and `capture_abandoned` says so rather than leaving a caller to
    # infer from a short capture that the child simply said little.

    # `overflow.is_set()` rather than `overflowed`, and the difference is the
    # whole determinism of this function. `overflowed` records whether the
    # supervisor happened to *notice* the overflow before the child exited,
    # which is a scheduling fact: the same command emitting the same 5 MiB is
    # killed on one run and reaped normally on the next. The event is set by the
    # reader threads on the bytes themselves, so it is a fact about the output.
    # A run that emitted more than this profile retains reports no exit code, on
    # every host and every scheduling — the child did not finish producing
    # evidence, whoever won the race.
    output_overflow = overflow.is_set()
    exit_code = proc.returncode if not (timed_out or output_overflow) else None
    stdout_seen = seen.get("stdout", 0)
    stderr_seen = seen.get("stderr", 0)
    return BoundedRun(
        exit_code=exit_code,
        timed_out=timed_out,
        output_overflow=output_overflow,
        stdout=captured.get("stdout", b""),
        stderr=captured.get("stderr", b""),
        stdout_truncated=stdout_seen > max_stdout_bytes,
        stderr_truncated=stderr_seen > max_stderr_bytes,
        stdout_bytes_seen=stdout_seen,
        stderr_bytes_seen=stderr_seen,
        capture_abandoned=abandoned,
        surviving_processes=remaining,
        process_containment=containment_facts,
        spawn_error=None,
        spawn_exception=None)
