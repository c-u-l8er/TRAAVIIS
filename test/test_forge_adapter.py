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


def test_real_adapter_binds_or_reports_unavailable():
    # forge_api.lower_source is now published (LowerResultV1). Where a compatible
    # engine is reachable the real adapter BINDS and must expose the versioned impl
    # id + honest data outcomes; where no engine is present (e.g. a bare checkout)
    # it must raise ForgeUnavailable, never a false pass. Both are correct — only a
    # silent wrong-binding is a bug.
    try:
        a = F.real_adapter()
    except F.ForgeUnavailable:
        return
    assert a.version.startswith("forge.identity.v1@trvm-")
    # Ordinary invalid WRL is a data outcome (ok=False), NOT an exception.
    bad = a.lower_source("profile forge.world.core.v1\n[bogus:x]{}\n")
    assert bad.ok is False
    assert bad.semantic_id is None
    assert bad.error


def test_real_adapter_unavailable_is_forge_unavailable(monkeypatch=None):
    # Deterministic unavailable path (independent of whether THIS environment has an
    # engine): with the soft loader unable to find a compatible engine, real_adapter
    # raises ForgeUnavailable so the identity verifier reports `error` (never a false
    # pass/fail) — the F4 "Forge unavailable → invalid config before agent run" law.
    from traaviis import engine

    saved_engine = engine._ENGINE
    saved_search = engine._search_candidates
    saved_override = os.environ.get("TRVS_FORGE_DIR")
    try:
        engine._ENGINE = None
        engine._search_candidates = lambda: []
        os.environ.pop("TRVS_FORGE_DIR", None)
        try:
            F.real_adapter()
        except F.ForgeUnavailable:
            pass
        else:
            raise AssertionError("expected ForgeUnavailable when no engine is found")
    finally:
        engine._ENGINE = saved_engine
        engine._search_candidates = saved_search
        if saved_override is not None:
            os.environ["TRVS_FORGE_DIR"] = saved_override


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
