"""Battery for the pure Residency verifiers (traaviis.verifiers, RFC §6/§7).

Pure state laws over in-memory snapshot content — no subprocess, no runner.
Covers citations (resolve + exact quote), patch (clean apply), and
finding_completeness (structural evidence). Malformed output -> fail, never error.

Runs with pytest, or standalone: `python3 test/test_verifiers.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import verifiers as V  # noqa: E402
from traaviis import reward as R  # noqa: E402


CONTENT = {
    "spec/WRL_CORE_0.1.md": "line one\nline two\nline three\nline four\n",
    "src/mod.py": "def f():\n    return 1\n",
}

GOOD_FINDING = {
    "finding_version": "residency.finding.v1",
    "claims": [{
        "statement": "Spec line two contradicts impl.",
        "citations": [{
            "path": "spec/WRL_CORE_0.1.md",
            "start_line": 2,
            "end_line": 3,
            "quote": "line two\nline three",
        }],
    }],
}


# --- citations ------------------------------------------------------------- #

def test_citations_pass_on_exact_span():
    assert V.verify_citations(GOOD_FINDING, CONTENT) == R.PASS


def test_citations_fail_on_quote_mismatch():
    f = {"claims": [{"statement": "x", "citations": [{
        "path": "spec/WRL_CORE_0.1.md", "start_line": 2, "end_line": 2,
        "quote": "line TWO"}]}]}
    assert V.verify_citations(f, CONTENT) == R.FAIL


def test_citations_fail_on_unknown_path():
    f = {"claims": [{"statement": "x", "citations": [{
        "path": "missing.md", "start_line": 1, "end_line": 1, "quote": "z"}]}]}
    assert V.verify_citations(f, CONTENT) == R.FAIL


def test_citations_fail_on_out_of_range_span():
    f = {"claims": [{"statement": "x", "citations": [{
        "path": "spec/WRL_CORE_0.1.md", "start_line": 3, "end_line": 99,
        "quote": "whatever"}]}]}
    assert V.verify_citations(f, CONTENT) == R.FAIL


def test_citations_fail_on_no_claims():
    assert V.verify_citations({"claims": []}, CONTENT) == R.FAIL


def test_citations_single_line_span():
    f = {"claims": [{"statement": "x", "citations": [{
        "path": "src/mod.py", "start_line": 2, "end_line": 2,
        "quote": "    return 1"}]}]}
    assert V.verify_citations(f, CONTENT) == R.PASS


# --- patch ----------------------------------------------------------------- #

def test_patch_pass_on_clean_apply():
    patch = {"diff": (
        "--- a/src/mod.py\n+++ b/src/mod.py\n"
        "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    )}
    assert V.verify_patch(patch, CONTENT) == R.PASS


def test_patch_fail_on_context_mismatch():
    patch = {"diff": (
        "--- a/src/mod.py\n+++ b/src/mod.py\n"
        "@@ -1,2 +1,2 @@\n def g():\n-    return 1\n+    return 2\n"
    )}
    assert V.verify_patch(patch, CONTENT) == R.FAIL


def test_patch_fail_on_missing_diff():
    assert V.verify_patch({}, CONTENT) == R.FAIL
    assert V.verify_patch({"diff": "   "}, CONTENT) == R.FAIL


def test_patch_does_not_mutate_content():
    patch = {"diff": (
        "--- a/src/mod.py\n+++ b/src/mod.py\n"
        "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    )}
    V.verify_patch(patch, CONTENT)
    assert CONTENT["src/mod.py"] == "def f():\n    return 1\n"


# --- finding_completeness -------------------------------------------------- #

def test_completeness_pass():
    assert V.verify_finding_completeness(GOOD_FINDING) == R.PASS


def test_completeness_fail_on_empty_statement():
    f = {"claims": [{"statement": "  ", "citations": GOOD_FINDING["claims"][0]["citations"]}]}
    assert V.verify_finding_completeness(f) == R.FAIL


def test_completeness_fail_on_no_citations():
    f = {"claims": [{"statement": "real claim", "citations": []}]}
    assert V.verify_finding_completeness(f) == R.FAIL


def test_completeness_fail_on_no_claims():
    assert V.verify_finding_completeness({"claims": []}) == R.FAIL


def test_completeness_is_structural_not_prose():
    # A terse but structurally complete finding passes; prose quality is not judged.
    f = {"claims": [{"statement": "x", "citations": [{
        "path": "src/mod.py", "start_line": 1, "end_line": 1, "quote": "def f():"}]}]}
    assert V.verify_finding_completeness(f) == R.PASS


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
