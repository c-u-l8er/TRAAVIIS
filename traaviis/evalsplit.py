"""`trvs eval` -- run an agent over a split of a packed environment (§7).

`eval-one` answers "did this agent solve *this* task?". `eval` answers "did this
agent solve *this split*?" -- and the RFC is deliberate about what that does and
does not create:

    §7  `trvs eval` -- run an agent over a split, emit an `episode-...` per task,
        score.

An `episode-...` per task. **Not** a new rung on the artifact ladder. The RFC's
identity ladder (§1) stops at `env-...` / `bundle-...`; there is no artifact id
for "a run over a split", so this module invents none. What it emits is an
*index* -- `EvaluationV1` -- which names the `env-...` it ran, the split it ran,
and the `episode-...` ids that were produced. Every one of those is already
content-addressed elsewhere; the index adds no identity of its own, and carries
no id of its own.

The order of work mirrors `pack`: everything that can be checked is checked
**before** a single agent process is launched.

    1. open_package        reopen the package through the §5a admission
                           interface -- every task/reward/subject id re-derived
                           from the written bytes
    2. resolve the split   the named split exists, is non-empty, and every
                           member resolves to a task inside the closure
    3. admit the subject   bind the on-disk subject tree to the snapshot once
    4. open one kernel     one `EpisodeKernelV1` over the whole admitted
                           environment, with the verifier wiring resolved for
                           every task in the package
    --- only now does anything run ---
    5. per task, in a deterministic order: one ephemeral session through that
       one kernel, optionally persist an episode bundle
    6. aggregate           an index over the episodes, written atomically

A split that names a missing task, a package whose ids do not re-derive, or a
subject tree that drifted from its snapshot fails **before** the agent runs, not
after N wasted episodes.

Step 4 is the Episode Kernel Closure's shape made real here: **one kernel = one
admitted environment**. The split does not open a kernel per task, because a
kernel per task would mean the environment was admitted N times and the "shared
registry, shared engine seam" guarantee would be a claim about N objects that
merely happen to agree. Each task is one ephemeral session against the single
kernel; the sessions are the per-task thing, and they are the only per-task thing.
"""

import json
import os
import tempfile

from . import admission, evalone, substrates
from .substrates import AdmissionError

__all__ = [
    "AGGREGATION_PROFILE",
    "EVALUATION_VERSION",
    "SplitError",
    "open_environment",
    "resolve_split",
    "eval_split",
    "write_evaluation",
]

EVALUATION_VERSION = "traaviis.evaluation.v1"

#: How `totals` were computed. Named, versioned, and written into every report.
#:
#: `reward_mean` is a number with no meaning until you know its denominator, and
#: there are at least two defensible ones: the mean over episodes that produced a
#: reward, or the mean over every task in the split (counting an errored task as
#: zero). Those differ sharply on exactly the runs that matter -- a split where
#: half the tasks crashed scores 1.0 under the first rule and 0.5 under the
#: second. Shipping the bare number invites a reader to assume whichever rule
#: flatters the run, so the report states the rule it used.
#:
#: The rule this profile names: the arithmetic mean over scored episodes only,
#: with unscored episodes excluded from the denominator rather than imputed.
#: Excluding is the honest default because an errored episode has no reward, and
#: a zero is a *measurement* -- claiming one for a task whose agent never
#: finished would be fabricating an outcome. The count of what was excluded is
#: carried alongside so the other statistic remains recoverable.
AGGREGATION_PROFILE = "traaviis.aggregation.mean-over-scored.v1"

#: Substrates that can currently be evaluated. `trvm.world.v1` packs and
#: reopens, but no `EpisodeKernelV1` implements its episode semantics, so it is
#: refused *by name* rather than half-run. `kernel.environment_kernel` refuses it
#: a second time, independently, for the same reason -- the refusal belongs to
#: the kernel, and this one is the early, better-worded version of it.
_EVALUABLE = ("residency.repository.v1",)


class SplitError(AdmissionError):
    """A typed split-evaluation failure. Same shape as an admission failure."""


def _verifiers_from(registry, on_wiring_notes=None):
    """The shared verifier wiring -- identical to what `eval-one` uses.

    `on_wiring_notes(notes)` is a **presentation-only** sink for the human
    warnings `wire_verifiers` produces (a declared signal the registry cannot
    supply). It exists so a caller that wants to print those notes does not have
    to take over the wiring to see them: replacing the seam is what made the CLI
    attest `caller_supplied` for verifiers that in fact came from its registry.
    The notes never enter `EvaluationV1` -- what the registry could and could not
    answer is already stated structurally in `runtime_context`.
    """
    from . import wiring

    def wire(task):
        extra, notes = wiring.wire_verifiers(task, registry)
        if notes and on_wiring_notes is not None:
            on_wiring_notes(notes)
        return extra
    return wire


def open_environment(package, engine=None):
    """Reopen a packed environment, fail-closed, and return (manifest, artifacts).

    This is the *only* door into a package: it re-derives `env-`, every
    `task-`, every `rew-` and the subject identity from the bytes on disk before
    anything is allowed to read them.
    """
    package = os.path.abspath(package)
    manifest_path = os.path.join(package, "environment.json")
    if not os.path.isfile(manifest_path) or os.path.islink(manifest_path):
        raise SplitError(
            "ENV_MISSING",
            "not a packed environment (no environment.json): %s" % package)
    with open(manifest_path, "rb") as fh:
        try:
            head = json.loads(fh.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as ex:
            raise SplitError("ENV_MALFORMED",
                             "environment.json is not valid JSON: %s" % ex)
    profile = head.get("substrate_profile")
    sub = substrates.for_profile(profile)
    if profile not in _EVALUABLE:
        raise SplitError(
            "SUBSTRATE_NOT_EVALUABLE",
            "substrate %r can be packed but not yet evaluated; the Episode "
            "Kernel for it is unbuilt" % profile)
    manifest, artifacts = sub.open_package(package, engine=engine)
    return manifest, artifacts


def resolve_split(manifest, split):
    """Resolve a split name to its task ids, in a deterministic order.

    A split is a *set* (§3), so the run order is the substrate-independent
    sorted order of its task ids -- never the order they happen to appear in the
    manifest. Two packages with the same split therefore run in the same order.
    """
    splits = manifest.get("splits") or {}
    if split not in splits:
        raise SplitError(
            "SPLIT_UNKNOWN",
            "environment declares no split %r (has: %s)"
            % (split, ", ".join(sorted(splits)) or "none"))
    members = list(splits[split])
    if not members:
        raise SplitError("SPLIT_EMPTY", "split %r names no tasks" % split)
    if len(set(members)) != len(members):
        raise SplitError("SPLIT_DUPLICATE",
                         "split %r names the same task more than once" % split)
    known = {e["task_id"] for e in manifest.get("tasks") or []}
    for m in members:
        if m not in known:
            raise SplitError(
                "SPLIT_UNRESOLVED",
                "split %r names %s, which is not a task in this environment"
                % (split, m))
    return sorted(members)


def eval_split(package, split, agent_command, *, output=None,
               platform="unknown", toolchain=None, engine=None, registry=None,
               extra_verifiers_for=None, on_episode=None, on_wiring_notes=None):
    """Run `agent_command` over every task in `split`. Returns an `EvaluationV1`.

    `registry` is the `VerifierRegistryV1` supplying implementations; it is built
    once (from `engine`) if not given, so every task in the split is answered by
    the same verifiers bound to the same Forge checkout. `extra_verifiers_for(task)`
    overrides the wiring outright -- passing `None` is not "no verifiers", it means
    "use the registry"; to run without any, pass `lambda t: {}`. Passing **both**
    is `VERIFIER_WIRING_AMBIGUOUS`, because the two seams answer the same
    question differently and the report has to attest one of them.
    `on_episode(index, total, entry)` is a progress hook; it never affects the
    result. `on_wiring_notes(notes)` receives the human wiring warnings so that a
    caller wanting to print them keeps using the registry seam instead of
    replacing it; the notes are presentation-only and never enter the report.

    Raises `SplitError` / `AdmissionError` **before** running anything if the
    package, the split, or the subject tree does not admit. Once running, a task
    that fails is *recorded* as a failed episode -- a bad episode is a result,
    not a crash, and the remaining tasks still run.
    """
    manifest, artifacts = open_environment(package, engine=engine)
    task_ids = resolve_split(manifest, split)

    package = os.path.abspath(package)
    subject = manifest["subject"]
    snapshot = _read_member(package, subject["snapshot"], "snapshot")
    subject_dir = os.path.join(package, subject["root"].replace("/", os.sep))
    # Bind the on-disk tree to the snapshot ONCE: every task in the split shares
    # this subject, so it is admitted once and reused, never re-read per task.
    content = admission.verify_subject_tree(snapshot, subject_dir)

    if not agent_command:
        raise SplitError("AGENT_MISSING", "no agent command given")
    agent_command = list(agent_command)

    # `wiring_source` records *how* the verifiers were chosen, which the
    # attestation needs and cannot infer afterwards: a caller-supplied
    # `extra_verifiers_for` leaves no registry to interrogate, and an absent
    # registry then means "nothing was asked" rather than "nothing was
    # available".
    if registry is not None and extra_verifiers_for is not None:
        # Both seams describe where implementations come from, and they
        # disagree. Silently preferring one is how a report comes to attest a
        # registry that did not supply anything -- or, worse, to disclaim one
        # that did.
        raise SplitError(
            "VERIFIER_WIRING_AMBIGUOUS",
            "pass either registry= (implementations from that registry) or "
            "extra_verifiers_for= (caller-injected implementations), not both")

    if extra_verifiers_for is None:
        wiring_source = "registry"
        if registry is None:
            from . import wiring
            registry = wiring.default_registry(engine)
        extra_verifiers_for = _verifiers_from(registry, on_wiring_notes)
    else:
        wiring_source = "caller_supplied"
        registry = None

    # One kernel for the whole admitted environment. Building it here resolves
    # the verifier wiring for *every* task in the package before the first agent
    # starts, which is the same discipline the rest of this function follows: a
    # task whose declared signal nothing can answer is a fact about the package,
    # knowable without running anything, and it should not be discovered halfway
    # through a split.
    from . import kernel as _kernel
    episode_kernel = _kernel.environment_kernel(
        manifest, artifacts, snapshot, content,
        extra_verifiers_for=extra_verifiers_for,
        platform=platform, toolchain=toolchain)

    episodes = []
    total = len(task_ids)
    for index, task_id in enumerate(task_ids):
        entry = _run_one(episode_kernel, task_id, agent_command, output=output)
        episodes.append(entry)
        if on_episode is not None:
            on_episode(index, total, entry)

    return _index(manifest, split, episodes,
                  _runtime_context(registry, wiring_source))


def _run_one(episode_kernel, task_id, agent_command, *, output):
    """Run one task as one ephemeral session and reduce it to an index entry.

    Never raises.

    The entry carries two independent outcomes. `status` is what the *evaluation*
    found; `persistence` is whether the evidence the caller asked to keep was
    actually kept. Conflating them once let a run report `ok` for an episode whose
    bundle had failed to write — a score with no retained proof, reported as if the
    proof existed.

    `persistence.reason` is the machine-readable half of `persistence.error`, and
    it exists because two very different things both leave a task without a
    bundle. An episode that produced nothing to keep is a *candidate* outcome; a
    bundle that could not be written is an *infrastructure* failure. A consumer
    that has to tell them apart -- `trvs batch` does, since one is a comparison
    refusal and the other aborts the run -- would otherwise be matching on
    English prose.

    `bundle` is a **legacy episode-member field**. It predates the `bundle-...`
    rung entirely and is not a package identity: its frozen meaning is `null` or
    exactly one `episode-<id>` directory name, relative to the episodes root. It
    keeps its name for compatibility -- `batch.py` and the B-battery read it --
    and a future `EvaluationV2` renames it `episode_member`. Nothing in v1 may
    write a `bundle-...` value here, and every human-facing surface calls it
    "episode evidence".
    """
    entry = {"task_id": task_id, "episode_id": None, "status": "error",
             "validity": None, "reward": None, "bundle": None, "error": None,
             "persistence": {"requested": bool(output), "status": "not_requested",
                             "reason": None, "error": None}}
    from . import kernel as _kernel
    try:
        catalog = episode_kernel.entry(task_id)
        run = _kernel.run_episode(episode_kernel, task_id, agent_command)
    except (admission.AdmissionError, AdmissionError,
            evalone.UnsupportedPolicyError, OSError) as ex:
        # `OSError` is here because the most ordinary way a run fails is that
        # the agent's argv[0] does not exist on this host, and `subprocess`
        # raises `FileNotFoundError` for that. Letting it escape abandoned every
        # remaining task in the split over one caller's typo -- and, once
        # several candidates share a split, every remaining *candidate* too. An
        # agent that cannot be launched is a result for this task, exactly like
        # one that ran and failed.
        entry["error"] = "%s: %s" % (type(ex).__name__, ex)
        if output:
            # The caller asked for evidence and there is no episode to keep. That
            # is an unmet request, and saying "not_requested" here would have made
            # it look like nothing was ever asked for.
            entry["persistence"].update(
                {"status": "unavailable", "reason": "no_episode",
                 "error": "the task did not evaluate, so there is no episode"})
        return entry

    receipt = run["receipt"]
    entry["episode_id"] = receipt["episode_id"]
    entry["status"] = receipt["status"]
    entry["validity"] = receipt["validity"]
    entry["reward"] = receipt["reward"]

    if output:
        entry["persistence"]["status"] = "error"
        if run.get("artifacts") is None:
            # Nothing to keep: the episode produced no artifacts to bundle. The
            # caller asked for evidence and there is none, so this is still an
            # unmet request, not a quiet success.
            entry["persistence"]["reason"] = "no_artifacts"
            entry["persistence"]["error"] = "episode produced no artifacts to persist"
            return entry
        from . import episode_bundle
        try:
            path = episode_bundle.write_episode_bundle(
                run, task=catalog.task, reward_spec=catalog.reward_spec,
                snapshot=catalog.snapshot, content=catalog.content,
                dest_root=output, extra_verifiers=catalog.extra_verifiers)
        except (episode_bundle.EpisodeBundleError, OSError) as ex:
            # The episode ran and scored; only *keeping* it failed. That does not
            # change the score, and it does not leave the command successful.
            # OSError is included deliberately: a read-only output directory or a
            # full disk is the most ordinary way persistence fails, and it must be
            # a recorded outcome for this task rather than an exception that
            # abandons the rest of the split.
            entry["persistence"]["reason"] = "write_failed"
            entry["persistence"]["error"] = "%s: %s" % (type(ex).__name__, ex)
        else:
            entry["bundle"] = os.path.basename(path)
            entry["persistence"]["status"] = "closed"
    return entry


def _runtime_context(registry, wiring_source):
    """Attest which verifier implementations this runtime actually had.

    An `EvaluationV1` is not content-addressed, so nothing about it is pinned by
    a hash -- which makes it the wrong place to leave a question unanswered. Two
    runs of the same `env-` can disagree because one of them had no Forge
    checkout and therefore never ran the identity verifier at all; without this
    block the two reports look alike, and the weaker one silently reads as
    corroboration of the stronger.

    What is recorded is only what the registry can state as fact: its version,
    which signals it could answer, and the version of each implementation. The
    registry's `notes` are deliberately *not* copied. They are prose explaining a
    Forge import failure, and prose in a report is a thing readers quote as if it
    were a finding. The absence of a signal from `verifiers_available` is the
    machine-checkable version of the same claim.
    """
    context = {
        "registry_version": None,
        "wiring": wiring_source,
        "verifiers_available": [],
        "verifier_versions": {},
    }
    if registry is None:
        # The caller supplied `extra_verifiers_for` outright, so no registry was
        # consulted. Saying so is the honest answer; an empty list would read as
        # "this runtime could answer nothing", which is a different claim.
        return context
    context["registry_version"] = getattr(
        registry, "registry_version", None)
    context["verifiers_available"] = list(registry.available())
    context["verifier_versions"] = {
        k: v for k, v in sorted(registry.versions().items())
    }
    return context


def _index(manifest, split, episodes, runtime_context=None):
    """Build the `EvaluationV1` index. It carries no identity of its own."""
    scored = [e["reward"] for e in episodes if e["reward"] is not None]

    def _persist(state):
        return sum(1 for e in episodes
                   if (e.get("persistence") or {}).get("status") == state)

    totals = {
        "tasks": len(episodes),
        "ok": sum(1 for e in episodes if e["status"] == "ok"),
        "invalid": sum(1 for e in episodes if e["status"] == "invalid"),
        "error": sum(1 for e in episodes if e["status"] == "error"),
        "scored": len(scored),
        "reward_sum": sum(scored),
        "reward_mean": (sum(scored) / len(scored)) if scored else None,
        # Counted separately from the evaluation outcome: a split can be fully
        # `ok` and still have failed to retain the evidence it was asked for.
        "persistence_closed": _persist("closed"),
        "persistence_error": _persist("error"),
    }
    return {
        "evaluation_version": EVALUATION_VERSION,
        "env_id": manifest["env_id"],
        "substrate_profile": manifest["substrate_profile"],
        "subject": {k: v for k, v in manifest["subject"].items()
                    if k in ("snapshot_id", "semantic_artifact_id")},
        "split": split,
        "aggregation_profile": {
            "profile": AGGREGATION_PROFILE,
            "statistic": "arithmetic_mean",
            "population": "scored_episodes",
            "unscored_policy": "excluded",
            "scored": len(scored),
            "unscored": len(episodes) - len(scored),
        },
        "runtime_context": runtime_context or _runtime_context(None, "unknown"),
        "episodes": episodes,
        "totals": totals,
    }


def write_evaluation(report, path):
    """Write the index atomically (temp sibling + one rename). Returns `path`."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".trvs-eval-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write((json.dumps(report, indent=2, sort_keys=True,
                                 ensure_ascii=False) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def _read_member(root, ref, what):
    from .paths import PathError, safe_relposix
    try:
        rel = safe_relposix(ref)
    except PathError as ex:
        raise SplitError("ENV_MEMBER", "%s reference escapes the package: %s"
                         % (what, ex))
    target = os.path.join(root, rel.replace("/", os.sep))
    if not os.path.isfile(target) or os.path.islink(target):
        raise SplitError("ENV_MEMBER", "%s not found in package: %s" % (what, ref))
    with open(target, "rb") as fh:
        try:
            return json.loads(fh.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as ex:
            raise SplitError("ENV_MEMBER", "%s is not valid JSON: %s" % (what, ex))
