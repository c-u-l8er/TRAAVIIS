"""The substrate admission interface (RFC Artifacts §5a) and its two profiles.

`init`, `pack` and import are written against a **profile-implemented**
interface, never against `world` verbs, so a second substrate is a new profile
rather than a new branch inside the packer:

    validate_subject             the sealed subject is well-formed for the profile
    verify_closure               every task / reward / verifier reference resolves
    recompute_subject_identity   re-derive the subject id from its bytes
    reopen_package               re-open the emitted artifact and re-verify all laws

Each substrate implements it differently (§5a):

    trvm.world.v1              re-lower source -> sem-...
    residency.repository.v1    recompute snapshot -> snap-...; verify task /
                               reward / verifier / run-policy closure

One admission law is deliberately **substrate-specific**: `residency.repository.v1`
refuses a subject file whose Unix mode is not already `0644` or `0755`
(`SUBJECT_MODE_NONCANONICAL`), because that substrate seals the raw mode into
`snap-...` and a distributed package carries only the canonical one.
`trvm.world.v1` does not inherit it -- a `.wrl` file's mode does not enter
`sem-...`, so there is nothing there for transport to break.

Everything here is **fail-closed**: an admission failure raises `AdmissionError`
with a typed code, never a warning and never a silent downgrade. Identity is
always *recomputed from bytes* -- a declared id is checked against the
recomputation, never trusted.
"""

import json
import os

from . import identity, snapshot as _snapshot
from .paths import PathError, safe_relposix

__all__ = [
    "AdmissionError",
    "SUBSTRATES",
    "for_profile",
    "verify_task_is_answerable",
    "CANONICAL_SUBJECT_MODES",
    "canonical_subject_mode",
    "require_canonical_subject_modes",
    "TrvmWorldV1",
    "ResidencyRepositoryV1",
]


class AdmissionError(Exception):
    """A typed admission failure: `code` names the law that was broken."""

    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def __str__(self):
        return "[%s] %s" % (self.code, self.message)


def _read_bytes(path, code, what):
    if not os.path.isfile(path) or os.path.islink(path):
        raise AdmissionError(code, "%s not found: %s" % (what, path))
    with open(path, "rb") as fh:
        return fh.read()


def verify_task_is_answerable(task, signals):
    """Can this task's verifier plan be answered by `signals`? Raise if not.

    Deliberately a free function taking the raw signal names rather than a method
    over derived artifacts: it depends on nothing but the two documents an author
    wrote, so it needs no snapshot, no derived ids, and above all **no engine**.
    That is what lets `pack` run it as a preflight -- an author who mistyped a
    signal name should be told *that*, not told the Forge engine is unavailable,
    which is a true statement about their machine and a useless one about their
    environment.
    """
    plan = task.get("verifier_plan") or {}
    required = set(plan.get("required") or [])
    missing = required - set(signals)
    if missing:
        raise AdmissionError(
            "CLOSURE_VERIFIER",
            "task requires signals its reward does not define: %s"
            % ", ".join(sorted(missing)))
    overlap = required & set(plan.get("not_applicable") or [])
    if overlap:
        raise AdmissionError(
            "CLOSURE_VERIFIER",
            "signals are both required and not-applicable: %s"
            % ", ".join(sorted(overlap)))
    run_policy = ((task.get("test_plan") or {}).get("run_policy") or {})
    if task.get("test_plan") is not None and not run_policy.get("runner_profile"):
        raise AdmissionError("CLOSURE_RUN_POLICY",
                             "test plan declares no runner_profile")


#: The only two file modes that survive canonical distribution. A bundle
#: archive carries the executable bit and nothing else (`bundle.canonical_mode`),
#: so any *other* mode is a mode the package cannot transport.
CANONICAL_SUBJECT_MODES = ("0644", "0755")


def canonical_subject_mode(mode):
    """The canonical mode a raw four-digit `mode` string would normalize to."""
    return "0755" if int(mode, 8) & 0o111 else "0644"


def require_canonical_subject_modes(file_modes, where):
    """Refuse a subject whose sealed modes cannot survive canonical transport.

    `SnapshotV1` seals the **raw** four-digit mode of every included file into
    `snap-...`, while a distributed package carries only the *canonical* mode.
    Those two facts are individually correct and jointly lethal: a subject file
    at `0664` seals one `snap-` on the authoring machine and a different one
    after any archive round trip, so the package verifies as a package and fails
    to reopen as an environment. The honest fix is neither to loosen `snap-` nor
    to widen the archive, but to refuse the nonportable mode *at admission*,
    which is what this does.

    `file_modes` is `SnapshotV1.file_modes` -- already post-exclusion and
    symlink-free -- so an excluded file with a noncanonical mode is out of scope
    here by construction rather than by a second, drifting glob match.
    """
    offenders = {
        rel: {"observed": mode, "required": canonical_subject_mode(mode)}
        for rel, mode in sorted((file_modes or {}).items())
        if mode not in CANONICAL_SUBJECT_MODES
    }
    if offenders:
        raise AdmissionError(
            "SUBJECT_MODE_NONCANONICAL",
            "%s: %d subject file(s) carry a file mode that cannot survive "
            "canonical distribution; a snapshot seals the exact mode, so such a "
            "package would reopen to a different snap- after any archive round "
            "trip. Set them to %s."
            % (where, len(offenders), " or ".join(CANONICAL_SUBJECT_MODES)),
            {"paths": offenders})
    return file_modes


def _canonical_sequence(entries, key, code, what):
    """Require `entries` to be a set in canonical (id-sorted) order."""
    ids = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get(key), str):
            raise AdmissionError(code, "%s entry declares no %s" % (what, key))
        ids.append(entry[key])
    if len(set(ids)) != len(ids):
        raise AdmissionError(
            code, "%s list names the same %s twice; it declares a set" % (what, key))
    if ids != sorted(ids):
        raise AdmissionError(
            code,
            "%s list is not in canonical %s order; an environment is a closed "
            "set and its members have exactly one written order" % (what, key))


def _verify_canonical_manifest(manifest):
    """Reject a manifest whose sets are unordered or repeated (see open_package)."""
    _canonical_sequence(manifest.get("tasks") or [], "task_id",
                        "MANIFEST_NONCANONICAL", "tasks")
    _canonical_sequence(manifest.get("rewards") or [], "reward_id",
                        "MANIFEST_NONCANONICAL", "rewards")
    for name, members in (manifest.get("splits") or {}).items():
        if len(set(members)) != len(members):
            raise AdmissionError(
                "MANIFEST_NONCANONICAL",
                "split %r names the same task twice; a split is a set" % name)
        if list(members) != sorted(members):
            raise AdmissionError(
                "MANIFEST_NONCANONICAL",
                "split %r is not in canonical task_id order; a split is a set"
                % name)


def _read_json(path, code, what):
    data = _read_bytes(path, code, what)
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as ex:
        raise AdmissionError(code, "%s is not valid JSON: %s" % (what, ex))


def _member(root, ref, code):
    """Resolve a manifest reference to a contained path, or refuse it."""
    if not isinstance(ref, str) or not ref:
        raise AdmissionError(code, "reference must be a non-empty path: %r" % (ref,))
    try:
        rel = safe_relposix(ref)
    except PathError as ex:
        raise AdmissionError(code, "reference escapes the environment: %s" % ex)
    target = os.path.join(root, rel.replace("/", os.sep))
    root_real = os.path.realpath(root)
    target_real = os.path.realpath(target)
    if target_real != root_real and not target_real.startswith(root_real + os.sep):
        raise AdmissionError(code, "reference escapes the environment root: %s" % ref)
    return target


class _Substrate(object):
    """Base: the four §5a operations, plus the closure checks shared by both."""

    profile = None
    subject_kind = None
    subject_id_field = None

    # -- validate_subject ----------------------------------------------------
    def validate_subject(self, env, root):
        subject = env.get("subject")
        if not isinstance(subject, dict):
            raise AdmissionError("SUBJECT_MALFORMED",
                                 "environment has no subject object")
        kind = subject.get("kind")
        if kind != self.subject_kind:
            raise AdmissionError(
                "SUBJECT_KIND",
                "subject kind %r is not admissible for %s (expected %r)"
                % (kind, self.profile, self.subject_kind))
        if self.subject_id_field in subject:
            raise AdmissionError(
                "SUBJECT_PREBOUND",
                "subject already declares %s; pack recomputes identity and will "
                "not accept a pre-bound one" % self.subject_id_field)

    # -- verify_closure ------------------------------------------------------
    def verify_closure(self, manifest, artifacts):
        """Every task / reward / subject reference resolves inside the closure."""
        subject = manifest.get("subject") or {}
        subject_id = subject.get(self.subject_id_field)
        if not subject_id:
            raise AdmissionError("CLOSURE_SUBJECT",
                                 "closed manifest has no subject identity")

        reward_ids = {r["reward_id"] for r in manifest.get("rewards", [])}
        task_ids = set()
        for entry in manifest.get("tasks", []):
            tid = entry.get("task_id")
            task_ids.add(tid)
            task = artifacts["tasks"].get(tid)
            if task is None:
                raise AdmissionError("CLOSURE_TASK",
                                     "task %s is referenced but not present" % tid)
            if task.get("reward_id") not in reward_ids:
                raise AdmissionError(
                    "CLOSURE_REWARD",
                    "task %s references reward %s, which is outside the closure"
                    % (tid, task.get("reward_id")))
            bound = (task.get("subject") or {}).get(self.subject_id_field)
            if bound != subject_id:
                raise AdmissionError(
                    "CLOSURE_SUBJECT_BINDING",
                    "task %s is bound to subject %s, not the environment's %s"
                    % (tid, bound, subject_id))
            self._verify_task_closure(task, artifacts, reward_ids)

        for split, members in (manifest.get("splits") or {}).items():
            for m in members:
                if m not in task_ids:
                    raise AdmissionError(
                        "CLOSURE_SPLIT",
                        "split %r names %s, which is not a task in this "
                        "environment" % (split, m))

    def _verify_task_closure(self, task, artifacts, reward_ids):
        pass

    # -- recompute_subject_identity -----------------------------------------
    def recompute_subject_identity(self, env, root, engine=None):
        raise NotImplementedError

    # -- reopen_package ------------------------------------------------------
    def reopen_package(self, path, engine=None):
        """Read the emitted package back off disk and re-verify every law.

        Nothing here trusts what `pack` computed in memory: ids are re-derived
        from the bytes that were actually written.
        """
        return self.open_package(path, engine=engine)[0]

    def open_package(self, path, engine=None):
        """`reopen_package` plus the closed artifacts it re-derived.

        A reader (`trvs eval`) needs the task and reward *documents*, not just
        the manifest -- and it must not re-read them through a second, weaker
        path. Reopening is the only door: what comes back has already had every
        id recomputed from the written bytes.
        """
        manifest = _read_json(os.path.join(path, "environment.json"),
                              "REOPEN_MANIFEST", "environment.json")
        # Canonical form is checked BEFORE identity. `pack` sorting its own
        # output is not enough: a hand-built package could list its tasks in any
        # order, compute `env-...` honestly over those bytes, and reopen as
        # self-consistent -- which would mean one environment had as many valid
        # identities as its task list has permutations. An environment is a
        # closed *set*, so a noncanonical manifest is inadmissible even when it
        # agrees with itself.
        _verify_canonical_manifest(manifest)
        declared_env = manifest.get("env_id")
        recomputed_env = identity.environment_id(manifest)
        if declared_env != recomputed_env:
            raise AdmissionError(
                "REOPEN_ENV_ID",
                "written environment.json does not re-derive to its env_id",
                {"declared": declared_env, "recomputed": recomputed_env})

        artifacts = {"tasks": {}, "rewards": {}}
        for entry in manifest.get("rewards", []):
            doc = _read_json(_member(path, entry["path"], "REOPEN_REWARD"),
                             "REOPEN_REWARD", "reward")
            got = identity.reward_id(doc)
            if got != entry["reward_id"] or doc.get("reward_id") != got:
                raise AdmissionError(
                    "REOPEN_REWARD_ID",
                    "written reward does not re-derive to its reward_id",
                    {"declared": entry["reward_id"], "recomputed": got})
            artifacts["rewards"][got] = doc
        for entry in manifest.get("tasks", []):
            doc = _read_json(_member(path, entry["path"], "REOPEN_TASK"),
                             "REOPEN_TASK", "task")
            got = identity.task_id(doc)
            if got != entry["task_id"] or doc.get("task_id") != got:
                raise AdmissionError(
                    "REOPEN_TASK_ID",
                    "written task does not re-derive to its task_id",
                    {"declared": entry["task_id"], "recomputed": got})
            artifacts["tasks"][got] = doc

        self._reopen_subject(manifest, path, engine)
        self.verify_closure(manifest, artifacts)
        return manifest, artifacts

    def _reopen_subject(self, manifest, path, engine):
        raise NotImplementedError


class TrvmWorldV1(_Substrate):
    """`trvm.world.v1` -- the subject is a WallRiderLang world source."""

    profile = "trvm.world.v1"
    subject_kind = "wrl_source"
    subject_id_field = "semantic_artifact_id"

    def validate_subject(self, env, root):
        _Substrate.validate_subject(self, env, root)
        subject = env["subject"]
        src = _read_bytes(_member(root, subject.get("path"), "SUBJECT_PATH"),
                          "SUBJECT_MISSING", "world source")
        if not src.strip():
            raise AdmissionError("SUBJECT_EMPTY", "world source is empty")
        head = src.decode("utf-8", "replace")
        if "profile " not in head:
            raise AdmissionError(
                "SUBJECT_NO_PROFILE",
                "world source declares no `profile` line; it cannot be lowered")

    def recompute_subject_identity(self, env, root, engine=None):
        path = _member(root, env["subject"]["path"], "SUBJECT_PATH")
        return {
            self.subject_id_field: _lower_world(path, engine),
            "path": env["subject"]["path"],
        }

    def _reopen_subject(self, manifest, path, engine):
        subject = manifest["subject"]
        declared = subject.get(self.subject_id_field)
        got = _lower_world(_member(path, subject["path"], "REOPEN_SUBJECT"), engine)
        if got != declared:
            raise AdmissionError(
                "REOPEN_SUBJECT_ID",
                "written world does not re-lower to its declared identity",
                {"declared": declared, "recomputed": got})


class ResidencyRepositoryV1(_Substrate):
    """`residency.repository.v1` -- the subject is a repository snapshot."""

    profile = "residency.repository.v1"
    subject_kind = "repository_snapshot"
    subject_id_field = "snapshot_id"

    def validate_subject(self, env, root):
        _Substrate.validate_subject(self, env, root)
        definition = self._definition(env, root)
        subject_root = _member(root, definition.get("root", "subject"),
                               "SUBJECT_ROOT")
        if not os.path.isdir(subject_root):
            raise AdmissionError("SUBJECT_MISSING",
                                 "subject root is not a directory: %s"
                                 % definition.get("root"))
        declared = set(definition.get("include") or [])
        if not declared:
            raise AdmissionError("SUBJECT_EMPTY",
                                 "snapshot definition includes no paths")
        actual = set()
        for dirpath, _dirs, names in os.walk(subject_root):
            for n in names:
                p = os.path.join(dirpath, n)
                actual.add(os.path.relpath(p, subject_root).replace(os.sep, "/"))
        if declared != actual:
            raise AdmissionError(
                "SUBJECT_DRIFT",
                "the snapshot definition and the subject tree disagree",
                {"declared_only": sorted(declared - actual),
                 "present_only": sorted(actual - declared)})

    def _definition(self, env, root):
        ref = (env.get("subject") or {}).get("snapshot_def")
        return _read_json(_member(root, ref, "SUBJECT_DEF"),
                          "SUBJECT_DEF", "snapshot definition")

    def recompute_subject_identity(self, env, root, engine=None):
        definition = self._definition(env, root)
        subject_root = _member(root, definition.get("root", "subject"),
                               "SUBJECT_ROOT")
        snap = _snapshot.build_snapshot(
            subject_root,
            exclusions=definition.get("exclusions") or (),
            binary_paths=definition.get("binary_paths") or (),
            base_revision=definition.get("base_revision"),
            visible_config=definition.get("visible_config") or {},
        )
        require_canonical_subject_modes(snap.get("file_modes"),
                                        "authoring this subject")
        return {self.subject_id_field: snap["snapshot_id"], "snapshot": snap}

    def _verify_task_closure(self, task, artifacts, reward_ids):
        """The verifier plan must be answerable by the reward it is bound to."""
        reward = artifacts["rewards"].get(task.get("reward_id")) or {}
        verify_task_is_answerable(task, set((reward.get("signals") or {}).keys()))

    def _reopen_subject(self, manifest, path, engine):
        subject = manifest["subject"]
        declared = subject.get(self.subject_id_field)
        snap = _read_json(_member(path, subject["snapshot"], "REOPEN_SUBJECT"),
                          "REOPEN_SUBJECT", "snapshot")
        if identity.snapshot_id(snap) != declared or snap.get("snapshot_id") != declared:
            raise AdmissionError(
                "REOPEN_SUBJECT_ID",
                "written snapshot does not re-derive to its snapshot_id",
                {"declared": declared, "recomputed": identity.snapshot_id(snap)})
        # Against the *declared* snapshot, and before the rebuilt bytes are
        # compared. A package sealed with a noncanonical mode -- hand-built, or
        # authored before this law -- must be diagnosed as nonportable, which is
        # true and actionable, rather than as REOPEN_SUBJECT_BYTES, which would
        # blame the tree for a defect in the seal.
        require_canonical_subject_modes(snap.get("file_modes"),
                                        "reopening this package")
        subject_root = _member(path, subject["root"], "REOPEN_SUBJECT")
        rebuilt = _snapshot.build_snapshot(
            subject_root,
            exclusions=snap.get("exclusions") or (),
            binary_paths=snap.get("binary_paths") or (),
            base_revision=snap.get("base_revision"),
            visible_config=snap.get("visible_config") or {},
        )
        if rebuilt["snapshot_id"] != declared:
            raise AdmissionError(
                "REOPEN_SUBJECT_BYTES",
                "the written subject tree does not reproduce the snapshot",
                {"declared": declared, "recomputed": rebuilt["snapshot_id"]})


def _lower_world(path, engine):
    """Re-lower a WRL source to its `sem-...` through the engine boundary."""
    if engine is None:
        raise AdmissionError(
            "ENGINE_UNAVAILABLE",
            "this subject must be lowered by the Forge engine, which is not "
            "available; set TRVS_FORGE_DIR")
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    payload = engine.lower_source(src)
    if not payload.get("ok"):
        raise AdmissionError("SUBJECT_UNLOWERABLE",
                             payload.get("error") or "world could not be lowered",
                             {"diagnostics": payload.get("diagnostics", [])})
    return payload["semantic_artifact_id"]


SUBSTRATES = {
    TrvmWorldV1.profile: TrvmWorldV1(),
    ResidencyRepositoryV1.profile: ResidencyRepositoryV1(),
}


def for_profile(profile):
    sub = SUBSTRATES.get(profile)
    if sub is None:
        raise AdmissionError(
            "UNKNOWN_SUBSTRATE",
            "no admission profile for substrate %r (known: %s)"
            % (profile, ", ".join(sorted(SUBSTRATES))))
    return sub
