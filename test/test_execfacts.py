"""Battery for canonical execution_facts.v1 (traaviis.execfacts, GPT-5.6 E1).

Pins the versioned schema, platform normalization, honest sandbox labels (R2),
termination/exit reporting, and the refusal to fabricate a sandbox label for an
unknown runner profile.

Runs with pytest, or standalone: `python3 test/test_execfacts.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import execfacts as X  # noqa: E402

PROFILE = "residency.trusted-local.v1"


def _run(**over):
    r = {"timed_out": False, "exit_code": 0,
         "stdout_truncated": False, "stderr_truncated": False}
    r.update(over)
    return r


def test_platform_from_mapping_normalized():
    p = X.normalize_platform({"os": "Darwin", "arch": "amd64"})
    assert p == {"os": "darwin", "arch": "x86_64"}


def test_platform_from_string_normalized():
    p = X.normalize_platform("linux-aarch64")
    assert p == {"os": "linux", "arch": "arm64"}


def test_platform_unknown_passes_through_lowercased():
    p = X.normalize_platform({"os": "Plan9", "arch": "Sparc"})
    assert p == {"os": "plan9", "arch": "sparc"}


def test_versioned_schema_and_honest_sandbox_labels():
    facts = X.build_execution_facts(
        _run(), runner_profile=PROFILE, platform="linux-x86_64")
    assert facts["execution_facts_version"] == X.EXECUTION_FACTS_VERSION
    assert facts["runner"] == {"profile": PROFILE}
    # R2: the trusted-local profile observes the FS and does not isolate network.
    assert facts["sandbox"] == {"filesystem": "observed", "network": "unrestricted"}


def test_termination_exited_vs_timed_out():
    exited = X.build_execution_facts(
        _run(exit_code=0), runner_profile=PROFILE, platform="linux-x86_64")
    assert exited["agent_process"]["termination"] == "exited"
    assert exited["agent_process"]["exit_code"] == 0
    timed = X.build_execution_facts(
        _run(timed_out=True, exit_code=None),
        runner_profile=PROFILE, platform="linux-x86_64")
    assert timed["agent_process"]["termination"] == "timed_out"
    assert timed["agent_process"]["exit_code"] is None


def test_truncation_flags_carried():
    facts = X.build_execution_facts(
        _run(stdout_truncated=True), runner_profile=PROFILE, platform="linux-x86_64")
    assert facts["agent_process"]["stdout_truncated"] is True
    assert facts["agent_process"]["stderr_truncated"] is False


def test_toolchain_shape_carried():
    tc = {"profile": "cpython-3.11",
          "resolved": {"python": {"version": "3.11.4"}}}
    facts = X.build_execution_facts(
        _run(), runner_profile=PROFILE, platform="linux-x86_64", toolchain=tc)
    assert facts["toolchain"]["profile"] == "cpython-3.11"
    assert facts["toolchain"]["resolved"]["python"]["version"] == "3.11.4"


def test_unknown_runner_profile_refuses_to_fabricate():
    try:
        X.build_execution_facts(
            _run(), runner_profile="residency.magic-sandbox.v9",
            platform="linux-x86_64")
    except KeyError:
        return
    raise AssertionError("expected KeyError for an unknown runner profile")


def _main():
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures.append((name, exc))
            print(f"FAIL  {name}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
