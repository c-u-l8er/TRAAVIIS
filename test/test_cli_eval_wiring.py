"""Laws for what `trvs eval` says about where its verifiers came from.

`EvaluationV1.runtime_context` exists to answer "which implementations actually
ran?". The library had that right and a library-level test proved it, but the
test called `eval_split(..., registry=registry)` while the CLI called
`eval_split(..., extra_verifiers_for=verifiers_for)` -- and the second seam
deliberately means "the caller brought its own implementations, there is no
registry to interrogate". So a normal `trvs eval` built a real registry, wired
every task from it, and then reported:

    {"wiring": "caller_supplied", "registry_version": null,
     "verifiers_available": [], "verifier_versions": {}}

The attestation was false on the only path a user actually takes, and it was
false in the direction that matters: it disclaimed a registry that had in fact
supplied everything. The cause was that printing a warning required taking over
the wiring, so `on_wiring_notes` now carries the notes and leaves the seam alone.

These laws run the real CLI as a subprocess, because the defect lived precisely
in the gap between the library call and the CLI call.

Run directly:      python3 test/test_cli_eval_wiring.py
Run under pytest:  pytest test/test_cli_eval_wiring.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import engine as _engine  # noqa: E402
from traaviis import evalsplit as ES, pack as P, scaffold as S  # noqa: E402
from traaviis import wiring  # noqa: E402

TEMPLATE = "residency-repair"
AGENT = [sys.executable, os.path.join(REPO, "test", "fixtures", "repair_agent.py")]


class Skip(Exception):
    pass


def _engine_or_skip():
    eng = _engine.try_load()
    if eng is None:
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    return eng


def _packed(tmp):
    env = os.path.join(tmp, "env")
    S.materialize(TEMPLATE, env)
    out = os.path.join(tmp, "pkg")
    P.pack(env, out, engine=_engine_or_skip())
    return out


def _cli_eval(package, *extra):
    argv = [sys.executable, "-m", "traaviis.cli", "eval", package,
            "--split", "all", "--json", *extra, "--", *AGENT, "ok"]
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
    return p


def _report(package, *extra):
    p = _cli_eval(package, *extra)
    assert p.stdout.strip(), (p.returncode, p.stderr[-2000:])
    return json.loads(p.stdout), p


# --- A1-A4: the report names the registry that actually supplied verifiers ---

def test_a1_cli_reports_wiring_registry():
    tmp = tempfile.mkdtemp(prefix="trvs-wiring-law-")
    try:
        report, p = _report(_packed(tmp))
        ctx = report["runtime_context"]
        assert ctx["wiring"] == "registry", (ctx, p.stderr[-800:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a2_cli_reports_the_actual_registry_version():
    tmp = tempfile.mkdtemp(prefix="trvs-wiring-law-")
    try:
        report, _p = _report(_packed(tmp))
        ctx = report["runtime_context"]
        assert ctx["registry_version"] == wiring.VERIFIER_REGISTRY_VERSION, ctx
        assert ctx["registry_version"] is not None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a3_available_signals_equal_what_the_registry_can_answer():
    tmp = tempfile.mkdtemp(prefix="trvs-wiring-law-")
    try:
        report, _p = _report(_packed(tmp))
        registry = wiring.default_registry(_engine_or_skip())
        assert report["runtime_context"]["verifiers_available"] == \
            registry.available()
        assert report["runtime_context"]["verifiers_available"], \
            "the CLI reported no available verifiers at all"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a4_implementation_versions_equal_the_registry_versions():
    tmp = tempfile.mkdtemp(prefix="trvs-wiring-law-")
    try:
        report, _p = _report(_packed(tmp))
        registry = wiring.default_registry(_engine_or_skip())
        assert report["runtime_context"]["verifier_versions"] == \
            registry.versions()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- A5: an absent implementation is visible as absent, not as silence -------

def test_a5_missing_identity_support_shows_as_an_absent_signal():
    """A runtime with no engine cannot answer `identity`, and must say so.

    The distinction this protects is "the runtime could not answer it" versus
    "the task never asked" -- which a report that simply omits the signal makes
    indistinguishable.
    """
    from traaviis import substrate_verifiers as SV

    with_engine = wiring.default_registry(_engine_or_skip())
    # Built directly rather than via `default_registry(None)`, which consults the
    # soft loader and so still finds an engine on a machine that has one.
    without = wiring.VerifierRegistryV1(
        tests=SV.tests_verifier, identity=None,
        notes=["identity verifier unavailable: no Forge engine"])

    assert "identity" in with_engine.available()
    assert "identity" not in without.available()
    assert "identity" not in without.versions()
    assert "tests" in without.available(), "the whole registry went missing"
    # The signal is absent, but the registry still reports itself: absence is a
    # fact about a known runtime, not an unknown runtime.
    assert without.registry_version == wiring.VERIFIER_REGISTRY_VERSION


# --- A6/A7: notes are presentation, and stay out of the evidence -------------

def test_a6_human_wiring_warnings_still_print_once():
    """The notes must survive the seam change that stopped them short-circuiting
    the registry -- and must not repeat per task."""
    from traaviis import substrate_verifiers as SV

    seen, calls = set(), []

    def report(notes):
        calls.append(list(notes))
        for n in notes:
            seen.add(n)

    registry = wiring.VerifierRegistryV1(
        tests=SV.tests_verifier, identity=None,
        notes=["identity verifier unavailable: no Forge engine"])
    wire = ES._verifiers_from(registry, report)

    # A task that declares `identity_policy` asks for the identity signal; this
    # registry cannot answer it, which is exactly the case a note explains.
    task = {"substrate_profile": "residency.repository.v1",
            "identity_policy": {"kind": "frozen_world"}}

    extra = wire(task)
    assert calls, "a declared-but-unavailable signal produced no note"
    assert "identity" not in extra, extra
    assert "identity verifier unavailable" in " ".join(seen), seen

    # Ten tasks, one gap: the sink is called per task, and de-duplication is the
    # caller's job -- which is what the CLI's `seen_notes` does.
    for _ in range(9):
        wire(task)
    assert len(calls) == 10, len(calls)
    assert len(seen) == len(calls[0]), seen


def test_a7_notes_do_not_enter_the_evaluation_report():
    tmp = tempfile.mkdtemp(prefix="trvs-wiring-law-")
    try:
        report, _p = _report(_packed(tmp))
        blob = json.dumps(report)
        assert "notes" not in report["runtime_context"], report["runtime_context"]
        assert "note" not in blob.lower() or "notes" not in report, report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- A8/A9: the caller-supplied seam still exists, and cannot be mixed -------

def test_a8_genuinely_injected_verifiers_still_report_caller_supplied():
    """The fix must not collapse the two seams: a caller that really does bring
    its own implementations is still `caller_supplied`, and still attests no
    registry -- because there is none to attest."""
    tmp = tempfile.mkdtemp(prefix="trvs-wiring-law-")
    try:
        out = _packed(tmp)
        report = ES.eval_split(out, "all", AGENT + ["ok"],
                               extra_verifiers_for=lambda t: {})
        ctx = report["runtime_context"]
        assert ctx["wiring"] == "caller_supplied", ctx
        assert ctx["registry_version"] is None, ctx
        assert ctx["verifiers_available"] == [], ctx
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a9_supplying_both_seams_is_refused():
    tmp = tempfile.mkdtemp(prefix="trvs-wiring-law-")
    try:
        out = _packed(tmp)
        registry = wiring.default_registry(_engine_or_skip())
        try:
            ES.eval_split(out, "all", AGENT + ["ok"],
                          registry=registry, extra_verifiers_for=lambda t: {})
        except ES.SplitError as ex:
            assert ex.code == "VERIFIER_WIRING_AMBIGUOUS", ex.code
            return
        raise AssertionError("expected VERIFIER_WIRING_AMBIGUOUS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- A10: the sealed per-episode versions agree with the attested context ----

def test_a10_sealed_verifier_versions_agree_with_the_attested_context():
    """The receipt seals what ran; `runtime_context` attests what was available.

    They are different statements and must stay consistent: every version a
    receipt sealed *for a registry-supplied signal* has to be the version the
    attested runtime offered, or the report is describing a runtime that did not
    produce these episodes.

    Only the registry-supplied signals are in scope. A receipt also seals the
    substrate-independent verifiers (`citations`, `patch`,
    `finding_completeness`), which no registry supplies and which
    `verifiers_available` therefore does not list -- requiring those to appear in
    the runtime context would assert something false.
    """
    tmp = tempfile.mkdtemp(prefix="trvs-wiring-law-")
    try:
        package = _packed(tmp)
        keep = os.path.join(tmp, "episodes")
        argv = [sys.executable, "-m", "traaviis.cli", "eval", package,
                "--split", "all", "--json", "--output", keep,
                "--", *AGENT, "ok"]
        p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
        report = json.loads(p.stdout)
        ctx = report["runtime_context"]

        for entry in report["episodes"]:
            bundle = os.path.join(keep, entry["bundle"])
            with open(os.path.join(bundle, "receipt.json")) as fh:
                sealed = json.load(fh)["verifier_versions"]
            assert sealed, "the episode sealed no verifier versions at all"
            overlap = set(sealed) & set(ctx["verifiers_available"])
            assert overlap, (
                "no sealed signal came from the attested registry", sealed, ctx)
            for signal in sorted(overlap):
                # A receipt seals `{contract, implementation}` -- the contract
                # the signal answers and the build that answered it. The
                # registry reports the implementation, so that is the field the
                # two statements have in common.
                assert sealed[signal]["implementation"] == \
                    ctx["verifier_versions"][signal], (
                        signal, sealed[signal], ctx["verifier_versions"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
