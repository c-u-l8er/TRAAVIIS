"""`BundleManifestV1` -- the distributed environment package (§5b).

`env-...` answers *"same evaluation meaning?"*. `bundle-...` answers a strictly
different question: *"same distributed package?"* -- the whole portable tree a
consumer receives, including the README, the docs and the screenshots that carry
no evaluation meaning at all.

    env-...       the evaluation meaning inside the package
    bundle-...    the portable package that carries it
    archive sha   the exact transport bytes that happened to move it

Those three are deliberately not the same claim, and this module is where the
second one is made honest. Two rules do the work:

1. **`bundle-...` addresses a logical tree, not an archive.** Members are
   identified by normalized relative POSIX path, file bytes, and *canonical*
   mode (`0644` / `0755` -- only the executable bit is portable). ZIP
   compression, entry order and timestamps are therefore outside the identity,
   so the same extracted package keeps one id whether it arrived as ZIP or tar.
   The archive's own SHA-256 remains available as a transport checksum, and is
   never reported as the bundle id.

2. **The manifest is closed both ways.** A manifested member that is missing is
   a closure failure; an unmanifested file that is present is *also* a closure
   failure. Only checking one direction would let anyone append a file to a
   verified package without invalidating it.

`TRAAVIIS_BUNDLE.json` excludes itself from `members` and `bundle_id` is
excluded from its own hash. That is not a special case bolted on -- it is the
only way to avoid a self-hash fiction, in which a file's recorded hash would
have to contain itself.

**Symlinks are refused in v1**, matching the snapshot rule in
`RFC_EVIDENCE_RESIDENCY.md` §5a: a link is not portable content, and following
one would let a package name bytes it does not carry.
"""

import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile

from . import identity
from .paths import PathError, safe_relposix
from .substrates import AdmissionError

__all__ = [
    "BUNDLE_VERSION", "MANIFEST_NAME", "BundleError",
    "canonical_mode", "build_manifest", "scan_tree", "read_manifest",
    "verify_bundle", "write_archive", "extract_archive", "archive_sha256",
]

BUNDLE_VERSION = "traaviis.bundle.v1"

#: The manifest lives at the package root under a name that cannot collide with
#: the *operational* `bundle.json` an eval-one package already emits. They are
#: different documents making different claims and must not share a filename.
MANIFEST_NAME = "TRAAVIIS_BUNDLE.json"

MODE_FILE = "0644"
MODE_EXEC = "0755"
_MODES = (MODE_FILE, MODE_EXEC)

#: Only these keys are admitted in a v1 manifest. An unknown key is refused
#: rather than ignored: it would silently enter `bundle-` (nothing is projected
#: out but `bundle_id`) while meaning nothing to a v1 reader.
_MANIFEST_KEYS = ("bundle_version", "env_id", "members", "bundle_id")
_MEMBER_KEYS = ("path", "sha256", "mode")


class BundleError(AdmissionError):
    """A packaging/closure failure. Same typed shape as an admission failure."""


def canonical_mode(raw_mode):
    """Project a host file mode onto the only bit that survives distribution.

    Group/other/setuid bits do not survive a ZIP round trip on every platform,
    and a umask difference is not a change to the package. The executable bit
    does survive and *is* meaningful -- a runnable helper that arrives
    non-executable is a broken package -- so it is the one bit kept.
    """
    return MODE_EXEC if raw_mode & 0o111 else MODE_FILE


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def archive_sha256(path):
    """The transport checksum of an archive's exact bytes. Not an identity."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member_path(path, code="BUNDLE_PATH_UNSAFE"):
    try:
        norm = safe_relposix(path)
    except PathError as ex:
        raise BundleError(code, "inadmissible bundle member path: %s" % ex)
    if norm != path:
        raise BundleError(
            code,
            "bundle member path %r is not already normalized (%r); a package "
            "must name its members exactly once, one way" % (path, norm))
    return norm


def build_manifest(env_id, members):
    """Build a `BundleManifestV1` over `members`.

    `members` is an iterable of `(path, data, raw_mode)`. Ordering of the input
    is irrelevant -- members are emitted sorted by normalized path, because a
    package is a *set* of members and listing them in a different order is not a
    different package.
    """
    if not isinstance(env_id, str) or not env_id.startswith("env-"):
        raise BundleError("BUNDLE_ENV_ID",
                          "a bundle manifest must name the env- it carries, "
                          "got %r" % (env_id,))
    entries, seen = [], set()
    for path, data, raw_mode in members:
        norm = _safe_member_path(path)
        if norm == MANIFEST_NAME:
            raise BundleError(
                "BUNDLE_SELF_MEMBER",
                "%s cannot be one of its own members; its hash would have to "
                "contain itself" % MANIFEST_NAME)
        if norm in seen:
            raise BundleError("BUNDLE_DUPLICATE_MEMBER",
                              "member %r is listed more than once" % norm)
        seen.add(norm)
        entries.append({
            "path": norm,
            "sha256": _sha256(data),
            "mode": canonical_mode(raw_mode),
        })
    entries.sort(key=lambda e: e["path"])
    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "env_id": env_id,
        "members": entries,
    }
    manifest["bundle_id"] = identity.bundle_id(manifest)
    return manifest


def scan_tree(root):
    """Read a package directory into `[(path, data, raw_mode)]`, manifest excluded.

    Refuses symlinks (v1) anywhere in the tree, including symlinked directories,
    which are not descended into and are reported rather than skipped -- a
    silently skipped link would make an unportable package verify.
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise BundleError("BUNDLE_MISSING",
                          "not a package directory: %s" % root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for d in sorted(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                rel = os.path.relpath(os.path.join(dirpath, d), root)
                raise BundleError(
                    "BUNDLE_SYMLINK",
                    "symlinked directory %r is not admissible in a v1 bundle"
                    % rel.replace(os.sep, "/"))
        dirnames.sort()
        for name in sorted(filenames):
            abspath = os.path.join(dirpath, name)
            rel = os.path.relpath(abspath, root).replace(os.sep, "/")
            if os.path.islink(abspath):
                raise BundleError(
                    "BUNDLE_SYMLINK",
                    "symlink %r is not admissible in a v1 bundle" % rel)
            if rel == MANIFEST_NAME:
                continue
            with open(abspath, "rb") as fh:
                data = fh.read()
            out.append((rel, data, stat.S_IMODE(os.lstat(abspath).st_mode)))
    out.sort(key=lambda e: e[0])
    return out


def _validate_manifest(doc):
    """Structural admission of a manifest read off disk."""
    if not isinstance(doc, dict):
        raise BundleError("BUNDLE_MALFORMED", "%s is not an object" % MANIFEST_NAME)
    unknown = sorted(set(doc) - set(_MANIFEST_KEYS))
    if unknown:
        raise BundleError(
            "BUNDLE_MALFORMED",
            "%s declares unknown key(s) %s; a v1 reader would ignore them "
            "while they still moved bundle-" % (MANIFEST_NAME, ", ".join(unknown)))
    if doc.get("bundle_version") != BUNDLE_VERSION:
        raise BundleError(
            "BUNDLE_VERSION",
            "unsupported bundle_version %r (expected %s)"
            % (doc.get("bundle_version"), BUNDLE_VERSION))
    env_id = doc.get("env_id")
    if not isinstance(env_id, str) or not env_id.startswith("env-"):
        raise BundleError("BUNDLE_ENV_ID",
                          "manifest names no env-: %r" % (env_id,))
    members = doc.get("members")
    if not isinstance(members, list):
        raise BundleError("BUNDLE_MALFORMED", "members is not a list")
    seen, paths = set(), []
    for entry in members:
        if not isinstance(entry, dict) or sorted(entry) != sorted(_MEMBER_KEYS):
            raise BundleError(
                "BUNDLE_MALFORMED",
                "member entry must declare exactly %s, got %r"
                % (", ".join(_MEMBER_KEYS), entry))
        path = _safe_member_path(entry["path"])
        if path == MANIFEST_NAME:
            raise BundleError("BUNDLE_SELF_MEMBER",
                              "%s lists itself as a member" % MANIFEST_NAME)
        if path in seen:
            raise BundleError("BUNDLE_DUPLICATE_MEMBER",
                              "member %r is listed more than once" % path)
        seen.add(path)
        paths.append(path)
        digest = entry["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or \
                any(c not in "0123456789abcdef" for c in digest):
            raise BundleError("BUNDLE_MALFORMED",
                              "member %r has no lowercase sha256 hex digest" % path)
        if entry["mode"] not in _MODES:
            raise BundleError(
                "BUNDLE_MODE",
                "member %r declares mode %r; a v1 bundle carries only %s"
                % (path, entry["mode"], " / ".join(_MODES)))
    if paths != sorted(paths):
        raise BundleError(
            "BUNDLE_NONCANONICAL",
            "members are not in canonical path order; a package is a set of "
            "members, so one package must not have as many manifests as its "
            "member list has permutations")
    declared = doc.get("bundle_id")
    recomputed = identity.bundle_id(doc)
    if declared != recomputed:
        raise BundleError(
            "BUNDLE_ID_MISMATCH",
            "%s does not re-derive to its declared bundle_id" % MANIFEST_NAME,
            {"declared": declared, "recomputed": recomputed})
    return doc


def read_manifest(root):
    """Read and structurally admit the manifest at the root of `root`."""
    path = os.path.join(root, MANIFEST_NAME)
    if not os.path.isfile(path) or os.path.islink(path):
        raise BundleError("BUNDLE_MISSING",
                          "%s not found at the package root: %s"
                          % (MANIFEST_NAME, root))
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as ex:
        raise BundleError("BUNDLE_MALFORMED",
                          "%s is not valid JSON: %s" % (MANIFEST_NAME, ex))
    return _validate_manifest(doc)


def verify_tree(root, manifest):
    """Both closure directions against the tree at `root`.

    Returns the scanned members. Raises on the first broken law -- a missing
    member, an extra unmanifested file, a changed byte, or a changed canonical
    mode. Checked in that order because a set difference is a better diagnosis
    than a hash mismatch on a file that should not be there at all.
    """
    scanned = scan_tree(root)
    present = {path: (data, mode) for path, data, mode in scanned}
    declared = {e["path"]: e for e in manifest["members"]}

    missing = sorted(set(declared) - set(present))
    if missing:
        raise BundleError(
            "BUNDLE_MEMBER_MISSING",
            "the package is missing %d manifested member(s)" % len(missing),
            {"missing": missing})
    extra = sorted(set(present) - set(declared))
    if extra:
        raise BundleError(
            "BUNDLE_MEMBER_EXTRA",
            "the package carries %d file(s) the manifest does not close over"
            % len(extra),
            {"extra": extra})
    for path in sorted(declared):
        data, raw_mode = present[path]
        entry = declared[path]
        got = _sha256(data)
        if got != entry["sha256"]:
            raise BundleError(
                "BUNDLE_MEMBER_HASH",
                "member %r does not match its manifested hash" % path,
                {"declared": entry["sha256"], "recomputed": got})
        got_mode = canonical_mode(raw_mode)
        if got_mode != entry["mode"]:
            raise BundleError(
                "BUNDLE_MEMBER_MODE",
                "member %r does not match its manifested mode" % path,
                {"declared": entry["mode"], "recomputed": got_mode})
    return scanned


def verify_bundle(root, *, engine=None, environment=True):
    """Verify a distributed package: bundle closure, then evaluation meaning.

    Bundle closure is checked first and on its own terms -- it needs no engine
    and no substrate -- so a tampered package is diagnosed as a tampered package
    rather than as a substrate failure. Only then is the environment reopened
    through the §5a admission interface, which re-derives `env-...`, the
    substrate subject id, and every task / reward reference from the bytes that
    are actually on disk.

    `environment=False` verifies only the package layer (used by the archive
    round-trip laws, where the point is that transport did not move `bundle-`).
    """
    from . import substrates

    root = os.path.abspath(root)
    manifest = read_manifest(root)
    members = verify_tree(root, manifest)

    report = {
        "bundle_id": manifest["bundle_id"],
        "bundle_manifest": MANIFEST_NAME,
        "bundle_version": manifest["bundle_version"],
        "env_id": manifest["env_id"],
        "members": len(members),
        "closed": True,
        "directory": root,
        "environment_verified": False,
    }
    if not environment:
        return report

    env_doc_path = os.path.join(root, "environment.json")
    if not os.path.isfile(env_doc_path):
        raise BundleError(
            "BUNDLE_NO_ENVIRONMENT",
            "the package carries no environment.json, so the env- it claims "
            "cannot be re-derived")
    with open(env_doc_path, "rb") as fh:
        try:
            env_doc = json.loads(fh.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as ex:
            raise BundleError("BUNDLE_MALFORMED",
                              "environment.json is not valid JSON: %s" % ex)
    profile = env_doc.get("substrate_profile")
    sub = substrates.for_profile(profile)
    reopened = sub.reopen_package(root, engine=engine)
    if reopened.get("env_id") != manifest["env_id"]:
        raise BundleError(
            "BUNDLE_ENV_MISMATCH",
            "the package reopens to a different environment than the manifest "
            "names",
            {"manifest": manifest["env_id"], "reopened": reopened.get("env_id")})
    report["environment_verified"] = True
    report["substrate_profile"] = profile
    subject = reopened.get("subject") or {}
    for field in ("snapshot_id", "semantic_artifact_id"):
        if field in subject:
            report["subject"] = {field: subject[field]}
            break
    report["tasks"] = [t["task_id"] for t in reopened.get("tasks", [])]
    report["rewards"] = [r["reward_id"] for r in reopened.get("rewards", [])]
    report["distribution"] = env_doc.get("distribution")
    return report


# --- transport ---------------------------------------------------------------
#
# An archive is a way to move a package, not the package. These helpers build a
# *canonical* ZIP so two builds of one tree are byte-identical (which is worth
# having), while the laws above guarantee that even a non-canonical archive
# round-trips to the same `bundle-...`.

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def write_archive(root, output, *, verify=True, engine=None):
    """Serialize a verified package tree into a deterministic ZIP.

    Publication is gated on a **serialized** round trip, not on the source tree:

        verify source tree
        -> write a temporary archive
        -> extract that archive to a temporary directory
        -> verify bundle closure from the extracted bytes
        -> when not package-only, re-derive env- and the subject closure too
        -> compare bundle_id and env_id
        -> atomically publish

    Verifying the directory and then serializing it proves the directory, which
    is not the artifact anyone receives. The claim that matters is about the
    bytes that leave the machine, so those are the bytes that get checked. A
    failed round trip leaves no archive at `output` at all.

    Returns `{bundle_id, archive, archive_sha256, entries, roundtrip_verified}`.
    `archive_sha256` is the transport checksum of these exact bytes and is
    deliberately reported under a different name than the identity -- renaming it
    `bundle-...` is the confusion this whole module exists to prevent.
    """
    root = os.path.abspath(root)
    output = os.path.abspath(output)
    manifest = read_manifest(root)
    if verify:
        verify_bundle(root, engine=engine)
    else:
        verify_tree(root, manifest)

    entries = []
    with open(os.path.join(root, MANIFEST_NAME), "rb") as fh:
        entries.append((MANIFEST_NAME, fh.read(), MODE_FILE))
    for e in manifest["members"]:
        with open(os.path.join(root, e["path"].replace("/", os.sep)), "rb") as fh:
            entries.append((e["path"], fh.read(), e["mode"]))
    entries.sort(key=lambda x: x[0])

    parent = os.path.dirname(output) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".trvs-archive-", dir=parent)
    os.close(fd)
    published = False
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, data, mode in entries:
                info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3  # unix, so external_attr carries the mode
                info.external_attr = (int(mode, 8) & 0xFFFF) << 16
                zf.writestr(info, data)
        os.chmod(tmp, 0o644)
        _verify_serialized(tmp, manifest, environment=bool(verify), engine=engine)
        os.replace(tmp, output)
        published = True
    finally:
        if not published and os.path.exists(tmp):
            os.unlink(tmp)
    return {
        "bundle_id": manifest["bundle_id"],
        "archive": output,
        "archive_sha256": archive_sha256(output),
        "entries": len(entries),
        "roundtrip_verified": True,
    }


def _verify_serialized(archive, manifest, *, environment, engine):
    """Extract `archive` and re-derive its identities. Raises, or returns None.

    Deliberately verifies *from the extracted bytes* rather than re-reading the
    source tree: the whole point is to catch anything serialization normalizes
    away -- a mode the archive cannot carry, a path the extractor renames -- and
    a check that consults the source can see none of it.
    """
    staging = tempfile.mkdtemp(prefix=".trvs-archive-check-",
                               dir=os.path.dirname(archive) or ".")
    try:
        extract_archive(archive, staging)
        report = verify_bundle(staging, engine=engine, environment=environment)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    for field in ("bundle_id", "env_id"):
        if report.get(field) != manifest[field]:
            raise BundleError(
                "BUNDLE_ARCHIVE_ROUNDTRIP",
                "the serialized archive does not carry the package it was built "
                "from: %s moved in transport" % field,
                {"field": field,
                 "directory": manifest[field],
                 "extracted": report.get(field)})


def extract_archive(archive, dest):
    """Extract a ZIP or tar package archive into `dest`, refusing unsafe entries.

    Every member path is validated with the same rule the manifest uses, so an
    archive cannot write outside `dest`, and links are refused rather than
    materialized.
    """
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                rel = _safe_member_path(info.filename, "BUNDLE_ARCHIVE_UNSAFE")
                target = os.path.join(dest, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    out.write(src.read())
                mode = (info.external_attr >> 16) & 0o7777
                if mode:
                    os.chmod(target, mode)
        return dest
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if member.isdir():
                    continue
                if not member.isfile():
                    raise BundleError(
                        "BUNDLE_ARCHIVE_UNSAFE",
                        "archive entry %r is not a regular file" % member.name)
                rel = _safe_member_path(member.name, "BUNDLE_ARCHIVE_UNSAFE")
                target = os.path.join(dest, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tf.extractfile(member)
                with open(target, "wb") as out:
                    out.write(src.read())
                os.chmod(target, member.mode & 0o7777)
        return dest
    raise BundleError("BUNDLE_ARCHIVE_UNREADABLE",
                      "not a readable ZIP or tar archive: %s" % archive)
