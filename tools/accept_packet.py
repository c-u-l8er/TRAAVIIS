"""Acceptance gate for a release packet.

    python3 tools/accept_packet.py PACKET.zip [--forge DIR] [--keep] [--gate G3]

Seven gates, G1..G7. All must pass or the packet is not releasable.

    0  every requested gate passed        ACCEPTED
    1  a gate judged the packet and it failed   REJECTED
    2  no verdict was reached -- the harness itself was wrong

The 1/2 split is load bearing and was added after it bit. `--forge` pointing at
a path that does not exist does not make the engine absent; it makes the engine
*broken*, so every engine-dependent law fails and the run prints REJECTED. That
is a report about the operator's typo wearing the packet's name. A run that
could not judge must say so instead of returning a verdict it did not earn.

Why a script and not a checklist. The clean-extraction procedure was, until now,
a paragraph in a memo: extract somewhere empty, run the battery, rebuild, compare
hashes. It found real defects -- a missing `typing` import that Python 3.14's
lazy annotations were hiding, and a packet hash that moved on rebuild -- which is
exactly the argument for it being a program. A procedure that lives in prose is
run when someone remembers to run it and is described afterwards from memory.
Both of those failure modes have already happened here: the hash was reported as
reproducible when it was only reproducible on the machine that built it, and a
battery total was reported from a hand count that did not match what the tests
printed.

The gates in order:

    G1  manifest present and well formed
    G2  manifest agrees with the archive, member for member, by hash
    G3  archive metadata is canonical (Unix modes, fixed timestamps, sorted)
    G4  the packet ships what the battery needs and excludes what it claims to
    G5  clean extraction, engine absent: nothing fails, nothing crashes
    G6  clean extraction, engine present: nothing fails, nothing skips
    G7  rebuild from two different extractors reproduces the delivered hash

G7 is the one that motivated the manifest. Python's `zipfile` does not restore
executable bits on extraction, `unzip` does; while the builder read modes off the
filesystem, those two extractors produced trees that rebuilt to *different*
archive hashes from identical content. Fixing the builder is half of it. Proving
the fix requires actually extracting both ways, which is this gate.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

MANIFEST_NAME = "PACKET_MANIFEST.json"
MANIFEST_VERSION = "traaviis.packet-manifest.v1"
CREATE_SYSTEM_UNIX = 3
FIXED_DATE = (1980, 1, 1, 0, 0, 0)

#: Paths a standalone battery cannot run without.
REQUIRED_MEMBERS = (
    "pyproject.toml",
    "README.md",
    "traaviis/__init__.py",
    "traaviis/cli.py",
    "tools/build_packet.py",
    "tools/run_battery.py",
    "test/fixtures/fake-claude.sh",
)

#: Prefixes the packet promises not to contain. `legacy/` is the quarantined Node
#: harness: it is excluded precisely because its declared test command is red from
#: a clean extraction, so its presence here would be a regression of the
#: standalone claim, not a harmless extra.
FORBIDDEN_PREFIXES = ("legacy/", "dist/", "old_scrap/", ".git/", "node_modules/")
FORBIDDEN_SUFFIXES = (".zip", ".pyc", ".pyo")


class GateFailure(Exception):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# --------------------------------------------------------------------------
# G1..G4 -- the archive, read without extracting it


def gate_g1(zf):
    """Manifest present and well formed."""
    names = zf.namelist()
    if MANIFEST_NAME not in names:
        raise GateFailure("no %s in the archive" % MANIFEST_NAME)
    doc = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
    if doc.get("manifest_version") != MANIFEST_VERSION:
        raise GateFailure("manifest_version is %r, expected %r"
                          % (doc.get("manifest_version"), MANIFEST_VERSION))
    entries = doc.get("entries")
    if not isinstance(entries, list) or not entries:
        raise GateFailure("manifest has no entries")
    paths = [e["path"] for e in entries]
    if paths != sorted(paths):
        raise GateFailure("manifest entries are not sorted")
    if len(set(paths)) != len(paths):
        raise GateFailure("manifest lists a path twice")
    if doc.get("entry_count") != len(entries):
        raise GateFailure("entry_count %r != %d entries"
                          % (doc.get("entry_count"), len(entries)))
    for e in entries:
        if not re.fullmatch(r"[0-9a-f]{64}", e.get("sha256", "")):
            raise GateFailure("entry %s has no sha256" % e.get("path"))
        if not re.fullmatch(r"0[0-7]{3}", e.get("mode", "")):
            raise GateFailure("entry %s has a malformed mode %r"
                              % (e.get("path"), e.get("mode")))
    return doc, "%d entries, %s" % (len(entries), MANIFEST_VERSION)


def gate_g2(zf, manifest):
    """Every archive member is in the manifest, with the hash it claims.

    Both directions. A manifest that omits a member would let an unlisted file
    ride along unhashed; a manifest that lists an absent one would let a reader
    believe a file was delivered that was not.
    """
    declared = {e["path"]: e for e in manifest["entries"]}
    present = {n for n in zf.namelist() if n != MANIFEST_NAME}
    missing = sorted(set(declared) - present)
    extra = sorted(present - set(declared))
    if missing:
        raise GateFailure("manifest lists members not in the archive: %s"
                          % ", ".join(missing[:5]))
    if extra:
        raise GateFailure("archive has members not in the manifest: %s"
                          % ", ".join(extra[:5]))
    bad = []
    for path, entry in sorted(declared.items()):
        if sha256_bytes(zf.read(path)) != entry["sha256"]:
            bad.append(path)
    if bad:
        raise GateFailure("content does not match the manifest hash: %s"
                          % ", ".join(bad[:5]))
    return "%d members hashed, all match" % len(declared)


def gate_g3(zf, manifest):
    """Archive metadata is canonical, not inherited from the build host."""
    declared = {e["path"]: e for e in manifest["entries"]}
    problems = []
    for info in zf.infolist():
        if info.create_system != CREATE_SYSTEM_UNIX:
            problems.append("%s: create_system %d, expected %d"
                            % (info.filename, info.create_system,
                               CREATE_SYSTEM_UNIX))
        if tuple(info.date_time) != FIXED_DATE:
            problems.append("%s: timestamp %s is not fixed"
                            % (info.filename, info.date_time))
        mode = (info.external_attr >> 16) & 0o7777
        if info.filename == MANIFEST_NAME:
            continue
        want = int(declared[info.filename]["mode"], 8)
        if mode != want:
            problems.append("%s: archived mode %04o, manifest says %04o"
                            % (info.filename, mode, want))
    if problems:
        raise GateFailure("; ".join(problems[:5]))
    names = [i.filename for i in zf.infolist() if i.filename != MANIFEST_NAME]
    if names != sorted(names):
        raise GateFailure("archive members are not in sorted order")
    return "unix modes, fixed timestamps, sorted"


def gate_g4(manifest):
    """The packet ships what the battery needs and excludes what it claims."""
    paths = {e["path"] for e in manifest["entries"]}
    missing = [p for p in REQUIRED_MEMBERS if p not in paths]
    if missing:
        raise GateFailure("required member(s) absent: %s" % ", ".join(missing))
    smuggled = sorted(
        p for p in paths
        if p.startswith(FORBIDDEN_PREFIXES) or p.endswith(FORBIDDEN_SUFFIXES)
    )
    if smuggled:
        raise GateFailure("excluded material shipped: %s"
                          % ", ".join(smuggled[:5]))
    escapes = sorted(
        p for p in paths
        if os.path.isabs(p) or p.startswith("../") or "/../" in p
    )
    if escapes:
        raise GateFailure("member escapes the extraction root: %s"
                          % ", ".join(escapes[:5]))
    tests = sorted(p for p in paths if re.fullmatch(r"test/test_\w+\.py", p))
    if len(tests) < 20:
        raise GateFailure("only %d test files shipped" % len(tests))
    return "%d members, %d test files, nothing smuggled" % (len(paths), len(tests))


# --------------------------------------------------------------------------
# extraction


def extract_python(packet, dest, manifest):
    """Extract with `zipfile`, then apply the manifest's modes.

    The second half is the point. `zipfile.extractall` drops the executable bit
    unconditionally, so a naive Python extraction of a correct archive yields an
    incorrect tree -- `fake-claude.sh` arrives 0644 and every test that execs it
    dies on a permission error. The manifest is what makes a Python extraction
    faithful; without it, this extractor and `unzip` genuinely disagree.
    """
    with zipfile.ZipFile(packet) as zf:
        zf.extractall(dest)
    manifest_path = os.path.join(dest, MANIFEST_NAME)
    if os.path.exists(manifest_path):
        os.remove(manifest_path)
    for entry in manifest["entries"]:
        path = os.path.join(dest, entry["path"].replace("/", os.sep))
        os.chmod(path, int(entry["mode"], 8))
    return dest


def extract_unzip(packet, dest):
    """Extract with the system `unzip`, which honours the archive's own modes."""
    proc = subprocess.run(
        ["unzip", "-qq", "-o", packet, "-d", dest],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise GateFailure("unzip failed: %s" % (proc.stderr.strip() or "?"))
    manifest_path = os.path.join(dest, MANIFEST_NAME)
    if os.path.exists(manifest_path):
        os.remove(manifest_path)
    return dest


# --------------------------------------------------------------------------
# G5, G6 -- the battery, from a clean extraction


_TOTAL = re.compile(r"^(\d+) passed, (\d+) skipped, (\d+) failed", re.M)


def run_battery(tree, forge):
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("TRVS_FORGE_DIR", "PYTHONPATH")
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if forge:
        env["TRVS_FORGE_DIR"] = forge
    proc = subprocess.run(
        [sys.executable, os.path.join(tree, "tools", "run_battery.py"), "--quiet"],
        cwd=tree, env=env, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    match = None
    for match in _TOTAL.finditer(out):
        pass
    if match is None:
        raise GateFailure("battery printed no total:\n%s" % out.strip()[-1500:])
    passed, skipped, failed = (int(g) for g in match.groups())
    crashed = "crashed:" in out
    return passed, skipped, failed, crashed, out


def gate_g5(tree):
    """Engine absent: nothing fails, nothing crashes; skips are expected.

    The extraction sits in a temporary directory with no TRVM checkout above it,
    so `traaviis.engine` genuinely cannot resolve a forge dir -- this is the real
    absence, not a mocked one.
    """
    passed, skipped, failed, crashed, out = run_battery(tree, forge=None)
    if crashed:
        raise GateFailure("a test file crashed:\n%s" % out.strip()[-1500:])
    if failed:
        raise GateFailure("%d failed with no engine:\n%s"
                          % (failed, out.strip()[-1500:]))
    if not skipped:
        raise GateFailure(
            "0 skipped with no engine -- the engine-dependent laws did not "
            "notice the engine was gone, which means they are not testing it")
    return "%d passed, %d skipped, 0 failed" % (passed, skipped)


def gate_g6(tree, forge):
    """Engine present: nothing fails and nothing skips."""
    if not forge:
        raise GateFailure("no engine given; pass --forge DIR")
    passed, skipped, failed, crashed, out = run_battery(tree, forge=forge)
    if crashed:
        raise GateFailure("a test file crashed:\n%s" % out.strip()[-1500:])
    if failed or skipped:
        raise GateFailure("%d failed, %d skipped with the engine present:\n%s"
                          % (failed, skipped, out.strip()[-1500:]))
    return "%d passed, 0 skipped, 0 failed" % passed


# --------------------------------------------------------------------------
# G7 -- rebuild determinism across extractors


def rebuild(tree, out_zip):
    proc = subprocess.run(
        [sys.executable, os.path.join(tree, "tools", "build_packet.py"), out_zip],
        cwd=tree, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise GateFailure("rebuild failed in %s:\n%s"
                          % (tree, (proc.stdout + proc.stderr).strip()[-1500:]))
    return sha256_file(out_zip)


def gate_g7(packet, manifest, workdir):
    """Rebuilding from either extractor reproduces the delivered hash."""
    delivered = sha256_file(packet)

    py_tree = extract_python(packet, os.path.join(workdir, "g7-python"), manifest)
    uz_tree = extract_unzip(packet, os.path.join(workdir, "g7-unzip"))

    # The two extractions must agree before their rebuilds can mean anything.
    diff = tree_mode_diff(py_tree, uz_tree)
    if diff:
        raise GateFailure("extractors disagree on modes: %s" % ", ".join(diff[:5]))

    py_hash = rebuild(py_tree, os.path.join(workdir, "rebuild-python.zip"))
    uz_hash = rebuild(uz_tree, os.path.join(workdir, "rebuild-unzip.zip"))

    if py_hash != uz_hash:
        raise GateFailure(
            "rebuild depends on the extractor:\n  zipfile %s\n  unzip   %s"
            % (py_hash, uz_hash))
    if py_hash != delivered:
        raise GateFailure(
            "rebuild does not reproduce the delivered packet:\n"
            "  delivered %s\n  rebuilt   %s" % (delivered, py_hash))
    return "%s reproduced from both extractors" % delivered[:16]


def tree_mode_diff(a, b):
    def walk(root):
        out = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                out[rel] = (
                    bool(os.stat(path).st_mode & stat.S_IXUSR),
                    sha256_file(path),
                )
        return out

    left, right = walk(a), walk(b)
    diffs = []
    for rel in sorted(set(left) | set(right)):
        if left.get(rel) != right.get(rel):
            diffs.append(rel)
    return diffs


# --------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("packet")
    ap.add_argument("--forge", default=os.environ.get("TRVS_FORGE_DIR"),
                    help="Forge/TRVM engine dir for G6")
    ap.add_argument("--keep", action="store_true",
                    help="keep the scratch extractions")
    ap.add_argument("--gate", action="append",
                    help="run only these gates (e.g. --gate G1 --gate G7)")
    args = ap.parse_args(argv)

    packet = os.path.abspath(args.packet)
    if not os.path.isfile(packet):
        print("no such packet: %s" % packet)
        return 2

    want = set(args.gate or ["G1", "G2", "G3", "G4", "G5", "G6", "G7"])

    # A misconfigured harness is not a bad packet. If --forge names something
    # that is not a directory, G6 runs the battery against an engine that
    # cannot resolve, every engine-dependent law fails, and the run prints
    # REJECTED -- blaming the packet for the operator's typo. Refuse to start
    # instead, and exit 2 ("could not judge") rather than 1 ("judged, bad").
    if "G6" in want and args.forge and not os.path.isdir(args.forge):
        print("--forge is not a directory: %s" % args.forge)
        print("no gate was run; this is a harness error, not a packet verdict")
        return 2
    workdir = tempfile.mkdtemp(prefix="traaviis-accept-")
    results = []
    ok = True

    print("packet    %s" % os.path.basename(packet))
    print("sha256    %s" % sha256_file(packet))
    print("scratch   %s\n" % workdir)

    try:
        with zipfile.ZipFile(packet) as zf:
            manifest, note = gate_g1(zf)
            results.append(("G1", "manifest well formed", True, note))

            for gid, label, fn in (
                ("G2", "manifest agrees with archive",
                 lambda: gate_g2(zf, manifest)),
                ("G3", "archive metadata canonical",
                 lambda: gate_g3(zf, manifest)),
                ("G4", "contents complete and clean",
                 lambda: gate_g4(manifest)),
            ):
                if gid not in want:
                    continue
                try:
                    results.append((gid, label, True, fn()))
                except GateFailure as ex:
                    results.append((gid, label, False, str(ex)))
                    ok = False

        if want & {"G5", "G6"}:
            tree = extract_python(packet, os.path.join(workdir, "battery"),
                                  manifest)
            for gid, label, fn in (
                ("G5", "battery, engine absent", lambda: gate_g5(tree)),
                ("G6", "battery, engine present",
                 lambda: gate_g6(tree, args.forge)),
            ):
                if gid not in want:
                    continue
                try:
                    results.append((gid, label, True, fn()))
                except GateFailure as ex:
                    results.append((gid, label, False, str(ex)))
                    ok = False

        if "G7" in want:
            try:
                results.append(("G7", "rebuild is extractor independent", True,
                                gate_g7(packet, manifest, workdir)))
            except GateFailure as ex:
                results.append(("G7", "rebuild is extractor independent", False,
                                str(ex)))
                ok = False
    except GateFailure as ex:
        results.append(("G1", "manifest well formed", False, str(ex)))
        ok = False
    finally:
        if args.keep:
            print("kept %s\n" % workdir)
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    for gid, label, passed, note in results:
        mark = "ok  " if passed else "FAIL"
        print("%s %s  %-34s %s" % (mark, gid, label, note.splitlines()[0]
                                   if note else ""))
        if not passed and note and len(note.splitlines()) > 1:
            for line in note.splitlines()[1:]:
                print("        " + line)

    print("\n%s" % ("ACCEPTED" if ok else "REJECTED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
