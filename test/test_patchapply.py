"""Battery for the strict unified-diff applier (traaviis.patchapply).

Pure application laws — exact context, no fuzz. Pins clean modify/create/delete,
context-mismatch rejection, trailing-newline exactness, and multi-hunk order.

Runs with pytest, or standalone: `python3 test/test_patchapply.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import patchapply as P  # noqa: E402


def test_clean_modify_applies():
    files = {"a.txt": "one\ntwo\nthree\n"}
    diff = (
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " one\n"
        "-two\n"
        "+TWO\n"
        " three\n"
    )
    out = P.apply_unified_diff(files, diff)
    assert out["a.txt"] == "one\nTWO\nthree\n"
    assert files["a.txt"] == "one\ntwo\nthree\n"  # input untouched


def test_context_mismatch_raises():
    files = {"a.txt": "one\nDIFFERENT\nthree\n"}
    diff = (
        "--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
    )
    try:
        P.apply_unified_diff(files, diff)
    except P.PatchError:
        return
    raise AssertionError("expected PatchError on context mismatch")


def test_removed_line_mismatch_raises():
    files = {"a.txt": "one\ntwo\n"}
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,1 @@\n one\n-XXX\n"
    try:
        P.apply_unified_diff(files, diff)
    except P.PatchError:
        return
    raise AssertionError("expected PatchError on removed-line mismatch")


def test_create_new_file():
    diff = (
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1,2 @@\n"
        "+hello\n"
        "+world\n"
    )
    out = P.apply_unified_diff({}, diff)
    assert out["new.txt"] == "hello\nworld\n"


def test_create_over_existing_raises():
    diff = "--- /dev/null\n+++ b/x.txt\n@@ -0,0 +1,1 @@\n+hi\n"
    try:
        P.apply_unified_diff({"x.txt": "already\n"}, diff)
    except P.PatchError:
        return
    raise AssertionError("expected PatchError creating over existing file")


def test_delete_file():
    diff = "--- a/gone.txt\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-bye\n"
    out = P.apply_unified_diff({"gone.txt": "bye\n"}, diff)
    assert "gone.txt" not in out


def test_missing_target_raises():
    diff = "--- a/nope.txt\n+++ b/nope.txt\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    try:
        P.apply_unified_diff({}, diff)
    except P.PatchError:
        return
    raise AssertionError("expected PatchError for missing target")


def test_no_newline_at_eof_is_exact():
    files = {"a.txt": "line\n"}
    diff = (
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1,1 +1,1 @@\n"
        "-line\n"
        "+line\n"
        "\\ No newline at end of file\n"
    )
    out = P.apply_unified_diff(files, diff)
    assert out["a.txt"] == "line"  # trailing newline removed exactly


def test_multi_hunk_in_order():
    files = {"a.txt": "1\n2\n3\n4\n5\n6\n"}
    diff = (
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1,2 +1,2 @@\n 1\n-2\n+two\n"
        "@@ -5,2 +5,2 @@\n 5\n-6\n+six\n"
    )
    out = P.apply_unified_diff(files, diff)
    assert out["a.txt"] == "1\ntwo\n3\n4\n5\nsix\n"


def test_malformed_hunk_header_raises():
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ garbage @@\n a\n"
    try:
        P.apply_unified_diff({"a.txt": "a\n"}, diff)
    except P.PatchError:
        return
    raise AssertionError("expected PatchError for malformed hunk header")


# --- multi-file sections: the counts, not the leading character, end a hunk ---

def test_two_file_sections_apply_exactly():
    """The regression that motivated the fix.

    A section header is `--- a/...` / `+++ b/...`, which by first character
    alone is indistinguishable from a removal followed by an addition. A body
    loop driven by the leading character swallows the next file's header into
    the previous hunk; only the declared counts say where a hunk stopped.
    """
    files = {"src/mod.py": "return 1\n", "spec/contract.md": "one\ntwo\n"}
    diff = (
        "--- a/src/mod.py\n+++ b/src/mod.py\n@@ -1,1 +1,1 @@\n-return 1\n+return 2\n"
        "--- a/spec/contract.md\n+++ b/spec/contract.md\n"
        "@@ -1,2 +1,2 @@\n-one\n-two\n+1\n+2\n"
    )
    out = P.apply_unified_diff(files, diff)
    assert out["src/mod.py"] == "return 2\n", out
    assert out["spec/contract.md"] == "1\n2\n", out


def test_a_removal_line_that_looks_like_a_header_is_still_a_removal():
    """The converse: inside a hunk the counts still admit `--- a/x` as content.

    Deleting a line whose text happens to be a diff header must keep working,
    or the fix would have traded one ambiguity for another.
    """
    files = {"a.txt": "--- a/decoy\nkeep\n"}
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,1 @@\n---- a/decoy\n keep\n"
    out = P.apply_unified_diff(files, diff)
    assert out["a.txt"] == "keep\n", out


def test_body_longer_than_declared_counts_is_rejected():
    """Terminating on counts must not silently skip the surplus."""
    files = {"a.txt": "1\n2\n"}
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1,1 +1,1 @@\n-1\n+one\n+smuggled\n"
    try:
        P.apply_unified_diff(files, diff)
    except P.PatchError:
        return
    raise AssertionError("expected PatchError for surplus hunk body")


def test_duplicate_section_still_rejected_across_files():
    files = {"a.txt": "1\n", "b.txt": "1\n"}
    diff = (
        "--- a/a.txt\n+++ b/a.txt\n@@ -1,1 +1,1 @@\n-1\n+one\n"
        "--- a/a.txt\n+++ b/a.txt\n@@ -1,1 +1,1 @@\n-one\n+two\n"
    )
    try:
        P.apply_unified_diff(files, diff)
    except P.PatchError:
        return
    raise AssertionError("expected PatchError for duplicate file section")


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
