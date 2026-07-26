"""trvs / traaviis -- a local-first toolchain for evidence-grade agent evaluation.

TRAAVIIS evaluates a user-supplied agent against a frozen subject and proves what
happened, with TRVM worlds as its exact-replay substrate. The world commands run
real over the Forge engine's stable public API (traaviis.engine -> forge_api):
`id`/`inspect` re-lower the source, `run` folds it through the ic_ref reducer, and
`verify` cross-checks ic_ref against the native ic32 reducer and the independent
Fixture oracle. `eval-one` admits an eval bundle, runs the agent, and folds one
content-addressed episode receipt.

    trvs doctor              -- engine location, versions, verifier availability
    trvs init    DIR         -- scaffold an environment for a substrate template
    trvs pack    ENV DIR     -- close a scaffold into a verified, reopened package
    trvs id      WORLD.wrl   -- the world's SemanticArtifactID (pure identity)
    trvs inspect WORLD.wrl   -- actors, edges, config, diagnostics
    trvs run     WORLD.wrl   -- lower + deterministically fold; per-epoch film
    trvs verify  WORLD.wrl   -- reference / native / oracle agreement (strict)
    trvs replay  WORLD.wrl   -- re-fold and print the film strip; --expect asserts
    trvs diff    A.wrl B.wrl -- compare two worlds' identity + per-epoch films
    trvs eval-one BUNDLE     -- run one trusted-local Residency episode over a bundle
    trvs eval    PKG --split -- run an agent over a split; one episode per task
    trvs verify-episode DIR  -- re-verify a saved episode bundle without an agent run

TRAAVIIS does not embed, select, or route a model. Evaluation runs a user-supplied
agent command, which may use one; the world commands call none.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

from . import __version__, engine as _engine
from . import scaffold as _scaffold
from .paths import PathError, safe_relposix

CHECK, CROSS, SKIP = "\u2713", "\u2717", "- skipped"

# Exit codes (verify contract): 0 all agree, 1 ran+disagree, 2 unavailable/error.
EXIT_OK, EXIT_DISAGREE, EXIT_UNAVAILABLE = 0, 1, 2

# The operational bundle manifest: names the files/dirs the eval-one loader reads.
# It is NOT a content-addressed artifact — just a descriptor. Every path it names
# must be a safe relative POSIX path inside the bundle (no absolute, no ``..``).
EVAL_BUNDLE_VERSION = "traaviis.eval-bundle.v1"


def _read_source(path):
    if not os.path.isfile(path):
        sys.stderr.write("trvs: no such world file: %s\n" % path)
        raise SystemExit(2)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _short(h, n=24):
    if not isinstance(h, str):
        return str(h)
    if "-" in h:
        kind, body = h.split("-", 1)
        return kind + "-" + body[:n]
    return h[:n]


def _field(label, value):
    return "  %-13s %s" % (label, value)


def _engine_error(payload):
    """Format an engine error payload into (code, message, diagnostics)."""
    pres = payload.get("error_presentation") or {}
    code = pres.get("code")
    msg = payload.get("error") or "unknown engine error"
    diags = [d.get("render") or d.get("message")
             for d in payload.get("diagnostics", [])]
    return code, msg, diags


def _fail(payload, exit_code=1):
    code, msg, diags = _engine_error(payload)
    sys.stderr.write("trvs: %s%s\n" % (("[%s] " % code) if code else "", msg))
    for d in diags:
        sys.stderr.write("      %s\n" % d)
    raise SystemExit(exit_code)


# ------------------------------------------------------------------ doctor
def cmd_doctor(engine, args):
    info = engine.engine_info()
    native = info["native_available"]
    oracle = info["oracle_available"]
    if args.json:
        print(json.dumps({"traaviis": __version__, **info}, indent=2))
        return
    print(_field("TRAAVIIS", __version__))
    print(_field("command", "trvs"))
    print(_field("forge", info["engine_dir"]))
    print(_field("engine API", info["api_version"]))
    print(_field("engine", info["bench_version"]))
    print(_field("ic_ref", CHECK if info["ic_ref"] else CROSS))
    if info["skip_native"]:
        print(_field("ic32", "%s gated off (TRVM_SKIP_NATIVE=1)" % SKIP))
    else:
        print(_field("ic32", ("%s executable" % CHECK) if native
                     else "%s not found" % CROSS))
    print(_field("oracle", ("%s available" % CHECK) if oracle
                 else "%s unavailable" % CROSS))
    print(_field("demo id", _short(info["demo_semantic_id"], 16)))
    if native and oracle:
        print(_field("status", "ready"))
    else:
        print(_field("verification",
                     "reference only" if not native else "no oracle"))
        if not native and not info["skip_native"]:
            print(_field("fix", "build ic32 (see TRVM/runtime/c) or set TRVM_IC32_PATH"))
        print(_field("status", "degraded"))


# ---------------------------------------------------------------------- id
def cmd_id(engine, args):
    src = _read_source(args.world)
    payload = engine.lower_source(src)
    if not payload.get("ok"):
        _fail(payload, EXIT_UNAVAILABLE)
    if args.json:
        print(json.dumps({"semantic_artifact_id": payload["semantic_artifact_id"],
                          "diagnostics": payload["diagnostics"]}, indent=2))
        return
    print(payload["semantic_artifact_id"])
    for d in payload.get("diagnostics", []):
        sys.stderr.write("  warn %s\n" % (d.get("render") or d.get("message")))


# ----------------------------------------------------------------- inspect
def cmd_inspect(engine, args):
    src = _read_source(args.world)
    payload = engine.lower_source(src)
    if not payload.get("ok"):
        _fail(payload, EXIT_UNAVAILABLE)
    graph = payload["graph"]
    nodes, edges = graph["nodes"], graph["edges"]
    diags = payload.get("diagnostics", [])
    if args.json:
        print(json.dumps(payload, indent=2))
        return

    def render_actors():
        print("  actors")
        width = max((len(n["id"]) for n in nodes), default=0)
        for n in nodes:
            cfg = " ".join("%s=%s" % (k, v)
                           for k, v in sorted((n.get("static_config") or {}).items()))
            print(("  %-*s %-9s %s" % (width, n["id"], n["role"].lower(), cfg)).rstrip())

    def render_graph():
        print("  graph")
        for e in edges:
            print("  %s --%s--> %s" % (e["src"], e["kind"], e["dst"]))

    def render_diagnostics():
        print("  diagnostics")
        if not diags:
            print("  (none)")
        for d in diags:
            print("  %s" % (d.get("render") or d.get("message")))

    if args.graph:
        render_graph()
        return
    if args.actors:
        render_actors()
        return
    if args.diagnostics:
        render_diagnostics()
        return
    # default: summary + actors + graph
    print(_field("world", payload["semantic_artifact_id"]))
    profile = src.split("profile", 1)[1].split("\n", 1)[0].strip() if "profile" in src else "?"
    print(_field("profile", profile))
    print(_field("actors", str(len(nodes))))
    print(_field("edges", str(len(edges))))
    print(_field("diagnostics", str(len(diags))))
    print()
    render_actors()
    print()
    render_graph()
    if diags:
        print()
        render_diagnostics()


# -------------------------------------------------------------------- run
def cmd_run(engine, args):
    src = _read_source(args.world)
    payload = engine.run_source(src)
    if not payload.get("ok"):
        _fail(payload, EXIT_UNAVAILABLE)
    if args.json:
        print(json.dumps(payload, indent=2))
        return
    rows = payload["epochs"]
    print(_field("world", payload["semantic_artifact_id"]))
    print(_field("scenario", payload["scenario_digest"]))
    print(_field("reducer", payload["reducer"]))
    print(_field("epochs", str(len(rows))))
    if rows:
        print()
        print("  epoch  film")
        for r in rows:
            print("  %-6d %s" % (r["t"], _short(r["film"])))
        print()
        print(_field("film", _short(rows[-1]["film"])))


# ----------------------------------------------------------------- verify
def _mark(state):
    return {"ok": CHECK, "bad": CROSS, "skip": SKIP}[state]


def cmd_verify(engine, args):
    src = _read_source(args.world)

    # --reference-only: the ic_ref fold is the sole verifier (weakest mode).
    if args.reference_only:
        payload = engine.run_source(src)
        if not payload.get("ok"):
            _fail(payload, EXIT_UNAVAILABLE)
        if args.json:
            print(json.dumps({"mode": "reference-only", **payload}, indent=2))
            return
        print(_field("world", payload["semantic_artifact_id"]))
        print(_field("scenario", payload["scenario_digest"]))
        print(_field("reference", _mark("ok")))
        print(_field("agreement", "1/1"))
        return

    oracle_expected = not args.no_oracle
    payload = engine.verify_source(src, oracle=oracle_expected)
    if not payload.get("ok"):
        code, _, _ = _engine_error(payload)
        native_issue = code in ("NATIVE_UNAVAILABLE", "NATIVE_BUILD_FAILED")
        if native_issue and args.allow_skipped:
            # Degrade to reference-only rather than failing hard.
            ref = engine.run_source(src)
            if not ref.get("ok"):
                _fail(ref, EXIT_UNAVAILABLE)
            print(_field("world", ref["semantic_artifact_id"]))
            print(_field("scenario", ref["scenario_digest"]))
            print(_field("reference", _mark("ok")))
            print(_field("native", _mark("skip")))
            if oracle_expected:
                print(_field("oracle", _mark("skip")))
            print(_field("agreement", "1/%d (skipped, --allow-skipped)"
                         % (3 if oracle_expected else 2)))
            return
        _fail(payload, EXIT_UNAVAILABLE)

    if args.json:
        print(json.dumps(payload, indent=2))

    native_skipped = bool(payload.get("skipped"))
    native_ok = bool(payload.get("native")) and bool(payload.get("parity"))
    oracle_report = payload.get("oracle") or {}
    oracle_present = "match" in oracle_report
    oracle_ok = bool(oracle_report.get("match"))

    total = 1 + 1 + (1 if oracle_expected else 0)  # reference + native + oracle?
    agreed = 1  # reference ran
    unavailable, disagree = [], []

    if native_skipped:
        native_state = "skip"
        unavailable.append("native")
    elif native_ok:
        native_state = "ok"
        agreed += 1
    else:
        native_state = "bad"
        disagree.append("native")

    oracle_state = None
    if oracle_expected:
        if not oracle_present:
            oracle_state = "skip"
            unavailable.append("oracle")
        elif oracle_ok:
            oracle_state = "ok"
            agreed += 1
        else:
            oracle_state = "bad"
            disagree.append("oracle")

    if not args.json:
        print(_field("world", payload["semantic_artifact_id"]))
        print(_field("scenario", payload["scenario_digest"]))
        print(_field("reference", _mark("ok")))
        print(_field("native", _mark(native_state)))
        if oracle_expected:
            print(_field("oracle", _mark(oracle_state)))
        print(_field("agreement", "%d/%d" % (agreed, total)))

    if disagree:
        raise SystemExit(EXIT_DISAGREE)
    if unavailable and not args.allow_skipped:
        raise SystemExit(EXIT_UNAVAILABLE)


# ----------------------------------------------------------------- replay
def cmd_replay(engine, args):
    """Re-fold the world and print its per-epoch film strip. With --expect or
    --film, assert that the fold reproduces a pinned SemanticArtifactID / final
    film hash (exit 0 reproduced, 1 mismatch) -- the verifiable-replay contract.
    """
    src = _read_source(args.world)
    payload = engine.run_source(src)
    if not payload.get("ok"):
        _fail(payload, EXIT_UNAVAILABLE)
    rows = payload["epochs"]
    sem = payload["semantic_artifact_id"]
    final = rows[-1]["film"] if rows else None

    checked = args.expect is not None or args.film is not None
    reproduced = ((args.expect is None or args.expect == sem)
                  and (args.film is None or args.film == final))

    if args.json:
        print(json.dumps({
            "semantic_artifact_id": sem,
            "scenario_digest": payload["scenario_digest"],
            "reducer": payload["reducer"],
            "final_film": final,
            "checked": checked,
            "reproduced": reproduced,
            "film_strip": [{"t": r["t"], "label": r.get("label"),
                            "film": r["film"]} for r in rows],
        }, indent=2))
        if checked and not reproduced:
            raise SystemExit(EXIT_DISAGREE)
        return

    print(_field("world", sem))
    print(_field("scenario", payload["scenario_digest"]))
    print(_field("reducer", payload["reducer"]))
    print(_field("epochs", str(len(rows))))
    if rows:
        print()
        print("  epoch  label            film")
        for r in rows:
            print("  %-6d %-16s %s" % (r["t"], (r.get("label") or "")[:16],
                                       _short(r["film"])))
        print()
        print(_field("final film", _short(final)))
    if checked:
        print(_field("reproduced", (CHECK + " yes") if reproduced
                     else (CROSS + " no")))
        if not reproduced:
            if args.expect is not None and args.expect != sem:
                print(_field("expected id", args.expect))
            if args.film is not None and args.film != final:
                print(_field("expected film", args.film))
            raise SystemExit(EXIT_DISAGREE)


# ------------------------------------------------------------------- diff
def cmd_diff(engine, args):
    """Fold two worlds and compare their identity + per-epoch films. Exit 0 when
    the films are identical, 1 when they diverge (git-diff convention)."""
    pa = engine.run_source(_read_source(args.world_a))
    if not pa.get("ok"):
        _fail(pa, EXIT_UNAVAILABLE)
    pb = engine.run_source(_read_source(args.world_b))
    if not pb.get("ok"):
        _fail(pb, EXIT_UNAVAILABLE)
    ra, rb = pa["epochs"], pb["epochs"]
    sem_a, sem_b = pa["semantic_artifact_id"], pb["semantic_artifact_id"]

    n = max(len(ra), len(rb))
    rows, first_div = [], None
    for i in range(n):
        fa = ra[i]["film"] if i < len(ra) else None
        fb = rb[i]["film"] if i < len(rb) else None
        same = fa == fb
        if not same and first_div is None:
            first_div = i + 1
        rows.append((i + 1, fa, fb, same))
    identical = first_div is None and len(ra) == len(rb)

    if args.json:
        print(json.dumps({
            "a": {"world": args.world_a, "semantic_artifact_id": sem_a,
                  "epochs": len(ra)},
            "b": {"world": args.world_b, "semantic_artifact_id": sem_b,
                  "epochs": len(rb)},
            "identity_match": sem_a == sem_b,
            "films_identical": identical,
            "first_divergence": first_div,
            "epochs": [{"t": t, "a_film": fa, "b_film": fb, "match": same}
                       for (t, fa, fb, same) in rows],
        }, indent=2))
        raise SystemExit(EXIT_OK if identical else EXIT_DISAGREE)

    print(_field("a", "%s  (%s)" % (sem_a, os.path.basename(args.world_a))))
    print(_field("b", "%s  (%s)" % (sem_b, os.path.basename(args.world_b))))
    print(_field("identity", (CHECK + " identical") if sem_a == sem_b
                 else (CROSS + " differ")))
    print()
    print("  epoch  %-26s %-26s" % ("a film", "b film"))
    for (t, fa, fb, same) in rows:
        print("  %-6d %-26s %-26s %s" % (t, _short(fa or "-", 20),
                                         _short(fb or "-", 20),
                                         "" if same else CROSS))
    if first_div is not None:
        print()
        print(_field("first divergence", "epoch %d" % first_div))
    raise SystemExit(EXIT_OK if identical else EXIT_DISAGREE)


# ---------------------------------------------------------------- eval-one
#
# `trvs eval-one` runs one trusted-local Residency episode against a *task bundle*:
# a directory that seals a subject + the reward/verifier configuration, and names
# the agent command to evaluate. GPT-5.6 authorized this public wiring once the
# Eval-One Closure battery was green. The whole pipeline is `traaviis.evalone`;
# this command only loads the bundle, wires the substrate verifiers, runs one
# episode, and renders / returns the `episode-…` receipt.
#
# Bundle layout (default names; an optional `bundle.json` manifest may remap them):
#
#   bundle/
#     bundle.json      optional eval-bundle.v1 manifest (below); when present it
#                      names each file/dir and every path must be safe + in-bundle
#     task.json        TaskSpecV1  (agent_run_policy, verifier_plan, optional
#                                    test_plan / identity_policy, termination, …)
#     reward.json      RewardSpecV1
#     snapshot.json    SnapshotV1  (the sealed subject)
#     subject/         the materialized subject tree (must bind to snapshot.json)
#     agent.json       optional {"command": ["python3","agent.py"], …}
#                      (or pass argv after `--`, or via --agent, on the command line)

_EVAL_EXIT = {"ok": EXIT_OK, "invalid": EXIT_DISAGREE, "error": EXIT_UNAVAILABLE}

# Manifest key → default bundle-relative path.
_BUNDLE_DEFAULTS = {
    "task": "task.json",
    "reward": "reward.json",
    "snapshot": "snapshot.json",
    "subject": "subject",
    "agent": "agent.json",
}


def _load_json(path, what):
    if not os.path.isfile(path):
        sys.stderr.write("trvs: bundle is missing %s: %s\n" % (what, path))
        raise SystemExit(EXIT_UNAVAILABLE)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        sys.stderr.write("trvs: %s is not valid JSON: %s\n" % (what, exc))
        raise SystemExit(EXIT_UNAVAILABLE)


def _bundle_member(root_real, key, ref):
    """Resolve one bundle-relative reference to a *contained*, symlink-free path.

    Lexical safety (``safe_relposix``: no absolute, no ``..``) is necessary but not
    sufficient — a lexically-clean ref can still point at an external file through a
    symlink (``bundle/task.json`` → ``/etc/passwd``), or the subject root itself can
    be a symlink to an external directory. So we also require *filesystem*
    containment: the fully-resolved real path must equal the lexical candidate (any
    symlink along the way — final or intermediate — makes them differ) and stay
    under the already-resolved bundle root. Anything else is refused (exit 2) before
    the path is ever opened.
    """
    if not isinstance(ref, str) or not ref.strip():
        sys.stderr.write("trvs: bundle.json %s reference must be a path\n" % key)
        raise SystemExit(EXIT_UNAVAILABLE)
    rel = ref.rstrip("/")  # a trailing slash on subject/ is cosmetic
    try:
        safe = safe_relposix(rel)
    except PathError as exc:
        sys.stderr.write(
            "trvs: bundle.json %s reference escapes the bundle: %s\n" % (key, exc))
        raise SystemExit(EXIT_UNAVAILABLE)
    candidate = os.path.normpath(os.path.join(root_real, safe.replace("/", os.sep)))
    candidate_real = os.path.realpath(candidate)
    if candidate_real != candidate:
        sys.stderr.write(
            "trvs: bundle.json %s reference resolves through a symlink\n" % key)
        raise SystemExit(EXIT_UNAVAILABLE)
    try:
        contained = os.path.commonpath([root_real, candidate_real]) == root_real
    except ValueError:  # different drives/roots (Windows)
        contained = False
    if not contained:
        sys.stderr.write(
            "trvs: bundle.json %s reference escapes the bundle root\n" % key)
        raise SystemExit(EXIT_UNAVAILABLE)
    return candidate


def _bundle_paths(bundle):
    """Resolve the bundle's member paths from its **required** ``bundle.json``.

    For the public developer preview a versioned bundle is never inferred from
    directory convention: a missing ``bundle.json`` is an admission error (exit 2).
    Its ``eval_bundle_version`` must match, and every referenced member is resolved
    through ``_bundle_member`` — safe relative path *and* filesystem containment (no
    symlink escape, no external subject root), rejected before it is opened.
    """
    root_real = os.path.realpath(bundle)  # resolve a symlinked bundle root once
    manifest_path = os.path.join(root_real, "bundle.json")
    if not os.path.isfile(manifest_path) or os.path.islink(manifest_path):
        sys.stderr.write(
            "trvs: bundle is missing a required bundle.json manifest "
            "(eval-bundle.v1)\n")
        raise SystemExit(EXIT_UNAVAILABLE)
    manifest = _load_json(manifest_path, "bundle.json")
    version = manifest.get("eval_bundle_version")
    if version != EVAL_BUNDLE_VERSION:
        sys.stderr.write(
            "trvs: bundle.json has unsupported eval_bundle_version %r "
            "(expected %r)\n" % (version, EVAL_BUNDLE_VERSION))
        raise SystemExit(EXIT_UNAVAILABLE)
    refs = dict(_BUNDLE_DEFAULTS)
    for key in refs:
        if key in manifest:
            refs[key] = manifest[key]
    resolved = {}
    for key, ref in refs.items():
        resolved[key] = _bundle_member(root_real, key, ref)
    return resolved


def _registry(engine):
    """Build this command's `VerifierRegistryV1` once, from the selected engine.

    Every command that verifies anything — `eval-one`, `eval`, `verify-episode` —
    takes its implementations from this one object, so the identity verifier binds
    to the same Forge checkout the command already selected rather than
    soft-loading its own. `needs_engine=False` commands pass None and the registry
    consults the soft loader exactly once, here.
    """
    from . import wiring
    return wiring.default_registry(engine)


def _wire_verifiers(task, registry):
    """Inject the substrate verifiers the task's config actually calls for.

    Delegates to ``traaviis.wiring`` so that `eval-one`, `eval`, and any library
    caller wire *identically* for the same task and runtime — two runs of one task
    are only comparable if the declared plan is a function of the task and the
    implementations come from one explicit registry.
    """
    from . import wiring
    return wiring.wire_verifiers(task, registry)


def cmd_eval_one(engine, args):
    from . import evalone, admission

    bundle = args.bundle
    if not os.path.isdir(bundle):
        sys.stderr.write("trvs: no such bundle directory: %s\n" % bundle)
        raise SystemExit(EXIT_UNAVAILABLE)

    paths = _bundle_paths(bundle)
    task = _load_json(paths["task"], "task.json")
    reward_spec = _load_json(paths["reward"], "reward.json")
    snapshot = _load_json(paths["snapshot"], "snapshot.json")

    subject_dir = paths["subject"]
    if not os.path.isdir(subject_dir):
        sys.stderr.write("trvs: bundle is missing subject dir: %s\n" % subject_dir)
        raise SystemExit(EXIT_UNAVAILABLE)
    # Tree-level admission: bind the on-disk subject to the snapshot (paths, hashes,
    # declared binaries as bytes, file modes, no symlinks) BEFORE anything runs.
    try:
        content = admission.verify_subject_tree(snapshot, subject_dir)
    except admission.AdmissionError as exc:
        sys.stderr.write("trvs: subject admission failed: %s\n" % exc)
        raise SystemExit(EXIT_UNAVAILABLE)

    # Agent command: argv after `--` wins, then --agent argv, then agent.json.
    agent_command = getattr(args, "agent_tail", None) or args.agent
    if not agent_command:
        agent_path = paths["agent"]
        spec = _load_json(agent_path, "agent.json") if os.path.isfile(agent_path) else {}
        agent_command = spec.get("command")
    if not agent_command:
        sys.stderr.write(
            "trvs: no agent command (pass `-- CMD...`, --agent CMD..., or add "
            "agent.json)\n")
        raise SystemExit(EXIT_UNAVAILABLE)

    toolchain = None
    if args.toolchain:
        toolchain = _load_json(args.toolchain, "toolchain file")

    extra, notes = _wire_verifiers(task, _registry(engine))
    for note in notes:
        sys.stderr.write("  warn %s\n" % note)

    try:
        run = evalone.evaluate(
            task, content, list(agent_command), reward_spec,
            snapshot=snapshot, extra_verifiers=extra,
            platform=args.platform, toolchain=toolchain,
        )
    except admission.AdmissionError as exc:
        sys.stderr.write("trvs: subject admission failed: %s\n" % exc)
        raise SystemExit(EXIT_UNAVAILABLE)
    except evalone.UnsupportedPolicyError as exc:
        sys.stderr.write("trvs: unsupported run policy: %s\n" % exc)
        raise SystemExit(EXIT_UNAVAILABLE)

    receipt = run["receipt"]

    # --output: durably persist the complete EvaluationRunV1 as an episode-<id>/
    # bundle. The bundle is built in a temp sibling and atomically renamed into
    # place, then re-verified against the receipt so we never hand back a path to a
    # bundle that does not close. An invalid-config episode (no evidence) is not
    # persisted — there is nothing to keep.
    bundle_path = None
    if args.output:
        from . import episode_bundle
        if run["artifacts"] is None:
            sys.stderr.write(
                "  warn invalid-config episode produced no evidence; "
                "nothing persisted\n")
        else:
            try:
                # write_episode_bundle stages the bundle, fully re-verifies the
                # staged tree against the same verifier set the CLI will replay
                # with, and only then atomically publishes it. A staged bundle
                # that does not close is never published (raises here).
                bundle_path = episode_bundle.write_episode_bundle(
                    run, task=task, reward_spec=reward_spec,
                    snapshot=snapshot, content=content, dest_root=args.output,
                    extra_verifiers=extra)
            except episode_bundle.EpisodeBundleError as exc:
                sys.stderr.write("trvs: could not write episode bundle: %s\n" % exc)
                raise SystemExit(EXIT_UNAVAILABLE)

    if args.json:
        if bundle_path:
            sys.stderr.write("episode evidence: %s\n" % bundle_path)
        print(json.dumps(receipt, indent=2))
        raise SystemExit(_EVAL_EXIT.get(receipt["status"], EXIT_UNAVAILABLE))

    print(_field("episode", receipt["episode_id"]))
    print(_field("subject", receipt["subject"]["snapshot_id"]))
    print(_field("task", receipt["task_id"]))
    print(_field("reward id", receipt["reward_id"]))
    status = receipt["status"]
    mark = {"ok": CHECK, "invalid": CROSS, "error": CROSS}.get(status, "?")
    print(_field("status", "%s %s" % (mark, status)))
    print(_field("validity", receipt["validity"]))
    reward_val = receipt["reward"]
    print(_field("reward", "null" if reward_val is None else ("%.4g" % reward_val)))

    verification = receipt.get("verification") or {}
    if verification:
        print()
        print("  signals")
        width = max(len(s) for s in verification)
        for sig in sorted(verification):
            print("  %-*s %s" % (width, sig, verification[sig]))

    outputs = receipt.get("outputs") or {}
    if outputs.get("finding_id") or outputs.get("patch_id"):
        print()
        if outputs.get("finding_id"):
            print(_field("finding", outputs["finding_id"]))
        if outputs.get("patch_id"):
            print(_field("patch", outputs["patch_id"]))

    if bundle_path:
        print()
        # Labelled "episode evidence", never "bundle": the receipt field is a
        # legacy episode-member name that predates `bundle-...` and carries no
        # package identity. Two different things must not read as one.
        print(_field("episode evidence", bundle_path))

    raise SystemExit(_EVAL_EXIT.get(status, EXIT_UNAVAILABLE))


# ------------------------------------------------------------- verify-episode
#
# `trvs verify-episode DIR` re-verifies a saved `episode-<id>/` bundle WITHOUT
# re-running the agent. It reopens every member under strict containment,
# recomputes each artifact id from the saved bytes, re-attests the process bytes
# against the trace digests, re-runs the declared verifiers against the saved
# subject + candidate, recomputes the reward, and confirms the receipt is
# self-consistent. Exit 0 when the whole bundle closes, 1 when a check fails,
# 2 when the bundle cannot be opened at all.

def _closure_summary(report):
    """A short one-line reason a bundle failed to close, for stderr."""
    failed = [name for name, c in (report.get("checks") or {}).items()
              if isinstance(c, dict) and "ok" in c and not c["ok"]]
    signals = report.get("checks", {}).get("signals") or {}
    failed += ["signal:" + s for s, c in signals.items() if not c.get("ok")]
    return ", ".join(failed) or "unknown"


def _wire_episode_verifiers(engine):
    """Wire the substrate verifiers a saved bundle may need on replay.

    A replay does not know which signals the episode scored until it opens the
    bundle, so it offers everything this runtime has rather than a task-derived
    plan — but the implementations still come from the one registry, built from
    the same selected engine as every other command. If the engine is unavailable,
    ``identity`` is left unwired and a saved episode that scored an ``identity``
    signal re-verifies as a mismatch — never a false pass.
    """
    registry = _registry(engine)
    extra = {s: registry.get(s) for s in registry.available()}
    notes = list(registry.notes)
    return extra, notes


def cmd_verify_episode(engine, args):
    from . import episode_bundle

    bundle = args.episode_dir
    if not os.path.isdir(bundle):
        sys.stderr.write("trvs: no such episode directory: %s\n" % bundle)
        raise SystemExit(EXIT_UNAVAILABLE)

    extra, notes = _wire_episode_verifiers(engine)
    for note in notes:
        sys.stderr.write("  warn %s\n" % note)

    report = episode_bundle.verify_episode_bundle(bundle, extra_verifiers=extra)

    # Three-way outcome → three-way exit: a closed bundle is OK (0); a genuine
    # evidence disagreement is a mismatch (1); a bundle that cannot be opened or
    # that was verified with a drifted verifier implementation is unavailable (2)
    # — the substrate could not answer, which is not a verdict against the run.
    outcome = report.get("outcome")
    exit_code = {
        episode_bundle.OUTCOME_CLOSED: EXIT_OK,
        episode_bundle.OUTCOME_MISMATCH: EXIT_DISAGREE,
        episode_bundle.OUTCOME_UNAVAILABLE: EXIT_UNAVAILABLE,
    }.get(outcome, EXIT_UNAVAILABLE)

    if args.json:
        print(json.dumps(report, indent=2))
        raise SystemExit(exit_code)

    checks = report.get("checks") or {}

    def _mark_check(c):
        return (CHECK if (isinstance(c, dict) and c.get("ok")) else CROSS)

    print(_field("episode", report.get("episode_id") or "?"))
    for stage in ("closure", "artifacts"):
        c = checks.get(stage) or {}
        print(_field(stage, "%s %s" % (_mark_check(c), c.get("detail", ""))))

    signals = checks.get("signals") or {}
    if signals:
        print()
        print("  signals")
        width = max(len(s) for s in signals)
        for sig in sorted(signals):
            c = signals[sig]
            note = "replay=%s receipt=%s" % (c.get("replayed"), c.get("receipt"))
            if "evidence_match" in c and not c["evidence_match"]:
                note += " (evidence digest mismatch)"
            print("  %-*s %s %s" % (width, sig, _mark_check(c), note))

    print()
    for stage in ("reward", "episode_id"):
        c = checks.get(stage) or {}
        label = "episode-id" if stage == "episode_id" else stage
        print(_field(label, "%s %s" % (_mark_check(c), c.get("detail", ""))))

    print()
    if outcome == episode_bundle.OUTCOME_CLOSED:
        verdict = CHECK + " closed"
    elif outcome == episode_bundle.OUTCOME_UNAVAILABLE:
        verdict = CROSS + " unavailable: " + _closure_summary(report)
    else:
        verdict = CROSS + " failed: " + _closure_summary(report)
    print(_field("verified", verdict))
    raise SystemExit(exit_code)


# ----------------------------------------------------------------- compare
#: Which reward relation each verdict prints as. The direction is stated in
#: words rather than left to a sign, because a reader who mixes up which side
#: is "left" has silently inverted the finding.
_RELATION_TEXT = {
    "left_higher": "left scored higher",
    "right_higher": "right scored higher",
    "equal": "equal reward",
    "incomparable": "incomparable (at least one side has no score)",
}


def cmd_compare(engine, args):
    """Compare two closed episode bundles over one task (§8).

    Runs no agent. Both sides are replayed to `closed` first, and the two must
    answer the same `task_id` -- otherwise the two rewards were produced by
    different rubrics and ranking them would be meaningless.
    """
    from . import comparison

    registry = _registry(engine)
    for note in registry.notes:
        sys.stderr.write("  warn %s\n" % note)

    try:
        report = comparison.compare_episodes(
            args.left, args.right, registry=registry)
    except comparison.ComparisonError as ex:
        sys.stderr.write("trvs: %s\n" % ex)
        for key, value in sorted((ex.detail or {}).items()):
            sys.stderr.write("      %s: %s\n" % (key, value))
        # A bundle that disagrees with its own evidence is a *finding* about
        # that episode (1). Everything else -- a missing bundle, an unreadable
        # receipt, two different tasks -- means this runtime could not judge the
        # pair at all (2), which is not a verdict against either side.
        raise SystemExit(
            EXIT_DISAGREE if ex.code == "EPISODE_EVIDENCE_MISMATCH"
            else EXIT_UNAVAILABLE)

    out_path = None
    if args.output:
        try:
            out_path = comparison.write_comparison(report, args.output)
        except OSError as ex:
            sys.stderr.write("trvs: could not write comparison: %s\n" % ex)
            raise SystemExit(EXIT_UNAVAILABLE)

    if args.json:
        if out_path:
            sys.stderr.write("comparison: %s\n" % out_path)
        print(json.dumps(report, indent=2, sort_keys=True))
        raise SystemExit(EXIT_OK)

    rel = report["relation"]
    print(_field("task", report["task_id"] or "?"))
    print(_field("reward", report["reward_id"] or "?"))
    print()
    print("  episodes")
    for side in ("left", "right"):
        s = report[side]
        reward = s.get("reward")
        print("  %-5s %-8s reward %s" % (
            side, s.get("status"),
            "null" if reward is None else ("%.4g" % reward)))
        print("        %s" % (s.get("episode_id") or "?"))

    print()
    print(_field("relation", _RELATION_TEXT.get(rel["reward"], rel["reward"])))
    delta = rel.get("right_minus_left")
    if delta is not None:
        print(_field("right - left", "%+.4g" % delta))
    print(_field("same episode", "yes" if rel["same_episode"] else "no"))
    print(_field("same trace", "yes" if rel["same_trace"] else "no"))

    differing = [f for f, d in sorted(report["differences"].items())
                 if d is not None]
    print()
    print(_field("differs in", ", ".join(differing) if differing
                 else "nothing (the two sides agree field-for-field)"))
    if out_path:
        print(_field("comparison", out_path))

    # A produced comparison is a success even when the two sides disagree: the
    # command's job is to report the relation, not to grade either candidate.
    raise SystemExit(EXIT_OK)


# -------------------------------------------------------------------- init
def cmd_init(engine, args):
    """Scaffold an environment for a substrate template (RFC Artifacts §6).

    Pure scaffolding: it seeds a subject and a manifest skeleton and derives no
    identity. `identity: unresolved` is stated in the output rather than left
    implicit -- the environment is not closed until `pack` verifies closure and
    computes `env-...` from the canonical bytes.
    """
    if args.list:
        print("  template             substrate profile          seeds")
        for name, profile, summary in _scaffold.templates():
            print("  %-20s %-26s %s" % (name, profile, summary))
        return

    if not args.template or not args.directory:
        sys.stderr.write(
            "trvs: init needs --template NAME and a destination directory\n"
            "      trvs init --list                 # available templates\n"
            "      trvs init --template NAME DIR\n")
        raise SystemExit(EXIT_UNAVAILABLE)

    try:
        written = _scaffold.materialize(args.template, args.directory)
    except _scaffold.ScaffoldError as ex:
        sys.stderr.write("trvs: %s\n" % ex)
        raise SystemExit(EXIT_UNAVAILABLE)
    except OSError as ex:
        sys.stderr.write("trvs: could not scaffold %s: %s\n" % (args.directory, ex))
        raise SystemExit(EXIT_UNAVAILABLE)

    profile = _scaffold.TEMPLATES[args.template]["substrate_profile"]
    if args.json:
        print(json.dumps({
            "template": args.template,
            "substrate_profile": profile,
            "directory": os.path.abspath(args.directory),
            "files": written,
            # Stated, not implied: init computed no content hash.
            "identity": "unresolved",
        }, indent=2))
        return

    print(_field("template", args.template))
    print(_field("substrate", profile))
    print(_field("directory", args.directory))
    print(_field("identity", "unresolved (init derives none; pack closes it)"))
    print()
    for rel in written:
        print("  %s %s" % (CHECK, rel))


# -------------------------------------------------------------------- pack
def cmd_pack(engine, args):
    """Close a scaffolded environment into a verified package (RFC Artifacts §6).

    Identity is recomputed from bytes and closure is verified *before* anything
    is written; the emitted package is then reopened and re-verified from disk.
    Any broken law is a typed, loud failure -- exit 2, nothing left behind.
    """
    from . import pack as _pack
    from .substrates import AdmissionError

    if not os.path.isdir(args.environment):
        sys.stderr.write("trvs: no such environment directory: %s\n"
                         % args.environment)
        raise SystemExit(EXIT_UNAVAILABLE)
    # Soft load: only substrates that must lower a world need the engine, and
    # they raise a typed ENGINE_UNAVAILABLE rather than the process dying here.
    engine = engine or _engine.try_load()
    try:
        report = _pack.pack(args.environment, args.output, engine=engine)
    except AdmissionError as ex:
        sys.stderr.write("trvs: %s\n" % ex)
        for key, value in sorted((ex.detail or {}).items()):
            sys.stderr.write("      %s: %s\n" % (key, value))
        raise SystemExit(EXIT_UNAVAILABLE)
    except OSError as ex:
        sys.stderr.write("trvs: could not pack: %s\n" % ex)
        raise SystemExit(EXIT_UNAVAILABLE)

    if args.json:
        print(json.dumps(report, indent=2))
        return

    subject_field, subject_id = next(iter(report["subject"].items()))
    label = {"snapshot_id": "snapshot", "semantic_artifact_id": "world"}.get(
        subject_field, "subject")
    print(_field("environment", report["env_id"]))
    print(_field("substrate", report["substrate_profile"]))
    print(_field(label, _short(subject_id)))
    for t in report["tasks"]:
        print(_field("task", _short(t["task_id"])))
    for r in report["rewards"]:
        print(_field("reward", _short(r["reward_id"])))
    print(_field("bundle", report["bundle_id"]))
    dist = report.get("distribution") or {}
    for kind in ("documentation", "screenshots", "assets"):
        for member in dist.get(kind) or []:
            print(_field(kind.rstrip("s"), member))
    print(_field("closure", CHECK + " verified before write"))
    print(_field("reopened", CHECK + " re-derived from the staged bytes"))
    if report["runnable"]:
        print()
        print("  runnable:  trvs eval-one %s --agent <your agent argv>"
              % report["directory"])
    elif report["tasks"]:
        print()
        print("  runnable:  trvs eval %s --split <name> --agent <your agent argv>"
              % report["directory"])


# ------------------------------------------------------------- verify-bundle
#
# `trvs verify-bundle DIR` re-derives a distributed package's `bundle-…` and the
# `env-…` inside it from the bytes on disk (RFC Artifacts §5b). It is the
# consumer-side counterpart of `pack`: `pack` earns the two identities, this
# proves them again on a machine that did not.


def cmd_verify_bundle(engine, args):
    """Verify a distributed package directory, or an archive of one."""
    from . import bundle as _bundle
    from .substrates import AdmissionError

    engine = engine or _engine.try_load()
    target = args.bundle
    tmp = None
    try:
        if os.path.isfile(target):
            tmp = tempfile.mkdtemp(prefix="trvs-bundle-")
            target = _bundle.extract_archive(target, os.path.join(tmp, "pkg"))
        elif not os.path.isdir(target):
            sys.stderr.write("trvs: no such bundle: %s\n" % args.bundle)
            raise SystemExit(EXIT_UNAVAILABLE)
        try:
            report = _bundle.verify_bundle(
                target, engine=engine, environment=not args.package_only)
        except AdmissionError as ex:
            sys.stderr.write("trvs: %s\n" % ex)
            for key, value in sorted((ex.detail or {}).items()):
                sys.stderr.write("      %s: %s\n" % (key, value))
            raise SystemExit(EXIT_DISAGREE)
        except OSError as ex:
            sys.stderr.write("trvs: could not read the bundle: %s\n" % ex)
            raise SystemExit(EXIT_UNAVAILABLE)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print(_field("bundle", report["bundle_id"]))
    print(_field("environment", report["env_id"]))
    print(_field("members", "%d closed both ways" % report["members"]))
    if report["environment_verified"]:
        print(_field("substrate", report.get("substrate_profile")))
        for field, value in (report.get("subject") or {}).items():
            label = {"snapshot_id": "snapshot",
                     "semantic_artifact_id": "world"}.get(field, "subject")
            print(_field(label, _short(value)))
        for t in report.get("tasks") or []:
            print(_field("task", _short(t)))
        for r in report.get("rewards") or []:
            print(_field("reward", _short(r)))
        print(_field("closure", CHECK + " every reference resolves"))
    else:
        print(_field("environment", SKIP + " (--package-only)"))


# ------------------------------------------------------------ archive-bundle
#
# Transport, not identity. The archive's SHA-256 is reported under its own name
# precisely so it is never mistaken for the bundle id (RFC Artifacts §5b).
#
# Publication is gated on the *serialized* form (§5c): the archive is written to
# a temporary path, extracted, re-verified from the extracted bytes, and only
# then atomically published. A failed round trip leaves no archive at all.


def cmd_archive_bundle(engine, args):
    """Serialize a verified package directory into a deterministic ZIP."""
    from . import bundle as _bundle
    from .substrates import AdmissionError

    engine = engine or _engine.try_load()
    if not os.path.isdir(args.bundle):
        sys.stderr.write("trvs: no such bundle directory: %s\n" % args.bundle)
        raise SystemExit(EXIT_UNAVAILABLE)
    try:
        report = _bundle.write_archive(args.bundle, args.output,
                                       verify=not args.package_only,
                                       engine=engine)
    except AdmissionError as ex:
        sys.stderr.write("trvs: %s\n" % ex)
        for key, value in sorted((ex.detail or {}).items()):
            sys.stderr.write("      %s: %s\n" % (key, value))
        raise SystemExit(EXIT_DISAGREE)
    except OSError as ex:
        sys.stderr.write("trvs: could not write the archive: %s\n" % ex)
        raise SystemExit(EXIT_UNAVAILABLE)

    if args.json:
        print(json.dumps(report, indent=2))
        return
    print(_field("bundle", report["bundle_id"]))
    print(_field("archive", report["archive"]))
    print(_field("archive sha", report["archive_sha256"]))
    print(_field("entries", str(report["entries"])))
    print(_field("roundtrip", "%s extracted and re-verified before publication"
                 % (CHECK if report.get("roundtrip_verified") else CROSS)))
    print()
    print("  the archive sha is a transport checksum of these exact bytes;")
    print("  the bundle id is the package it carries. They are not the same claim.")


# --------------------------------------------------------------------- eval
#
# `trvs eval PKG --split NAME` runs an agent over a *split* of a packed
# environment and emits one `episode-…` per task (RFC Artifacts §7). It creates
# no new artifact id: the ladder stops at `env-…`, so what comes back is an
# index over episodes that are each already content-addressed.
#
# Exit codes follow the established contract: 0 when every task ran and scored
# `ok`, 1 when it ran but at least one task disagreed (invalid/error), 2 when
# the split could not be run at all (bad package, unknown split, drifted
# subject) — a refusal is never reported as a score of zero.
def cmd_eval(engine, args):
    from . import evalsplit
    from .substrates import AdmissionError

    if not os.path.isdir(args.package):
        sys.stderr.write("trvs: no such package directory: %s\n" % args.package)
        raise SystemExit(EXIT_UNAVAILABLE)

    agent_command = getattr(args, "agent_tail", None) or args.agent
    if not agent_command:
        sys.stderr.write(
            "trvs: no agent command (pass `-- CMD...` or --agent CMD...)\n")
        raise SystemExit(EXIT_UNAVAILABLE)

    toolchain = _load_json(args.toolchain, "toolchain file") if args.toolchain else None

    # One engine, one registry, for the whole split. Resolving it here means the
    # environment is reopened and every task's identity verifier re-lowers through
    # the *same* Forge checkout — and that one registry answers every task, so a
    # split cannot silently change its verifier set partway through.
    engine = engine or _engine.try_load()
    registry = _registry(engine)
    seen_notes = set()

    def report_notes(notes):
        """Print wiring warnings without taking over the wiring.

        This used to be a `verifiers_for` callback passed as
        `extra_verifiers_for`, which meant the run reported its verifiers as
        `caller_supplied` and attested no registry version -- even though the
        implementations came from the registry built two lines above. Printing a
        warning is presentation; it must not change what the evidence says about
        where the implementations came from.
        """
        for note in notes:
            if note not in seen_notes:  # the same gap, once, not once per task
                seen_notes.add(note)
                sys.stderr.write("  warn %s\n" % note)

    def progress(index, total, entry):
        if args.json:
            return
        mark = {"ok": CHECK, "invalid": CROSS, "error": CROSS}.get(
            entry["status"], "?")
        reward = entry["reward"]
        sys.stderr.write("  [%d/%d] %s %s  %s  reward %s\n" % (
            index + 1, total, mark, _short(entry["task_id"]), entry["status"],
            "null" if reward is None else ("%.4g" % reward)))

    try:
        report = evalsplit.eval_split(
            args.package, args.split, list(agent_command),
            output=args.output, platform=args.platform, toolchain=toolchain,
            engine=engine, registry=registry,
            on_episode=progress, on_wiring_notes=report_notes)
    except AdmissionError as ex:
        sys.stderr.write("trvs: %s\n" % ex)
        for key, value in sorted((ex.detail or {}).items()):
            sys.stderr.write("      %s: %s\n" % (key, value))
        raise SystemExit(EXIT_UNAVAILABLE)

    index_path = None
    if args.output:
        try:
            index_path = evalsplit.write_evaluation(
                report, os.path.join(args.output, "evaluation.json"))
        except OSError as ex:
            sys.stderr.write("trvs: could not write evaluation index: %s\n" % ex)
            raise SystemExit(EXIT_UNAVAILABLE)

    totals = report["totals"]
    # Three outcomes, in precedence order. Evidence the caller asked for and did
    # not get makes the command *unavailable* (2), not merely disagreeing (1) and
    # certainly not successful: a score whose proof was not retained is not a
    # result the user can take away. A genuine agent disagreement is 1. Only a
    # fully valid, fully retained run is 0.
    if totals.get("persistence_error"):
        exit_code = EXIT_UNAVAILABLE
    elif totals["ok"] != totals["tasks"]:
        exit_code = EXIT_DISAGREE
    else:
        exit_code = EXIT_OK

    if args.json:
        if index_path:
            sys.stderr.write("evaluation: %s\n" % index_path)
        print(json.dumps(report, indent=2, sort_keys=True))
        raise SystemExit(exit_code)

    print(_field("environment", report["env_id"]))
    print(_field("split", "%s (%d task%s)" % (
        report["split"], totals["tasks"], "" if totals["tasks"] == 1 else "s")))
    print()
    print("  episodes")
    for entry in report["episodes"]:
        mark = {"ok": CHECK, "invalid": CROSS, "error": CROSS}.get(
            entry["status"], "?")
        reward = entry["reward"]
        print("  %s %s  %-8s reward %s" % (
            mark, _short(entry["task_id"]), entry["status"],
            "null" if reward is None else ("%.4g" % reward)))
        if entry["episode_id"]:
            print("      %s" % entry["episode_id"])
        if entry["error"]:
            print("      %s" % entry["error"])
        persistence = entry.get("persistence") or {}
        if persistence.get("status") == "error":
            print("      %s not persisted: %s" % (CROSS, persistence.get("error")))
    print()
    print(_field("ok", "%d/%d" % (totals["ok"], totals["tasks"])))
    mean = totals["reward_mean"]
    print(_field("mean reward", "null" if mean is None else ("%.4g" % mean)))
    # Reported whenever evidence was *asked* for, not only when keeping it failed.
    # `ok` answers "did the agent solve the split"; this answers "is the proof of
    # that still on disk". Printing the second only on failure would let silence
    # mean "kept", which is the same conflation the split index was split apart to
    # avoid -- and silence is exactly what a reader cannot audit.
    if any((e.get("persistence") or {}).get("requested") for e in report["episodes"]):
        kept = "%d/%d" % (totals["persistence_closed"], totals["tasks"])
        if totals.get("persistence_error"):
            kept += "  (%d failed to persist)" % totals["persistence_error"]
        print(_field("episodes kept", kept))
    if index_path:
        print(_field("evaluation", index_path))

    raise SystemExit(exit_code)


def cmd_serve(engine, args):
    """Serve one packed environment as an ORS submission endpoint.

    Everything is admitted before the socket is bound, and the banner is printed
    only once the server is listening — so the last line of output is a true
    statement about a server that exists, and a failure produces no banner at
    all rather than a banner followed by a traceback.
    """
    from . import ors as _ors
    from . import ors_server as _ors_server
    from .substrates import AdmissionError

    if not args.ors:  # pragma: no cover - argparse marks --ors required
        sys.stderr.write("trvs: serve currently speaks only --ors\n")
        raise SystemExit(EXIT_UNAVAILABLE)
    if not os.path.isdir(args.package):
        sys.stderr.write("trvs: no such package directory: %s\n" % args.package)
        raise SystemExit(EXIT_UNAVAILABLE)

    toolchain = _load_json(args.toolchain, "toolchain file") if args.toolchain else None
    engine = engine or _engine.try_load()
    registry = _registry(engine)
    seen_notes = set()

    def report_notes(notes):
        for note in notes:
            if note not in seen_notes:
                seen_notes.add(note)
                sys.stderr.write("  warn %s\n" % note)

    sys.stderr.write("admitting %s ...\n" % args.package)
    try:
        server = _ors_server.serve(
            args.package, args.split, args.output,
            host=args.host, port=args.port, allow_remote=args.allow_remote,
            engine=engine, registry=registry, platform=args.platform,
            toolchain=toolchain, on_wiring_notes=report_notes)
    except AdmissionError as ex:
        sys.stderr.write("trvs: %s\n" % ex)
        for key, value in sorted((getattr(ex, "detail", None) or {}).items()):
            sys.stderr.write("      %s: %s\n" % (key, value))
        raise SystemExit(EXIT_UNAVAILABLE)
    except OSError as ex:
        sys.stderr.write("trvs: could not bind %s:%s: %s\n"
                         % (args.host, args.port, ex))
        raise SystemExit(EXIT_UNAVAILABLE)

    describe = server.adapter.describe()
    print(_field("environment", describe["env_id"]))
    print(_field("split", args.split))
    print(_field("substrate", describe["substrate_profile"]))
    print(_field("profile", describe["ors_profile_version"]))
    print(_field("runner", describe["runner_profile"]))
    print(_field("listening", server.base_url))
    print(_field("episodes", os.path.abspath(args.output)))
    print()
    print("  tools")
    for tool in describe["tools"]:
        print("  %s %s" % (CHECK, tool["name"]))
    unsupported = [op for op, ok in sorted(describe["operations"].items())
                   if not ok]
    if unsupported:
        print()
        print("  refused by this substrate")
        for op in unsupported:
            print("  %s %s" % (CROSS, op))
    print()
    if server.host in _ors_server.LOOPBACK_HOSTS:
        print("  loopback only. ctrl-c to stop.")
    else:
        print("  %s reachable beyond loopback (--allow-remote). ctrl-c to stop."
              % CROSS)
    sys.stdout.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nstopping\n")
    finally:
        server.shutdown()
    raise SystemExit(EXIT_OK)


def cmd_batch(engine, args):
    """Run every candidate over one split and compare them pairwise."""
    from . import batch as B
    from .substrates import AdmissionError

    if not os.path.isdir(args.package):
        sys.stderr.write("trvs: no such package directory: %s\n" % args.package)
        raise SystemExit(EXIT_UNAVAILABLE)

    toolchain = _load_json(args.toolchain, "toolchain file") if args.toolchain else None

    # One engine and one registry for the whole matrix -- for the runs *and* for
    # every replay. Two registries would mean the comparison judged the episodes
    # by verifiers other than the ones that produced them, which is exactly the
    # confusion `runtime_context` exists to make impossible.
    engine = engine or _engine.try_load()
    registry = _registry(engine)
    seen_notes = set()

    def report_notes(notes):
        for note in notes:
            if note not in seen_notes:  # the same gap once, not once per episode
                seen_notes.add(note)
                sys.stderr.write("  warn %s\n" % note)

    def on_candidate(index, total, key, report):
        if args.json or report is None:
            return
        totals = report["totals"]
        sys.stderr.write("  [%d/%d] %s  ok %d/%d\n" % (
            index + 1, total, key, totals["ok"], totals["tasks"]))

    try:
        candidates = B.load_candidate_set(args.candidates)
        report = B.run_batch(
            args.package, args.split, candidates, args.output,
            engine=engine, registry=registry, platform=args.platform,
            toolchain=toolchain, on_candidate=on_candidate,
            on_wiring_notes=report_notes)
    except AdmissionError as ex:
        sys.stderr.write("trvs: %s\n" % ex)
        for key, value in sorted((ex.detail or {}).items()):
            sys.stderr.write("      %s: %s\n" % (key, value))
        raise SystemExit(EXIT_UNAVAILABLE)
    except OSError as ex:
        sys.stderr.write("trvs: could not write the batch output: %s\n" % ex)
        raise SystemExit(EXIT_UNAVAILABLE)

    refusals = [r for row in report["tasks"] for r in row["refusals"]]
    # A pair that refused for any *other* reason is a fact about those bundles;
    # evidence that would not reverify minutes after this command wrote it is a
    # disagreement about the evidence itself, and that is the one thing a
    # completed batch reports as failure.
    mismatched = [r for r in refusals if r["code"] == "EPISODE_EVIDENCE_MISMATCH"]
    exit_code = EXIT_DISAGREE if mismatched else EXIT_OK

    if args.json:
        sys.stderr.write("batch: %s\n" % args.output)
        print(json.dumps(report, indent=2, sort_keys=True))
        raise SystemExit(exit_code)

    totals = report["totals"]
    print(_field("environment", report["env_id"]))
    print(_field("split", "%s (%d task%s)" % (
        report["split"], totals["task_count"],
        "" if totals["task_count"] == 1 else "s")))
    print(_field("candidates", ", ".join(report["candidate_order"])))
    print()
    for row in report["tasks"]:
        print("  %s" % _short(row["task_id"]))
        for key in report["candidate_order"]:
            cell = row["episodes"][key]
            reward = cell["reward"]
            mark = {"ok": CHECK, "invalid": CROSS, "error": CROSS}.get(
                cell["status"], "?")
            print("    %s %-12s %-8s reward %s" % (
                mark, key, cell["status"],
                "null" if reward is None else ("%.4g" % reward)))
        for entry in row["comparisons"]:
            relation = entry["relation"]
            print("      %s vs %s: %s" % (
                entry["left_candidate"], entry["right_candidate"],
                _RELATION_TEXT.get(relation["reward"], relation["reward"])))
        for entry in row["refusals"]:
            print("      %s %s vs %s: %s" % (
                CROSS, entry["left_candidate"], entry["right_candidate"],
                entry["code"]))
    print()
    print(_field("episodes", "%d (%d scored, %d unscored)" % (
        totals["episode_count"], totals["scored_count"], totals["unscored_count"])))
    print(_field("comparisons", "%d" % totals["comparison_count"]))
    if totals["refusal_count"]:
        print(_field("refusals", "%d" % totals["refusal_count"]))
    print(_field("batch", os.path.join(args.output, "batch.json")))

    raise SystemExit(exit_code)


# ------------------------------------------------------------------ parser
def build_parser():
    p = argparse.ArgumentParser(
        prog="trvs",
        description="TRAAVIIS -- a local-first toolchain for evidence-grade agent "
                    "evaluation, with TRVM worlds as its exact-replay substrate.")
    p.add_argument("--version", action="version", version="traaviis %s" % __version__)
    sub = p.add_subparsers(dest="command", metavar="<command>")

    d = sub.add_parser("doctor", help="engine location, versions, verifier availability")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor, needs_engine=True)

    ini = sub.add_parser("init", help="scaffold an environment for a substrate template")
    ini.add_argument("directory", metavar="DIR", nargs="?",
                     help="destination directory (must not exist, or be empty)")
    ini.add_argument("--template", metavar="NAME",
                     help="which substrate to scaffold (see --list)")
    ini.add_argument("--list", action="store_true", help="list available templates")
    ini.add_argument("--json", action="store_true", help="emit the scaffold report")
    ini.set_defaults(func=cmd_init, needs_engine=False)

    pk = sub.add_parser("pack", help="close a scaffold into a verified package")
    pk.add_argument("environment", metavar="ENV",
                    help="a scaffolded environment directory (contains env.json)")
    pk.add_argument("output", metavar="DIR",
                    help="destination package directory (must not exist, or be empty)")
    pk.add_argument("--json", action="store_true", help="emit the raw pack report")
    pk.set_defaults(func=cmd_pack, needs_engine=False)

    vb = sub.add_parser("verify-bundle",
                        help="re-derive a distributed package's bundle- and env-")
    vb.add_argument("bundle", metavar="DIR|ARCHIVE",
                    help="a packed environment directory, or a ZIP/tar of one")
    vb.add_argument("--package-only", action="store_true",
                    help="check package closure only; do not reopen the environment")
    vb.add_argument("--json", action="store_true",
                    help="emit the raw verification report")
    vb.set_defaults(func=cmd_verify_bundle, needs_engine=False)

    ab = sub.add_parser("archive-bundle",
                        help="serialize a package into a deterministic ZIP")
    ab.add_argument("bundle", metavar="DIR", help="a packed environment directory")
    ab.add_argument("output", metavar="OUTPUT.zip", help="archive to write")
    ab.add_argument("--package-only", action="store_true",
                    help="check package closure only; do not reopen the environment")
    ab.add_argument("--json", action="store_true", help="emit the raw report")
    ab.set_defaults(func=cmd_archive_bundle, needs_engine=False)

    def world_cmd(name, func, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("world", metavar="WORLD.wrl", help="a WallRiderLang world file")
        sp.add_argument("--json", action="store_true", help="emit the raw engine payload")
        sp.set_defaults(func=func, needs_engine=True)
        return sp

    world_cmd("id", cmd_id, "print the world's SemanticArtifactID")
    ins = world_cmd("inspect", cmd_inspect, "actors, edges, config, diagnostics")
    ins.add_argument("--graph", action="store_true", help="only the edge list")
    ins.add_argument("--actors", action="store_true", help="only the actor list")
    ins.add_argument("--diagnostics", action="store_true", help="only diagnostics")
    world_cmd("run", cmd_run, "lower and deterministically fold the world")
    ver = world_cmd("verify", cmd_verify, "reference / native / oracle agreement (strict)")
    ver.add_argument("--reference-only", action="store_true",
                     help="run only the ic_ref reducer (weakest)")
    ver.add_argument("--no-oracle", action="store_true",
                     help="skip the independent Fixture oracle (reference vs native)")
    ver.add_argument("--allow-skipped", action="store_true",
                     help="exit 0 even if a required verifier was unavailable")

    rep = world_cmd("replay", cmd_replay, "re-fold and print the film strip")
    rep.add_argument("--expect", metavar="sem-ID",
                     help="assert the fold reproduces this SemanticArtifactID")
    rep.add_argument("--film", metavar="HASH",
                     help="assert the fold reproduces this final film hash")

    df = sub.add_parser("diff", help="compare two worlds' identity + films")
    df.add_argument("world_a", metavar="A.wrl", help="a WallRiderLang world file")
    df.add_argument("world_b", metavar="B.wrl", help="a WallRiderLang world file")
    df.add_argument("--json", action="store_true", help="emit the raw comparison")
    df.set_defaults(func=cmd_diff, needs_engine=True)

    ev = sub.add_parser(
        "eval-one",
        help="run one trusted-local Residency episode over a task bundle")
    ev.add_argument("bundle", metavar="BUNDLE",
                    help="a task-bundle directory (task/reward/snapshot/subject)")
    ev.add_argument("--agent", nargs="+", metavar="ARG",
                    help="the agent argv to evaluate (overrides bundle agent.json); "
                         "for an agent argv containing dashed flags, pass it after "
                         "a `--` separator instead")
    ev.add_argument("--platform", default="unknown",
                    help="platform label sealed into execution_facts (e.g. linux-x86_64)")
    ev.add_argument("--toolchain", metavar="FILE",
                    help="a JSON toolchain descriptor for execution_facts")
    ev.add_argument("--output", metavar="DIR",
                    help="persist the episode as DIR/episode-<id>/ (written "
                         "atomically, re-verified for closure before returning)")
    ev.add_argument("--json", action="store_true", help="emit the raw episode receipt")
    ev.set_defaults(func=cmd_eval_one, needs_engine=False)

    evs = sub.add_parser(
        "eval",
        help="run an agent over a split of a packed environment")
    evs.add_argument("package", metavar="PKG",
                     help="a packed environment directory (contains environment.json)")
    evs.add_argument("--split", required=True, metavar="NAME",
                     help="which split to run (e.g. train / dev / test)")
    evs.add_argument("--agent", nargs="+", metavar="ARG",
                     help="the agent argv to evaluate; for an argv containing "
                          "dashed flags, pass it after a `--` separator instead")
    evs.add_argument("--platform", default="unknown",
                     help="platform label sealed into execution_facts")
    evs.add_argument("--toolchain", metavar="FILE",
                     help="a JSON toolchain descriptor for execution_facts")
    evs.add_argument("--output", metavar="DIR",
                     help="persist each episode as DIR/episode-<id>/ plus "
                          "DIR/evaluation.json")
    evs.add_argument("--json", action="store_true",
                     help="emit the raw evaluation index")
    evs.set_defaults(func=cmd_eval, needs_engine=False)

    vep = sub.add_parser(
        "verify-episode",
        help="re-verify a saved episode bundle without running the agent")
    vep.add_argument("episode_dir", metavar="DIR",
                     help="a saved episode-<id> bundle directory")
    vep.add_argument("--json", action="store_true", help="emit the raw closure report")
    vep.set_defaults(func=cmd_verify_episode, needs_engine=False)

    cmp_ = sub.add_parser(
        "compare",
        help="compare two closed episode bundles answering one task")
    cmp_.add_argument("left", metavar="LEFT",
                      help="a saved episode-<id> bundle directory")
    cmp_.add_argument("right", metavar="RIGHT",
                      help="a saved episode-<id> bundle directory")
    cmp_.add_argument("--output", metavar="FILE",
                      help="write the ComparisonV1 report to FILE (atomically)")
    cmp_.add_argument("--json", action="store_true",
                      help="emit the raw ComparisonV1 report")
    cmp_.set_defaults(func=cmd_compare, needs_engine=False)

    bat = sub.add_parser(
        "batch",
        help="run several candidates over one split and compare them pairwise")
    bat.add_argument("package", metavar="PKG",
                     help="a packed environment directory (contains environment.json)")
    bat.add_argument("split", metavar="SPLIT",
                     help="which split every candidate runs (e.g. dev / test)")
    bat.add_argument("--candidates", required=True, metavar="FILE",
                     help="a CandidateSetV1 JSON file: one candidate_key and "
                          "argv per candidate")
    bat.add_argument("--output", required=True, metavar="DIR",
                     help="where the batch is published; required, because a "
                          "comparison replays persisted episode bundles")
    bat.add_argument("--platform", default="unknown",
                     help="platform label sealed into execution_facts")
    bat.add_argument("--toolchain", metavar="FILE",
                     help="a JSON toolchain descriptor for execution_facts")
    bat.add_argument("--json", action="store_true",
                     help="emit the raw SerialBatchV1 report")
    bat.set_defaults(func=cmd_batch, needs_engine=False)

    srv = sub.add_parser(
        "serve",
        help="serve a packed environment as a remote submission endpoint")
    srv.add_argument("package", metavar="PKG",
                     help="a packed environment directory (contains environment.json)")
    srv.add_argument("--ors", action="store_true", required=True,
                     help="serve the Residency Submission ORS Profile v1; the "
                          "only protocol this verb speaks")
    srv.add_argument("--split", required=True, metavar="NAME",
                     help="which split is served (e.g. dev / test)")
    srv.add_argument("--output", required=True, metavar="DIR",
                     help="where every accepted submission's episode bundle is "
                          "published; required, because `finished` is a claim "
                          "that the evidence reached disk")
    srv.add_argument("--host", default="127.0.0.1",
                     help="bind address (default 127.0.0.1; anything else needs "
                          "--allow-remote)")
    srv.add_argument("--port", type=int, default=8080,
                     help="bind port (0 picks a free one and prints it)")
    srv.add_argument("--allow-remote", action="store_true",
                     help="permit a non-loopback bind address")
    srv.add_argument("--platform", default="unknown",
                     help="platform label sealed into execution_facts")
    srv.add_argument("--toolchain", metavar="FILE",
                     help="a JSON toolchain descriptor for execution_facts")
    srv.set_defaults(func=cmd_serve, needs_engine=False)
    return p


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    # A standalone `--` ends trvs's own options: everything after it is the agent
    # argv, passed through verbatim (dashed flags included). This is the robust way
    # to hand flags to the evaluated agent without argparse claiming them.
    agent_tail = None
    if "--" in argv:
        cut = argv.index("--")
        argv, agent_tail = argv[:cut], argv[cut + 1:]

    parser = build_parser()
    args = parser.parse_args(argv)
    args.agent_tail = agent_tail
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    # Only world commands need the Forge engine; eval-one operates on task bundles.
    engine = _engine.load() if getattr(args, "needs_engine", True) else None
    args.func(engine, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
