"""Battery for the frozen verifier interface (traaviis.vcontext).

Every verifier — pure or substrate — is called as ``verifier(context) ->
VerifierResult``. These pin the immutable context shape and the result vocab.

Runs with pytest, or standalone: `python3 test/test_vcontext.py`.
"""

import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis.vcontext import VerifierContextV1, VerifierResult  # noqa: E402


def _ctx(**over):
    base = dict(task={}, snapshot={}, original_content={}, run={})
    base.update(over)
    return VerifierContextV1(**base)


def test_context_defaults_are_none():
    c = _ctx()
    assert c.finding is None and c.patch is None and c.patched_content is None


def test_context_is_frozen():
    c = _ctx()
    try:
        c.finding = {"x": 1}  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("expected VerifierContextV1 to be immutable")


def test_context_carries_optional_outputs():
    c = _ctx(finding={"f": 1}, patch={"p": 1}, patched_content={"a": "b"})
    assert c.finding == {"f": 1}
    assert c.patch == {"p": 1}
    assert c.patched_content == {"a": "b"}


def test_result_str_is_the_state():
    r = VerifierResult("pass")
    assert str(r) == "pass"
    assert r.state == "pass"
    assert r.detail == {}


def test_result_carries_detail():
    r = VerifierResult("fail", {"reason": "no patched tree"})
    assert r.state == "fail"
    assert r.detail["reason"] == "no patched tree"


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
