"""Write commit provenance a reader can *check*, not only read.

    python3 tools/build_provenance.py            # regenerate both artifacts
    python3 tools/build_provenance.py --verify   # recompute every hash offline

Two artifacts, and the difference between them is the point:

    COMMIT_PROVENANCE.json      the raw bytes of each commit object
    provenance/history.bundle   a git bundle of the branch

Why this exists
===============
The previous packet shipped a `COMMIT_PROVENANCE.json` holding, per commit, the
commit hash, its parents, its tree hash and its subject. As reviewed, that is
"better than an ungrounded memo" and it is **not** proof:

    the commit hashes cannot be independently recomputed from those fields.
    A Git commit hash also closes over the full commit message, author identity
    and timestamp, committer identity and timestamp, encoding and headers.
    Those fields are absent, and the packet contains no Git objects.

So the file was a provenance *assertion* with useful cross-checks. Both halves
of that are fixed here, by the two routes the ruling offered.

**The raw object.** Each entry now carries `raw`, the exact byte content of the
commit object, base64-encoded. Git's object id is
`sha1(b"commit %d\\0" % len(raw) + raw)`, so a reader with nothing but this JSON
file and a SHA-1 implementation can recompute every hash in it and check it
against the recorded one. `--verify` does exactly that and is what CI should
run; the point of writing it down is that the *reader* can run it without
trusting this script.

**The bundle.** `provenance/history.bundle` is a real Git bundle of the branch.
`git clone provenance/history.bundle <dir>` reconstructs the full history --
commits, trees and blobs -- so the tree hashes the JSON names can be resolved
rather than taken on faith, and the working tree the packet ships can be diffed
against the commit it claims to be.

Base64 rather than raw text, deliberately: a commit message can hold any bytes,
including invalid UTF-8, and a provenance file that could not represent one
would be a provenance file that silently omitted the commits that most needed
recording.
"""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

PROVENANCE = os.path.join(REPO, "COMMIT_PROVENANCE.json")
BUNDLE_DIR = os.path.join(REPO, "provenance")
BUNDLE = os.path.join(BUNDLE_DIR, "history.bundle")

PROVENANCE_VERSION = "traaviis.commit-provenance.v2"


def _git(*args, binary=False):
    proc = subprocess.run(["git", "-C", REPO] + list(args), check=True,
                          stdout=subprocess.PIPE)
    return proc.stdout if binary else proc.stdout.decode("utf-8").strip()


def git_object_id(kind, raw):
    """Git's content address for one loose object, recomputed from its bytes.

    The whole of Git's hashing rule for an object: the type, a space, the
    decimal byte length, a NUL, then the content. Written out here rather than
    shelled out to `git hash-object`, because a verifier that asked Git to check
    Git would prove nothing about whether the bytes in this packet are the ones
    the hashes name.
    """
    header = ("%s %d\0" % (kind, len(raw))).encode("ascii")
    return hashlib.sha1(header + raw).hexdigest()


def collect(rev="HEAD"):
    """Every commit reachable from `rev`, newest first, with its raw object."""
    revisions = _git("rev-list", rev).splitlines()
    commits = []
    for sha in revisions:
        raw = _git("cat-file", "commit", sha, binary=True)
        recomputed = git_object_id("commit", raw)
        if recomputed != sha:
            raise SystemExit(
                "git disagrees with its own hashing rule for %s (got %s); "
                "refusing to write provenance that is already wrong"
                % (sha, recomputed))
        header, _, message = raw.partition(b"\n\n")
        fields = {}
        for line in header.decode("utf-8", "replace").splitlines():
            key, _, value = line.partition(" ")
            fields.setdefault(key, []).append(value)
        commits.append({
            "commit": sha,
            "tree": (fields.get("tree") or [None])[0],
            "parents": fields.get("parent", []),
            "author": (fields.get("author") or [None])[0],
            "committer": (fields.get("committer") or [None])[0],
            "subject": message.decode("utf-8", "replace").splitlines()[0]
                       if message.strip() else "",
            # The bytes the hash is actually over. Everything above is a
            # convenience projection of this field and is checkable against it.
            "raw_base64": base64.b64encode(raw).decode("ascii"),
        })
    return commits


def build(rev="HEAD"):
    commits = collect(rev)
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    document = {
        "provenance_version": PROVENANCE_VERSION,
        "branch": branch,
        "head": commits[0]["commit"] if commits else None,
        "bundle": os.path.relpath(BUNDLE, REPO).replace(os.sep, "/"),
        "how_to_verify": [
            "python3 tools/build_provenance.py --verify",
            "git clone provenance/history.bundle /tmp/traaviis-history",
        ],
        "commits_newest_first": commits,
    }
    with open(PROVENANCE, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")

    os.makedirs(BUNDLE_DIR, exist_ok=True)
    subprocess.run(["git", "-C", REPO, "bundle", "create", BUNDLE, "--all"],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    subprocess.run(["git", "-C", REPO, "bundle", "verify", BUNDLE],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print("wrote %s (%d commits)" % (os.path.basename(PROVENANCE), len(commits)))
    print("wrote %s (%d bytes)" % (os.path.relpath(BUNDLE, REPO),
                                   os.path.getsize(BUNDLE)))
    return 0


def verify():
    """Recompute every commit hash from the file alone. No Git involved.

    This is the check the previous provenance file could not offer, and it is
    written so a reader can run it against an extracted packet on a machine with
    no repository and no `git` at all.
    """
    if not os.path.isfile(PROVENANCE):
        print("no %s" % PROVENANCE)
        return 2
    with open(PROVENANCE, encoding="utf-8") as fh:
        document = json.load(fh)
    commits = document.get("commits_newest_first") or []
    if not commits:
        print("provenance records no commits")
        return 2

    by_id = {}
    problems = []
    for entry in commits:
        encoded = entry.get("raw_base64")
        if not encoded:
            problems.append("%s carries no raw object; the hash cannot be "
                            "recomputed from this file" % entry.get("commit"))
            continue
        raw = base64.b64decode(encoded)
        recomputed = git_object_id("commit", raw)
        if recomputed != entry["commit"]:
            problems.append("%s recomputes to %s" % (entry["commit"], recomputed))
            continue
        by_id[entry["commit"]] = raw
        # The projected fields must agree with the bytes they were projected
        # from, or the readable half of the file would be free to drift from
        # the checkable half -- which is the failure the previous version had in
        # its most complete form.
        header = raw.partition(b"\n\n")[0].decode("utf-8", "replace")
        lines = [line.split(" ", 1) for line in header.splitlines()]
        tree = next((v for k, v in lines if k == "tree"), None)
        parents = [v for k, v in lines if k == "parent"]
        if tree != entry.get("tree"):
            problems.append("%s: recorded tree disagrees with the object"
                            % entry["commit"])
        if parents != list(entry.get("parents") or []):
            problems.append("%s: recorded parents disagree with the object"
                            % entry["commit"])

    # The chain closes: every parent named is either in the file or is the
    # boundary of what was recorded. A dangling parent inside the range would
    # mean the history has a hole nobody declared.
    known = set(by_id)
    for entry in commits:
        for parent in entry.get("parents") or []:
            if parent not in known and parent != commits[-1].get("commit"):
                if all(parent != c.get("commit") for c in commits):
                    problems.append(
                        "%s names parent %s, which this file does not record"
                        % (entry["commit"], parent))

    for line in problems:
        print("PROBLEM  %s" % line)
    if problems:
        print("\nFAIL: provenance does not recompute.")
        return 1
    print("recomputed %d commit hashes from the file alone: all agree"
          % len(by_id))
    if os.path.isfile(BUNDLE):
        print("bundle present: %s (%d bytes) -- `git clone` it to resolve trees"
              % (os.path.relpath(BUNDLE, REPO), os.path.getsize(BUNDLE)))
    else:
        print("NOTE: no bundle at %s; commit hashes check out but tree and blob "
              "objects cannot be resolved from this packet"
              % os.path.relpath(BUNDLE, REPO))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true",
                        help="recompute hashes from COMMIT_PROVENANCE.json alone")
    parser.add_argument("--rev", default="HEAD")
    args = parser.parse_args(argv)
    return verify() if args.verify else build(args.rev)


if __name__ == "__main__":
    sys.exit(main())
