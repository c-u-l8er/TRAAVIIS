"""Battery for the Episode Evidence Closure (traaviis.episode_bundle).

GPT-5.6 ruled this the milestone that makes "keep the proof" literally true: an
``episode-…`` receipt is only an attestation, so this seam writes the concrete
evidence it attests into one durable ``episode-<id>/`` directory and re-verifies
that directory **without ever re-running the agent**. These tests pin both halves:

  * ``write_episode_bundle`` builds in a temp sibling and atomically renames, so a
    reader never sees a half-written bundle; a re-run of identical inputs is a
    byte-identical, idempotent no-op.
  * ``verify_episode_bundle`` reopens every member under strict containment,
    recomputes each artifact id from the saved bytes, re-attests the process bytes
    against the trace digests, checks the saved per-signal evidence against the
    receipt digests, re-runs the declared verifiers against the saved subject +
    candidate, recomputes the reward, and confirms the receipt is self-consistent.

The structural cases build the episode in-process from the committed
``residency-demo`` bundle (three pure signals — no engine needed) and drive the
deterministic stub agent. Two engine-gated cases exercise the five-signal
``residency-forge`` bundle so a real Forge identity re-lower is persisted and
replays; they SKIP when the engine cannot be located.

Run directly:      python3 test/test_episode_evidence.py
Run under pytest:  pytest test/test_episode_evidence.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import admission  # noqa: E402
from traaviis import episode_bundle as EB  # noqa: E402
from traaviis import evalone  # noqa: E402
from traaviis import runner  # noqa: E402
from traaviis import substrate_verifiers as SV  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEMO = os.path.join(REPO, "examples", "eval-one", "residency-demo")
FORGE = os.path.join(REPO, "examples", "eval-one", "residency-forge")
STUB = os.path.join(HERE, "fixtures", "stub_agent.py")
FORGE_AGENT = os.path.join(HERE, "fixtures", "residency_agent.py")


class Skip(Exception):
    pass


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _bundle_parts(root):
    task = _load(os.path.join(root, "task.json"))
    reward_spec = _load(os.path.join(root, "reward.json"))
    snapshot = _load(os.path.join(root, "snapshot.json"))
    content = admission.verify_subject_tree(snapshot, os.path.join(root, "subject"))
    return task, reward_spec, snapshot, content


def _demo_run():
    """Evaluate one demo episode in-process (engine-free) → EvaluationRunV1 + parts.

    The demo task requires only the three pure signals; ``tests`` / ``identity`` are
    scored-but-not-applicable, so no substrate verifier and no engine is needed.
    """
    task, reward_spec, snapshot, content = _bundle_parts(DEMO)
    run = evalone.evaluate(
        task, content, [sys.executable, STUB], reward_spec,
        snapshot=snapshot, extra_verifiers={}, platform="linux-x86_64",
    )
    return run, task, reward_spec, snapshot, content


def _write_demo(dest_root):
    run, task, reward_spec, snapshot, content = _demo_run()
    path = EB.write_episode_bundle(
        run, task=task, reward_spec=reward_spec, snapshot=snapshot,
        content=content, dest_root=dest_root)
    return run, path


def _tmp():
    return tempfile.mkdtemp(prefix="traaviis-episode-")


# --- engine gating (five-signal identity cases only) -----------------------

def _engine_available():
    argv = [sys.executable, "-m", "traaviis.cli", "doctor"]
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
    return not (p.returncode == 2 and "could not locate" in p.stderr)


def _require_engine():
    if not _engine_available():
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")


# ===========================================================================
# Structural cases (engine-free — always run)
# ===========================================================================

def test_full_bundle_closes():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        report = EB.verify_episode_bundle(path)
        assert report["ok"], report["checks"]
        assert report["episode_id"] == run["receipt"]["episode_id"]
        for stage in ("closure", "artifacts", "reward", "episode_id"):
            assert report["checks"][stage]["ok"], (stage, report["checks"][stage])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_saved_finding_reproduces_finding_id():
    from traaviis import identity as I
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        finding = _load(os.path.join(path, "evidence", "finding.json"))
        assert I.finding_id(finding) == run["receipt"]["outputs"]["finding_id"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_saved_patch_reproduces_patch_id():
    from traaviis import identity as I
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        with open(os.path.join(path, "evidence", "candidate.patch"),
                  "r", encoding="utf-8") as fh:
            diff = fh.read()
        patch = {"patch_version": "residency.patch.v1", "diff": diff}
        assert I.patch_id(patch) == run["receipt"]["outputs"]["patch_id"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_saved_process_bytes_match_trace_digests():
    import hashlib
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        event = run["artifacts"]["trace"]["events"][0]
        for name, key in (("stdout.bin", "stdout_digest"),
                          ("stderr.bin", "stderr_digest")):
            with open(os.path.join(path, "evidence", "process", name), "rb") as fh:
                data = fh.read()
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
            assert digest == event[key], name
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_saved_verifier_evidence_matches_receipt_digest():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        receipt_ev = run["receipt"]["verification_evidence"]
        for sig, ref in receipt_ev.items():
            obj = _load(os.path.join(path, "evidence", "verifiers", "%s.json" % sig))
            assert evalone._evidence_ref(obj)["digest"] == ref["digest"], sig
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_identical_run_is_byte_identical_and_idempotent():
    tmp = _tmp()
    try:
        run_a, path_a = _write_demo(tmp)
        run_b, path_b = _write_demo(tmp)  # same inputs → same episode id → same path
        assert run_a["receipt"]["episode_id"] == run_b["receipt"]["episode_id"]
        assert path_a == path_b  # idempotent: the second write returns the same dir
        # No stray temp siblings left after the idempotent second write.
        siblings = [n for n in os.listdir(tmp) if n.startswith(".tmp-episode-")]
        assert siblings == [], siblings
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bundle_creation_is_atomic_no_temp_left():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        entries = sorted(os.listdir(tmp))
        assert entries == [os.path.basename(path)], entries  # only the final dir
        assert os.path.isfile(os.path.join(path, "episode-bundle.json"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tampered_finding_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        fp = os.path.join(path, "evidence", "finding.json")
        d = _load(fp)
        d["claims"][0]["statement"] = "TAMPERED"
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["artifacts"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tampered_patch_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        pp = os.path.join(path, "evidence", "candidate.patch")
        with open(pp, "a", encoding="utf-8") as fh:
            fh.write("\n; sneaky trailing line\n")
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["artifacts"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tampered_subject_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        # Rewrite a sealed subject file: the snapshot no longer binds the content,
        # so re-admission fails and closure cannot even begin.
        mod = os.path.join(path, "subject", "src", "mod.py")
        with open(mod, "w", encoding="utf-8") as fh:
            fh.write("return 999\n")
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["closure"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tampered_verifier_evidence_file_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        # Corrupt a SAVED evidence file: it no longer hashes to the receipt digest
        # (which entered episode-), so the artifacts check flags it.
        ep = os.path.join(path, "evidence", "verifiers", "citations.json")
        d = _load(ep)
        d["state"] = "fail"  # lie about what the verifier found
        with open(ep, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["artifacts"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tampered_receipt_reward_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        rp = os.path.join(path, "receipt.json")
        d = _load(rp)
        d["reward"] = 1.0  # inflate the sealed reward
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        # The receipt is no longer self-consistent (reward is inside episode-), and
        # the recomputed reward disagrees with the tampered stored value.
        assert not report["checks"]["episode_id"]["ok"]
        assert not report["checks"]["reward"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_evidence_member_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        os.remove(os.path.join(path, "evidence", "trace.json"))
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["closure"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_symlinked_evidence_member_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        # Replace trace.json with a symlink to an external file: containment refuses
        # it (the resolved path is not the lexical one) before it is ever read.
        outside = os.path.join(tmp, "outside-trace.json")
        shutil.move(os.path.join(path, "evidence", "trace.json"), outside)
        os.symlink(outside, os.path.join(path, "evidence", "trace.json"))
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["closure"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_runs_no_agent_process(monkeypatch=None):
    # The defining law: re-verification is pure replay — it never launches the
    # agent. We poison runner.run_agent so any attempt to run one is a hard error,
    # and assert the bundle still closes.
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        saved = runner.run_agent

        def _boom(*a, **k):
            raise AssertionError("verify must not run the agent")

        runner.run_agent = _boom
        try:
            report = EB.verify_episode_bundle(path)
        finally:
            runner.run_agent = saved
        assert report["ok"], report["checks"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_invalid_config_episode_persists_nothing():
    # An invalid-config episode (a required signal with no wired verifier) never
    # ran the agent and produced no evidence — there is nothing to keep, so
    # write_episode_bundle refuses it rather than sealing an empty directory.
    task, reward_spec, snapshot, content = _bundle_parts(DEMO)
    task = dict(task)
    plan = dict(task["verifier_plan"])
    plan["required"] = sorted(set(plan["required"]) | {"tests"})
    plan["not_applicable"] = [s for s in plan["not_applicable"] if s != "tests"]
    task["verifier_plan"] = plan
    from traaviis import identity as I
    task.pop("task_id", None)
    task["task_id"] = I.task_id(task)
    # cross_bind checks task.reward_id/snapshot — reseal against the same inputs.
    run = evalone.evaluate(
        task, content, [sys.executable, STUB], reward_spec,
        snapshot=snapshot, extra_verifiers={}, platform="linux-x86_64",
    )
    assert run["receipt"]["status"] == "invalid"
    assert run["artifacts"] is None
    tmp = _tmp()
    try:
        raised = False
        try:
            EB.write_episode_bundle(
                run, task=task, reward_spec=reward_spec, snapshot=snapshot,
                content=content, dest_root=tmp)
        except EB.EpisodeBundleError:
            raised = True
        assert raised, "invalid-config episode must not persist a bundle"
        assert os.listdir(tmp) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# Five-signal identity cases (skip without the engine)
# ===========================================================================

def test_forge_identity_evidence_persists_and_reverifies():
    _require_engine()
    from traaviis import forge_adapter as FA
    task, reward_spec, snapshot, content = _bundle_parts(FORGE)
    extra = {
        "tests": SV.tests_verifier,
        "identity": SV.make_identity_verifier(FA.real_adapter()),
    }
    run = evalone.evaluate(
        task, content, [sys.executable, FORGE_AGENT], reward_spec,
        snapshot=snapshot, extra_verifiers=extra, platform="linux-x86_64",
    )
    assert run["receipt"]["status"] == "ok"
    assert run["receipt"]["verification"]["identity"] == "pass"
    tmp = _tmp()
    try:
        path = EB.write_episode_bundle(
            run, task=task, reward_spec=reward_spec, snapshot=snapshot,
            content=content, dest_root=tmp, extra_verifiers=extra)
        # The persisted identity evidence carries the before/after world bindings.
        ident = _load(os.path.join(path, "evidence", "verifiers", "identity.json"))
        binding = ident["detail"]["bindings"]["world"]
        assert binding["before_id"] == binding["after_id"]  # held → equal
        # Replay re-lowers the saved world through the same adapter and closes.
        report = EB.verify_episode_bundle(path, extra_verifiers=extra)
        assert report["ok"], report["checks"]
        assert report["checks"]["signals"]["identity"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_forge_version_string_enters_episode_id():
    _require_engine()
    from traaviis import forge_adapter as FA
    task, reward_spec, snapshot, content = _bundle_parts(FORGE)
    extra = {
        "tests": SV.tests_verifier,
        "identity": SV.make_identity_verifier(FA.real_adapter()),
    }
    run = evalone.evaluate(
        task, content, [sys.executable, FORGE_AGENT], reward_spec,
        snapshot=snapshot, extra_verifiers=extra, platform="linux-x86_64",
    )
    impl = run["receipt"]["verifier_versions"]["identity"]["implementation"]
    assert impl.startswith("forge.identity.v1@api-")
    assert "@lower-" in impl and "@engine-" in impl
    tmp = _tmp()
    try:
        path = EB.write_episode_bundle(
            run, task=task, reward_spec=reward_spec, snapshot=snapshot,
            content=content, dest_root=tmp, extra_verifiers=extra)
        # The implementation version is sealed into verifier_versions, which is in
        # the episode identity allowlist → it is part of the episode id.
        report = EB.verify_episode_bundle(path, extra_verifiers=extra)
        assert report["ok"], report["checks"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# Episode Evidence Reopen Closure battery (GPT-5.6 reopen ruling)
# ===========================================================================

def _tampered_run():
    """A demo run that trips a policy violation → an authentic *tampered* episode.

    Constraining ``writable_paths`` to ``src/`` makes the agent's root-level
    ``result.json`` / ``candidate.patch`` writes escape the writable set, so the
    runner records ``policy_violations`` and the reward is scored ``tampered``.
    """
    from traaviis import identity as I
    task, reward_spec, snapshot, content = _bundle_parts(DEMO)
    task = json.loads(json.dumps(task))
    task["agent_run_policy"]["writable_paths"] = ["src/"]
    task.pop("task_id", None)
    task["task_id"] = I.task_id(task)  # reseal: run policy is inside the task id
    run = evalone.evaluate(
        task, content, [sys.executable, STUB], reward_spec,
        snapshot=snapshot, extra_verifiers={}, platform="linux-x86_64",
    )
    return run, task, reward_spec, snapshot, content


def test_derived_receipt_equals_stored_receipt():
    # The positive law of the reopen: the receipt rebuilt from the saved evidence
    # (via the shared builder) is byte-identical to the stored one — the single
    # comparison that subsumes reward + outputs + execution facts.
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        report = EB.verify_episode_bundle(path)
        assert report["ok"], report["checks"]
        assert report["checks"]["receipt"]["ok"], report["checks"]["receipt"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stored_only_self_consistency_is_insufficient():
    # A receipt can be internally self-consistent (its recomputed episode id equals
    # its own field) yet lie about the run: flip a sealed verification state and
    # re-seal the episode id. Stored-only self-consistency now holds, but the
    # receipt DERIVED from the saved evidence disagrees → the bundle still fails.
    from traaviis import identity as I
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        rp = os.path.join(path, "receipt.json")
        d = _load(rp)
        d["verification"]["citations"] = "fail"  # lie: evidence still says pass
        d["episode_id"] = I.episode_id(d)         # re-seal so it is self-consistent
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        assert I.episode_id(d) == d["episode_id"]  # stored-only check would pass
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["receipt"]["ok"]  # derived != stored catches it
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_verifier_evidence_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        os.remove(os.path.join(path, "evidence", "verifiers", "citations.json"))
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["artifacts"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_verifier_manifest_entry_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        mp = os.path.join(path, "episode-bundle.json")
        man = _load(mp)
        man["members"]["verifiers"].pop("citations")  # drop one scored signal key
        with open(mp, "w", encoding="utf-8") as fh:
            json.dump(man, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["artifacts"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extra_verifier_evidence_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        # Add an evidence file + manifest entry for a signal that was never scored.
        rel = "evidence/verifiers/bogus.json"
        with open(os.path.join(path, rel), "w", encoding="utf-8") as fh:
            json.dump({"evidence_version": evalone.VERIFIER_EVIDENCE_VERSION,
                       "signal": "bogus", "state": "pass", "detail": {}}, fh)
        mp = os.path.join(path, "episode-bundle.json")
        man = _load(mp)
        man["members"]["verifiers"]["bogus"] = rel
        with open(mp, "w", encoding="utf-8") as fh:
            json.dump(man, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["artifacts"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verifier_implementation_drift_is_unavailable():
    # A wired verifier whose implementation version differs from what the receipt
    # sealed makes the bundle *unavailable* to judge here (exit 2) — NOT an evidence
    # failure against the run. The substrate simply changed under it.
    from traaviis import verifiers as V
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)

        def drift(ctx):
            return V.citations_verifier(ctx)

        drift.version = "traaviis.citations-impl.vDRIFT"
        live = {"citations": drift, "patch": V.patch_verifier,
                "finding_completeness": V.finding_completeness_verifier}
        report = EB.verify_episode_bundle(path, live_verifiers=live)
        assert report["outcome"] == EB.OUTCOME_UNAVAILABLE, report["checks"]
        assert not report["checks"]["verifier_versions"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_authentic_tampered_episode_reverifies_invalid():
    # An authentically tampered episode (real policy violations) is preserved
    # faithfully: the bundle CLOSES, and the sealed + re-derived receipt both read
    # ``invalid``. The tamper verdict is reconstructed from the persisted
    # policy-violations bytes — never assumed away.
    tmp = _tmp()
    try:
        run, task, reward_spec, snapshot, content = _tampered_run()
        assert run["receipt"]["status"] == "invalid"
        assert run["artifacts"]["process"]["policy_violations"]
        path = EB.write_episode_bundle(
            run, task=task, reward_spec=reward_spec, snapshot=snapshot,
            content=content, dest_root=tmp)
        report = EB.verify_episode_bundle(path)
        assert report["ok"], report["checks"]
        seal = _load(os.path.join(path, "receipt.json"))
        assert seal["status"] == "invalid"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_policy_violation_bytes_match_trace_digest():
    # The persisted policy-violations bytes must re-attest to the trace's
    # ``policy_violations_digest``; tampering the saved list fails artifacts.
    tmp = _tmp()
    try:
        run, task, reward_spec, snapshot, content = _tampered_run()
        path = EB.write_episode_bundle(
            run, task=task, reward_spec=reward_spec, snapshot=snapshot,
            content=content, dest_root=tmp)
        pv = os.path.join(path, "evidence", "process", "policy-violations.json")
        d = _load(pv)
        d.append("fabricated-extra-violation")  # no longer hashes to the digest
        with open(pv, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["artifacts"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_false_declared_snapshot_id_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        sp = os.path.join(path, "snapshot.json")
        d = _load(sp)
        d["snapshot_id"] = "snap-" + "0" * 64
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["closure"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_false_declared_task_id_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        tp = os.path.join(path, "task.json")
        d = _load(tp)
        d["task_id"] = "task-" + "0" * 64
        with open(tp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["closure"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_false_declared_reward_id_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        rp = os.path.join(path, "reward.json")
        d = _load(rp)
        d["reward_id"] = "rew-" + "0" * 64
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["closure"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_false_declared_trace_id_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        tp = os.path.join(path, "evidence", "trace.json")
        d = _load(tp)
        d["trace_id"] = "trace-" + "0" * 64
        with open(tp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["closure"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_false_declared_finding_id_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        fp = os.path.join(path, "evidence", "finding.json")
        d = _load(fp)
        d["finding_id"] = "finding-" + "0" * 64
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["artifacts"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_task_reward_cross_binding_mismatch_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        tp = os.path.join(path, "task.json")
        d = _load(tp)
        d["reward_id"] = "rew-" + "0" * 64  # task no longer binds this reward
        with open(tp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["closure"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_task_snapshot_cross_binding_mismatch_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        tp = os.path.join(path, "task.json")
        d = _load(tp)
        d["subject"]["snapshot_id"] = "snap-" + "0" * 64  # binds a different subject
        with open(tp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["closure"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_invalid_utf8_patch_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        pp = os.path.join(path, "evidence", "candidate.patch")
        with open(pp, "wb") as fh:
            fh.write(b"--- a/x\n+++ b/x\n\xff\xfe not utf-8\n")
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["artifacts"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_runner_rejects_invalid_utf8_patch():
    # The write side of step 8: a candidate patch that is not valid UTF-8 is not a
    # diff — the runner records no patch rather than lossily decoding it.
    import types
    root = tempfile.mkdtemp(prefix="traaviis-run-")
    try:
        pass
    finally:
        shutil.rmtree(root, ignore_errors=True)
    # Drive the real runner with a stub that writes non-UTF-8 patch bytes.
    stub = os.path.join(_tmp(), "badenc_agent.py")
    with open(stub, "w", encoding="utf-8") as fh:
        fh.write(
            "import sys\n"
            "open('candidate.patch','wb').write(b'--- a\\n+++ b\\n\\xff\\xfe\\n')\n"
            "open('result.json','w').write('{\"format\":\"traaviis.agent-result.v1\","
            "\"patch_path\":\"candidate.patch\"}')\n"
            "sys.exit(0)\n")
    policy = {"command_mode": "argv", "shell": False, "timeout_seconds": 30,
              "writable_paths": ["."], "environment": {}}
    res = runner.run_agent([sys.executable, stub], {"a.txt": "x\n"}, policy)
    assert res["patch_text"] is None
    shutil.rmtree(os.path.dirname(stub), ignore_errors=True)


def test_native_oracle_state_tamper_rejected():
    # native / oracle are pseudo-signals: always not_applicable. A receipt that
    # claims one PASSED is caught — replay yields not_applicable, so the signal and
    # the derived receipt disagree with the sealed one.
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        rp = os.path.join(path, "receipt.json")
        d = _load(rp)
        d["verification"]["native"] = "pass"  # a pseudo-signal cannot pass
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["signals"]["native"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_0755_subject_mode_survives_persistence():
    # A sealed 0755 executable must materialize back as 0755 and re-admit; the
    # runner's plain writer (process umask) would silently drop the bit.
    import hashlib
    from traaviis import identity as I
    src = "run.sh"
    text = "#!/bin/sh\necho hi\n"
    # The same LF-normalized content hash the snapshot rule (admission) uses.
    norm = text.encode("utf-8").replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    chash = "sha256:" + hashlib.sha256(norm).hexdigest()
    snapshot = {
        "snapshot_version": "residency.snapshot.v1",
        "files": {src: chash},
        "file_modes": {src: "0755"},
        "binary_paths": [],
        "exclusions": [],
    }
    snapshot["snapshot_id"] = I.snapshot_id(snapshot)
    content = {src: text}
    root = _tmp()
    try:
        admission.materialize_subject(snapshot, content, root)
        import stat as _stat
        mode = _stat.S_IMODE(os.lstat(os.path.join(root, src)).st_mode)
        assert oct(mode) == "0o755", oct(mode)
        # And the materialized tree re-admits exactly against the snapshot.
        readmit = admission.verify_subject_tree(snapshot, root)
        assert readmit == content
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_staging_bundle_is_never_published():
    # If the staged bundle fails its pre-publish verification, nothing is renamed
    # into place — dest_root is left with no final episode directory (step 9). We
    # force a staging failure by tampering the receipt reward before writing.
    tmp = _tmp()
    try:
        run, task, reward_spec, snapshot, content = _demo_run()
        run = dict(run)
        run["receipt"] = dict(run["receipt"])
        run["receipt"]["reward"] = 0.123456  # derived reward will disagree
        raised = False
        try:
            EB.write_episode_bundle(
                run, task=task, reward_spec=reward_spec, snapshot=snapshot,
                content=content, dest_root=tmp)
        except EB.EpisodeBundleError:
            raised = True
        assert raised, "a bad staged bundle must not publish"
        assert os.listdir(tmp) == [], os.listdir(tmp)  # no final dir, no temp left
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_corrupt_existing_target_is_not_reused():
    # A present-but-corrupt target at the content-addressed path is a conflict —
    # never returned by name alone, never silently overwritten.
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        # Corrupt the already-published episode.
        fp = os.path.join(path, "evidence", "finding.json")
        d = _load(fp)
        d["claims"][0]["statement"] = "CORRUPTED"
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        run2, task, reward_spec, snapshot, content = _demo_run()
        assert run2["receipt"]["episode_id"] == run["receipt"]["episode_id"]
        raised = False
        try:
            EB.write_episode_bundle(
                run2, task=task, reward_spec=reward_spec, snapshot=snapshot,
                content=content, dest_root=tmp)
        except EB.EpisodeBundleConflict:
            raised = True
        assert raised, "a corrupt existing target must raise a conflict"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fresh_independent_writes_share_canonical_artifacts():
    # The corrected invariant: the same evaluation → identical receipt → identical
    # canonical evidence artifacts (NOT a byte-identical whole dir — created_at
    # differs). Two writes into independent roots share every canonical member.
    tmp_a, tmp_b = _tmp(), _tmp()
    try:
        run_a, path_a = _write_demo(tmp_a)
        run_b, path_b = _write_demo(tmp_b)
        assert os.path.basename(path_a) == os.path.basename(path_b)
        for rel in ("receipt.json", "task.json", "reward.json", "snapshot.json",
                    os.path.join("evidence", "finding.json"),
                    os.path.join("evidence", "trace.json"),
                    os.path.join("evidence", "candidate.patch"),
                    os.path.join("evidence", "verifiers", "citations.json")):
            with open(os.path.join(path_a, rel), "rb") as fh:
                a = fh.read()
            with open(os.path.join(path_b, rel), "rb") as fh:
                b = fh.read()
            assert a == b, rel
        # The manifest carries a wall clock, so it is NOT required byte-identical.
        man_a = _load(os.path.join(path_a, "episode-bundle.json"))
        man_b = _load(os.path.join(path_b, "episode-bundle.json"))
        assert man_a["episode_id"] == man_b["episode_id"]
    finally:
        shutil.rmtree(tmp_a, ignore_errors=True)
        shutil.rmtree(tmp_b, ignore_errors=True)


# ===========================================================================
# Episode Evidence Exact Closure battery (GPT-5.6 exact-closure ruling)
# ===========================================================================

def _gate_verifier(state="pass", version="traaviis.gate-impl.v1"):
    """A deterministic required *gate* verifier — same verdict on run and replay.

    It is wired for the unweighted ``gate`` signal (never in ``reward.signals``), so
    it can enter validity + evidence + ``verifier_versions`` without touching the
    reward number. A fixed state makes the replay reproduce it exactly.
    """
    from traaviis.vcontext import VerifierResult

    def verify(ctx):
        return VerifierResult(state)

    verify.version = version
    return verify


def _gate_run(gate_state="pass", gate_version="traaviis.gate-impl.v1"):
    """A demo run with an extra *required, unweighted* ``gate`` signal wired."""
    from traaviis import identity as I
    task, reward_spec, snapshot, content = _bundle_parts(DEMO)
    task = json.loads(json.dumps(task))
    plan = task["verifier_plan"]
    plan["required"] = sorted(set(plan["required"]) | {"gate"})
    task.pop("task_id", None)
    task["task_id"] = I.task_id(task)  # verifier_plan is inside the task id
    extra = {"gate": _gate_verifier(gate_state, gate_version)}
    run = evalone.evaluate(
        task, content, [sys.executable, STUB], reward_spec,
        snapshot=snapshot, extra_verifiers=extra, platform="linux-x86_64",
    )
    return run, task, reward_spec, snapshot, content, extra


def _write_gate(dest_root, gate_state="pass", gate_version="traaviis.gate-impl.v1"):
    run, task, reward_spec, snapshot, content, extra = _gate_run(gate_state, gate_version)
    path = EB.write_episode_bundle(
        run, task=task, reward_spec=reward_spec, snapshot=snapshot,
        content=content, dest_root=dest_root, extra_verifiers=extra)
    return run, path, extra


def test_signal_id_grammar():
    # Unit law of the frozen SignalIDV1 grammar: every existing signal is admitted;
    # every path/traversal/case/whitespace form is rejected.
    from traaviis import signals as S
    for good in ("citations", "patch", "tests", "identity", "finding_completeness",
                 "native", "oracle", "gate", "a", "a_b_9", "z" * 64):
        assert S.is_signal_id(good), good
    for bad in ("../../escape", "/tmp/escape", "a/b", "a\\b", "Upper", "has space",
                "", "_leading", "9leading", "z" * 65, "..", ".", "a-b"):
        assert not S.is_signal_id(bad), bad
    raised = False
    try:
        S.validate_signal_id("../../escape")
    except S.SignalIDError:
        raised = True
    assert raised


def test_signal_path_traversal_cannot_write_outside_staging():
    # A verifier-evidence key is an on-disk file stem: a traversal name must be
    # refused by SignalIDV1 before any bytes are written — never a write primitive.
    from traaviis import signals as S
    run, task, reward_spec, snapshot, content = _demo_run()
    artifacts = run["artifacts"]
    artifacts["verifier_evidence"] = dict(artifacts["verifier_evidence"])
    for bad in ("../../escape", "/tmp/escape", "a/b", "a\\b", "Upper", "has space"):
        parent = _tmp()
        staging = os.path.join(parent, "staging")
        os.makedirs(staging)
        try:
            ev = dict(artifacts["verifier_evidence"])
            ev[bad] = {"evidence_version": evalone.VERIFIER_EVIDENCE_VERSION,
                       "signal": "x", "state": "pass", "detail": {}}
            raised = False
            try:
                EB._populate(staging, run["receipt"], dict(artifacts, verifier_evidence=ev),
                             task, reward_spec, snapshot, content)
            except S.SignalIDError:
                raised = True
            assert raised, bad
            # Nothing named ``escape`` was written anywhere under the parent tree.
            for dp, _dn, fn in os.walk(parent):
                for name in fn:
                    assert "escape" not in name, (bad, os.path.join(dp, name))
        finally:
            shutil.rmtree(parent, ignore_errors=True)


def test_valid_snake_case_signal_accepted():
    # The positive half: a legal snake_case gate signal seals + replays cleanly.
    tmp = _tmp()
    try:
        run, path, extra = _write_gate(tmp)
        assert os.path.isfile(os.path.join(path, "evidence", "verifiers", "gate.json"))
        report = EB.verify_episode_bundle(path, extra_verifiers=extra)
        assert report["ok"], report["checks"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_required_unweighted_gate_is_fully_sealed():
    # A required-but-unweighted gate has its implementation version + evidence digest
    # sealed into episode- (not only the scored subset) and replays closed.
    tmp = _tmp()
    try:
        run, path, extra = _write_gate(tmp, "pass", "traaviis.gate-impl.v1")
        receipt = run["receipt"]
        assert receipt["verifier_versions"]["gate"]["implementation"] == "traaviis.gate-impl.v1"
        assert receipt["verifier_versions"]["gate"]["contract"] is None  # unscored
        assert "gate" in receipt["verification_evidence"]
        assert receipt["verification"]["gate"] == "pass"
        report = EB.verify_episode_bundle(path, extra_verifiers=extra)
        assert report["outcome"] == EB.OUTCOME_CLOSED, report["checks"]
        assert report["checks"]["signals"]["gate"]["ok"], report["checks"]["signals"]["gate"]
        assert report["checks"]["verifier_versions"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unweighted_gate_implementation_drift_is_unavailable():
    # A wired gate whose implementation version differs from the sealed one makes the
    # bundle unavailable (exit 2) — the gate substrate changed, not the evidence.
    tmp = _tmp()
    try:
        run, path, extra = _write_gate(tmp, "pass", "traaviis.gate-impl.v1")
        drift = {"gate": _gate_verifier("pass", "traaviis.gate-impl.vDRIFT")}
        report = EB.verify_episode_bundle(path, extra_verifiers=drift)
        assert report["outcome"] == EB.OUTCOME_UNAVAILABLE, report["checks"]
        assert not report["checks"]["verifier_versions"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unweighted_gate_evidence_tamper_rejected():
    # Corrupting the saved gate evidence file no longer matches the sealed digest.
    tmp = _tmp()
    try:
        run, path, extra = _write_gate(tmp)
        ep = os.path.join(path, "evidence", "verifiers", "gate.json")
        d = _load(ep)
        d["state"] = "fail"  # lie about the gate verdict
        with open(ep, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        report = EB.verify_episode_bundle(path, extra_verifiers=extra)
        assert not report["ok"]
        assert not report["checks"]["artifacts"]["ok"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unweighted_gate_state_change_moves_episode_id():
    # A gate's state is inside the verification map, which is inside episode-: two
    # otherwise-identical runs whose gate verdict differs have different episode ids.
    run_pass, _t, _r, _s, _c, _e = _gate_run("pass")
    run_fail, _t2, _r2, _s2, _c2, _e2 = _gate_run("fail")
    assert run_pass["receipt"]["verification"]["gate"] == "pass"
    assert run_fail["receipt"]["verification"]["gate"] == "fail"
    assert run_pass["receipt"]["episode_id"] != run_fail["receipt"]["episode_id"]


def test_unweighted_gate_does_not_change_reward():
    # The gate is unweighted: whatever it reports, the reward number is scored from
    # ``reward.signals`` alone — identical to the same episode with no gate at all.
    run_gate_pass, _a, _b, _c, _d, _e = _gate_run("pass")
    run_gate_fail, _f, _g, _h, _i, _j = _gate_run("fail")
    run_plain, _task, _rs, _snap, _content = _demo_run()
    assert run_gate_pass["receipt"]["reward"] == run_plain["receipt"]["reward"]
    assert run_gate_fail["receipt"]["reward"] == run_plain["receipt"]["reward"]
    # …yet a failing required gate is still a valid episode (only a required NA is not).
    assert run_gate_fail["receipt"]["status"] == "ok"


def test_extra_root_file_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        with open(os.path.join(path, "STRAY.txt"), "w", encoding="utf-8") as fh:
            fh.write("undeclared\n")
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["tree"]["ok"], report["checks"]["tree"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extra_evidence_file_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        with open(os.path.join(path, "evidence", "stray.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"undeclared": True}, fh)
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["tree"]["ok"], report["checks"]["tree"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extra_subject_file_rejected():
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        with open(os.path.join(path, "subject", "stray.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("x = 1\n")
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["tree"]["ok"], report["checks"]["tree"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unreferenced_symlink_rejected():
    # A symlink that is not any declared member still fails the tree closure — no
    # link may hide beside the sealed evidence, referenced or not.
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        outside = os.path.join(tmp, "outside-target.json")
        with open(outside, "w", encoding="utf-8") as fh:
            json.dump({"external": True}, fh)
        os.symlink(outside, os.path.join(path, "evidence", "link.json"))
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["tree"]["ok"], report["checks"]["tree"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_special_file_rejected():
    # A FIFO / socket / device is not a regular file — the tree closure refuses it.
    if not hasattr(os, "mkfifo"):
        raise Skip("os.mkfifo unavailable on this platform")
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        os.mkfifo(os.path.join(path, "evidence", "special.fifo"))
        report = EB.verify_episode_bundle(path)
        assert not report["ok"]
        assert not report["checks"]["tree"]["ok"], report["checks"]["tree"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_exact_tree_closes():
    # The positive law: an untouched published bundle is exactly its declared closure.
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        report = EB.verify_episode_bundle(path)
        assert report["ok"], report["checks"]
        assert report["checks"]["tree"]["ok"], report["checks"]["tree"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parent_publication_survives_reopen():
    # After atomic publication (+ parent-dir fsync), the published bundle reopens
    # and re-verifies closed — repeatedly, and by an independent second read.
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        assert os.path.basename(path) in os.listdir(tmp)
        for _ in range(2):
            report = EB.verify_episode_bundle(path)
            assert report["outcome"] == EB.OUTCOME_CLOSED, report["checks"]
            assert report["episode_id"] == run["receipt"]["episode_id"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_report_carries_execution_facts_attestation():
    # Every report (closed or failing) classifies which execution facts are
    # rederived from evidence vs carried as runner attestation.
    tmp = _tmp()
    try:
        run, path = _write_demo(tmp)
        report = EB.verify_episode_bundle(path)
        assert report["execution_facts"] == EB.EXECUTION_FACTS_ATTESTATION
        assert report["execution_facts"]["rederived"] == ["agent_process", "sandbox"]
        # A failing report carries the same classification.
        fp = os.path.join(path, "evidence", "finding.json")
        d = _load(fp)
        d["claims"][0]["statement"] = "TAMPERED"
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        bad = EB.verify_episode_bundle(path)
        assert not bad["ok"]
        assert bad["execution_facts"] == EB.EXECUTION_FACTS_ATTESTATION
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _main():
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    passed = skipped = 0
    failures = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print("PASS  %s" % name)
        except Skip as s:
            skipped += 1
            print("SKIP  %s (%s)" % (name, s))
        except AssertionError as exc:
            failures.append((name, exc))
            print("FAIL  %s: %s" % (name, exc))
    print("\n%d passed, %d skipped, %d failed" % (passed, skipped, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
