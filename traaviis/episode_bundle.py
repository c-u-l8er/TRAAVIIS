"""``traaviis.episode-bundle.v1`` — a self-contained, replayable episode directory.

The Episode Evidence Closure (GPT-5.6): making "keep the proof" literally true. An
``episode-…`` receipt is only an *attestation*; this module writes the concrete
evidence it attests into one durable directory, and re-verifies that directory
without ever re-running the agent.

A bundle is a **transport/closure format, not a new identity domain** — it carries
the existing content-addressed artifacts (snapshot / finding / patch / trace /
verifier evidence / receipt) plus the raw process bytes, laid out as::

    episode-<id>/
      episode-bundle.json          # this manifest (non-canonical: carries a wall clock)
      receipt.json                 # the EpisodeReceiptV1
      task.json  reward.json  snapshot.json
      subject/…                    # the materialized sealed subject tree
      evidence/
        trace.json                 # the canonical TraceV1 (trace_id inside)
        finding.json               # the FindingV1 (omitted when the agent produced none)
        candidate.patch            # the raw unified diff (omitted when there was none)
        verifiers/<signal>.json    # one canonical VerifierEvidence per scored signal
        process/stdout.bin         # the exact captured (capped) agent stdout bytes
        process/stderr.bin         # …and stderr; both hash to the trace's digests

``write_episode_bundle`` builds the tree in a temp sibling and **atomically renames**
it into place, so a reader never sees a half-written episode. ``verify_episode_bundle``
re-opens every member under strict containment (no symlink / ``..`` escape),
recomputes each artifact id from its saved bytes, re-attests the process bytes
against the trace digests, re-runs the declared verifiers against the saved subject +
candidate (no agent process), recomputes the reward, and confirms the receipt is
self-consistent — reporting one line per closure/artifacts/signal/reward/episode-id.
"""

import hashlib
import json
import os
import shutil
import tempfile
from typing import Any, Callable, Dict, Mapping, Optional

from . import (admission, execfacts, identity, patchapply, reward as _reward,
               signals as _signals)
from .evalone import (EVALUATION_RUN_VERSION, VERIFIER_EVIDENCE_VERSION,
                      build_receipt_v1, _evidence_object, _evidence_ref)
from .paths import PathError, safe_join, safe_relposix
from .vcontext import VerifierContextV1, VerifierResult

__all__ = [
    "EPISODE_BUNDLE_VERSION",
    "EpisodeBundleError",
    "EpisodeBundleConflict",
    "write_episode_bundle",
    "verify_episode_bundle",
]

EPISODE_BUNDLE_VERSION = "traaviis.episode-bundle.v1"

_PSEUDO_SIGNALS = ("native", "oracle")

# Verification outcomes (map to CLI exit codes): a fully-closed bundle, a bundle
# whose replay disagrees with the sealed receipt, or a bundle that cannot be
# judged here because the wired verifier versions do not match what it sealed.
OUTCOME_CLOSED = "closed"
OUTCOME_MISMATCH = "mismatch"
OUTCOME_UNAVAILABLE = "unavailable"

# Execution-facts attestation classification (GPT-5.6 honesty clarification): only
# ``agent_process`` + ``sandbox`` are independently *rederived* from the saved
# process evidence; ``runner`` / ``platform`` / ``toolchain`` are carried as the
# runner's own *attestation* — closure proves the saved evidence and receipt are
# internally consistent with that attestation, NOT its external historical truth.
EXECUTION_FACTS_ATTESTATION = {
    "rederived": ["agent_process", "sandbox"],
    "runner_attested": ["runner", "platform", "toolchain"],
}


class EpisodeBundleError(Exception):
    """A bundle could not be written, or a saved bundle failed re-verification."""


class EpisodeBundleConflict(EpisodeBundleError):
    """A different (or corrupt) bundle already occupies the content-addressed path.

    Idempotent reuse is only safe when the existing ``episode-<id>/`` re-verifies
    closed *and* carries the same episode id. A target that is present but invalid,
    or that verifies to a different episode, is a genuine conflict — never silently
    overwritten and never returned by name alone.
    """


# --------------------------------------------------------------------- writing
def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")


def _write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, (bytes, bytearray)) else bytes(data))


def _populate(root: str, receipt, artifacts, task, reward_spec, snapshot, content):
    """Write every bundle member under ``root`` (an empty staging directory)."""
    _write_json(os.path.join(root, "receipt.json"), receipt)
    _write_json(os.path.join(root, "task.json"), task)
    _write_json(os.path.join(root, "reward.json"), reward_spec)
    _write_json(os.path.join(root, "snapshot.json"), snapshot)

    # The sealed subject, materialized as its canonical bytes (binaries as bytes)
    # *with the snapshot's sealed file modes restored* — a 0755 executable must
    # survive persistence and re-admit, which the runner's plain writer (process
    # umask) would silently drop.
    subject_root = os.path.join(root, "subject")
    os.makedirs(subject_root, exist_ok=True)
    admission.materialize_subject(snapshot, content, subject_root)

    ev_dir = os.path.join(root, "evidence")
    _write_json(os.path.join(ev_dir, "trace.json"), artifacts["trace"])

    members: Dict[str, Any] = {
        "receipt": "receipt.json",
        "task": "task.json",
        "reward": "reward.json",
        "snapshot": "snapshot.json",
        "subject": "subject",
        "trace": "evidence/trace.json",
        "finding": None,
        "patch": None,
        "verifiers": {},
    }

    finding = artifacts.get("finding")
    if finding is not None:
        _write_json(os.path.join(ev_dir, "finding.json"), finding)
        members["finding"] = "evidence/finding.json"

    patch = artifacts.get("patch")
    if patch is not None:
        # The raw unified diff exactly as the agent produced it (patch_id is over it).
        _write_bytes(os.path.join(ev_dir, "candidate.patch"),
                     patch["diff"].encode("utf-8"))
        members["patch"] = "evidence/candidate.patch"

    for sig, ev in (artifacts.get("verifier_evidence") or {}).items():
        # The signal id becomes an on-disk file stem: validate it against the
        # frozen SignalIDV1 grammar AND build the path through safe_join, so a
        # traversal name (``../../escape``) can never write outside the staging
        # directory — the write is refused before any bytes touch the disk.
        _signals.validate_signal_id(sig, where="verifier evidence signal")
        rel = "evidence/verifiers/%s.json" % sig
        _write_json(safe_join(root, rel), ev)
        members["verifiers"][sig] = rel

    proc = artifacts["process"]
    _write_bytes(os.path.join(ev_dir, "process", "stdout.bin"), proc["stdout"])
    _write_bytes(os.path.join(ev_dir, "process", "stderr.bin"), proc["stderr"])
    # The observed write-escape list (R3/E2), persisted so replay can re-attest it
    # against the trace's policy_violations_digest and reconstruct the tampered
    # verdict — a nonempty list means the episode was tampered.
    violations = list(proc.get("policy_violations", []))
    _write_json(os.path.join(ev_dir, "process", "policy-violations.json"), violations)
    members["process"] = {
        "stdout": "evidence/process/stdout.bin",
        "stderr": "evidence/process/stderr.bin",
        "policy_violations": "evidence/process/policy-violations.json",
        "stdout_truncated": bool(proc["stdout_truncated"]),
        "stderr_truncated": bool(proc["stderr_truncated"]),
    }

    from datetime import datetime, timezone
    manifest = {
        "episode_bundle_version": EPISODE_BUNDLE_VERSION,
        "episode_id": receipt["episode_id"],
        # Wall-clock provenance only — non-canonical, never enters any id.
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "members": members,
    }
    _write_json(os.path.join(root, "episode-bundle.json"), manifest)


def _fsync_tree(root: str) -> None:
    """fsync every file + directory under ``root`` before the atomic rename.

    Durability: the staged bundle's bytes must reach stable storage before it is
    published, so a crash between rename and flush cannot expose a torn episode.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                fd = os.open(os.path.join(dirpath, name), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass
        try:
            dfd = os.open(dirpath, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass


def write_episode_bundle(
    evaluation_run: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
    reward_spec: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    content: Mapping[str, Any],
    dest_root: str,
    extra_verifiers: Optional[Mapping[str, Callable]] = None,
    live_verifiers: Optional[Mapping[str, Callable]] = None,
) -> str:
    """Durably persist one ``EvaluationRunV1`` as an ``episode-<id>/`` directory.

    Builds the whole tree in a temp sibling under ``dest_root``, **fully verifies
    the staged bundle** with the same ``verify_episode_bundle`` a reader would run,
    fsyncs it, then atomically renames it into ``dest_root/episode-<id>/`` — a
    reader never observes a partial *or* an unverified bundle, and a staging
    failure leaves no final directory (step 9).

    Content-addressed and idempotent (step 10): if the target already exists it is
    *verified*, and only returned when it re-verifies closed with the same episode
    id. A present-but-invalid or conflicting target raises ``EpisodeBundleConflict``
    — never returned by name alone, never silently overwritten. ``extra_verifiers``
    / ``live_verifiers`` are the same verifier set the CLI will replay with, so the
    staging check exercises the exact closure a later ``verify-episode`` will.

    Raises ``EpisodeBundleError`` for an invalid-config episode that produced no
    evidence to keep.
    """
    if evaluation_run.get("evaluation_run_version") != EVALUATION_RUN_VERSION:
        raise EpisodeBundleError("not an EvaluationRunV1")
    receipt = evaluation_run["receipt"]
    artifacts = evaluation_run["artifacts"]
    if artifacts is None:
        raise EpisodeBundleError(
            "episode produced no evidence (invalid config); nothing to persist")

    episode_id = receipt["episode_id"]
    if not (isinstance(episode_id, str) and episode_id.startswith("episode-")):
        raise EpisodeBundleError("receipt has no episode_id")

    def _verify(path: str) -> Dict[str, Any]:
        return verify_episode_bundle(
            path, extra_verifiers=extra_verifiers, live_verifiers=live_verifiers)

    os.makedirs(dest_root, exist_ok=True)
    final = os.path.join(dest_root, episode_id)
    if os.path.exists(final):
        # Verify the existing target before reusing it — never trust the name.
        report = _verify(final)
        if report.get("outcome") == OUTCOME_CLOSED \
                and report.get("episode_id") == episode_id:
            return final  # idempotent: the same episode is already sealed + closed
        raise EpisodeBundleConflict(
            "target %s exists but does not re-verify as this episode (outcome=%r, "
            "episode_id=%r)" % (final, report.get("outcome"), report.get("episode_id")))

    tmp = tempfile.mkdtemp(prefix=".tmp-episode-", dir=dest_root)
    try:
        _populate(tmp, receipt, artifacts, task, reward_spec, snapshot, content)
        # Verify the *staged* tree fully before it is ever published (step 9).
        report = _verify(tmp)
        if report.get("outcome") != OUTCOME_CLOSED:
            raise EpisodeBundleError(
                "staged bundle failed verification (outcome=%r): %s"
                % (report.get("outcome"), report.get("checks")))
        _fsync_tree(tmp)
        os.rename(tmp, final)  # atomic within dest_root's filesystem
        # Best-effort crash durability: fsync the parent so the new directory entry
        # itself survives a crash right after the rename (where the platform
        # supports directory fsync).
        try:
            dfd = os.open(dest_root, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


# ------------------------------------------------------------------- verifying
def _member(root_real: str, ref: str, what: str) -> str:
    """Resolve one bundle-relative reference to a contained, symlink-free path.

    Lexical safety (no absolute / ``..``) plus filesystem containment: the resolved
    real path must equal the lexical candidate (any symlink makes them differ) and
    stay under the resolved bundle root. Anything else raises ``EpisodeBundleError``
    before the path is opened — the same discipline the eval-bundle loader uses.
    """
    if not isinstance(ref, str) or not ref.strip():
        raise EpisodeBundleError("manifest %s reference must be a path" % what)
    try:
        safe = safe_relposix(ref.rstrip("/"))
    except PathError as exc:
        raise EpisodeBundleError("manifest %s escapes the bundle: %s" % (what, exc))
    candidate = os.path.normpath(os.path.join(root_real, safe.replace("/", os.sep)))
    if os.path.realpath(candidate) != candidate:
        raise EpisodeBundleError("manifest %s resolves through a symlink" % what)
    try:
        contained = os.path.commonpath([root_real, candidate]) == root_real
    except ValueError:
        contained = False
    if not contained:
        raise EpisodeBundleError("manifest %s escapes the bundle root" % what)
    return candidate


def _load_json(path: str, what: str) -> Any:
    if not os.path.isfile(path):
        raise EpisodeBundleError("bundle is missing %s: %s" % (what, path))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        raise EpisodeBundleError("%s is not valid JSON: %s" % (what, exc))


def _read_bytes(path: str, what: str) -> bytes:
    if not os.path.isfile(path):
        raise EpisodeBundleError("bundle is missing %s: %s" % (what, path))
    with open(path, "rb") as fh:
        return fh.read()


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_json(obj: Any) -> str:
    return _sha256_bytes(identity.canonical_bytes(obj))


# The fixed single-file bundle members, by manifest key (always present).
_FIXED_FILE_MEMBERS = ("receipt", "task", "reward", "snapshot", "trace")


def _verify_tree_closure(
    root_real: str, members: Mapping[str, Any], snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    """Require the on-disk tree to be EXACTLY the manifest's declared closure.

    The manifest must define the *complete* bundle, not merely the members TRAAVIIS
    chooses to inspect. The expected regular-file set is the fixed members ∪ the
    snapshot's subject files ∪ the persisted verifier evidence ∪ the process
    evidence. Every actual regular file must be in that set and vice-versa; any
    symlink, socket, device, FIFO, or undeclared file fails (blocker 1). Empty
    directories are ignored consistently.
    """
    problems = []
    expected = {"episode-bundle.json"}
    try:
        for key in _FIXED_FILE_MEMBERS:
            expected.add(safe_relposix(members[key]))
        for key in ("finding", "patch"):
            if members.get(key):
                expected.add(safe_relposix(members[key]))
        for rel in (members.get("verifiers") or {}).values():
            expected.add(safe_relposix(rel))
        proc = members.get("process") or {}
        for key in ("stdout", "stderr", "policy_violations"):
            if proc.get(key):
                expected.add(safe_relposix(proc[key]))
        subject_rel = safe_relposix(members["subject"])
        for rel in (snapshot.get("files") or {}):
            expected.add(subject_rel + "/" + safe_relposix(rel))
    except (PathError, KeyError, TypeError) as exc:
        return {"ok": False, "detail": "malformed manifest member: %s" % exc}

    actual = set()
    for dirpath, dirnames, filenames in os.walk(root_real, followlinks=False):
        for name in list(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                rel = os.path.relpath(
                    os.path.join(dirpath, name), root_real).replace(os.sep, "/")
                problems.append("symlinked directory: %s" % rel)
        for name in filenames:
            abspath = os.path.join(dirpath, name)
            rel = os.path.relpath(abspath, root_real).replace(os.sep, "/")
            if os.path.islink(abspath):
                problems.append("symlink: %s" % rel)
            elif not os.path.isfile(abspath):
                problems.append("non-regular file: %s" % rel)
            else:
                actual.add(rel)

    missing = expected - actual
    extra = actual - expected
    if missing:
        problems.append("missing declared files: %s" % sorted(missing))
    if extra:
        problems.append("undeclared files: %s" % sorted(extra))
    return {"ok": not problems,
            "detail": "tree is exactly the declared closure" if not problems
            else "; ".join(problems)}


def _reconstruct_execution_facts(stored_ef, event, proc) -> Any:
    """Rebuild ``execution_facts`` from the saved trace + manifest for replay.

    Only ``agent_process`` (termination / exit_code / truncation) and ``sandbox``
    are re-derivable from the bundle's own evidence — the trace's ``exit_code`` and
    the manifest's truncation flags for the former, the runner profile for the
    latter. The irreducible run-time config (runner profile, platform, toolchain)
    is not attestable from the evidence, so it is carried verbatim from the stored
    facts. A stored ``agent_process`` / ``sandbox`` that lies about the run therefore
    differs from this reconstruction, moving the derived episode id and failing the
    receipt comparison.
    """
    if not isinstance(stored_ef, Mapping):
        return stored_ef
    ef = dict(stored_ef)
    exit_code = event.get("exit_code")
    ef["agent_process"] = {
        "termination": "timed_out" if exit_code is None else "exited",
        "exit_code": exit_code,
        "stdout_truncated": bool(proc.get("stdout_truncated")),
        "stderr_truncated": bool(proc.get("stderr_truncated")),
    }
    profile = (stored_ef.get("runner") or {}).get("profile")
    if profile in execfacts.RUNNER_PROFILES:
        ef["sandbox"] = dict(execfacts.RUNNER_PROFILES[profile])
    return ef


def verify_episode_bundle(
    bundle_dir: str,
    *,
    extra_verifiers: Optional[Mapping[str, Callable[[VerifierContextV1], VerifierResult]]] = None,
    live_verifiers: Optional[Mapping[str, Callable[[VerifierContextV1], VerifierResult]]] = None,
) -> Dict[str, Any]:
    """Re-verify a saved episode bundle **without running the agent**. Pure replay.

    Derives a *fresh* receipt from the saved evidence via the same
    ``evalone.build_receipt_v1`` the live evaluation used, and requires it to be
    byte-identical to the stored one — stored-only self-consistency is not enough
    (GPT-5.6 reopen ruling). ``live_verifiers`` are the substrate-independent
    verifiers (citations / patch / finding_completeness); ``extra_verifiers`` inject
    ``tests`` / ``identity`` (wired by the CLI). Returns a report::

        {ok, outcome, episode_id, checks: {...}}

    ``outcome`` is one of:

      * ``closed``      — fully replayed and every check matched.
      * ``mismatch``    — the bundle opened but the replay disagrees with the sealed
                          receipt (tampering / drift). Exit 1.
      * ``unavailable`` — the bundle cannot be judged here: it could not be opened,
                          or the wired verifier versions do not match what the
                          receipt sealed (e.g. a required engine is absent). Exit 2.

    ``ok`` is true only when ``outcome == closed``.
    """
    from . import verifiers as _pure
    if live_verifiers is None:
        live_verifiers = {
            "citations": _pure.citations_verifier,
            "patch": _pure.patch_verifier,
            "finding_completeness": _pure.finding_completeness_verifier,
        }
    extra_verifiers = dict(extra_verifiers or {})

    checks: Dict[str, Any] = {}

    def _fail_report(stage: str, detail: str,
                     outcome: str = OUTCOME_MISMATCH) -> Dict[str, Any]:
        checks.setdefault(stage, {"ok": False, "detail": detail})
        return {"ok": False, "outcome": outcome, "episode_id": None, "checks": checks,
                "execution_facts": dict(EXECUTION_FACTS_ATTESTATION)}

    root_real = os.path.realpath(bundle_dir)
    if not os.path.isdir(root_real):
        return _fail_report("closure", "no such bundle directory: %s" % bundle_dir,
                            OUTCOME_UNAVAILABLE)

    manifest_path = os.path.join(root_real, "episode-bundle.json")
    if not os.path.isfile(manifest_path) or os.path.islink(manifest_path):
        return _fail_report("closure", "missing episode-bundle.json manifest",
                            OUTCOME_UNAVAILABLE)
    try:
        manifest = _load_json(manifest_path, "episode-bundle.json")
        if manifest.get("episode_bundle_version") != EPISODE_BUNDLE_VERSION:
            return _fail_report(
                "closure", "unsupported episode_bundle_version %r"
                % manifest.get("episode_bundle_version"), OUTCOME_UNAVAILABLE)
        members = manifest.get("members") or {}

        # Resolve + load the core records under strict containment.
        receipt = _load_json(_member(root_real, members["receipt"], "receipt"),
                             "receipt.json")
        task = _load_json(_member(root_real, members["task"], "task"), "task.json")
        reward_spec = _load_json(_member(root_real, members["reward"], "reward"),
                                 "reward.json")
        snapshot = _load_json(_member(root_real, members["snapshot"], "snapshot"),
                              "snapshot.json")
        subject_dir = _member(root_real, members["subject"], "subject")
        trace = _load_json(_member(root_real, members["trace"], "trace"),
                           "trace.json")
    except (EpisodeBundleError, KeyError, TypeError) as exc:
        return _fail_report("closure", str(exc), OUTCOME_UNAVAILABLE)

    episode_id = receipt.get("episode_id")
    scored = list(reward_spec.get("signals", {}))
    plan = task.get("verifier_plan") or {}
    required = list(plan.get("required", []))
    declared_na = list(plan.get("not_applicable", []))
    sealed_versions = receipt.get("verifier_versions") or {}
    receipt_ev = receipt.get("verification_evidence") or {}

    # --- SignalIDV1 admission (exact closure) ---------------------------------
    # Every signal id a saved bundle carries — as a reward/plan key, a receipt key,
    # or a persisted evidence stem — must match the frozen grammar. An illegal id in
    # a saved bundle is tampering: fail before any of it is used to build a path.
    bad_ids = [v for v in (
        list(scored) + required + declared_na
        + list(receipt.get("verification") or {}) + list(sealed_versions)
        + list(receipt_ev) + list(members.get("verifiers") or {}))
        if not _signals.is_signal_id(v)]
    if bad_ids:
        return _fail_report(
            "closure", "invalid signal ids in bundle: %s" % sorted(set(bad_ids)))

    # Evidence + versions cover every non-pseudo declared verifier (exact closure).
    evidence_signals = sorted(
        (set(scored) | set(required) | set(declared_na)) - set(_PSEUDO_SIGNALS))

    # --- tree: the manifest defines the COMPLETE bundle closure ---------------
    # Build the exact expected regular-file set (fixed members ∪ subject files ∪
    # verifier evidence ∪ process evidence) and require the on-disk tree to match
    # it exactly — no undeclared file, no symlink, no special file may hide beside
    # the sealed evidence.
    checks["tree"] = _verify_tree_closure(root_real, members, snapshot)

    # --- closure: every id attests from the saved bytes -----------------------
    closure_problems = []
    try:
        content = admission.verify_subject_tree(snapshot, subject_dir)
    except admission.AdmissionError as exc:
        return _fail_report("closure", "subject does not admit: %s" % exc)

    # Each record's own declared self-id must be internally consistent, and must
    # equal the id the receipt references (both directions — step 3).
    def _declared_ok(obj, field, compute):
        try:
            admission.verify_declared_id(obj, field, compute)
            return True
        except admission.AdmissionError:
            return False

    snap_id = identity.snapshot_id(snapshot)
    if not _declared_ok(snapshot, "snapshot_id", identity.snapshot_id):
        closure_problems.append("snapshot self-id")
    if not _declared_ok(reward_spec, "reward_id", identity.reward_id):
        closure_problems.append("reward self-id")
    if not _declared_ok(task, "task_id", identity.task_id):
        closure_problems.append("task self-id")
    if not _declared_ok(trace, "trace_id", identity.trace_id):
        closure_problems.append("trace self-id")

    if snap_id != (receipt.get("subject") or {}).get("snapshot_id"):
        closure_problems.append("snapshot_id")
    if identity.reward_id(reward_spec) != receipt.get("reward_id"):
        closure_problems.append("reward_id")
    if identity.task_id(task) != receipt.get("task_id"):
        closure_problems.append("task_id")
    if identity.trace_id(trace) != receipt.get("trace_id"):
        closure_problems.append("trace_id")

    # The task must actually reference *these* verified reward + snapshot (step 3):
    # rerun the same cross-binding the live admission enforced before the run.
    try:
        admission.cross_bind_task(task, identity.reward_id(reward_spec), snap_id)
    except admission.AdmissionError as exc:
        closure_problems.append("cross-bind: %s" % exc)
    checks["closure"] = {"ok": not closure_problems,
                         "detail": "all members bind" if not closure_problems
                         else "mismatch: " + ", ".join(closure_problems)}

    # --- artifacts: finding / patch ids + honest process bytes ----------------
    artifact_problems = []
    outputs = receipt.get("outputs") or {}

    finding = None
    if members.get("finding"):
        try:
            finding = _load_json(_member(root_real, members["finding"], "finding"),
                                 "finding.json")
        except EpisodeBundleError as exc:
            artifact_problems.append(str(exc))
        else:
            if not _declared_ok(finding, "finding_id", identity.finding_id):
                artifact_problems.append("finding self-id")
            if identity.finding_id(finding) != outputs.get("finding_id"):
                artifact_problems.append("finding_id")
    elif outputs.get("finding_id") is not None:
        artifact_problems.append("finding evidence missing but receipt cites it")

    patch = None
    if members.get("patch"):
        try:
            diff_bytes = _read_bytes(_member(root_real, members["patch"], "patch"),
                                     "candidate.patch")
        except EpisodeBundleError as exc:
            artifact_problems.append(str(exc))
        else:
            # The candidate diff is UTF-8 text; decode strictly (step 8). Invalid
            # bytes are not a valid patch — an artifact failure, never a lossy read.
            try:
                diff_text = diff_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                artifact_problems.append("candidate.patch is not valid UTF-8: %s" % exc)
            else:
                patch = {"patch_version": "residency.patch.v1", "diff": diff_text}
                patch["patch_id"] = identity.patch_id(patch)
                if patch["patch_id"] != outputs.get("patch_id"):
                    artifact_problems.append("patch_id")
    elif outputs.get("patch_id") is not None:
        artifact_problems.append("patch evidence missing but receipt cites it")

    # Process bytes must hash to the trace's recorded digests (Req 6, honesty).
    proc = members.get("process") or {}
    event = (trace.get("events") or [{}])[0]
    try:
        stdout_bytes = _read_bytes(_member(root_real, proc["stdout"], "stdout"),
                                   "stdout.bin")
        stderr_bytes = _read_bytes(_member(root_real, proc["stderr"], "stderr"),
                                   "stderr.bin")
    except (EpisodeBundleError, KeyError) as exc:
        artifact_problems.append("process bytes: %s" % exc)
        stdout_bytes = stderr_bytes = None
    else:
        if _sha256_bytes(stdout_bytes) != event.get("stdout_digest"):
            artifact_problems.append("stdout bytes do not match trace digest")
        if _sha256_bytes(stderr_bytes) != event.get("stderr_digest"):
            artifact_problems.append("stderr bytes do not match trace digest")

    # Policy-violation bytes must re-attest to the trace's digest (step 6). A
    # nonempty list means the episode was tampered — replay must reproduce that
    # verdict, so ``tampered`` is derived here (reversing the prior "score the
    # un-tampered path" approach the reopen ruling rejected).
    tampered = False
    pv_ref = proc.get("policy_violations")
    if pv_ref:
        try:
            violations = _load_json(
                _member(root_real, pv_ref, "policy-violations"),
                "policy-violations.json")
        except EpisodeBundleError as exc:
            artifact_problems.append(str(exc))
            violations = []
        if not isinstance(violations, list):
            artifact_problems.append("policy-violations.json is not a list")
            violations = []
        if _sha256_json(violations) != event.get("policy_violations_digest"):
            artifact_problems.append("policy-violations bytes do not match trace digest")
        tampered = bool(violations)
    else:
        artifact_problems.append("bundle is missing process.policy_violations member")

    # Exact verifier-evidence closure (step 4 + exact closure): the manifest's
    # persisted verifier keys, the receipt's verification_evidence keys, and the
    # non-pseudo declared verifier set (scored ∪ required ∪ not_applicable) must be
    # identical — a missing / extra / swapped signal evidence fails here.
    saved_ev = members.get("verifiers") or {}
    expected_ev = set(evidence_signals)
    if set(saved_ev.keys()) != expected_ev:
        artifact_problems.append(
            "persisted verifier keys %s != evidence signals %s"
            % (sorted(saved_ev), sorted(expected_ev)))
    if set(receipt_ev.keys()) != expected_ev:
        artifact_problems.append(
            "receipt verification_evidence keys %s != evidence signals %s"
            % (sorted(receipt_ev), sorted(expected_ev)))

    # Each SAVED per-signal evidence file must hash to the digest the receipt sealed
    # (which entered episode-): a tampered ``evidence/verifiers/<sig>.json`` no
    # longer matches ``receipt.verification_evidence[sig].digest``.
    for sig, rel in saved_ev.items():
        try:
            obj = _load_json(_member(root_real, rel, "verifier evidence"), rel)
        except EpisodeBundleError as exc:
            artifact_problems.append(str(exc))
            continue
        if _evidence_ref(obj)["digest"] != (receipt_ev.get(sig) or {}).get("digest"):
            artifact_problems.append("saved evidence %s does not match receipt digest" % sig)
    checks["artifacts"] = {"ok": not artifact_problems,
                           "detail": "finding/patch/process/evidence attest"
                           if not artifact_problems
                           else "; ".join(artifact_problems)}

    # --- verifier versions: compare fresh wiring to the sealed receipt (step 5)-
    # The receipt's sealed versions drive replay wiring: a scored signal whose
    # sealed implementation is null was UNWIRED at eval time, so it stays unwired
    # here (→ not_applicable) rather than manufacturing a spurious mismatch; a
    # sealed implementation must be reproduced by an available verifier of the same
    # version, else the bundle is *unavailable* to judge here (not an evidence fail).
    def _available(sig):
        if sig in live_verifiers:
            return live_verifiers[sig]
        if sig in extra_verifiers:
            return extra_verifiers[sig]
        return None

    def _impl(v):
        ver = getattr(v, "version", None) if v is not None else None
        return ver if isinstance(ver, str) else None

    spec_signals = reward_spec.get("signals", {})
    fresh_versions = {}
    for sig in evidence_signals:
        spec = spec_signals.get(sig)
        contract = spec.get("verifier") if isinstance(spec, Mapping) else None
        sealed_impl = (sealed_versions.get(sig) or {}).get("implementation")
        impl = None if sealed_impl is None else _impl(_available(sig))
        fresh_versions[sig] = {"contract": contract, "implementation": impl}
    versions_match = fresh_versions == sealed_versions
    checks["verifier_versions"] = {
        "ok": versions_match,
        "detail": "verifier versions match the sealed receipt" if versions_match
        else "wired verifier versions differ from the receipt",
    }

    # --- signals: replay the COMPLETE verification map (step 11) ---------------
    # Rebuild the patched tree from the saved candidate (never re-run the agent).
    patched_content = None
    if patch is not None:
        try:
            patched_content = patchapply.apply_unified_diff(content, patch["diff"])
        except patchapply.PatchError:
            patched_content = None

    context = VerifierContextV1(
        task=task, snapshot=snapshot, original_content=content,
        run={},  # verifiers read only the sealed subject + agent outputs on replay
        finding=finding, patch=patch, patched_content=patched_content,
    )

    def _effective_verifier(sig):
        if sig in _PSEUDO_SIGNALS:
            return None
        sealed = sealed_versions.get(sig)
        if sealed is not None:
            # scored signal: reproduce eval-time wiring from its sealed impl.
            if sealed.get("implementation") is None:
                return None
            return _available(sig)
        return _available(sig)  # non-scored: wire whatever is available

    def _resolve(sig):
        v = _effective_verifier(sig)
        return v(context) if v is not None else VerifierResult(_reward.NOT_APPLICABLE)

    signal_ids = set(scored) | set(required) | set(declared_na) | set(_PSEUDO_SIGNALS)
    replay_results = {sig: _resolve(sig) for sig in signal_ids}
    replay_state = {sig: r.state for sig, r in replay_results.items()}

    # Reconstruct the substrate-run-failure override from the SAVED trace + manifest
    # (Req 6): a timeout (exit_code null), a disallowed exit, or a truncated capture
    # turns every run-dependent signal to error — exactly as the live run did.
    policy = task.get("agent_run_policy") or {}
    exit_code = event.get("exit_code")
    timed_out = exit_code is None
    allowed = list(policy.get("allowed_exit_codes", [0]))
    bad_exit = (not timed_out) and exit_code not in allowed
    output_truncated = bool(proc.get("stdout_truncated")) or bool(proc.get("stderr_truncated"))
    if timed_out or bad_exit or output_truncated:
        for sig in replay_state:
            if sig not in _PSEUDO_SIGNALS:
                replay_state[sig] = _reward.ERROR

    receipt_verif = receipt.get("verification") or {}
    key_match = set(replay_state) == set(receipt_verif)
    signals_report = {}
    for sig in sorted(set(replay_state) | set(receipt_verif)):
        want = receipt_verif.get(sig)
        got = replay_state.get(sig)
        entry = {"ok": got == want, "replayed": got, "receipt": want}
        if sig in evidence_signals and sig in replay_results:
            ev = _evidence_object(sig, got, replay_results[sig].detail)
            dm = _evidence_ref(ev)["digest"] == (receipt_ev.get(sig) or {}).get("digest")
            entry["evidence_match"] = dm
            entry["ok"] = entry["ok"] and dm
        signals_report[sig] = entry
    checks["signals"] = signals_report
    checks["signal_keys"] = {
        "ok": key_match,
        "detail": "replay covers exactly the receipt's signals" if key_match
        else "signal key set %s != receipt %s" % (
            sorted(replay_state), sorted(receipt_verif)),
    }

    # --- derived receipt: rebuild via the shared builder and require equality --
    # (steps 2 + 6): reconstruct execution_facts from the saved trace/manifest,
    # carry tamper from the persisted violations, and rebuild the whole receipt
    # through evalone.build_receipt_v1. The derived receipt must be byte-identical
    # to the stored one — this single comparison subsumes reward + outputs + facts.
    derived_ef = _reconstruct_execution_facts(receipt.get("execution_facts"),
                                              event, proc)
    reward_ok = False
    receipt_match = False
    eid_ok = False
    try:
        derived_receipt, _derived_ev = build_receipt_v1(
            substrate_profile=task.get("substrate_profile", "residency.repository.v1"),
            task_id=identity.task_id(task),
            reward_id=identity.reward_id(reward_spec),
            snapshot_id=snap_id,
            verifier_versions=fresh_versions,
            trace_id=identity.trace_id(trace),
            finding=finding,
            patch=patch,
            evidence_signals=evidence_signals,
            verification=replay_state,
            results=replay_results,
            reward_spec=reward_spec,
            required=required,
            tampered=tampered,
            execution_facts=derived_ef,
        )
    except ValueError as exc:
        reward_detail = "cannot rebuild receipt: %s" % exc
        derived_receipt = None
    else:
        reward_ok = (derived_receipt.get("reward") == receipt.get("reward")
                     and derived_receipt.get("status") == receipt.get("status")
                     and derived_receipt.get("validity") == receipt.get("validity"))
        reward_detail = "replayed reward %r vs receipt %r" % (
            derived_receipt.get("reward"), receipt.get("reward"))
        receipt_match = (identity.canonicalize_episode(derived_receipt)
                         == identity.canonicalize_episode(receipt))
        eid_ok = (derived_receipt["episode_id"] == episode_id
                  and episode_id == identity.episode_id(receipt)
                  and episode_id == manifest.get("episode_id"))
    checks["reward"] = {"ok": reward_ok, "detail": reward_detail}
    checks["receipt"] = {
        "ok": receipt_match,
        "detail": "derived receipt equals the stored receipt" if receipt_match
        else "derived receipt differs from stored",
    }
    checks["episode_id"] = {
        "ok": eid_ok,
        "detail": "episode-id closes over the derived receipt" if eid_ok
        else "episode_id mismatch",
    }

    core_ok = (checks["tree"]["ok"] and checks["closure"]["ok"]
               and checks["artifacts"]["ok"]
               and key_match and all(s["ok"] for s in signals_report.values())
               and checks["reward"]["ok"] and checks["receipt"]["ok"]
               and checks["episode_id"]["ok"])
    if not versions_match:
        outcome = OUTCOME_UNAVAILABLE
    elif core_ok:
        outcome = OUTCOME_CLOSED
    else:
        outcome = OUTCOME_MISMATCH
    return {"ok": outcome == OUTCOME_CLOSED, "outcome": outcome,
            "episode_id": episode_id, "checks": checks,
            "execution_facts": dict(EXECUTION_FACTS_ATTESTATION)}
