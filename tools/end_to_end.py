"""One pass through the whole stack, with every id pinned.

    python3 tools/end_to_end.py             # run the pass, check it against the pins
    python3 tools/end_to_end.py --write     # (re)write the pins from this run
    python3 tools/end_to_end.py --json      # the same run as one JSON object
    python3 tools/end_to_end.py --keep      # leave the scratch package on disk

Why this exists
---------------

Four repositories are organized around a claim -- *WRLM proposes, WRL seals, TRVM
reduces, TRAAVIIS admits* -- and until this script ran, the claim had never been
executed. Each of the four has a green battery. A battery that passes in four
repositories separately is precisely the evidence a layering error survives:
every seam is tested from one side, by the people who own that side, using
fixtures that side wrote.

So this is not a test of any one repository. It is a test of the *joins*, and it
is written to be run by somebody who has never seen any of them: it prints every
intermediate identity, and it re-derives rather than trusts at each step.

What it found
-------------

Two things, and the second is the point of writing it down.

**The joins hold.** WRLM proposes a world offline, carrying a `sem-` it was
handed at capture time. Forge, asked independently, lowers that same source to
the same `sem-`. TRAAVIIS's packer, re-lowering a third time from the bytes that
actually landed on disk, agrees again. Three derivations, one id.

**The chain does not reach an episode, and stops by name.** `trvs eval` refuses a
`trvm.world.v1` package with `SUBSTRATE_NOT_EVALUABLE`, because no
`EpisodeKernelV1` implements TRVM episode semantics -- the D5 process model is
ruled and unbuilt. So the honest end of this pass is `bundle-`, not `episode-`.

That refusal is **asserted here as a pinned outcome**, not tolerated. If somebody
builds the TRVM kernel, this script fails, and the failure is the notification
that the pass can now go further. A demonstration that quietly stopped early
would be a demonstration that could not tell the difference between "not built
yet" and "broken".

Exit codes, matching the house three-way reading
------------------------------------------------

    0   reproduced   every stage ran and every id matched its pin
    1   drift        a stage ran and disagreed -- evidence, and a real finding
    2   unavailable  a stage could not run at all (no engine, no WRLM, no pins)

`unavailable` is kept distinct from `drift` for the reason it is kept distinct
everywhere else in this repository: a missing engine is not evidence that an id
moved, and a run that cannot reach a claim must never be reported as a run that
refuted it.

What a pin failure means
------------------------

Every id below is a function of inputs this file records alongside it -- the
coverage spec version, the generator id and version, the sha256 of the proved
pool, and the engine's own `bench_version`. A drift report names the inputs too,
so the first question ("what moved?") is answerable from the failure output
without re-running anything.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

# Import from live sibling trees without leaving `__pycache__` in them. WRLM
# records this as a standing rule about its own engine, and it is right: a tool
# that inspects a working tree should not modify it.
sys.dont_write_bytecode = True

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINS = os.path.join(REPO, "examples", "end-to-end", "PINNED.json")

# Running `python3 tools/end_to_end.py` puts `tools/` on `sys.path`, not the repo
# root, so `traaviis` would be unimportable from the one invocation the docstring
# tells a stranger to use. Added here rather than documented as a prerequisite:
# an instruction to set PYTHONPATH first is an instruction that gets skipped.
if REPO not in sys.path:
    sys.path.insert(0, REPO)

#: The corpus seed. Everything WRLM derives is a function of this string, so it
#: is written here rather than passed in: a demonstration whose contents depend
#: on an argument nobody recorded is not reproducible.
CORPUS_SEED = "traaviis-end-to-end-v1"

#: The scaffold template for a WallRiderLang world. `evidence-residency` is the
#: other one, and it is deliberately not what this pass exercises -- the point is
#: to carry a *TRVM* world across the joins.
TEMPLATE = "golden-spinner"

PINS_VERSION = "traaviis.end-to-end-pins.v1"


class Unavailable(Exception):
    """A stage could not run. Exit 2, never exit 1."""


# ------------------------------------------------------------------ locating

def _is_wrlm_dir(path):
    """A WRLM dir is usable only if it exposes the modules this pass drives."""
    return bool(path) and all(
        os.path.isfile(os.path.join(path, name))
        for name in ("generator.py", "coverage.py", "taskbundle.py"))


def _wrlm_candidates():
    """Mirror `traaviis.engine._search_candidates`, one directory over.

    Deliberately the same shape as the engine locator rather than a new
    convention: somebody who has already set `TRVS_FORGE_DIR` should not have to
    learn a second, differently-spelled rule for the repository sitting beside
    it.
    """
    seen = []

    def add(p):
        if p:
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.append(ap)

    add(os.path.join(os.path.dirname(REPO), "TRVM", "wrlm"))
    cur = os.path.abspath(os.getcwd())
    while True:
        add(os.path.join(cur, "TRVM", "wrlm"))
        add(os.path.join(cur, "wrlm"))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return seen


def resolve_wrlm():
    """Return the WRLM package directory, or raise `Unavailable`.

    `$TRVS_WRLM_DIR` overrides and, if set, must be valid: falling through to a
    different WRLM after being told which one to use would silently produce a
    corpus from the wrong generator, and every id below would move for a reason
    the output did not mention.
    """
    override = os.environ.get("TRVS_WRLM_DIR")
    if override:
        if not os.path.isdir(override):
            raise Unavailable("TRVS_WRLM_DIR is not a directory: %s" % override)
        if not _is_wrlm_dir(override):
            raise Unavailable(
                "TRVS_WRLM_DIR is not a WRLM package (no generator.py): %s"
                % override)
        return os.path.abspath(override)
    for c in _wrlm_candidates():
        if _is_wrlm_dir(c):
            return c
    raise Unavailable(
        "could not locate WRLM.\n"
        "  Set TRVS_WRLM_DIR to your TRVM/wrlm directory, e.g.\n"
        "    export TRVS_WRLM_DIR=/path/to/TRVM/wrlm")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(data, what, **bounds):
    """Parse through the package's bounded boundary, refusing as `Unavailable`.

    Law A scopes itself to `traaviis/` and puts `tools/` outside, which is right
    -- `tools/` is the harness, not the shipped package -- so nothing obliged
    this file to use the boundary. Its *reasoning* still applies: a malformed
    pool or a truncated pins file read with a bare `json.load` raises an untyped
    exception, the tool dies, and a crash is indistinguishable from "this script
    is broken" when the honest answer is "your input is malformed, exit 2".

    Routing through `load_json_bounded` makes that a typed refusal for free,
    because `BoundedJsonError` is the only thing it raises.
    """
    from traaviis import boundedjson
    try:
        return boundedjson.load_json_bounded(data, **bounds)
    except boundedjson.BoundedJsonError as ex:
        raise Unavailable("%s is not an in-bounds JSON document: %s"
                          % (what, ex.message))


# -------------------------------------------------------------- the stages

def stage_wrlm():
    """WRLM proposes one task, offline, with no engine present.

    The pool is a set of `WorldRecordV1`s whose `sem-` was proved by something
    that had a Forge, once, at capture. From here they are data. That is the
    whole reason `worldrecord.py` was written before the generator was.
    """
    wrlm_dir = resolve_wrlm()
    trvm_root = os.path.dirname(wrlm_dir)
    if trvm_root not in sys.path:
        sys.path.insert(0, trvm_root)
    try:
        from wrlm import coverage as C
        from wrlm import generator as GEN
        from wrlm import taskbundle as T
    except Exception as ex:
        raise Unavailable("failed to import WRLM at %s\n  %s: %s"
                          % (wrlm_dir, type(ex).__name__, ex))

    pool_path = os.path.join(wrlm_dir, "fixtures", "pool_records.json")
    if not os.path.isfile(pool_path):
        raise Unavailable("WRLM has no proved pool at %s" % pool_path)
    with open(pool_path, "rb") as fh:
        raw = fh.read()
    # Default bounds, measured rather than assumed: the 58-record pool is 349 KB
    # and parses inside every one of them. An earlier draft raised `max_nodes`
    # here on the guess that a pool of artifacts would exceed 100,000 nodes; it
    # does not, and a loosened bound nobody needed is a bound nobody will
    # tighten back.
    pool = _load_json(raw, "the WRLM proved pool")

    spec = C.CoverageSpecV1(corpus_seed=CORPUS_SEED)
    corpus, ledger = GEN.generate_corpus(pool, spec, limit=1)
    if not corpus:
        raise Unavailable(
            "the generator accepted nothing at seed %r; the pass has no task "
            "to carry" % CORPUS_SEED)
    item = corpus[0]
    task = item["task"]

    # Re-derive rather than trust. `task_bundle_id` is a function of the bundle's
    # own bytes, so a corpus entry that disagrees with its own task is a WRLM
    # defect this pass should surface rather than carry forward.
    rederived = T.task_bundle_id(task)
    if rederived != item["task_id"]:
        return None, {
            "stage": "wrlm",
            "detail": "corpus task_id %s does not re-derive from its bytes (%s)"
                      % (item["task_id"], rederived)}

    return {
        "coverage_spec_version": C.COVERAGE_SPEC_VERSION,
        "generator_id": GEN.GENERATOR_ID,
        "generator_version": GEN.GENERATOR_VERSION,
        "corpus_seed": CORPUS_SEED,
        "pool_records": len(pool),
        # The binding gate, recorded rather than discarded. A record whose `sem-`
        # was inherited from a publisher rather than re-lowered is `asserted`,
        # and a corpus built on those is measuring the publisher. WRLM counts the
        # exclusions instead of quietly dropping them, for the reason its own
        # source gives: a pool that halved on the way in and a pool that was
        # always small produce the same corpus and call for opposite responses.
        # Pinning both means a pool that starts admitting asserted worlds drifts
        # this pass rather than passing it with weaker evidence.
        "pool_admitted": getattr(ledger, "pool_admitted", None),
        "pool_excluded": getattr(ledger, "pool_excluded", None),
        "pool_sha256": _sha256_file(pool_path),
        "cell": item["cell"],
        "goal_id": task["objective"].get("goal_spec_id"),
        "task_id": item["task_id"],
        "case_id": item["case_id"],
        "declared_semantic_id": task["base_world"]["semantic_id"],
        "source_sha256": hashlib.sha256(
            task["base_world"]["source"].encode("utf-8")).hexdigest(),
        "source_bytes": len(task["base_world"]["source"].encode("utf-8")),
    }, task["base_world"]["source"]


def stage_forge(source):
    """Forge lowers the proposed source independently.

    This is the join that had never been executed. WRLM carries a `sem-` it was
    handed; Forge is asked, from the source alone, what that world's identity is.
    Agreement here is the whole WRL half of the claim.
    """
    try:
        from traaviis import engine
    except Exception as ex:
        raise Unavailable("failed to import traaviis: %s" % ex)

    # `try_load` is the soft locator: on an invalid `$TRVS_FORGE_DIR` it does not
    # fail, it keeps searching. That is right for its own caller -- the identity
    # verifier runs under `needs_engine=False` and must be able to catch an
    # absent engine -- and wrong here. An operator who names an engine and gets a
    # silently different one has been handed ids derived from a build they did
    # not choose, which is the same hazard `resolve_wrlm` refuses above. So the
    # override is honoured strictly here, matching `engine._resolve_forge_dir`,
    # before the soft path is allowed to run at all.
    #
    # Found by testing this script's own failure modes: `TRVS_FORGE_DIR=/nonexistent`
    # exited 0 and reported a reproduced pass.
    override = os.environ.get("TRVS_FORGE_DIR")
    if override:
        if not os.path.isdir(override):
            raise Unavailable("TRVS_FORGE_DIR is not a directory: %s" % override)
        if not os.path.isfile(os.path.join(override, "forge_api.py")):
            raise Unavailable(
                "TRVS_FORGE_DIR has no forge_api.py (not a Forge engine): %s"
                % override)

    api = engine.try_load()
    if api is None:
        raise Unavailable(
            "no Forge engine reachable.\n"
            "  Set TRVS_FORGE_DIR to your TRVM/forge directory.")
    info = api.engine_info()
    lowered = api.lower_source(source)
    run = api.run_source(source)
    return {
        "engine_api_version": info.get("api_version"),
        "bench_version": info.get("bench_version"),
        "semantic_id": lowered["semantic_artifact_id"],
        "reducer": run.get("reducer"),
        "scenario_digest": run.get("scenario_digest"),
        "epochs": len(run.get("epochs") or []),
        "films": [e["film"] for e in (run.get("epochs") or [])],
    }


def _trvs(args, cwd=None):
    """Run the real CLI, the way a stranger would.

    The TRAAVIIS half goes through `trvs` rather than through imports on purpose.
    A demonstration that reached past the command line would be demonstrating a
    surface nobody uses, and would not notice a CLI that stopped wiring its own
    library correctly.
    """
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", "-m", "traaviis.cli"] + args,
                          cwd=cwd or REPO, capture_output=True, text=True,
                          env=env)


def stage_traaviis(source, workdir):
    """Scaffold, pack, verify -- and then meet the seam that is not built.

    `init` mints no identity by design (§6: a scaffold cannot know the hash of
    bytes not yet written, and a placeholder hash is a lie that later verifies),
    so the world is written into the scaffold and `pack` closes the ids from what
    actually landed on disk.
    """
    env_dir = os.path.join(workdir, "env")
    pkg_dir = os.path.join(workdir, "pkg")

    r = _trvs(["init", "--template", TEMPLATE, env_dir])
    if r.returncode != 0:
        raise Unavailable("trvs init failed (rc=%d)\n%s" % (r.returncode, r.stderr))

    world = os.path.join(env_dir, "world.wrl")
    if not os.path.isfile(world):
        raise Unavailable("the %s scaffold wrote no world.wrl" % TEMPLATE)
    with open(world, "w") as fh:
        fh.write(source)

    r = _trvs(["pack", "--json", env_dir, pkg_dir])
    if r.returncode != 0:
        return None, {"stage": "pack",
                      "detail": "trvs pack refused (rc=%d): %s"
                                % (r.returncode, (r.stderr or r.stdout).strip())}
    # `BoundedJsonError` subclasses `ValueError`, so this handler types the
    # refusal without being widened -- the property that made the package's own
    # incremental sweep safe, working here for the same reason.
    try:
        packed = _load_json(r.stdout, "the output of `trvs pack --json`")
    except Unavailable as ex:
        return None, {"stage": "pack", "detail": str(ex)}
    except ValueError as ex:
        return None, {"stage": "pack",
                      "detail": "pack --json emitted no JSON object: %s" % ex}

    vb = _trvs(["verify-bundle", pkg_dir])

    # The end of the chain. Asserted, not tolerated: `--split train` names a
    # split this package does not have, and the refusal must still be the
    # SUBSTRATE one -- if the substrate were evaluable, the failure would be
    # about the missing split instead, and that difference is the signal.
    ev = _trvs(["eval", pkg_dir, "--split", "train",
                "--output", os.path.join(workdir, "out"),
                "--agent", "/bin/true"])
    ev_text = (ev.stderr or "") + (ev.stdout or "")
    refusal = ("SUBSTRATE_NOT_EVALUABLE"
               if "SUBSTRATE_NOT_EVALUABLE" in ev_text else None)

    return {
        "env_id": packed.get("env_id"),
        "bundle_id": packed.get("bundle_id"),
        "substrate_profile": packed.get("substrate_profile"),
        "packed_semantic_id": (packed.get("subject") or {}).get(
            "semantic_artifact_id"),
        "members": sorted(packed.get("files") or []),
        "verify_bundle_exit": vb.returncode,
        "eval_exit": ev.returncode,
        "eval_refusal": refusal,
    }, None


# -------------------------------------------------------------- the report

def run_pass(workdir):
    """Execute every stage, returning the observation dict.

    Raises `Unavailable` for anything that could not run. Returns
    `(observed, defect)` where a non-None `defect` is a disagreement found
    *within* a stage -- which is drift, not unavailability.
    """
    wrlm, second = stage_wrlm()
    if wrlm is None:
        return None, second
    source = second

    forge = stage_forge(source)
    traaviis, defect = stage_traaviis(source, workdir)
    if traaviis is None:
        return None, defect

    observed = {
        "pins_version": PINS_VERSION,
        "wrlm": wrlm,
        "forge": forge,
        "traaviis": traaviis,
    }

    # The three-derivation agreement. Checked here rather than left to the pins,
    # because it is a property of the run and not of any recorded value: a pass
    # in which the three disagree is broken even on a machine that has never
    # seen PINNED.json.
    ids = {
        "wrlm declared": wrlm["declared_semantic_id"],
        "forge lowered": forge["semantic_id"],
        "pack re-lowered": traaviis["packed_semantic_id"],
    }
    if len(set(ids.values())) != 1:
        return observed, {
            "stage": "join",
            "detail": "the three derivations of the world's identity disagree: "
                      + "; ".join("%s=%s" % (k, v) for k, v in ids.items())}
    observed["semantic_id_agreed"] = forge["semantic_id"]
    return observed, None


#: Fields excluded from pin comparison, with the reason each is excluded.
#:
#: Nothing is excluded for being inconvenient. `members` and `epochs` and every
#: id ARE compared. The one exclusion is the engine build string, which is
#: reported in both directions and compared separately, so that "the engine
#: moved" is a distinguishable message rather than one more mismatched line.
_REPORT_ONLY = (("forge", "bench_version"),)


def _walk(prefix, node, out):
    if isinstance(node, dict):
        for k in sorted(node):
            _walk(prefix + (k,), node[k], out)
    else:
        out[prefix] = node


def compare(observed, pinned):
    """Return the list of drifted fields, deepest-first, as (path, pin, got)."""
    a, b = {}, {}
    _walk((), pinned, a)
    _walk((), observed, b)
    skip = set(_REPORT_ONLY)
    drift = []
    for path in sorted(set(a) | set(b)):
        if path[:2] in skip or path[:1] in skip:
            continue
        if a.get(path) != b.get(path):
            drift.append((".".join(str(p) for p in path),
                          a.get(path, "<absent>"), b.get(path, "<absent>")))
    return drift


def _row(label, value):
    return "  %-16s %s" % (label, value)


def render(observed):
    w, f, t = observed["wrlm"], observed["forge"], observed["traaviis"]
    L = []
    L.append("WRLM proposes  (offline -- no engine is loaded for this stage)")
    L.append(_row("spec", "%s / %s v%s" % (w["coverage_spec_version"],
                                           w["generator_id"],
                                           w["generator_version"])))
    L.append(_row("seed", w["corpus_seed"]))
    L.append(_row("pool", "%d records, %s admitted / %s excluded by binding, "
                          "sha256 %s"
                  % (w["pool_records"], w["pool_admitted"], w["pool_excluded"],
                     w["pool_sha256"][:16])))
    L.append(_row("cell", json.dumps(w["cell"], sort_keys=True)))
    L.append(_row("goal-", w["goal_id"]))
    L.append(_row("task-", w["task_id"]))
    L.append(_row("case-", w["case_id"]))
    L.append("")
    L.append("Forge seals, TRVM folds")
    L.append(_row("engine", "%s (api %s)" % (f["bench_version"],
                                             f["engine_api_version"])))
    L.append(_row("sem-", f["semantic_id"]))
    L.append(_row("scen-", f["scenario_digest"]))
    L.append(_row("film", "%d epochs, %s reducer, last %s"
                  % (f["epochs"], f["reducer"],
                     (f["films"] or ["-"])[-1][:16])))
    L.append("")
    L.append("TRAAVIIS admits")
    L.append(_row("substrate", t["substrate_profile"]))
    L.append(_row("env-", t["env_id"]))
    L.append(_row("bundle-", t["bundle_id"]))
    L.append(_row("members", ", ".join(t["members"])))
    L.append(_row("verify-bundle", "exit %d%s"
                  % (t["verify_bundle_exit"],
                     "  closed" if t["verify_bundle_exit"] == 0 else "")))
    L.append("")
    L.append("The chain ends here, by name")
    L.append(_row("eval", "exit %d  [%s]" % (t["eval_exit"],
                                             t["eval_refusal"] or "no refusal")))
    L.append("    a trvm.world.v1 package packs and verifies, and cannot be")
    L.append("    evaluated: no EpisodeKernelV1 implements its episode")
    L.append("    semantics. The honest end of this pass is bundle-, not")
    L.append("    episode-, and that is asserted rather than assumed.")
    L.append("")
    L.append("Three independent derivations of one world identity")
    L.append(_row("wrlm carried", observed["wrlm"]["declared_semantic_id"][:28] + "..."))
    L.append(_row("forge lowered", f["semantic_id"][:28] + "..."))
    L.append(_row("pack re-lowered", t["packed_semantic_id"][:28] + "..."))
    L.append(_row("", "agree"))
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="end_to_end.py",
        description="Run one WRLM -> WRL -> TRVM -> TRAAVIIS pass and check "
                    "every id against examples/end-to-end/PINNED.json.")
    ap.add_argument("--write", action="store_true",
                    help="rewrite the pins from this run instead of checking")
    ap.add_argument("--json", action="store_true",
                    help="emit the observation as one JSON object")
    ap.add_argument("--keep", action="store_true",
                    help="leave the scratch environment and package on disk")
    args = ap.parse_args(argv)

    workdir = tempfile.mkdtemp(prefix="traaviis-e2e-")
    try:
        # The handler wraps the WHOLE body, not just `run_pass`. It used to wrap
        # only the pass, which left the pins read below able to raise
        # `Unavailable` past it and die in a traceback -- the one outcome that
        # reports neither of this tool's three readings.
        try:
            return _main_body(args, workdir)
        except Unavailable as ex:
            sys.stderr.write("end_to_end: unavailable -- %s\n" % ex)
            return 2
    finally:
        if args.keep:
            sys.stdout.write("  scratch kept at %s\n" % workdir)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _main_body(args, workdir):
    observed, defect = run_pass(workdir)

    # A stage that found a disagreement *inside itself* returns no observation,
    # and there is nothing to render. Handled before the renderer rather than
    # inside it: `render(None)` raised `TypeError` and took the tool down with a
    # traceback on precisely the path that exists to report a finding, which is
    # the same "the reporter erased the result" shape `evalone` closed as route 5.
    if observed is None:
        sys.stderr.write("end_to_end: DRIFT in stage %s\n  %s\n"
                         % (defect["stage"], defect["detail"]))
        return 1

    # Under `--json`, stdout carries the observation and NOTHING else; every
    # human line goes to stderr. The same split `trvs eval-one --json` makes,
    # for the same reason: a caller piping this into a parser should not have to
    # strip a success sentence off the end, and the first draft of this tool
    # emitted both and was unparseable.
    note = sys.stderr if args.json else sys.stdout
    if args.json:
        sys.stdout.write(json.dumps(observed, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render(observed) + "\n")

    if defect is not None:
        sys.stderr.write("\nend_to_end: DRIFT in stage %s\n  %s\n"
                         % (defect["stage"], defect["detail"]))
        return 1

    if args.write:
        os.makedirs(os.path.dirname(PINS), exist_ok=True)
        with open(PINS, "w") as fh:
            fh.write(json.dumps(observed, indent=2, sort_keys=True) + "\n")
        note.write("\npins written: %s\n" % os.path.relpath(PINS, REPO))
        return 0

    if not os.path.isfile(PINS):
        sys.stderr.write(
            "\nend_to_end: unavailable -- no pins at %s\n"
            "  Run once with --write to record them.\n"
            % os.path.relpath(PINS, REPO))
        return 2
    with open(PINS, "rb") as fh:
        pinned = _load_json(fh.read(), "the pins file")

    drift = compare(observed, pinned)
    pin_engine = (pinned.get("forge") or {}).get("bench_version")
    got_engine = observed["forge"]["bench_version"]
    if pin_engine != got_engine:
        note.write("\n  note  engine build moved: pinned %s, running %s\n"
                   % (pin_engine, got_engine))

    if drift:
        sys.stderr.write("\nend_to_end: DRIFT -- %d field%s moved\n"
                         % (len(drift), "" if len(drift) == 1 else "s"))
        for path, pin, got in drift:
            sys.stderr.write("  %s\n    pinned %s\n    got    %s\n"
                             % (path, pin, got))
        return 1

    note.write("\n  reproduced: every id matches "
               + os.path.relpath(PINS, REPO) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
