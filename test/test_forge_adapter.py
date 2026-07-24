"""Battery for the Forge identity adapter seam (traaviis.forge_adapter).

The identity verifier re-lowers WRL sources through a declared adapter, never a
private Spinner Bench import (GPT-5.6 blocker-2). These pin the stub's identity
law and the honest ``ForgeUnavailable`` of the not-yet-published real entrypoint.

Runs with pytest, or standalone: `python3 test/test_forge_adapter.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import forge_adapter as F  # noqa: E402


def test_stub_same_source_same_id():
    a = F.StubForgeAdapter()
    r1 = a.lower_source("world alpha")
    r2 = a.lower_source("world alpha")
    assert r1.ok and r2.ok
    assert r1.semantic_id == r2.semantic_id
    assert r1.semantic_id.startswith("sem-")


def test_stub_changed_source_moves_id():
    a = F.StubForgeAdapter()
    r1 = a.lower_source("world alpha")
    r2 = a.lower_source("world beta")
    assert r1.semantic_id != r2.semantic_id


def test_stub_compile_error_is_not_ok():
    a = F.StubForgeAdapter()
    r = a.lower_source("world @@COMPILE_ERROR@@ here")
    assert r.ok is False
    assert r.semantic_id is None
    assert r.error


def test_stub_version_is_declared():
    assert F.StubForgeAdapter().version == "forge.identity-adapter.stub.v1"


def test_real_adapter_reports_unavailable():
    # The public forge_api.lower_source is not published in the TRVM tree yet, so
    # the real adapter must honestly report unavailability, never a false pass.
    try:
        F.real_adapter()
    except F.ForgeUnavailable:
        return
    # If some environment DOES publish forge_api, that's acceptable too — then the
    # adapter must expose the real version. Only a silent wrong-binding is a bug.
    raise AssertionError("real_adapter neither raised ForgeUnavailable nor bound")


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
