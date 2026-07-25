"""`trvs pack` -- close a scaffolded environment into a verified package (§6).

`init` scaffolds and derives nothing; `pack` is where identity is *earned*. The
ruling in RFC Artifacts §6 fixes the order, and this module follows it literally:

    1. validate_subject             the subject is well-formed for the profile
    2. recompute_subject_identity   derive snap-... / sem-... FROM THE BYTES
    3. bind + close                 reward -> rew-..., task -> task-... (bound to
                                    the recomputed subject and reward), manifest
                                    -> env-...
    4. verify_closure               every reference resolves inside the closure
    5. collect members              generated documents + declared presentation
    6. derive bundle-...            over the canonical member tree (§5b)
    7. stage                        write the whole tree into a temp sibling
    8. reopen                       re-derive bundle-..., env-..., the subject
                                    id and every reference FROM THE STAGED BYTES
    9. publish                      one atomic rename

Steps 1-6 happen **before** anything is written, and step 8 re-derives every id
from what actually landed on disk -- while it is still staged, so a package that
fails any law never reaches its destination at all. Nothing is reported as
packed because the in-memory computation happened to agree with itself.

Two identities come out, and they are different claims (§5b):

    env-...      the evaluation meaning: subject, tasks, rewards, splits, profiles
    bundle-...   the distributed package: that plus every shipped README, doc and
                 screenshot, by path, bytes and canonical mode

Rewriting a README moves `bundle-...` and must leave `env-...` exactly where it
was -- otherwise a documentation edit would silently claim to be a different
experiment.

Scaffold-level references (`reward_spec`, `snapshot_def`) are resolved into
content-addressed ones here: that substitution is the whole point of packing,
and it is why `init` was right not to invent them.
"""

import json
import os
import shutil
import stat
import tempfile

from . import bundle as _bundle, identity, substrates
from .paths import PathError, safe_relposix
from .substrates import AdmissionError

__all__ = ["pack", "PackError"]

EVAL_BUNDLE_VERSION = "traaviis.eval-bundle.v1"

#: Package paths that `pack` generates itself. A declared presentation asset may
#: not land on one of them -- the collision would be silently destructive in
#: whichever direction it was resolved.
_RESERVED = ("environment.json", "bundle.json", _bundle.MANIFEST_NAME)

#: The presentation-only lists `env.json` may declare. `entrypoint` names one of
#: the declared documentation members.
_DISTRIBUTION_LISTS = ("documentation", "screenshots", "assets")
_DISTRIBUTION_KEYS = ("entrypoint",) + _DISTRIBUTION_LISTS


class PackError(AdmissionError):
    """A packaging failure. Same typed shape as an admission failure."""


def _doc(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _read_json(path, code, what):
    if not os.path.isfile(path) or os.path.islink(path):
        raise PackError(code, "%s not found: %s" % (what, path))
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as ex:
        raise PackError(code, "%s is not valid JSON: %s" % (what, ex))


def _distinct_refs(refs, code, what):
    """Return `refs` unchanged, refusing a reference listed more than once.

    `env.json` declares *sets* of tasks and rewards. A repeated reference is not
    a set written twice, it is a malformed manifest -- so it is refused rather
    than silently deduplicated (pack never repairs a scaffold).
    """
    seen = set()
    for ref in refs:
        if ref in seen:
            raise PackError(code, "%s %r is listed more than once; the "
                                  "environment declares a set" % (what, ref))
        seen.add(ref)
    return list(refs)


def _claim(files, path, code, what):
    """Reserve `path` as a package member, refusing to overwrite another."""
    if path in files:
        raise PackError(
            code,
            "%s would be written to %r, which another package member already "
            "occupies; two documents cannot share one path" % (what, path))
    return path


def _read_member(src, code, what):
    """Read a source file as a package member: its bytes and its host mode.

    The mode is carried because it is not decoration. The Residency snapshot
    seals `file_modes`, so a package that wrote every member at the umask
    default would fail to reproduce its own subject the moment an author shipped
    an executable file -- a latent break that only stayed invisible because
    every scaffolded template happens to be 0644.
    """
    if os.path.islink(src):
        raise PackError(code, "%s is a symlink; v1 packages carry no links: %s"
                        % (what, src))
    if not os.path.isfile(src):
        raise PackError(code, "%s not found: %s" % (what, src))
    with open(src, "rb") as fh:
        data = fh.read()
    return data, stat.S_IMODE(os.lstat(src).st_mode)


def _distribution(manifest_src, env_dir, files, modes):
    """Admit and collect the presentation-only `distribution` block (§5b).

    Returns the block to emit in `environment.json`, or `None`. Everything here
    is *outside* `env-...` by the identity allowlist and *inside* `bundle-...`
    by being a member -- which is exactly the distinction the block exists to
    make usable. Reclassifying a file from documentation to screenshot changes
    this block, so it changes `environment.json`'s bytes, so it moves
    `bundle-...` even though not one shipped byte of the file itself changed.

    Every path is validated *before* anything is copied, so a typo in the last
    screenshot does not leave the first three staged.
    """
    dist = manifest_src.get("distribution")
    if dist is None:
        return None
    if not isinstance(dist, dict):
        raise PackError("DISTRIBUTION_MALFORMED",
                        "distribution must be an object, got %r" % type(dist).__name__)
    unknown = sorted(set(dist) - set(_DISTRIBUTION_KEYS))
    if unknown:
        raise PackError(
            "DISTRIBUTION_MALFORMED",
            "distribution declares unknown key(s) %s (known: %s)"
            % (", ".join(unknown), ", ".join(_DISTRIBUTION_KEYS)))

    block, collected, seen = {}, [], {}
    for key in _DISTRIBUTION_LISTS:
        refs = dist.get(key)
        if refs is None:
            continue
        if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
            raise PackError("DISTRIBUTION_MALFORMED",
                            "distribution.%s must be a list of paths" % key)
        out = []
        for ref in refs:
            try:
                norm = safe_relposix(ref)
            except PathError as ex:
                raise PackError("DISTRIBUTION_PATH_UNSAFE",
                                "distribution.%s: %s" % (key, ex))
            if norm in _RESERVED:
                raise PackError(
                    "DISTRIBUTION_COLLISION",
                    "distribution.%s declares %r, which pack generates itself"
                    % (key, norm))
            if norm in seen:
                raise PackError(
                    "DISTRIBUTION_DUPLICATE",
                    "%r is declared twice (as %s and as %s); a package member "
                    "has one classification" % (norm, seen[norm], key))
            seen[norm] = key
            _claim(files, norm, "DISTRIBUTION_COLLISION",
                   "distribution.%s member %r" % (key, norm))
            data, mode = _read_member(
                os.path.join(env_dir, norm.replace("/", os.sep)),
                "DISTRIBUTION_MISSING", "distribution.%s member %r" % (key, norm))
            collected.append((norm, data, mode))
            out.append(norm)
        # A list of shipped files is a set of members; its declared order is not
        # a fact about the package, so it is canonicalized rather than sealed.
        block[key] = sorted(out)

    entry = dist.get("entrypoint")
    if entry is not None:
        if not isinstance(entry, str):
            raise PackError("DISTRIBUTION_MALFORMED",
                            "distribution.entrypoint must be a path")
        try:
            entry = safe_relposix(entry)
        except PathError as ex:
            raise PackError("DISTRIBUTION_PATH_UNSAFE",
                            "distribution.entrypoint: %s" % ex)
        if entry not in (block.get("documentation") or []):
            raise PackError(
                "DISTRIBUTION_ENTRYPOINT",
                "distribution.entrypoint %r is not one of the declared "
                "documentation members" % entry)
        block["entrypoint"] = entry

    for norm, data, mode in collected:
        files[norm] = data
        modes[norm] = mode
    return block or None


def _strip_ids(doc, *keys):
    """Refuse a source document that pre-declares an identity we recompute."""
    for k in keys:
        if k in doc:
            raise PackError(
                "SOURCE_PREBOUND",
                "source document declares %s; pack recomputes identity and will "
                "not accept a pre-bound one" % k)
    return dict(doc)


def pack(env_dir, dest, engine=None):
    """Close the environment in `env_dir` into a verified package at `dest`.

    Returns a report dict. Raises `PackError` / `AdmissionError` (both typed)
    on any broken law, having written nothing or having removed what it wrote.
    """
    env_dir = os.path.abspath(env_dir)
    dest = os.path.abspath(dest)

    manifest_src = _read_json(os.path.join(env_dir, "env.json"),
                              "ENV_MISSING", "env.json")
    if manifest_src.get("environment_version") != "traaviis.environment.v1":
        raise PackError(
            "ENV_VERSION",
            "unsupported environment_version %r (expected traaviis.environment.v1)"
            % manifest_src.get("environment_version"))
    if "env_id" in manifest_src:
        raise PackError("SOURCE_PREBOUND",
                        "env.json declares env_id; pack derives it")

    profile = manifest_src.get("substrate_profile")
    sub = substrates.for_profile(profile)

    # --- 0. preflight -------------------------------------------------------
    # Author errors that need nothing but the documents on disk, checked before
    # any work that needs the environment. See `_preflight`.
    _preflight(manifest_src, env_dir, dest)

    # --- 1. validate_subject ------------------------------------------------
    sub.validate_subject(manifest_src, env_dir)

    # --- 2. recompute_subject_identity --------------------------------------
    subject_identity = sub.recompute_subject_identity(manifest_src, env_dir,
                                                      engine=engine)
    subject_id = subject_identity[sub.subject_id_field]

    files = {}  # relative path -> bytes, the package built in memory first
    modes = {}  # relative path -> host mode, for the members that carry one
    subject_block = dict(manifest_src["subject"])
    subject_block[sub.subject_id_field] = subject_id

    if profile == "residency.repository.v1":
        snap = subject_identity["snapshot"]
        files["snapshot.json"] = _doc(snap)
        definition = sub._definition(manifest_src, env_dir)
        subject_root = definition.get("root", "subject")
        subject_block.pop("snapshot_def", None)
        subject_block["snapshot"] = "snapshot.json"
        subject_block["root"] = subject_root
        for rel in sorted(definition.get("include") or []):
            src = os.path.join(env_dir, subject_root, rel.replace("/", os.sep))
            member = subject_root + "/" + rel
            files[member], modes[member] = _read_member(
                src, "SUBJECT_MISSING", "subject file %r" % rel)
    else:
        world_ref = manifest_src["subject"]["path"]
        files[world_ref], modes[world_ref] = _read_member(
            os.path.join(env_dir, world_ref), "SUBJECT_MISSING", "world source")

    # --- 3. bind + close ----------------------------------------------------
    rewards, reward_by_path = [], {}
    artifacts = {"tasks": {}, "rewards": {}}
    reward_refs = _distinct_refs(manifest_src.get("rewards") or [],
                                 "REWARD_DUPLICATE_REF", "reward")
    for ref in reward_refs:
        doc = _strip_ids(_read_json(os.path.join(env_dir, ref),
                                    "REWARD_MISSING", "reward spec"), "reward_id")
        doc["reward_id"] = identity.reward_id(doc)
        if doc["reward_id"] in artifacts["rewards"]:
            # Two distinct source files that canonicalize to one reward. The set
            # has fewer members than the list, so the list was not a set.
            raise PackError(
                "REWARD_DUPLICATE_ID",
                "reward %r derives %s, which another listed reward already "
                "derives; two files are the same reward" % (ref, doc["reward_id"]))
        out_path = _claim(files, os.path.basename(ref), "MEMBER_COLLISION", "reward")
        files[out_path] = _doc(doc)
        rewards.append({"path": out_path, "reward_id": doc["reward_id"]})
        reward_by_path[ref] = doc["reward_id"]
        artifacts["rewards"][doc["reward_id"]] = doc

    tasks, by_ref = [], {}
    task_refs = _distinct_refs(manifest_src.get("tasks") or [],
                               "TASK_DUPLICATE_REF", "task")
    for ref in task_refs:
        doc = _strip_ids(_read_json(os.path.join(env_dir, ref),
                                    "TASK_MISSING", "task spec"), "task_id")
        reward_ref = doc.pop("reward_spec", None)
        if reward_ref is None:
            raise PackError("TASK_UNBOUND", "task declares no reward_spec")
        if reward_ref not in reward_by_path:
            raise PackError(
                "TASK_UNBOUND",
                "task references reward %r, which the environment does not list"
                % reward_ref)
        doc["reward_id"] = reward_by_path[reward_ref]
        subject_ref = dict(doc.get("subject") or {})
        subject_ref.pop("snapshot_def", None)
        subject_ref[sub.subject_id_field] = subject_id
        doc["subject"] = subject_ref
        doc = _bind_identity_policy(doc, env_dir, sub, manifest_src, engine)
        doc["task_id"] = identity.task_id(doc)
        if doc["task_id"] in artifacts["tasks"]:
            raise PackError(
                "TASK_DUPLICATE_ID",
                "task %r derives %s, which another listed task already derives; "
                "two files are the same task" % (ref, doc["task_id"]))
        out_path = _claim(files, os.path.basename(ref), "MEMBER_COLLISION", "task")
        files[out_path] = _doc(doc)
        tasks.append({"path": out_path, "task_id": doc["task_id"]})
        by_ref[ref] = doc["task_id"]
        artifacts["tasks"][doc["task_id"]] = doc

    # §5: an environment is a closed *set* of tasks and rewards, so the manifest
    # stores them in canonical (id-sorted) order. Listing the same tasks in a
    # different order in `env.json` is the same environment and must not move
    # `env-...`; only membership may. Sorting happens after `by_ref` is built so
    # split resolution stays keyed on the source reference, not on position.
    tasks.sort(key=lambda e: e["task_id"])
    rewards.sort(key=lambda e: e["reward_id"])

    # §3: a split is a *named set* of task ids, so it is canonicalized as a
    # sorted set -- listing the same tasks in a different order is the same
    # split and must not move `env-...`. A repeated member is not "a set written
    # twice", it is a malformed split, so it is refused rather than collapsed
    # (pack never repairs a scaffold).
    splits = {}
    for name, members in (manifest_src.get("splits") or {}).items():
        resolved = []
        for m in members:
            if m not in by_ref:
                raise PackError(
                    "SPLIT_UNRESOLVED",
                    "split %r names %r, which is not a task in this environment"
                    % (name, m))
            resolved.append(by_ref[m])
        if len(set(resolved)) != len(resolved):
            raise PackError(
                "SPLIT_DUPLICATE",
                "split %r names the same task more than once; a split is a set"
                % name)
        splits[name] = sorted(resolved)

    # --- 5. collect presentation members -----------------------------------
    # Declared *before* the manifest is built, because the manifest carries the
    # block; collected here so a bad path is refused before any id is derived.
    distribution = _distribution(manifest_src, env_dir, files, modes)

    manifest = {
        "environment_version": manifest_src["environment_version"],
        "substrate_profile": profile,
        # Presentation: carried, but excluded from env- by the §5 identity
        # allowlist, so renaming an environment never moves its identity.
        "name": manifest_src.get("name"),
        "description": manifest_src.get("description"),
        "distribution": distribution,
        "subject": subject_block,
        "tasks": tasks,
        "rewards": rewards,
        "profiles": manifest_src.get("profiles") or {},
        "splits": splits,
    }
    manifest["env_id"] = identity.environment_id(manifest)

    # --- 4. verify_closure (before a single byte is written) ----------------
    sub.verify_closure(manifest, artifacts)

    files["environment.json"] = _doc(manifest)

    # A single-task residency package is also a runnable eval-one bundle. The
    # manifest is operational (not content-addressed), so it is emitted only
    # when it would actually be true.
    if profile == "residency.repository.v1" and len(tasks) == 1:
        # The reward the single task actually binds -- not simply the first one
        # listed, which is only the same document when exactly one is declared.
        bound = artifacts["tasks"][tasks[0]["task_id"]]["reward_id"]
        reward_path = next(r["path"] for r in rewards if r["reward_id"] == bound)
        files["bundle.json"] = _doc({
            "eval_bundle_version": EVAL_BUNDLE_VERSION,
            "task": tasks[0]["path"],
            "reward": reward_path,
            "snapshot": "snapshot.json",
            "subject": subject_block["root"],
        })

    # --- 6. derive bundle- over the canonical member tree (§5b) -------------
    bundle_manifest = _bundle.build_manifest(
        manifest["env_id"],
        [(rel, files[rel], modes.get(rel, 0o644)) for rel in sorted(files)])
    files[_bundle.MANIFEST_NAME] = _doc(bundle_manifest)

    # --- 7/8/9. stage, reopen from the staged bytes, then publish -----------
    # Verification happens while the tree is still a temporary sibling, so a
    # package that breaks a law never occupies `dest` for even an instant. The
    # earlier order (publish, verify, delete on failure) reported the same
    # verdicts but briefly made a bad package visible at its final path.
    staged = _stage(files, modes, dest)
    try:
        reopened_bundle = _bundle.verify_bundle(staged, engine=engine,
                                                environment=False)
        if reopened_bundle["bundle_id"] != bundle_manifest["bundle_id"]:
            raise PackError(
                "REOPEN_BUNDLE_ID",
                "the staged package does not reopen to the derived bundle_id")
        reopened = sub.reopen_package(staged, engine=engine)
        if reopened.get("env_id") != manifest["env_id"]:
            raise PackError(
                "REOPEN_ENV_ID",
                "the written package does not reopen to the packed env_id")
        _publish(staged, dest)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise

    return {
        "env_id": manifest["env_id"],
        "bundle_id": bundle_manifest["bundle_id"],
        "bundle_manifest": _bundle.MANIFEST_NAME,
        "substrate_profile": profile,
        "subject": {sub.subject_id_field: subject_id},
        "tasks": tasks,
        "rewards": rewards,
        "distribution": distribution,
        "files": sorted(files),
        "directory": dest,
        "reopened": True,
        "runnable": "bundle.json" in files,
    }


def _bind_identity_policy(task, env_dir, sub, manifest_src, engine):
    """Bind `identity_policy.must_remain.*.before_id` by lowering the real file.

    The scaffold states *which* file must keep its identity but not *what* that
    identity is -- stating it would have been inventing one. Pack computes it
    from the bytes now, so the task can be checked later.
    """
    policy = task.get("identity_policy")
    if not isinstance(policy, dict):
        return task
    must = policy.get("must_remain") or {}
    if not must:
        return task
    root = "subject"
    if sub.profile == "residency.repository.v1":
        root = sub._definition(manifest_src, env_dir).get("root", "subject")
    bound = {}
    for name, pin in must.items():
        pin = dict(pin)
        if "before_id" in pin:
            raise PackError("SOURCE_PREBOUND",
                            "identity policy %r declares before_id; pack "
                            "recomputes it" % name)
        target = os.path.join(env_dir, root, pin["path"].replace("/", os.sep))
        if not os.path.isfile(target):
            raise PackError("POLICY_TARGET_MISSING",
                            "identity policy %r pins a missing path: %s"
                            % (name, pin["path"]))
        pin["before_id"] = substrates._lower_world(target, engine)
        bound[name] = pin
    task = dict(task)
    task["identity_policy"] = {**policy, "must_remain": bound}
    return task


def _check_destination(dest):
    """Refuse a destination that is not an empty directory (or absent)."""
    if os.path.exists(dest):
        if not os.path.isdir(dest):
            raise PackError("DEST_NOT_DIR",
                            "destination exists and is not a directory: %s" % dest)
        if os.listdir(dest):
            raise PackError("DEST_NOT_EMPTY",
                            "refusing to pack into a non-empty directory: %s" % dest)


def _preflight(manifest_src, env_dir, dest):
    """Everything an author can get wrong that needs no engine, checked first.

    `pack`'s order otherwise follows §6 literally -- validate the subject,
    recompute its identity, bind, close, write -- and identity recomputation may
    reach for the Forge engine. That put a whole class of *author* mistakes
    (a split naming a task that is not listed, a plan requiring a signal its
    reward never defines, a destination that already has files in it) behind an
    *environmental* prerequisite: on a machine with no engine on-path, a typo in
    `env.json` was reported as `ENGINE_UNAVAILABLE`. The message was true and the
    diagnosis was wrong, which is worse than either alone.

    None of these checks need the engine, the subject, or any derived id -- only
    the documents already on disk -- so they run before §6 step 1. `_write_tree`
    still re-checks the destination: a preflight is not a lock, and the answer
    can change between here and the write.
    """
    _check_destination(dest)

    listed = set(_distinct_refs(manifest_src.get("tasks") or [],
                                "TASK_DUPLICATE_REF", "task"))
    for name, members in (manifest_src.get("splits") or {}).items():
        for m in members:
            if m not in listed:
                raise PackError(
                    "SPLIT_UNRESOLVED",
                    "split %r names %r, which is not a task in this environment"
                    % (name, m))
        if len(set(members)) != len(members):
            raise PackError(
                "SPLIT_DUPLICATE",
                "split %r names the same task more than once; a split is a set"
                % name)

    # The plan/reward agreement is purely lexical -- which signal names a task
    # requires against which its reward defines -- so it is answerable here, from
    # the author's own two files, long before either has an id.
    signals_by_ref = {}
    for ref in _distinct_refs(manifest_src.get("rewards") or [],
                              "REWARD_DUPLICATE_REF", "reward"):
        doc = _read_json(os.path.join(env_dir, ref), "REWARD_MISSING",
                         "reward spec")
        signals_by_ref[ref] = set((doc.get("signals") or {}).keys())
    for ref in listed:
        doc = _read_json(os.path.join(env_dir, ref), "TASK_MISSING", "task spec")
        reward_ref = doc.get("reward_spec")
        if reward_ref not in signals_by_ref:
            # Left to the main pass, which owns the TASK_UNBOUND diagnosis.
            continue
        substrates.verify_task_is_answerable(doc, signals_by_ref[reward_ref])


def _stage(files, modes, dest):
    """Write the whole package into a temporary sibling of `dest`. No publish.

    Returns the staging path. The caller verifies *this* tree and only then
    publishes it, so nothing unverified ever appears at the destination.
    """
    _check_destination(dest)
    parent = os.path.dirname(dest) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".trvs-pack-", dir=parent)
    try:
        for rel in sorted(files):
            target = os.path.join(tmp, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(files[rel])
            if rel in modes:
                os.chmod(target, modes[rel])
        return tmp
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _publish(staged, dest):
    """One rename. The destination is re-checked: a preflight is not a lock."""
    _check_destination(dest)
    if os.path.isdir(dest):
        os.rmdir(dest)  # empty, checked above
    os.replace(staged, dest)
