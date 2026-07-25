"""`trvs batch` -- run several candidates over one split and compare them (§8).

`eval` answers *"did this agent solve this split?"*. `compare` answers *"which of
these two sealed episodes did better on this task?"*. A batch is the composition
of the two, and deliberately nothing more: a **candidate-by-task matrix** built
from `eval_split` and `ComparisonV1`, with no third mechanism underneath it.

    open the package, rederive the closure
    resolve the split                       once
    admit the shared subject                once
    validate the whole candidate set        before anything runs
    build one engine and one registry       for every candidate and every replay
    -- only now does an agent run --
    per candidate, in sorted key order: eval_split, persisting every episode
    per task, per unordered candidate pair: reopen both bundles, compare
    write SerialBatchV1, then publish the output directory by one rename

Everything checkable is checked before the first agent process, because the
expensive half of a batch is N candidates x M tasks and a split that names a
missing task should cost nothing to discover.

**A batch mints no identity.** There is no `batch-`, no `candidate-`, no
`agent-` and no `compare-` rung. `SerialBatchV1` is an index over things that
are already addressed: an `env-`, a set of `task-`, the `episode-` ids that were
produced, and the `ComparisonV1` reports read back off them. A `candidate_key`
is a **local report label**, not an agent identity -- renaming one changes the
batch report and nothing else, because it never enters a hash.

Three properties are what make the matrix mean anything:

- **One environment, one subject, one registry.** Every candidate answers the
  same `task-` bytes over the same admitted subject tree, judged by the same
  verifier implementations. Candidate mode rides in `argv`, never in the task,
  so the tasks are byte-identical across the whole matrix.
- **Comparisons replay; they do not trust the run that just happened.** A pair
  is compared by reopening both persisted bundles through `compare_episodes`,
  which reverifies each side to `closed` first. Evidence that will not reverify
  minutes after being written is a refusal, not a number.
- **A refusal is recorded, not smoothed over.** A pair that cannot be compared
  produces a typed entry naming both candidates and the code -- never a
  fabricated relation -- and the rest of the matrix still runs.
"""

import json
import os
import re
import shutil
import tempfile

from . import comparison, evalsplit
from .substrates import AdmissionError

__all__ = [
    "BATCH_VERSION",
    "CANDIDATE_SET_VERSION",
    "BatchError",
    "load_candidate_set",
    "validate_candidate_set",
    "run_batch",
]

BATCH_VERSION = "traaviis.serial-batch.v1"
CANDIDATE_SET_VERSION = "traaviis.candidate-set.v1"

#: A candidate key is a report label *and* a path segment *and* half of a
#: comparison filename, so it is restricted to what is unambiguous in all three
#: roles: it cannot traverse, cannot hide (no leading dot), and cannot contain
#: the `--` that separates the two sides of a pair filename.
_KEY_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")

#: The pair separator. Forbidden inside a key so `<left>--<right>.json` parses
#: back to exactly one pair of keys.
_PAIR_SEP = "--"

#: Exactly the fields a candidate entry may carry. Strict, and for one reason:
#: an `env` key silently ignored would look like a way to give one candidate a
#: different environment, which is precisely the thing a batch must not permit.
_CANDIDATE_FIELDS = ("argv", "candidate_key")


class BatchError(AdmissionError):
    """A typed batch failure. Same shape as an admission failure."""


# ------------------------------------------------------------- candidate set
def validate_candidate_set(doc):
    """Validate a `CandidateSetV1` whole, and return its candidates sorted.

    Whole, not lazily: a malformed fifth candidate must be found before the
    first one runs, or a batch spends four agent runs to discover a typo.

    Returns a list of `{"candidate_key": str, "argv": [str, ...]}` in
    `candidate_key` order, which is the order the batch executes in.
    """
    if not isinstance(doc, dict):
        raise BatchError("CANDIDATE_SET_MALFORMED",
                         "candidate set must be a JSON object, got %s"
                         % type(doc).__name__)
    version = doc.get("candidate_set_version")
    if version != CANDIDATE_SET_VERSION:
        raise BatchError(
            "CANDIDATE_SET_VERSION",
            "expected candidate_set_version %r, got %r"
            % (CANDIDATE_SET_VERSION, version))

    unknown = sorted(set(doc) - {"candidate_set_version", "candidates"})
    if unknown:
        raise BatchError("CANDIDATE_SET_MALFORMED",
                         "unknown candidate set field(s): %s" % ", ".join(unknown))

    candidates = doc.get("candidates")
    if not isinstance(candidates, list):
        raise BatchError("CANDIDATE_SET_MALFORMED",
                         "candidates must be a list, got %s"
                         % type(candidates).__name__)
    if not candidates:
        raise BatchError("CANDIDATE_SET_EMPTY",
                         "candidate set names no candidates")

    out, seen = [], {}
    for position, entry in enumerate(candidates):
        out.append(_candidate(entry, position, seen))
    return sorted(out, key=lambda c: c["candidate_key"])


def _candidate(entry, position, seen):
    where = "candidates[%d]" % position
    if not isinstance(entry, dict):
        raise BatchError("CANDIDATE_SET_MALFORMED",
                         "%s must be an object, got %s"
                         % (where, type(entry).__name__))
    unknown = sorted(set(entry) - set(_CANDIDATE_FIELDS))
    if unknown:
        raise BatchError(
            "CANDIDATE_SET_MALFORMED",
            "%s carries unknown field(s): %s. A candidate is a key and an argv; "
            "anything else would be a per-candidate difference the sealed "
            "environment cannot account for" % (where, ", ".join(unknown)))

    key = entry.get("candidate_key")
    if not isinstance(key, str) or not _KEY_RE.match(key) or _PAIR_SEP in key:
        raise BatchError(
            "CANDIDATE_KEY_INVALID",
            "%s: candidate_key must match %s and must not contain %r; got %r"
            % (where, _KEY_RE.pattern, _PAIR_SEP, key))
    if key in seen:
        raise BatchError(
            "CANDIDATE_KEY_DUPLICATE",
            "candidate_key %r is used by both %s and %s; keys label the columns "
            "of the matrix, so two columns cannot share one" % (key, seen[key], where))
    seen[key] = where

    argv = entry.get("argv")
    if isinstance(argv, str):
        raise BatchError(
            "CANDIDATE_ARGV_INVALID",
            "%s: argv must be a list of arguments, not a shell string. A string "
            "would have to be split by some shell's quoting rules, and the batch "
            "does not have a shell" % where)
    if not isinstance(argv, list) or not argv:
        raise BatchError("CANDIDATE_ARGV_INVALID",
                         "%s: argv must be a non-empty list" % where)
    for i, arg in enumerate(argv):
        if not isinstance(arg, str):
            raise BatchError("CANDIDATE_ARGV_INVALID",
                             "%s: argv[%d] must be a string, got %s"
                             % (where, i, type(arg).__name__))
    return {"candidate_key": key, "argv": list(argv)}


def load_candidate_set(path):
    """Read and validate a `CandidateSetV1` from disk."""
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
    except OSError as ex:
        raise BatchError("CANDIDATE_SET_UNREADABLE",
                         "could not read candidate set: %s" % ex)
    except (ValueError, UnicodeDecodeError) as ex:
        raise BatchError("CANDIDATE_SET_MALFORMED",
                         "candidate set is not valid JSON: %s" % ex)
    return validate_candidate_set(doc)


# -------------------------------------------------------------------- batch
def run_batch(package, split, candidates, output, *, engine=None, registry=None,
              platform="unknown", toolchain=None, on_candidate=None,
              on_pair=None, on_wiring_notes=None):
    """Run every candidate over `split` and compare them. Returns `SerialBatchV1`.

    `output` is **mandatory**. A comparison is a reading of two *persisted*
    closures, so a batch that kept nothing would have nothing to compare; there
    is no in-memory shortcut, because taking one would mean ranking two episodes
    on the strength of the run that just produced them rather than on evidence
    that reopens.

    One seam supplies verifiers: `registry=` (or `engine=`, from which one
    registry is built here). There is deliberately no caller-injected verifier
    parameter -- the attestation in the report is derived from the single
    registry that both ran and replayed every episode.

    Raises `BatchError` / `AdmissionError` before any agent runs if the package,
    the split, the subject tree, the candidate set or the output path does not
    admit. An infrastructure failure part-way through aborts and leaves no
    output directory; a *comparison* failure is recorded as a typed refusal and
    the batch continues.
    """
    if not output:
        raise BatchError("OUTPUT_REQUIRED",
                         "batch requires --output: comparisons replay persisted "
                         "episode bundles, so there is nothing to compare without "
                         "somewhere to keep them")
    output = os.path.abspath(output)
    if os.path.exists(output):
        raise BatchError("OUTPUT_EXISTS",
                         "refusing to write into an existing path: %s" % output)

    # --- preflight. Nothing below this block launches a process. -----------
    # `eval_split` re-opens and re-admits per candidate, which is a *re-check*,
    # not a substitute for this one: doing it here is what makes a bad package
    # or a bad split cost zero agent runs instead of one.
    manifest, _artifacts = evalsplit.open_environment(package, engine=engine)
    task_ids = evalsplit.resolve_split(manifest, split)
    candidates = _admit_candidates(candidates)

    if registry is None:
        from . import wiring
        registry = wiring.default_registry(engine)

    parent = os.path.dirname(output) or "."
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".trvs-batch-", dir=parent)
    try:
        report = _fill(staging, package, split, manifest, task_ids, candidates,
                       registry=registry, engine=engine, platform=platform,
                       toolchain=toolchain, on_candidate=on_candidate,
                       on_pair=on_pair, on_wiring_notes=on_wiring_notes)
        # One rename publishes the whole matrix. A reader never sees a batch
        # with three of four candidates in it, and an aborted batch leaves no
        # directory to be mistaken for a finished one.
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return report


def _admit_candidates(candidates):
    """Accept either a validated list or a raw `CandidateSetV1` document."""
    if isinstance(candidates, dict):
        return validate_candidate_set(candidates)
    if isinstance(candidates, list):
        # Re-validate: a caller-built list has not been through the gate, and a
        # duplicate key would otherwise collide two candidates onto one output
        # directory -- silently, and with the second overwriting the first.
        return validate_candidate_set(
            {"candidate_set_version": CANDIDATE_SET_VERSION,
             "candidates": candidates})
    raise BatchError("CANDIDATE_SET_MALFORMED",
                     "candidates must be a CandidateSetV1 object or a list of "
                     "candidate entries, got %s" % type(candidates).__name__)


def _fill(staging, package, split, manifest, task_ids, candidates, *, registry,
          engine, platform, toolchain, on_candidate, on_pair, on_wiring_notes):
    """Run the matrix into `staging`. Returns the `SerialBatchV1`."""
    order = [c["candidate_key"] for c in candidates]
    evaluations, bundles, losses = {}, {}, {}

    for index, candidate in enumerate(candidates):
        key = candidate["candidate_key"]
        if on_candidate is not None:
            on_candidate(index, len(candidates), key, None)
        home = os.path.join(staging, "candidates", key)
        episodes_dir = os.path.join(home, "episodes")
        os.makedirs(episodes_dir)
        report = evalsplit.eval_split(
            package, split, candidate["argv"], output=episodes_dir,
            platform=platform, toolchain=toolchain, engine=engine,
            registry=registry, on_wiring_notes=on_wiring_notes)
        evalsplit.write_evaluation(report, os.path.join(home, "evaluation.json"))
        evaluations[key] = report
        bundles[key], losses[key] = _retained(key, report, episodes_dir)
        if on_candidate is not None:
            on_candidate(index, len(candidates), key, report)

    tasks = [_task_row(staging, task_id, order, evaluations, bundles, losses,
                       registry, on_pair)
             for task_id in task_ids]

    batch = {
        "batch_version": BATCH_VERSION,
        "substrate_profile": manifest["substrate_profile"],
        "env_id": manifest["env_id"],
        "split": split,
        "task_ids": list(task_ids),
        "runtime_context": _runtime_context(registry),
        "candidate_order": list(order),
        "candidates": [_candidate_row(key, evaluations[key]) for key in order],
        "tasks": tasks,
    }
    batch["totals"] = _totals(batch)
    _write_json(batch, os.path.join(staging, "batch.json"))
    return batch


def _retained(key, report, episodes_dir):
    """`({task_id: bundle_dir}, {task_id: why_not})` for one candidate.

    Two ways a cell of the matrix can end up with no bundle, and they are not
    the same event:

    - the **candidate** produced nothing to keep -- it crashed, or the task did
      not evaluate. That is a result about this candidate, so the batch records
      it, keeps going, and every pair that needed it becomes a typed refusal.
    - the **batch** could not write what it was handed. That is infrastructure:
      a read-only output, a full disk. Continuing would publish a matrix full of
      refusals that blame the evidence for a failure of the machine writing it,
      so it aborts and no output directory appears.

    `persistence.reason` is what separates them, rather than the wording of an
    error message.
    """
    bundles, losses = {}, {}
    for entry in report["episodes"]:
        persistence = entry.get("persistence") or {}
        if persistence.get("status") == "closed" and entry.get("bundle"):
            bundles[entry["task_id"]] = os.path.join(
                episodes_dir, entry["bundle"])
            continue
        if persistence.get("reason") == "write_failed":
            raise BatchError(
                "EPISODE_NOT_PERSISTED",
                "candidate %r evaluated %s but its episode could not be "
                "written: %s" % (key, entry["task_id"], persistence.get("error")),
                {"candidate_key": key, "task_id": entry["task_id"]})
        # The *reason code*, not the message. A launch failure's message names
        # the argv that could not be launched, and an absolute host path in
        # `batch.json` would make two identical batches on two machines differ.
        # The prose is kept, unabridged, in that candidate's `evaluation.json`.
        losses[entry["task_id"]] = persistence.get("reason") or "not_retained"
    return bundles, losses


def _candidate_row(key, report):
    return {
        "candidate_key": key,
        # The member path, not the document: `EvaluationV1` is written in full
        # beside the episodes it indexes, and copying it in here would give a
        # reader two copies to keep in agreement.
        "evaluation": "candidates/%s/evaluation.json" % key,
        "episode_ids": [e["episode_id"] for e in report["episodes"]],
        "totals": dict(report["totals"]),
    }


def _task_row(staging, task_id, order, evaluations, bundles, losses, registry,
              on_pair):
    """One row of the matrix: every candidate's episode, and every pair."""
    episodes = {}
    for key in order:
        entry = next(e for e in evaluations[key]["episodes"]
                     if e["task_id"] == task_id)
        episodes[key] = {"episode_id": entry["episode_id"],
                         "status": entry["status"], "reward": entry["reward"]}

    comparisons, refusals = [], []
    for left, right in _pairs(order):
        member = "comparisons/%s/%s%s%s.json" % (task_id, left, _PAIR_SEP, right)
        missing = [k for k in (left, right) if task_id not in bundles[k]]
        if missing:
            # There is no second reading to compare against. Naming which side
            # is absent, and why, is the whole content of the refusal.
            refusals.append({
                "left_candidate": left, "right_candidate": right,
                "code": "EPISODE_UNAVAILABLE",
                "detail": "no retained episode bundle for %s"
                          % "; ".join(
                              "%s (%s)" % (k, losses[k].get(task_id,
                                                            "not_retained"))
                              for k in missing)})
            if on_pair is not None:
                on_pair(task_id, left, right, None, None)
            continue
        try:
            report = comparison.compare_episodes(
                bundles[left][task_id], bundles[right][task_id],
                registry=registry)
        except comparison.ComparisonError as ex:
            # No fabricated relation, and no aborted batch: the rest of the
            # matrix is still evidence, and this pair's failure is itself a
            # finding about these two bundles.
            refusals.append({"left_candidate": left, "right_candidate": right,
                             "code": ex.code, "detail": str(ex)})
            if on_pair is not None:
                on_pair(task_id, left, right, None, ex)
            continue
        _write_json(report, os.path.join(staging, member.replace("/", os.sep)))
        comparisons.append({"left_candidate": left, "right_candidate": right,
                            "relation": dict(report["relation"]),
                            "comparison_member": member})
        if on_pair is not None:
            on_pair(task_id, left, right, report, None)

    return {"task_id": task_id, "episodes": episodes,
            "comparisons": comparisons, "refusals": refusals}


def _pairs(order):
    """Every unordered pair, once, in sorted-key direction.

    Once, because `compare(a, b)` and `compare(b, a)` are the same reading with
    the sign flipped, and writing both would let the two disagree.
    """
    return [(order[i], order[j])
            for i in range(len(order)) for j in range(i + 1, len(order))]


def _totals(batch):
    episodes = [e for row in batch["tasks"] for e in row["episodes"].values()]
    scored = [e for e in episodes if isinstance(e["reward"], (int, float))
              and not isinstance(e["reward"], bool)]
    return {
        "candidate_count": len(batch["candidate_order"]),
        "task_count": len(batch["task_ids"]),
        "episode_count": len(episodes),
        "scored_count": len(scored),
        "unscored_count": len(episodes) - len(scored),
        "comparison_count": sum(len(r["comparisons"]) for r in batch["tasks"]),
        "refusal_count": sum(len(r["refusals"]) for r in batch["tasks"]),
    }


def _runtime_context(registry):
    """Attest the one registry that ran *and* replayed every episode."""
    return {
        "registry_version": getattr(registry, "registry_version", None),
        "wiring": "registry",
        "verifiers_available": list(registry.available()),
        "verifier_versions": dict(sorted(registry.versions().items())),
    }


def _write_json(document, path):
    """Write one member atomically inside the staging tree."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".trvs-batch-member-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write((json.dumps(document, indent=2, sort_keys=True,
                                 ensure_ascii=False) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path
