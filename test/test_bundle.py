"""Laws for `bundle-…` -- the distributed environment package (§5b).

`env-…` and `bundle-…` are two different questions about the same directory:

    env-…       same evaluation meaning?      subject, tasks, rewards, splits
    bundle-…    same distributed package?     that, plus every shipped README,
                                              doc and screenshot, by path,
                                              bytes and canonical mode
    archive sha same transport bytes?         a checksum, not an identity

Almost every law here is a statement about a way those three could quietly
collapse into each other:

- a README edit could claim to be a different experiment (D7-D9);
- a subject edit could fail to reach the package (D10);
- a package could be verified in one direction only, so anyone could append a
  file to a "verified" package (D14-D15);
- the manifest could try to hash itself (D2, D18);
- ZIP metadata -- timestamps, compression, entry order -- could leak into the
  identity, so the same package would have one id per transport (D22-D23);
- the packet gate's archive SHA-256 could be renamed `bundle-…` (D25-D26);
- a bundle field could appear in an evaluation report, so a screenshot swap
  would rewrite an experiment (D27-D28);
- a package could carry a subject mode that its own archive cannot, so it would
  verify as a package and fail to reopen as an environment (D31-D36);
- an archive could be published on the strength of the directory it was built
  from rather than of the bytes it actually contains (D37-D38).

**On what needs an engine.** A package tree is a substrate-neutral object, so
the manifest, closure, transport and publication laws (D2, D3, D14-D16, D18,
D22-D26, D30, D32, D37, D38, D40) are proven against a *synthetic* tree that no
substrate produced and no engine can lower. That is not a convenience: those are
exactly the laws a **recipient** needs, and a recipient verifying a downloaded
package usually has no Forge checkout at all. The laws that genuinely concern an
environment -- the env/bundle split, substrate reopening, subject-mode
admission, and anything that runs an agent -- pack a real template and SKIP when
no engine is locatable.

Run directly:      python3 test/test_bundle.py
Run under pytest:  pytest test/test_bundle.py
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import bundle as BD  # noqa: E402
from traaviis import engine as _engine  # noqa: E402
from traaviis import identity, pack as P, scaffold as S  # noqa: E402
from traaviis.substrates import AdmissionError  # noqa: E402

TEMPLATE = "residency-repair"
AGENT = [sys.executable, os.path.join(REPO, "test", "fixtures", "repair_agent.py")]


class Skip(Exception):
    pass


# --------------------------------------------------------------- fixtures
_FIXTURE = {}


def _tmp():
    if "tmp" not in _FIXTURE:
        _FIXTURE["tmp"] = tempfile.mkdtemp(prefix="trvs-bundle-law-")
    return _FIXTURE["tmp"]


def _engine_or_skip():
    eng = _engine.try_load()
    if eng is None:
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    return eng


#: A package tree with no substrate behind it. It carries a nested path, an
#: executable member and a would-be `environment.json`, so the structural laws
#: have something with real shape to bite on.
SYNTH_ENV = "env-" + "a1" * 32
SYNTH_MEMBERS = (
    ("README.md", b"# a synthetic package\n", 0o644),
    ("environment.json", b'{"name":"synthetic"}\n', 0o644),
    ("docs/guide.md", b"how to use it\n", 0o644),
    ("bin/run.sh", b"#!/bin/sh\necho hi\n", 0o755),
    ("subject/mod.py", b"def f():\n    return 1\n", 0o644),
)


def _synth(name, members=SYNTH_MEMBERS, env_id=SYNTH_ENV):
    """Write a synthetic package tree; return `(root, manifest)`.

    Every law about the *package* -- what the manifest covers, that closure is
    checked both ways, that a container leaves no trace -- is a statement about
    a canonical tree. Proving it here rather than on a packed environment keeps
    it true, and running, on a machine that has no engine: which is the machine
    a package is usually *received* on.
    """
    root = os.path.join(_tmp(), "synth-" + name)
    shutil.rmtree(root, ignore_errors=True)
    for rel, data, mode in members:
        path = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        os.chmod(path, mode)
    manifest = BD.build_manifest(env_id, list(members))
    with open(os.path.join(root, BD.MANIFEST_NAME), "w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    return root, manifest


def _scaffold(name, mutate=None):
    """A scaffolded source environment, optionally edited before packing."""
    env = os.path.join(_tmp(), "env-" + name)
    shutil.rmtree(env, ignore_errors=True)
    S.materialize(TEMPLATE, env)
    if mutate is not None:
        mutate(env)
    return env


def _pack(name, mutate=None, env=None):
    """Pack a (possibly edited) scaffold into its own destination."""
    env = env or _scaffold(name, mutate)
    out = os.path.join(_tmp(), "pkg-" + name)
    shutil.rmtree(out, ignore_errors=True)
    return env, out, P.pack(env, out, engine=_engine_or_skip())


def _fixture():
    """One packed reference package, shared by every reading law."""
    if "report" not in _FIXTURE:
        env, out, report = _pack("base")
        _FIXTURE.update({"env": env, "pkg": out, "report": report})
    return _FIXTURE


def _edit_env(env, mutate):
    path = os.path.join(env, "env.json")
    with open(path) as fh:
        doc = json.load(fh)
    mutate(doc)
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2)


def _copy(name):
    """An independent copy of the reference package, safe to tamper with."""
    dest = os.path.join(_tmp(), "copy-" + name)
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(_fixture()["pkg"], dest)
    return dest


def _manifest(root):
    with open(os.path.join(root, BD.MANIFEST_NAME)) as fh:
        return json.load(fh)


def _reseal(root):
    """Rewrite `TRAAVIIS_BUNDLE.json` to honestly describe the tree as it is now.

    Used by the laws that must reach *past* package closure: a tampered member
    is caught by the manifest long before the substrate sees it, so proving that
    the substrate would also have caught it requires handing it a package whose
    package layer is beyond reproach.
    """
    manifest = BD.build_manifest(_manifest(root)["env_id"], BD.scan_tree(root))
    with open(os.path.join(root, BD.MANIFEST_NAME), "w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    return manifest


def _refuses(fn, code, what):
    try:
        fn()
    except AdmissionError as ex:
        assert ex.code == code, "%s: expected %s, got %s (%s)" % (
            what, code, ex.code, ex)
        return ex
    raise AssertionError("%s: expected %s, got no refusal" % (what, code))


def _bundle_of(root):
    """Re-derive `bundle-…` from a tree, independent of what it claims."""
    return BD.build_manifest(_manifest(root)["env_id"],
                             BD.scan_tree(root))["bundle_id"]


# --- D1-D3: what does NOT move the identity ----------------------------------

def test_d1_equivalent_package_trees_derive_the_same_bundle():
    """Two packs of the same source, and a byte copy of a package, are the same
    distributed package. If they were not, no consumer could ever confirm they
    received what was published."""
    f = _fixture()
    _, again, report = _pack("d1-again", env=f["env"])
    assert report["bundle_id"] == f["report"]["bundle_id"]
    assert report["env_id"] == f["report"]["env_id"]

    copied = _copy("d1")
    assert _bundle_of(copied) == f["report"]["bundle_id"]
    # And the tree really is the same package, not merely a matching id.
    assert BD.verify_bundle(copied)["bundle_id"] == f["report"]["bundle_id"]


def test_d2_bundle_id_is_excluded_from_its_own_hash():
    """The self-exclusion is not an optimization -- without it the manifest
    would have to contain its own hash, which no document can."""
    _root, doc = _synth("d2")
    without = {k: v for k, v in doc.items() if k != "bundle_id"}
    assert identity.bundle_id(without) == doc["bundle_id"]

    # Overwriting the field cannot change what the manifest re-derives to;
    # it can only make the manifest disagree with itself, which is D16's job.
    forged = dict(doc, bundle_id="bundle-" + "0" * 64)
    assert identity.bundle_id(forged) == doc["bundle_id"]
    assert "bundle_id" not in json.loads(
        identity.canonicalize_bundle(doc).decode("utf-8"))


def test_d3_member_ordering_does_not_move_identity():
    """A package is a *set* of members. If order moved the id, one package
    would have as many identities as its member list has permutations."""
    root, declared = _synth("d3")
    members = BD.scan_tree(root)
    assert len(members) > 2, "too few members for the law to say anything"
    forward = BD.build_manifest(SYNTH_ENV, members)
    backward = BD.build_manifest(SYNTH_ENV, list(reversed(members)))
    assert forward == backward
    assert forward["bundle_id"] == declared["bundle_id"]
    # The emitted manifest is itself canonical, so a reader never has to sort.
    paths = [e["path"] for e in forward["members"]]
    assert paths == sorted(paths)


# --- D4-D6: what DOES move the identity --------------------------------------

def test_d4_a_member_byte_change_moves_the_bundle():
    """Any shipped byte is part of the package, whether or not it has meaning
    to the evaluation."""
    f = _fixture()
    tampered = _copy("d4")
    path = os.path.join(tampered, "README.md")
    with open(path, "a") as fh:
        fh.write("\nan extra line\n")
    assert _bundle_of(tampered) != f["report"]["bundle_id"]


def test_d5_a_member_path_rename_moves_the_bundle():
    """Where a file ships is part of what shipped -- a consumer following a
    documented path finds nothing when it moves."""
    def rename(env):
        os.rename(os.path.join(env, "README.md"), os.path.join(env, "GUIDE.md"))
        _edit_env(env, lambda d: d.__setitem__(
            "distribution", {"entrypoint": "GUIDE.md",
                             "documentation": ["GUIDE.md"]}))

    f = _fixture()
    _, out, report = _pack("d5", rename)
    assert report["bundle_id"] != f["report"]["bundle_id"]
    assert report["env_id"] == f["report"]["env_id"], \
        "renaming a doc changed the evaluation meaning"
    assert os.path.isfile(os.path.join(out, "GUIDE.md"))


def test_d6_a_canonical_mode_change_moves_the_bundle():
    """The executable bit is the one mode bit that survives distribution, and a
    helper that arrives non-executable is a broken package. A doc member is used
    so the law isolates *mode*: a subject mode change would also move `snap-`."""
    def make_executable(env):
        path = os.path.join(env, "README.md")
        os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) | 0o111)

    f = _fixture()
    _, out, report = _pack("d6", make_executable)
    assert report["bundle_id"] != f["report"]["bundle_id"]
    assert report["env_id"] == f["report"]["env_id"]
    entry = next(e for e in _manifest(out)["members"] if e["path"] == "README.md")
    assert entry["mode"] == BD.MODE_EXEC, entry
    # And the bit actually reached the written file, not just the manifest.
    assert os.lstat(os.path.join(out, "README.md")).st_mode & 0o111

    # Group/other/umask noise is not a package change: 0644 and 0664 are one
    # canonical mode, so a differently-umasked machine publishes one bundle.
    assert BD.canonical_mode(0o644) == BD.canonical_mode(0o664) == BD.MODE_FILE
    assert BD.canonical_mode(0o755) == BD.canonical_mode(0o700) == BD.MODE_EXEC


# --- D7-D10: the whole point of two identities -------------------------------

def test_d7_name_and_description_move_the_bundle_not_the_environment():
    """Presentation is carried by the package and excluded from `env-…`. This
    is the law that lets a release be re-described without anyone having to
    re-run the experiments in it."""
    f = _fixture()
    _, _, report = _pack("d7", lambda env: _edit_env(env, lambda d: d.update({
        "name": "residency-repair (renamed)",
        "description": "the same environment, described differently"})))
    assert report["env_id"] == f["report"]["env_id"]
    assert report["bundle_id"] != f["report"]["bundle_id"]


def test_d8_documentation_changes_move_the_bundle_not_the_environment():
    """Rewriting the shipped README changes what was distributed and nothing
    about what is evaluated."""
    f = _fixture()
    _, _, report = _pack("d8", lambda env: open(
        os.path.join(env, "README.md"), "a").write("\n## Extra section\n"))
    assert report["env_id"] == f["report"]["env_id"]
    assert report["bundle_id"] != f["report"]["bundle_id"]


def test_d9_screenshot_changes_move_the_bundle_not_the_environment():
    """Screenshots are shipped bytes with no evaluation meaning at all -- the
    clearest case of the split, and the reason `distribution` exists."""
    def with_shot(env, data=b"\x89PNG\r\n\x1a\nfirst"):
        os.makedirs(os.path.join(env, "screenshots"), exist_ok=True)
        with open(os.path.join(env, "screenshots", "overview.png"), "wb") as fh:
            fh.write(data)
        _edit_env(env, lambda d: d.__setitem__("distribution", {
            "entrypoint": "README.md",
            "documentation": ["README.md"],
            "screenshots": ["screenshots/overview.png"]}))

    f = _fixture()
    _, first, one = _pack("d9a", with_shot)
    _, _, two = _pack("d9b", lambda env: with_shot(env, b"\x89PNG\r\n\x1a\nsecond"))

    assert one["env_id"] == two["env_id"] == f["report"]["env_id"]
    assert one["bundle_id"] != two["bundle_id"]
    assert one["bundle_id"] != f["report"]["bundle_id"]
    assert os.path.isfile(os.path.join(first, "screenshots", "overview.png"))

    # Reclassification is a presentation change even when no byte moves: the
    # same file, shipped as a screenshot rather than as documentation, is a
    # differently-described package.
    def reclassified(env):
        with_shot(env)
        _edit_env(env, lambda d: d.__setitem__("distribution", {
            "entrypoint": "README.md",
            "documentation": ["README.md", "screenshots/overview.png"]}))

    _, _, three = _pack("d9c", reclassified)
    assert three["env_id"] == f["report"]["env_id"]
    assert three["bundle_id"] != one["bundle_id"]


def test_d10_subject_task_reward_and_split_changes_move_both():
    """The converse law. If an evaluation-meaning change could leave
    `bundle-…` still, a consumer could receive a package byte-identical to one
    they had already verified while it evaluated something else."""
    f = _fixture()
    base = (f["report"]["env_id"], f["report"]["bundle_id"])

    def subject(env):
        path = os.path.join(env, "subject", "src", "mod.py")
        with open(path, "a") as fh:
            fh.write("\n# a real edit to the subject\n")

    def reward(env):
        path = os.path.join(env, "reward.json")
        with open(path) as fh:
            doc = json.load(fh)
        signal = sorted(doc["signals"])[0]
        doc["signals"][signal]["weight"] = \
            round(float(doc["signals"][signal].get("weight", 1.0)) / 2, 4)
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=2)

    def task(env):
        path = os.path.join(env, "task.json")
        with open(path) as fh:
            doc = json.load(fh)
        doc["instructions"]["objective"] += " (restated)"
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=2)

    def split(env):
        _edit_env(env, lambda d: d["splits"].__setitem__("smoke", ["task.json"]))

    for label, mutate in (("subject", subject), ("reward", reward),
                          ("task", task), ("split", split)):
        _, _, report = _pack("d10-" + label, mutate)
        assert report["env_id"] != base[0], "%s left env- still" % label
        assert report["bundle_id"] != base[1], "%s left bundle- still" % label


# --- D11-D13: reopening proves the meaning, not just the bytes ---------------

def test_d11_environment_json_rederives_its_declared_env_id():
    """A package that merely *stated* an `env-…` would be asking to be
    believed. Verification re-derives it from the bytes on disk."""
    pkg = _fixture()["pkg"]
    with open(os.path.join(pkg, "environment.json")) as fh:
        doc = json.load(fh)
    assert doc["env_id"] == identity.environment_id(doc)
    report = BD.verify_bundle(pkg, engine=_engine_or_skip())
    assert report["env_id"] == doc["env_id"]
    assert report["environment_verified"] is True


def test_d12_the_substrate_subject_rederives_its_declared_identity():
    """Reach *past* package closure: reseal the manifest so the package layer
    is beyond reproach, and check that the substrate still refuses a subject
    whose bytes no longer reproduce its `snap-…`."""
    tampered = _copy("d12")
    with open(os.path.join(tampered, "subject", "src", "mod.py"), "a") as fh:
        fh.write("\n# tampered after packing\n")
    _reseal(tampered)

    # Package closure now passes -- which is exactly the point.
    BD.verify_bundle(tampered, environment=False)
    _refuses(lambda: BD.verify_bundle(tampered, engine=_engine_or_skip()),
             "REOPEN_SUBJECT_BYTES", "tampered subject with an honest manifest")


def test_d13_every_task_reward_and_profile_reference_resolves():
    """Same shape, one rung up: a resealed package whose task bytes changed
    must fail to reopen, because the task no longer derives its own `task-…`."""
    tampered = _copy("d13")
    path = os.path.join(tampered, "task.json")
    with open(path) as fh:
        doc = json.load(fh)
    doc["instructions"]["objective"] += " (edited in the package)"
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2)
    _reseal(tampered)

    BD.verify_bundle(tampered, environment=False)
    _refuses(lambda: BD.verify_bundle(tampered, engine=_engine_or_skip()),
             "REOPEN_TASK_ID", "tampered task with an honest manifest")

    # And a reward the manifest still closes over, but the environment no
    # longer references, is an unresolved closure rather than a silent pass.
    orphaned = _copy("d13b")
    os.rename(os.path.join(orphaned, "reward.json"),
              os.path.join(orphaned, "reward-moved.json"))
    _reseal(orphaned)
    _refuses(lambda: BD.verify_bundle(orphaned, engine=_engine_or_skip()),
             "REOPEN_REWARD", "a reward the manifest carries but env- cannot find")


# --- D14-D18: closure in both directions -------------------------------------

def test_d14_a_missing_manifested_member_is_refused():
    """Half of closure. A package that lost a file is not the package."""
    broken, _ = _synth("d14")
    os.unlink(os.path.join(broken, "README.md"))
    ex = _refuses(lambda: BD.verify_bundle(broken, environment=False),
                  "BUNDLE_MEMBER_MISSING", "a deleted member")
    assert ex.detail["missing"] == ["README.md"], ex.detail


def test_d15_an_extra_unmanifested_member_is_refused():
    """The other half, and the one that is easy to leave out. Without it,
    anyone could append a file to a verified package and it would still
    verify -- which would make "verified" mean nothing about what arrived."""
    broken, _ = _synth("d15")
    with open(os.path.join(broken, "EXTRA.md"), "w") as fh:
        fh.write("smuggled in after publication\n")
    ex = _refuses(lambda: BD.verify_bundle(broken, environment=False),
                  "BUNDLE_MEMBER_EXTRA", "an appended file")
    assert ex.detail["extra"] == ["EXTRA.md"], ex.detail

    nested, _ = _synth("d15b")
    os.makedirs(os.path.join(nested, "subject", "sneaky"))
    with open(os.path.join(nested, "subject", "sneaky", "x.py"), "w") as fh:
        fh.write("pass\n")
    ex = _refuses(lambda: BD.verify_bundle(nested, environment=False),
                  "BUNDLE_MEMBER_EXTRA", "a file appended deep in the tree")
    assert ex.detail["extra"] == ["subject/sneaky/x.py"], ex.detail


def test_d16_a_hash_or_mode_mismatch_is_refused():
    """Both member fields are load-bearing, so both are checked."""
    changed, _ = _synth("d16a")
    with open(os.path.join(changed, "README.md"), "a") as fh:
        fh.write("edited\n")
    _refuses(lambda: BD.verify_bundle(changed, environment=False),
             "BUNDLE_MEMBER_HASH", "a changed member byte")

    chmodded, _ = _synth("d16b")
    path = os.path.join(chmodded, "README.md")
    os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) | 0o111)
    _refuses(lambda: BD.verify_bundle(chmodded, environment=False),
             "BUNDLE_MEMBER_MODE", "a changed member mode")

    forged, doc = _synth("d16c")
    doc = dict(doc, bundle_id="bundle-" + "0" * 64)
    with open(os.path.join(forged, BD.MANIFEST_NAME), "w") as fh:
        json.dump(doc, fh, indent=2)
    _refuses(lambda: BD.verify_bundle(forged, environment=False),
             "BUNDLE_ID_MISMATCH", "a manifest that disagrees with itself")


def test_d17_unsafe_duplicate_and_symlinked_members_are_refused():
    """v1 carries regular files at safe relative paths and nothing else. A
    link is not portable content, and following one would let a package name
    bytes it does not carry."""
    for path in ("../escape.md", "/etc/passwd", "docs/../../up.md"):
        _refuses(lambda p=path: BD.build_manifest("env-" + "0" * 64,
                                                  [(p, b"x", 0o644)]),
                 "BUNDLE_PATH_UNSAFE", "member path %r" % path)
    # A path that would *normalize* is refused too: a package names each
    # member exactly once, one way.
    _refuses(lambda: BD.build_manifest("env-" + "0" * 64,
                                       [("./docs/x.md", b"x", 0o644)]),
             "BUNDLE_PATH_UNSAFE", "an unnormalized member path")
    _refuses(lambda: BD.build_manifest(
        "env-" + "0" * 64, [("a.md", b"x", 0o644), ("a.md", b"y", 0o644)]),
        "BUNDLE_DUPLICATE_MEMBER", "a duplicate member path")

    linked = _copy("d17")
    os.symlink("README.md", os.path.join(linked, "LINK.md"))
    _refuses(lambda: BD.verify_bundle(linked), "BUNDLE_SYMLINK",
             "a symlinked file in the tree")

    linked_dir = _copy("d17b")
    os.symlink("subject", os.path.join(linked_dir, "subject-link"))
    _refuses(lambda: BD.verify_bundle(linked_dir), "BUNDLE_SYMLINK",
             "a symlinked directory in the tree")

    # And pack refuses at the source, before anything is derived.
    def link_a_doc(env):
        os.unlink(os.path.join(env, "README.md"))
        os.symlink("/etc/hostname", os.path.join(env, "README.md"))

    _refuses(lambda: _pack("d17c", link_a_doc), "DISTRIBUTION_MISSING",
             "a symlinked documentation member at the source")


def test_d18_the_manifest_excludes_itself_without_a_self_hash_fiction():
    """`TRAAVIIS_BUNDLE.json` cannot be one of its own members: its recorded
    hash would have to contain itself. It is excluded by construction at every
    seam -- building, scanning and reading."""
    pkg, doc = _synth("d18")
    paths = [e["path"] for e in doc["members"]]
    assert BD.MANIFEST_NAME not in paths, paths
    assert BD.MANIFEST_NAME in os.listdir(pkg), "the manifest is not even there"
    assert BD.MANIFEST_NAME not in [p for p, _d, _m in BD.scan_tree(pkg)]

    _refuses(lambda: BD.build_manifest("env-" + "0" * 64,
                                       [(BD.MANIFEST_NAME, b"{}", 0o644)]),
             "BUNDLE_SELF_MEMBER", "a manifest listing itself")

    self_listing, doc = _synth("d18b")
    doc["members"].append({"path": BD.MANIFEST_NAME,
                           "sha256": "0" * 64, "mode": BD.MODE_FILE})
    doc["members"].sort(key=lambda e: e["path"])
    doc["bundle_id"] = identity.bundle_id(
        {k: v for k, v in doc.items() if k != "bundle_id"})
    with open(os.path.join(self_listing, BD.MANIFEST_NAME), "w") as fh:
        json.dump(doc, fh, indent=2)
    _refuses(lambda: BD.verify_bundle(self_listing, environment=False),
             "BUNDLE_SELF_MEMBER", "a self-listing manifest read off disk")


# --- D19-D21: publication ----------------------------------------------------

def test_d19_pack_writes_and_publishes_the_complete_tree_atomically():
    """Observed at the seam, because that is the only place the claim is
    visible: at the moment of publication the destination does not exist, and
    the staged tree is already complete and already verifiable."""
    observed = {}
    real = os.replace

    def spy(src, dst, *a, **kw):
        if isinstance(dst, str) and dst.endswith("pkg-d19"):
            observed["dest_exists"] = os.path.exists(dst)
            observed["staged"] = sorted(
                p for p, _d, _m in BD.scan_tree(src))
            observed["verifies"] = BD.verify_bundle(
                src, environment=False)["bundle_id"]
        return real(src, dst, *a, **kw)

    os.replace = spy
    try:
        _, out, report = _pack("d19")
    finally:
        os.replace = real

    assert observed["dest_exists"] is False, \
        "the destination already existed when the package was published"
    assert observed["verifies"] == report["bundle_id"]
    published = sorted(p for p, _d, _m in BD.scan_tree(out))
    assert observed["staged"] == published, (observed["staged"], published)


def test_d20_a_failed_pack_leaves_no_destination_or_partial_bundle():
    """Two failures, one early and one as late as it can be. Neither may leave
    a destination behind -- a half-package at the published path is worse than
    no package, because it looks like one."""
    def bad_doc(env):
        _edit_env(env, lambda d: d.__setitem__("distribution", {
            "entrypoint": "MISSING.md", "documentation": ["MISSING.md"]}))

    env = _scaffold("d20a", bad_doc)
    out = os.path.join(_tmp(), "pkg-d20a")
    _refuses(lambda: P.pack(env, out, engine=_engine_or_skip()),
             "DISTRIBUTION_MISSING", "a declared doc that is not there")
    assert not os.path.exists(out)

    # The latest possible failure: everything derived, everything staged, and
    # the reopen refuses. The destination must still never have existed.
    from traaviis import substrates
    sub = substrates.SUBSTRATES["residency.repository.v1"]
    real = sub.reopen_package
    env = _scaffold("d20b")
    out = os.path.join(_tmp(), "pkg-d20b")
    sub.reopen_package = lambda *a, **kw: {"env_id": "env-" + "0" * 64}
    try:
        _refuses(lambda: P.pack(env, out, engine=_engine_or_skip()),
                 "REOPEN_ENV_ID", "a package that does not reopen")
    finally:
        sub.reopen_package = real
    assert not os.path.exists(out)
    leftovers = [n for n in os.listdir(_tmp()) if n.startswith(".trvs-pack-")]
    assert leftovers == [], leftovers


def test_d21_reopening_proves_both_bundle_and_environment_identities():
    """One command, two claims, both re-derived from disk. A verification that
    proved only one of them would leave the other asking to be believed."""
    f = _fixture()
    report = BD.verify_bundle(f["pkg"], engine=_engine_or_skip())
    assert report["bundle_id"] == f["report"]["bundle_id"]
    assert report["env_id"] == f["report"]["env_id"]
    assert report["closed"] is True and report["environment_verified"] is True
    assert report["subject"]["snapshot_id"].startswith("snap-")
    assert report["tasks"] and all(t.startswith("task-") for t in report["tasks"])

    # `pack` reports the same two, under the names the RFC fixed.
    assert f["report"]["bundle_manifest"] == BD.MANIFEST_NAME
    assert f["report"]["reopened"] is True

    p = subprocess.run(
        [sys.executable, "-m", "traaviis.cli", "verify-bundle", f["pkg"],
         "--json"], cwd=REPO, capture_output=True, text=True)
    assert p.returncode == 0, (p.returncode, p.stderr[-800:])
    assert json.loads(p.stdout)["bundle_id"] == f["report"]["bundle_id"]


# --- D22-D25: transport is not identity --------------------------------------

def _noncanonical_zip(root, output):
    """A ZIP of the same tree with every metadata choice made differently.

    Modes are deliberately *preserved*. Order, compression, timestamps and
    comments are facts about a container; a member's mode is part of the
    package, so an archive that dropped it would be carrying different content
    and would rightly fail to verify. This helper varies transport and only
    transport -- which is the whole claim D22 is making.
    """
    modes = {e["path"]: e["mode"] for e in _manifest(root)["members"]}
    modes[BD.MANIFEST_NAME] = BD.MODE_FILE
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as zf:
        for name in reversed(sorted(modes)):          # reversed entry order
            info = zipfile.ZipInfo(name, date_time=(2031, 7, 24, 13, 45, 2))
            info.compress_type = zipfile.ZIP_STORED   # stored, not deflated
            info.create_system = 3
            info.external_attr = (int(modes[name], 8) & 0xFFFF) << 16
            info.comment = b"a comment that is not part of the package"
            with open(os.path.join(root, name.replace("/", os.sep)), "rb") as fh:
                zf.writestr(info, fh.read())
    return output


def test_d22_zip_metadata_changes_do_not_move_the_bundle():
    """Compression, entry order, timestamps and comments are facts about an
    archive, not about the package inside it. If they entered the identity,
    one package would have a different id per transport."""
    root, declared = _synth("d22")
    archive = _noncanonical_zip(root, os.path.join(_tmp(), "d22.zip"))
    canonical = BD.write_archive(root, os.path.join(_tmp(), "d22-canon.zip"),
                                 verify=False)
    assert BD.archive_sha256(archive) != canonical["archive_sha256"], \
        "the two archives are byte-identical, so the law proves nothing"

    dest = BD.extract_archive(archive, os.path.join(_tmp(), "d22-extract"))
    assert BD.verify_bundle(dest, environment=False)["bundle_id"] == \
        declared["bundle_id"]


def test_d23_zip_and_tar_extractions_of_one_tree_derive_the_same_id():
    """The identity is of the logical tree, so the container it travelled in
    leaves no trace. Proven with two genuinely different container formats."""
    root, declared = _synth("d23")
    zip_path = os.path.join(_tmp(), "d23.zip")
    BD.write_archive(root, zip_path, verify=False)

    tar_path = os.path.join(_tmp(), "d23.tar")
    with tarfile.open(tar_path, "w") as tf:
        for name in [BD.MANIFEST_NAME] + \
                [e["path"] for e in declared["members"]]:
            tf.add(os.path.join(root, name.replace("/", os.sep)), arcname=name)

    from_zip = BD.extract_archive(zip_path, os.path.join(_tmp(), "d23-z"))
    from_tar = BD.extract_archive(tar_path, os.path.join(_tmp(), "d23-t"))
    a = BD.verify_bundle(from_zip, environment=False)["bundle_id"]
    b = BD.verify_bundle(from_tar, environment=False)["bundle_id"]
    assert a == b == declared["bundle_id"], (a, b)


def test_d24_canonical_zip_builds_are_byte_identical():
    """Worth having even though it is not the identity: a reproducible archive
    is what lets a mirror prove it forwarded the bytes it received."""
    root, _ = _synth("d24")
    one = BD.write_archive(root, os.path.join(_tmp(), "d24-a.zip"), verify=False)
    two = BD.write_archive(root, os.path.join(_tmp(), "d24-b.zip"), verify=False)
    assert one["archive_sha256"] == two["archive_sha256"]
    assert open(os.path.join(_tmp(), "d24-a.zip"), "rb").read() == \
        open(os.path.join(_tmp(), "d24-b.zip"), "rb").read()


def test_d25_the_archive_sha256_stays_distinct_from_the_bundle():
    """Two different claims, reported under two different names. Collapsing
    them is the exact confusion this rung exists to prevent."""
    root, _ = _synth("d25")
    report = BD.write_archive(root, os.path.join(_tmp(), "d25.zip"), verify=False)
    assert report["bundle_id"].startswith("bundle-")
    assert not report["archive_sha256"].startswith("bundle-")
    assert report["archive_sha256"] != report["bundle_id"].split("-", 1)[1]
    assert set(report) == {"bundle_id", "archive", "archive_sha256", "entries",
                           "roundtrip_verified"}

    # The same package in two different archives: one bundle, two checksums.
    other = _noncanonical_zip(root, os.path.join(_tmp(), "d25-b.zip"))
    assert BD.archive_sha256(other) != report["archive_sha256"]
    dest = BD.extract_archive(other, os.path.join(_tmp(), "d25-x"))
    assert BD.verify_bundle(dest, environment=False)["bundle_id"] == \
        report["bundle_id"]


def test_d26_the_packet_sha256_is_never_interpreted_as_a_bundle():
    """`accept_packet.py` gates a *source release* for a reviewer; a bundle is
    a domain artifact a TRAAVIIS consumer runs. They may share canonical-tree
    utilities, but they must keep different schemas and different claims."""
    import tools.build_packet as BP  # noqa: E402

    assert BP.MANIFEST_VERSION != BD.BUNDLE_VERSION
    assert BP.MANIFEST_NAME != BD.MANIFEST_NAME

    for module in ("tools/build_packet.py", "tools/accept_packet.py"):
        with open(os.path.join(REPO, module)) as fh:
            source = fh.read()
        assert '"bundle-' not in source and "'bundle-" not in source, module
        assert "bundle_id" not in source, module


# --- D27-D30: nothing downstream learns about bundles ------------------------

def _episode_ids(package, output_name):
    """Run one deterministic agent over `package`, returning its episode ids."""
    from traaviis import batch as _batch, wiring

    eng = _engine.try_load()
    if eng is None:
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    output = os.path.join(_tmp(), output_name)
    shutil.rmtree(output, ignore_errors=True)
    candidates = {
        "candidate_set_version": _batch.CANDIDATE_SET_VERSION,
        "candidates": [{"candidate_key": "repair", "argv": AGENT + ["ok"]},
                       {"candidate_key": "nofix", "argv": AGENT + ["nofix"]}],
    }
    report = _batch.run_batch(package, "all", candidates, output,
                              registry=wiring.default_registry(eng))
    return report, output


def _walk(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + (k,), k, v
            for item in _walk(v, path + (k,)):
                yield item
    elif isinstance(node, list):
        for i, v in enumerate(node):
            for item in _walk(v, path + (i,)):
                yield item


def test_d27_evaluation_and_batch_reports_gain_no_bundle_identity():
    """A presentation-only package change must not rewrite an experiment
    report. The cleanest way to guarantee that is for the reports not to carry
    a bundle identity at all.

    The law is about *identity*, not about a word. `EvaluationV1` has carried a
    field literally named `bundle` since the Episode Evidence Closure, and it
    names the episode evidence directory an episode was kept in -- an
    `episode-…` path component, minted by `episode_bundle`, with no relation to
    the package rung. So this asserts the two claims that actually matter: no
    document anywhere mints or references a `bundle-…`, and no document carries
    a `bundle_id`. Then it pins the collision shut from the other side, by
    requiring every surviving `bundle` field to hold an episode directory --
    which is what stops the older field from ever being *read* as the new
    identity."""
    report, output = _episode_ids(_fixture()["pkg"], "d27-out")

    documents = {"batch.json": report}
    for dirpath, _dirs, names in os.walk(output):
        for n in names:
            if not n.endswith(".json"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, n), output)
            with open(os.path.join(dirpath, n)) as fh:
                documents[rel] = json.load(fh)
    assert len(documents) > 3, sorted(documents)

    seen_episode_dir = False
    for name, doc in documents.items():
        for keypath, key, value in _walk(doc):
            assert key != "bundle_id", \
                "%s carries a bundle_id at %s" % (name, keypath)
            if isinstance(value, str):
                assert not value.startswith("bundle-"), \
                    "%s carries a bundle- id at %s" % (name, keypath)
            if key == "bundle" and value is not None:
                # The pre-existing field. It must still be an episode evidence
                # directory, so no reader can mistake it for the package rung.
                assert isinstance(value, str) and value.startswith("episode-"), \
                    "%s: `bundle` at %s is not an episode directory: %r" % (
                        name, keypath, value)
                seen_episode_dir = True
    assert seen_episode_dir, "no episode was kept, so the collision went untested"
    assert report["env_id"] == _fixture()["report"]["env_id"]


def test_d28_presentation_only_edits_move_no_episode_identity():
    """The law the whole rung is for. Re-describe a release, ship a new
    screenshot, rewrite the README -- and every episode already recorded
    against it stays exactly as valid, because none of it entered `env-…`."""
    f = _fixture()

    def presentation(env):
        with open(os.path.join(env, "README.md"), "a") as fh:
            fh.write("\n## Release notes\n\nA new section.\n")
        os.makedirs(os.path.join(env, "screenshots"), exist_ok=True)
        with open(os.path.join(env, "screenshots", "overview.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\nnew art")
        _edit_env(env, lambda d: (
            d.__setitem__("name", "residency-repair (v2 packaging)"),
            d.__setitem__("distribution", {
                "entrypoint": "README.md",
                "documentation": ["README.md"],
                "screenshots": ["screenshots/overview.png"]})))

    _, repackaged, second = _pack("d28", presentation)
    assert second["bundle_id"] != f["report"]["bundle_id"], \
        "the presentation edit did not reach the package"
    assert second["env_id"] == f["report"]["env_id"]

    before, _ = _episode_ids(f["pkg"], "d28-before")
    after, _ = _episode_ids(repackaged, "d28-after")

    for candidate in ("repair", "nofix"):
        was = next(c for c in before["candidates"]
                   if c["candidate_key"] == candidate)["episode_ids"]
        now = next(c for c in after["candidates"]
                   if c["candidate_key"] == candidate)["episode_ids"]
        assert was == now, (candidate, was, now)
    assert before["task_ids"] == after["task_ids"]


def test_d29_the_serial_batch_and_comparison_batteries_remain_green():
    """This slice touched `pack`, so it touched what every batch runs over.
    Re-asserted structurally here; the full batteries run in the tree battery."""
    import inspect
    from traaviis import batch as _batch, comparison as C, evalsplit as ES

    for module in (_batch, C, ES):
        source = inspect.getsource(module)
        assert "bundle_id" not in source, \
            "%s learned about bundle identity" % module.__name__

    params = inspect.signature(C.compare_episodes).parameters
    assert set(params) == {"left_dir", "right_dir", "registry",
                           "extra_verifiers"}, sorted(params)
    assert "SerialBatchV1" not in inspect.getsource(BD)


def test_d30_the_cli_surface_is_complete_and_typed():
    """The command set the ruling named, end to end, through the real CLI: a
    package verifies, an archive of it verifies, and a tampered package is a
    typed disagreement (exit 1) rather than a crash."""
    f = _fixture()

    def run(*argv):
        return subprocess.run([sys.executable, "-m", "traaviis.cli", *argv],
                              cwd=REPO, capture_output=True, text=True)

    p = run("verify-bundle", f["pkg"])
    assert p.returncode == 0, (p.returncode, p.stderr[-800:])
    assert f["report"]["bundle_id"] in p.stdout

    archive = os.path.join(_tmp(), "d30.zip")
    p = run("archive-bundle", f["pkg"], archive, "--json")
    assert p.returncode == 0, (p.returncode, p.stderr[-800:])
    doc = json.loads(p.stdout)
    assert doc["bundle_id"] == f["report"]["bundle_id"]
    assert doc["archive_sha256"] == BD.archive_sha256(archive)

    p = run("verify-bundle", archive, "--json")
    assert p.returncode == 0, (p.returncode, p.stderr[-800:])
    assert json.loads(p.stdout)["bundle_id"] == f["report"]["bundle_id"]

    broken = _copy("d30")
    with open(os.path.join(broken, "EXTRA.md"), "w") as fh:
        fh.write("appended\n")
    p = run("verify-bundle", broken)
    assert p.returncode == 1, (p.returncode, p.stdout, p.stderr)
    assert "BUNDLE_MEMBER_EXTRA" in p.stderr, p.stderr

    p = run("verify-bundle", os.path.join(_tmp(), "does-not-exist"))
    assert p.returncode == 2, (p.returncode, p.stderr)


# --- D31-D40: portable subject-mode closure (§5c) ----------------------------
#
# `bundle-…` carries the canonical mode; `snap-…` seals the exact one. Both are
# right and together they were lethal: a `0664` subject file sealed one `snap-…`
# before transport and a different one after, so the package verified as a
# package and failed to reopen as an environment. These laws close that by
# refusing the nonportable mode at admission, and by refusing to *publish* an
# archive that has not been extracted and re-verified from its own bytes.

_SUBJECT_FILE = os.path.join("subject", "src", "mod.py")


def _chmod(root, relpath, mode):
    path = os.path.join(root, relpath)
    os.chmod(path, mode)
    return path


def _snapshot_def(env, mutate):
    path = os.path.join(env, "snapshot_def.json")
    with open(path) as fh:
        doc = json.load(fh)
    mutate(doc)
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2)


def test_d31_a_noncanonical_residency_subject_mode_is_refused():
    """The authoring half. A `0664` subject file is not a cosmetic difference:
    it is a package that cannot survive its own archive, and the only place to
    say so is before `env-…` and `bundle-…` are reported as packed."""
    env = _scaffold("d31", lambda e: _chmod(e, _SUBJECT_FILE, 0o664))
    out = os.path.join(_tmp(), "pkg-d31")
    shutil.rmtree(out, ignore_errors=True)
    ex = _refuses(lambda: P.pack(env, out, engine=_engine_or_skip()),
                  "SUBJECT_MODE_NONCANONICAL", "a 0664 subject file")
    assert ex.detail["paths"]["src/mod.py"] == \
        {"observed": "0664", "required": "0644"}, ex.detail
    assert not os.path.exists(out), "a refused pack still published a package"

    # The reopening half, at the seam `reopen_package` actually uses. A package
    # sealed elsewhere -- hand-built, or authored before this law -- is
    # self-consistent and still nonportable, so it must be refused on the way
    # *in* as well as on the way out.
    from traaviis import snapshot as _snap, substrates
    pkg = _copy("d31-reopen")
    _chmod(pkg, _SUBJECT_FILE, 0o664)
    subject_root = os.path.join(pkg, "subject")
    with open(os.path.join(pkg, "snapshot.json")) as fh:
        old = json.load(fh)
    resealed = _snap.build_snapshot(
        subject_root,
        exclusions=old.get("exclusions") or (),
        binary_paths=old.get("binary_paths") or (),
        base_revision=old.get("base_revision"),
        visible_config=old.get("visible_config") or {})
    assert resealed["file_modes"]["src/mod.py"] == "0664"
    assert resealed["snapshot_id"] != old["snapshot_id"], \
        "the mode did not reach snap-, so the defect being closed is not real"
    with open(os.path.join(pkg, "snapshot.json"), "w") as fh:
        json.dump(resealed, fh, indent=2)

    sub = substrates.SUBSTRATES["residency.repository.v1"]
    manifest = {"subject": {"snapshot_id": resealed["snapshot_id"],
                            "snapshot": "snapshot.json", "root": "subject"}}
    ex = _refuses(lambda: sub._reopen_subject(manifest, pkg, None),
                  "SUBJECT_MODE_NONCANONICAL", "a self-consistent 0664 seal")
    assert ex.detail["paths"]["src/mod.py"]["observed"] == "0664"

    # The law would be unusable if `trvs init` could not satisfy it. A scaffold
    # written under a loose umask must still pack, so templates are written at
    # an explicit mode rather than at whatever the machine happened to be set
    # to -- otherwise every author on `umask 002` would be refused by default.
    old = os.umask(0o002)
    try:
        loose = _scaffold("d31-umask")
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.lstat(os.path.join(loose, _SUBJECT_FILE)).st_mode) \
        == 0o644, "init emitted a subject its own packer would refuse"
    out = os.path.join(_tmp(), "pkg-d31-umask")
    shutil.rmtree(out, ignore_errors=True)
    assert P.pack(loose, out, engine=_engine_or_skip())["env_id"] == \
        _fixture()["report"]["env_id"]


def test_d32_every_other_noncanonical_mode_is_refused_too():
    """`0644` and `0755` are the whole admissible set, so the law is stated as
    an allowlist rather than as a list of known-bad modes. Proven against real
    sealed snapshots -- including set-ID bits, which a snapshot renders as a
    four-digit mode and an archive cannot carry at all.

    Engine-free on purpose: this is a statement about a directory of files, and
    it should keep running on a machine that has no Forge checkout."""
    from traaviis import snapshot as _snap
    from traaviis.substrates import (CANONICAL_SUBJECT_MODES,
                                     canonical_subject_mode,
                                     require_canonical_subject_modes)

    assert CANONICAL_SUBJECT_MODES == ("0644", "0755")

    root = os.path.join(_tmp(), "d32-subject")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    for name in ("a.py", "b.sh", "c.md"):
        with open(os.path.join(root, name), "w") as fh:
            fh.write("x\n")

    for mode, required in ((0o600, "0644"), (0o640, "0644"), (0o664, "0644"),
                           (0o775, "0755"), (0o4755, "0755"), (0o2755, "0755"),
                           (0o1644, "0644"), (0o444, "0644"), (0o700, "0755")):
        os.chmod(os.path.join(root, "a.py"), mode)
        os.chmod(os.path.join(root, "b.sh"), 0o644)
        os.chmod(os.path.join(root, "c.md"), 0o644)
        snap = _snap.build_snapshot(root)
        observed = snap["file_modes"]["a.py"]
        if observed in CANONICAL_SUBJECT_MODES:
            # The host refused to set the bit (some filesystems drop set-ID on
            # a plain file). Nothing to prove here, and nothing to pretend.
            continue
        ex = _refuses(lambda: require_canonical_subject_modes(snap["file_modes"],
                                                             "test"),
                      "SUBJECT_MODE_NONCANONICAL", "mode %04o" % mode)
        assert ex.detail["paths"] == {
            "a.py": {"observed": observed, "required": required}}, ex.detail
        assert canonical_subject_mode(observed) == required

    # And the allowlist admits: a tree that is already portable passes silently.
    for name, mode in (("a.py", 0o644), ("b.sh", 0o755), ("c.md", 0o644)):
        os.chmod(os.path.join(root, name), mode)
    snap = _snap.build_snapshot(root)
    assert require_canonical_subject_modes(snap["file_modes"], "test") is \
        snap["file_modes"]


def test_d33_canonical_0644_subjects_survive_directory_zip_extract_verify():
    """The claim the whole slice exists to make true, end to end: pack, archive,
    extract on another machine, and re-derive *both* identities from the
    extracted bytes -- not from the directory that produced them."""
    f = _fixture()
    archive = os.path.join(_tmp(), "d33.zip")
    report = BD.write_archive(f["pkg"], archive, engine=_engine_or_skip())
    assert report["roundtrip_verified"] is True

    dest = BD.extract_archive(archive, os.path.join(_tmp(), "d33-x"))
    got = BD.verify_bundle(dest, engine=_engine_or_skip())
    assert got["bundle_id"] == f["report"]["bundle_id"]
    assert got["env_id"] == f["report"]["env_id"]
    assert got["environment_verified"] is True
    assert got["subject"]["snapshot_id"] == \
        BD.verify_bundle(f["pkg"], engine=_engine_or_skip())["subject"]["snapshot_id"]


def test_d34_canonical_0755_subjects_survive_the_same_path():
    """The executable bit is the one mode bit that must cross transport intact,
    and it is also the one that enters `snap-…`. A subject helper that arrives
    non-executable is a different repository, so this is the mode the law has to
    carry rather than normalize."""
    def make_executable(env):
        _chmod(env, _SUBJECT_FILE, 0o755)
        _snapshot_def(env, lambda d: d["file_modes"].__setitem__(
            "src/mod.py", "0755"))

    env, out, report = _pack("d34", make_executable)
    with open(os.path.join(out, "snapshot.json")) as fh:
        assert json.load(fh)["file_modes"]["src/mod.py"] == "0755"
    assert os.lstat(os.path.join(out, _SUBJECT_FILE)).st_mode & 0o111

    archive = os.path.join(_tmp(), "d34.zip")
    BD.write_archive(out, archive, engine=_engine_or_skip())
    dest = BD.extract_archive(archive, os.path.join(_tmp(), "d34-x"))
    assert os.lstat(os.path.join(dest, _SUBJECT_FILE)).st_mode & 0o111, \
        "the executable bit did not survive the archive"

    got = BD.verify_bundle(dest, engine=_engine_or_skip())
    assert got["bundle_id"] == report["bundle_id"]
    assert got["env_id"] == report["env_id"]
    assert got["environment_verified"] is True
    # It really is a different environment than the 0644 fixture: the mode is
    # inside `snap-…`, so this law is not quietly asserting nothing.
    assert got["env_id"] != _fixture()["report"]["env_id"]


def test_d35_excluded_files_with_noncanonical_modes_do_not_affect_admission():
    """The law is scoped to the modes that actually enter `snap-…`. An excluded
    file is not sealed, so its mode cannot move the identity and must not be
    able to refuse the package either. Enforced structurally -- the check reads
    `SnapshotV1.file_modes`, which is already post-exclusion -- rather than by a
    second glob match that could drift away from the first."""
    def junk_at(mode):
        def mutate(env):
            junk = os.path.join(env, "subject", "build", "artifact.o")
            os.makedirs(os.path.dirname(junk), exist_ok=True)
            with open(junk, "wb") as fh:
                fh.write(b"\x00not source\n")
            os.chmod(junk, mode)
            _snapshot_def(env, lambda d: (
                d.__setitem__("include",
                              sorted(d["include"] + ["build/artifact.o"])),
                d.__setitem__("exclusions", ["build/*"])))
        return mutate

    # `0600` would be refused outright if the law were scoped to the tree rather
    # than to the seal. It packs, because the file never enters `file_modes`.
    _env, out, report = _pack("d35", junk_at(0o600))
    with open(os.path.join(out, "snapshot.json")) as fh:
        snap = json.load(fh)
    assert "build/artifact.o" not in snap["file_modes"], \
        "the excluded file was sealed after all, so the law proves nothing"
    assert "build/artifact.o" not in snap["files"]
    assert snap["snapshot_id"] == \
        BD.verify_bundle(out, engine=_engine_or_skip())["subject"]["snapshot_id"]
    # It is still a *shipped* file, so it is inside `bundle-…` -- excluded from
    # the seal is not the same as absent from the package.
    assert any(e["path"] == "subject/build/artifact.o"
               for e in _manifest(out)["members"])

    # The same tree with the excluded file at a *different* noncanonical mode:
    # admitted again, and neither identity moved. (`0640` canonicalizes to the
    # same `0644`, so the package is unchanged too.)
    _env2, out2, again = _pack("d35b", junk_at(0o640))
    assert again["env_id"] == report["env_id"]
    assert again["bundle_id"] == report["bundle_id"]
    with open(os.path.join(out2, "snapshot.json")) as fh:
        assert json.load(fh)["snapshot_id"] == snap["snapshot_id"]

    # Whereas the *included* sibling at 0640 is refused, which is what makes the
    # exclusion above a real scope boundary rather than a hole in the law.
    def included_junk(env):
        junk_at(0o600)(env)
        _chmod(env, _SUBJECT_FILE, 0o640)

    env3 = _scaffold("d35c", included_junk)
    out3 = os.path.join(_tmp(), "pkg-d35c")
    shutil.rmtree(out3, ignore_errors=True)
    ex = _refuses(lambda: P.pack(env3, out3, engine=_engine_or_skip()),
                  "SUBJECT_MODE_NONCANONICAL", "an included 0640 subject file")
    assert sorted(ex.detail["paths"]) == ["src/mod.py"], ex.detail


def test_d36_presentation_modes_may_normalize_without_moving_the_environment():
    """The mirror image of D31. A `0664` README is not a nonportable package:
    presentation members are outside `snap-…` entirely, so the archive is free
    to normalize the mode, and normalizing it moves neither identity."""
    env, out, report = _pack("d36", lambda e: _chmod(e, "README.md", 0o664))
    assert report["env_id"] == _fixture()["report"]["env_id"]

    entry = next(e for e in _manifest(out)["members"] if e["path"] == "README.md")
    assert entry["mode"] == BD.MODE_FILE, entry
    assert stat.S_IMODE(os.lstat(os.path.join(out, "README.md")).st_mode) == 0o664, \
        "pack stopped writing the exact source mode"

    archive = os.path.join(_tmp(), "d36.zip")
    BD.write_archive(out, archive, engine=_engine_or_skip())
    dest = BD.extract_archive(archive, os.path.join(_tmp(), "d36-x"))
    assert stat.S_IMODE(os.lstat(os.path.join(dest, "README.md")).st_mode) == 0o644

    got = BD.verify_bundle(dest, engine=_engine_or_skip())
    assert got["bundle_id"] == report["bundle_id"], \
        "normalizing a presentation mode moved the package"
    assert got["env_id"] == report["env_id"]
    assert got["environment_verified"] is True


def test_d37_archive_publication_verifies_the_extracted_archive_before_rename():
    """Observed at the seam. Verifying the source directory and then serializing
    it proves the directory, which is not the artifact anyone receives -- so
    what must be verified, before publication, is an *extraction* of the archive
    that is about to be published."""
    root, declared = _synth("d37")
    output = os.path.join(_tmp(), "d37.zip")
    if os.path.exists(output):
        os.unlink(output)

    seen = []
    real_verify, real_replace = BD.verify_bundle, os.replace

    def spy_verify(target, **kw):
        report = real_verify(target, **kw)
        seen.append(("verify", os.path.abspath(target), report["bundle_id"]))
        return report

    def spy_replace(src, dst, *a, **kw):
        if dst == output:
            seen.append(("publish", os.path.exists(dst), None))
        return real_replace(src, dst, *a, **kw)

    BD.verify_bundle, os.replace = spy_verify, spy_replace
    try:
        report = BD.write_archive(root, output, verify=False)
    finally:
        BD.verify_bundle, os.replace = real_verify, real_replace

    kinds = [s[0] for s in seen]
    assert "publish" in kinds, "the archive was never published"
    before = seen[:kinds.index("publish")]
    assert before, "the archive was published without being verified at all"
    for _kind, target, bundle in before:
        assert target != os.path.abspath(root), \
            "publication was gated on the source tree, not on the archive"
        assert bundle == declared["bundle_id"], (target, bundle)
    assert seen[kinds.index("publish")][1] is False, \
        "the destination already existed at the moment of publication"
    assert report["roundtrip_verified"] is True
    assert BD.archive_sha256(output) == report["archive_sha256"]


def test_d38_a_failed_archive_roundtrip_leaves_no_final_archive():
    """Both ways the round trip can fail. An archive that reached the published
    path and then turned out not to carry its package would be worse than no
    archive, because it would look like one."""
    root, declared = _synth("d38")
    output = os.path.join(_tmp(), "d38.zip")
    if os.path.exists(output):
        os.unlink(output)
    real = BD.verify_bundle

    def boom(*_a, **_kw):
        raise BD.BundleError("BUNDLE_MEMBER_HASH", "simulated transport damage")

    BD.verify_bundle = boom
    try:
        _refuses(lambda: BD.write_archive(root, output, verify=False),
                 "BUNDLE_MEMBER_HASH", "an extraction that does not verify")
    finally:
        BD.verify_bundle = real
    assert not os.path.exists(output)

    # And the identity comparison, which is the check that would catch a
    # *silent* normalization: the extraction verifies fine, as some other
    # package.
    def other(*_a, **_kw):
        return dict(declared, bundle_id="bundle-" + "0" * 64,
                    env_id=declared["env_id"])

    BD.verify_bundle = other
    try:
        ex = _refuses(lambda: BD.write_archive(root, output, verify=False),
                      "BUNDLE_ARCHIVE_ROUNDTRIP", "an archive that moved bundle-")
    finally:
        BD.verify_bundle = real
    assert ex.detail["field"] == "bundle_id"
    assert ex.detail["directory"] == declared["bundle_id"]
    assert not os.path.exists(output)

    leftovers = sorted(n for n in os.listdir(_tmp())
                       if n.startswith(".trvs-archive-"))
    assert leftovers == [], leftovers

    # The happy path still publishes, so the two refusals above are not simply
    # a broken writer.
    assert BD.write_archive(root, output, verify=False)["bundle_id"] == \
        declared["bundle_id"]
    assert os.path.isfile(output)


def test_d39_zip_and_tar_full_environment_verification_agree():
    """D23 proved the two containers agree about the *package*. Now that a
    subject's modes are admitted as portable, they must also agree about the
    *environment* -- which is the claim that was false before this slice."""
    f = _fixture()
    eng = _engine_or_skip()

    zip_path = os.path.join(_tmp(), "d39.zip")
    BD.write_archive(f["pkg"], zip_path, engine=eng)

    tar_path = os.path.join(_tmp(), "d39.tar")
    names = [BD.MANIFEST_NAME] + [e["path"] for e in _manifest(f["pkg"])["members"]]
    with tarfile.open(tar_path, "w") as tf:
        for name in names:
            tf.add(os.path.join(f["pkg"], name.replace("/", os.sep)), arcname=name)

    from_zip = BD.extract_archive(zip_path, os.path.join(_tmp(), "d39-z"))
    from_tar = BD.extract_archive(tar_path, os.path.join(_tmp(), "d39-t"))
    a = BD.verify_bundle(from_zip, engine=eng)
    b = BD.verify_bundle(from_tar, engine=eng)

    assert a["bundle_id"] == b["bundle_id"] == f["report"]["bundle_id"]
    assert a["env_id"] == b["env_id"] == f["report"]["env_id"]
    assert a["subject"] == b["subject"]
    assert a["environment_verified"] is b["environment_verified"] is True


def test_d40_the_earlier_laws_and_the_packet_gates_are_untouched():
    """A completeness check over this battery, not a re-run of it -- D1-D30 are
    re-run by the battery itself, in this process, every time. What is asserted
    here is that adding the mode law did not quietly drop, rename or narrow one
    of them, and did not leak into the unrelated source-release gate."""
    laws = sorted(k for k in globals()
                  if k.startswith("test_d") and callable(globals()[k]))
    numbers = sorted(int(k.split("_")[1][1:]) for k in laws)
    assert numbers == list(range(1, 41)), numbers

    # The packet gate is a source-release gate for a reviewer. It knows nothing
    # about the package rung and nothing about substrate admission.
    for module in ("tools/build_packet.py", "tools/accept_packet.py"):
        with open(os.path.join(REPO, module)) as fh:
            source = fh.read()
        assert "bundle_id" not in source, module
        assert "SUBJECT_MODE_NONCANONICAL" not in source, module

    # And the law is genuinely substrate-specific: a world subject's Unix mode
    # does not enter `sem-…`, so `trvm.world.v1` must not inherit it.
    import inspect
    from traaviis import substrates
    world = inspect.getsource(substrates.TrvmWorldV1)
    assert "require_canonical_subject_modes" not in world
    residency = inspect.getsource(substrates.ResidencyRepositoryV1)
    assert residency.count("require_canonical_subject_modes") == 2, \
        "the mode law must be enforced at both admission seams"


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = skipped = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print("PASS %s" % t.__name__)
        except Skip as s:
            skipped += 1
            print("SKIP %s (%s)" % (t.__name__, s))
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (t.__name__, e))
    print("\n%d passed, %d skipped, %d failed" % (passed, skipped, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
