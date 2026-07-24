"""Battery for the safe relative-POSIX path validator (traaviis.paths).

Path admission is the single seam that stops an absolute or ``..`` path from
smuggling into a snapshot, a diff header, a test cwd, or an identity binding.
Every downstream module routes through here, so these laws are load-bearing.

Runs with pytest, or standalone: `python3 test/test_paths.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import paths as P  # noqa: E402


def test_plain_relative_path_passes():
    assert P.safe_relposix("src/mod.py") == "src/mod.py"


def test_backslashes_normalized_to_posix():
    assert P.safe_relposix("src\\mod.py") == "src/mod.py"


def test_dot_segments_collapsed():
    assert P.safe_relposix("src/./mod.py") == "src/mod.py"


def test_absolute_path_rejected():
    for bad in ("/etc/passwd", "/abs"):
        try:
            P.safe_relposix(bad)
        except P.PathError:
            continue
        raise AssertionError(f"expected PathError for {bad!r}")


def test_parent_traversal_rejected():
    for bad in ("../secret", "a/../../b", "..", "a/.."):
        try:
            P.safe_relposix(bad)
        except P.PathError:
            continue
        raise AssertionError(f"expected PathError for {bad!r}")


def test_windows_drive_rejected():
    try:
        P.safe_relposix("C:/x")
    except P.PathError:
        return
    raise AssertionError("expected PathError for a drive-letter path")


def test_empty_and_dot_rejected_unless_allowed():
    for bad in ("", "."):
        try:
            P.safe_relposix(bad)
        except P.PathError:
            continue
        raise AssertionError(f"expected PathError for {bad!r}")
    # allow_empty admits the current-dir cwd form
    assert P.safe_relposix(".", allow_empty=True) in (".", "")


def test_non_string_rejected():
    try:
        P.safe_relposix(123)
    except P.PathError:
        return
    raise AssertionError("expected PathError for a non-string path")


def test_is_safe_and_all_safe():
    assert P.is_safe_relposix("a/b") is True
    assert P.is_safe_relposix("../b") is False
    assert P.all_safe(["a", "b/c"]) is True
    assert P.all_safe(["a", "../c"]) is False


def test_safe_join_stays_under_root():
    root = "/tmp/root"
    assert P.safe_join(root, "a/b.txt") == os.path.join(root, "a/b.txt")
    try:
        P.safe_join(root, "../escape")
    except P.PathError:
        return
    raise AssertionError("expected PathError joining a traversal path")


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
