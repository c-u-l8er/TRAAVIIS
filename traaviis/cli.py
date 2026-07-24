"""trvs / traaviis -- a local-first toolchain for evidence-grade agent evaluation.

TRAAVIIS evaluates a user-supplied agent against a frozen subject and proves what
happened, with TRVM worlds as its exact-replay substrate. The world commands run
real over the Forge engine's stable public API (traaviis.engine -> forge_api):
`id`/`inspect` re-lower the source, `run` folds it through the ic_ref reducer, and
`verify` cross-checks ic_ref against the native ic32 reducer and the independent
Fixture oracle. `eval-one` admits an eval bundle, runs the agent, and folds one
content-addressed episode receipt.

    trvs doctor              -- engine location, versions, verifier availability
    trvs id      WORLD.wrl   -- the world's SemanticArtifactID (pure identity)
    trvs inspect WORLD.wrl   -- actors, edges, config, diagnostics
    trvs run     WORLD.wrl   -- lower + deterministically fold; per-epoch film
    trvs verify  WORLD.wrl   -- reference / native / oracle agreement (strict)
    trvs replay  WORLD.wrl   -- re-fold and print the film strip; --expect asserts
    trvs diff    A.wrl B.wrl -- compare two worlds' identity + per-epoch films
    trvs eval-one BUNDLE     -- run one trusted-local Residency episode over a bundle

TRAAVIIS does not embed, select, or route a model. Evaluation runs a user-supplied
agent command, which may use one; the world commands call none.
"""
import argparse
import json
import os
import sys

from . import __version__, engine as _engine
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


def _wire_verifiers(task):
    """Inject the substrate verifiers the task's config actually calls for.

    ``tests`` is wired whenever a ``test_plan`` is present; ``identity`` is wired
    to the real Forge adapter whenever an ``identity_policy`` is present. If the
    Forge re-lower engine is unavailable (the public entrypoint is not published
    yet) identity is left unwired — the eval-one config preflight (F4) then reports
    a required ``identity`` signal as invalid-config rather than faking a result.
    """
    from . import substrate_verifiers as SV
    from . import forge_adapter as FA

    extra, notes = {}, []
    if isinstance(task.get("test_plan"), dict):
        extra["tests"] = SV.tests_verifier
    if isinstance(task.get("identity_policy"), dict):
        try:
            extra["identity"] = SV.make_identity_verifier(FA.real_adapter())
        except FA.ForgeUnavailable as exc:
            notes.append("identity verifier unavailable: %s" % exc)
    return extra, notes


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

    extra, notes = _wire_verifiers(task)
    for note in notes:
        sys.stderr.write("  warn %s\n" % note)

    try:
        receipt = evalone.eval_one(
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

    if args.json:
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

    raise SystemExit(_EVAL_EXIT.get(status, EXIT_UNAVAILABLE))


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
    ev.add_argument("--json", action="store_true", help="emit the raw episode receipt")
    ev.set_defaults(func=cmd_eval_one, needs_engine=False)
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
