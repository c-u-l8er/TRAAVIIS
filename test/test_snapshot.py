"""Snapshot-builder battery for traaviis.snapshot (RFC Evidence Residency §4/§5a).

Read-only build laws over a real temp tree — no subprocess, no verifier. Pins:
LF normalization, byte-change sensitivity, exclusions, symlink exclusion (v1),
POSIX relative paths (never absolute / never ..), mode capture, and that
base_revision / visible_config enter identity.

Runs with pytest, or standalone: `python3 test/test_snapshot.py`.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import snapshot as S  # noqa: E402


def _write(root, relpath, data, mode=None):
    path = os.path.join(root, relpath.replace("/", os.sep))
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    if mode is not None:
        os.chmod(path, mode)


def _tree(files):
    d = tempfile.mkdtemp(prefix="traaviis-snap-")
    for rel, data in files.items():
        _write(d, rel, data)
    return d


# --------------------------------------------------------------------------- #

def test_line_ending_normalization_same_snap():
    a = _tree({"src/a.py": "x = 1\ny = 2\n", "README.md": "hi\n"})
    b = _tree({"src/a.py": "x = 1\r\ny = 2\r\n", "README.md": "hi\r"})
    sa = S.build_snapshot(a)
    sb = S.build_snapshot(b)
    assert sa["snapshot_id"] == sb["snapshot_id"]


def test_repository_byte_change_new_snap():
    a = _tree({"src/a.py": "x = 1\n"})
    b = _tree({"src/a.py": "x = 2\n"})
    assert S.build_snapshot(a)["snapshot_id"] != S.build_snapshot(b)["snapshot_id"]


def test_declared_binary_is_byte_exact():
    # CRLF vs LF in a *declared binary* path must move the snapshot (no LF fold).
    a = _tree({"blob.bin": b"\x00\x01\r\n\x02"})
    b = _tree({"blob.bin": b"\x00\x01\n\x02"})
    sa = S.build_snapshot(a, binary_paths=["blob.bin"])
    sb = S.build_snapshot(b, binary_paths=["blob.bin"])
    assert sa["snapshot_id"] != sb["snapshot_id"]


def test_exclusions_drop_files_and_change_nothing_else():
    root = _tree({"keep.py": "1\n", "junk/__pycache__/x.pyc": b"\x00"})
    incl = S.build_snapshot(root)
    excl = S.build_snapshot(root, exclusions=["**/__pycache__/**"])
    assert "junk/__pycache__/x.pyc" in incl["files"]
    assert "junk/__pycache__/x.pyc" not in excl["files"]
    assert "keep.py" in excl["files"]
    assert incl["snapshot_id"] != excl["snapshot_id"]


def test_symlinks_excluded_v1():
    root = _tree({"real.txt": "hello\n"})
    link = os.path.join(root, "link.txt")
    try:
        os.symlink(os.path.join(root, "real.txt"), link)
    except (OSError, NotImplementedError):
        return  # platform without symlink support; law is vacuously fine
    snap = S.build_snapshot(root)
    assert "real.txt" in snap["files"]
    assert "link.txt" not in snap["files"]


def test_paths_are_relative_posix_never_absolute_or_dotdot():
    root = _tree({"pkg/mod/a.py": "1\n", "top.py": "2\n"})
    snap = S.build_snapshot(root)
    for rel in snap["files"]:
        assert not os.path.isabs(rel)
        assert ".." not in rel.split("/")
        assert "\\" not in rel
    assert "pkg/mod/a.py" in snap["files"]


def test_file_modes_captured():
    root = _tree({"run.sh": "#!/bin/sh\n"})
    os.chmod(os.path.join(root, "run.sh"), 0o755)
    snap = S.build_snapshot(root)
    assert snap["file_modes"]["run.sh"] == "0755"


def test_content_hash_shape():
    root = _tree({"a.py": "1\n"})
    h = S.build_snapshot(root)["files"]["a.py"]
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64
    int(h[len("sha256:"):], 16)


def test_base_revision_and_visible_config_enter_identity():
    root = _tree({"a.py": "1\n"})
    base = S.build_snapshot(root)
    rev = S.build_snapshot(root, base_revision="abc123")
    cfg = S.build_snapshot(root, visible_config={"python": "3.11"})
    assert base["snapshot_id"] != rev["snapshot_id"]
    assert base["snapshot_id"] != cfg["snapshot_id"]


def test_walk_order_independent():
    files = {f"d{i}/f{i}.py": f"{i}\n" for i in range(6)}
    a = _tree(files)
    b = _tree(files)
    assert S.build_snapshot(a)["snapshot_id"] == S.build_snapshot(b)["snapshot_id"]


# --------------------------------------------------------------------------- #

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
