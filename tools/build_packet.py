"""Build the standalone review packet.

A *standalone* packet, not a delta: extracting it into an empty directory must be
enough to reproduce the battery, with no reference to any earlier archive. The
last handoff was a delta and the reviewer could not reproduce 314/314 from it, so
this builder takes the opposite default -- it ships the whole tree and names the
few things it deliberately leaves out, rather than shipping a hand-picked list
and hoping nothing was missed.

    python3 tools/build_packet.py NAME.zip

Determinism, and what that word has to mean here. Entries are sorted, timestamps
fixed, and modes taken from a repository-defined list, so two builds of an
unchanged tree are byte-identical *even across a round trip through a different
extractor*, and the packet's own SHA-256 is a usable identifier for "which base
was this built on".

That last clause is the whole reason the mode list exists. This builder used to
read the executable bit off the working tree with ``os.access(src, os.X_OK)``.
That is not a property of the repository, it is a property of whatever filesystem
the source happens to be sitting on -- and Python's own ZIP extraction does not
restore executable bits. So the loop closed wrong: build a packet, extract it
with Python, rebuild from the extraction, and two members came back 0644 that had
gone in 0755, producing a different archive hash from identical content. The
packet's identity was reading the host, not the repository. Declaring the
executable set in source fixes that at the root: the same tree always yields the
same modes, whatever it was extracted with, or however anyone's umask is set.
"""

import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

#: Directories never shipped. Build artifacts, caches, superseded material, and
#: previously delivered archives -- none of which a clean extraction should need,
#: and all of which would make the packet's hash depend on local history.
#:
#: ``legacy`` is here for a different reason than the rest. It is not junk; it is
#: the original Node harness, kept for history. It is excluded because the packet
#: claims one authoritative package (the Python distribution in pyproject.toml),
#: and shipping a second one whose declared test command is red from a clean
#: extraction contradicts that claim. See legacy/node-harness/README.md.
SKIP_DIRS = {
    "__pycache__", ".git", "node_modules", "dist", "old_scrap", "legacy",
    ".pytest_cache", ".ruff_cache", ".venv", "venv", ".mypy_cache",
}

#: Files never shipped, by suffix or exact name.
#:
#: MANIFEST_NAME is in here so that rebuilding from an extraction reproduces the
#: delivered archive. Without it the manifest left behind by the previous
#: extraction would be picked up as an ordinary payload member *and* a fresh
#: manifest written over it -- a duplicate entry and a different hash, which
#: would break exactly the round trip this builder exists to make exact.
SKIP_SUFFIX = (".zip", ".pyc", ".pyo", ".so", ".egg-info")
SKIP_NAMES = {".DS_Store", "PACKET_MANIFEST.json"}

FIXED_DATE = (1980, 1, 1, 0, 0, 0)

#: The repository's executable set, repo-relative. Membership here -- not the
#: host filesystem -- decides the mode a member is archived with.
#:
#: Paths that are not shipped may still be listed; the entry documents that the
#: file is meant to be executable wherever it lives. ``_check_executable_set``
#: enforces the other direction: nothing in the working tree may be executable
#: without being declared here, so the list cannot silently fall behind.
EXECUTABLE_PATHS = {
    "legacy/node-harness/bin/traaviis.mjs",
    "test/fixtures/fake-claude.sh",
}

MODE_EXECUTABLE = 0o755
MODE_REGULAR = 0o644

#: Written into the archive; every payload member with its hash and mode.
MANIFEST_NAME = "PACKET_MANIFEST.json"
MANIFEST_VERSION = "traaviis.packet-manifest.v1"

#: ZIP "created by" system field. 3 is Unix, which is what makes the mode bits in
#: ``external_attr`` meaningful to a reader. CPython infers this from the host, so
#: an archive built on Windows would describe the same content with modes nobody
#: should trust. Pinning it keeps the manifest's ``mode`` column honest.
CREATE_SYSTEM_UNIX = 3


def _members():
    """Every shipped path, repo-relative, in sorted order."""
    out = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIX):
                continue
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue
            out.append(os.path.relpath(path, REPO).replace(os.sep, "/"))
    return sorted(out)


def _mode_for(rel):
    return MODE_EXECUTABLE if rel in EXECUTABLE_PATHS else MODE_REGULAR


def _check_executable_set():
    """Refuse to build if the tree is executable somewhere undeclared.

    The declared set only helps if it is complete. Without this the failure is
    silent and late: someone chmod +x's a new fixture, the packet archives it
    0644, and the break surfaces as a permission denied inside a clean
    extraction long after the build looked fine. Fail at build time instead,
    naming the file.

    Only walks the shipped tree; ``legacy`` and the other skipped directories may
    do as they like, since nothing there reaches the archive.
    """
    stray = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue
            rel = os.path.relpath(path, REPO).replace(os.sep, "/")
            if os.access(path, os.X_OK) and rel not in EXECUTABLE_PATHS:
                stray.append(rel)
    return stray


def _manifest(payload):
    """The in-archive manifest: filename -> sha256 + mode, for every member.

    Excludes itself, necessarily -- a file cannot state its own hash. Its
    integrity comes from the archive hash instead, which is what the acceptance
    gate pins.
    """
    return {
        "manifest_version": MANIFEST_VERSION,
        "entry_count": len(payload),
        "entries": [
            {"path": rel, "sha256": digest, "mode": "%04o" % _mode_for(rel)}
            for rel, digest, _ in payload
        ],
    }


def _write(zf, rel, data):
    info = zipfile.ZipInfo(rel, date_time=FIXED_DATE)
    info.create_system = CREATE_SYSTEM_UNIX
    info.external_attr = _mode_for(rel) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def build(dest):
    stray = _check_executable_set()
    if stray:
        raise SystemExit(
            "executable bit set on undeclared paths; add them to "
            "EXECUTABLE_PATHS or chmod -x:\n  " + "\n  ".join(stray)
        )

    payload = []
    for rel in _members():
        src = os.path.join(REPO, rel.replace("/", os.sep))
        with open(src, "rb") as fh:
            data = fh.read()
        payload.append((rel, hashlib.sha256(data).hexdigest(), data))

    manifest = json.dumps(
        _manifest(payload), indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        _write(zf, MANIFEST_NAME, manifest)
        for rel, _, data in payload:
            _write(zf, rel, data)
    return [rel for rel, _, _ in payload]


def main():
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2
    dest = os.path.abspath(sys.argv[1])
    members = build(dest)
    with open(dest, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    print("packet    %s" % os.path.basename(dest))
    print("entries   %d  (%d payload + manifest)" % (len(members) + 1, len(members)))
    print("bytes     %d" % os.path.getsize(dest))
    print("sha256    %s" % digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
