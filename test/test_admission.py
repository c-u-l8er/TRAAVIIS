"""Battery for preflight admission (traaviis.admission).

Closes the two integrity gaps GPT-5.6 flagged: a self-asserted ``*_id`` is never
trusted (recompute + reconcile), and the working ``content`` must bind exactly to
the sealed ``snap-…`` before any agent runs. A mismatch is rejected here, never
laundered into an ``episode-…`` receipt.

Runs with pytest, or standalone: `python3 test/test_admission.py`.
"""

import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import admission as A  # noqa: E402
from traaviis import identity as I  # noqa: E402

CONTENT = {"spec/one.md": "alpha\nbeta\n", "src/mod.py": "return 1\n"}


def _write_tree(root, files):
    for rel, data in files.items():
        p = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p) or root, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)


def _content_hash(text):
    data = text.encode("utf-8").replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _snapshot(files=None):
    snap = {"snapshot_version": "residency.snapshot.v1",
            "files": files if files is not None
            else {k: _content_hash(v) for k, v in CONTENT.items()},
            "exclusions": [], "binary_paths": [], "file_modes": {},
            "base_revision": None, "visible_config": {}}
    snap["snapshot_id"] = I.snapshot_id(snap)
    return snap


# --- declared-id verification ------------------------------------------------

def test_absent_declared_id_is_computed():
    obj = {"a": 1}
    got = A.verify_declared_id(obj, "reward_id", I.reward_id)
    assert got == I.reward_id(obj)


def test_correct_declared_id_accepted():
    obj = {"a": 1}
    obj["reward_id"] = I.reward_id(obj)
    got = A.verify_declared_id(obj, "reward_id", I.reward_id)
    assert got == obj["reward_id"]


def test_false_declared_id_rejected():
    obj = {"a": 1, "reward_id": "rew-deadbeef"}
    try:
        A.verify_declared_id(obj, "reward_id", I.reward_id)
    except A.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a false declared id")


# --- snapshot <-> content binding --------------------------------------------

def test_exact_content_binds():
    A.verify_materialization(_snapshot(), CONTENT)  # returns None, no raise


def test_content_hash_mismatch_rejected():
    tampered = dict(CONTENT, **{"src/mod.py": "return 999\n"})
    try:
        A.verify_materialization(_snapshot(), tampered)
    except A.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a content-hash mismatch")


def test_missing_path_rejected():
    short = {"spec/one.md": CONTENT["spec/one.md"]}
    try:
        A.verify_materialization(_snapshot(), short)
    except A.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a missing sealed path")


def test_extra_path_rejected():
    extra = dict(CONTENT, **{"src/new.py": "x\n"})
    try:
        A.verify_materialization(_snapshot(), extra)
    except A.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a path not in the snapshot")


def test_unsafe_snapshot_path_rejected():
    snap = _snapshot(files={"../secret": "sha256:x"})
    try:
        A.verify_materialization(snap, {"../secret": "x\n"})
    except A.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a '..' snapshot path")


def test_binary_path_hashed_byte_exact():
    # A CRLF file declared binary must match byte-exact, not LF-normalized.
    raw = "a\r\nb\r\n"
    digest = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    snap = _snapshot(files={"bin.dat": digest})
    A.verify_materialization(snap, {"bin.dat": raw}, binary_paths=["bin.dat"])
    # Without the binary declaration the same bytes LF-normalize and mismatch.
    try:
        A.verify_materialization(snap, {"bin.dat": raw})
    except A.AdmissionError:
        return
    raise AssertionError("expected AdmissionError: LF-normalized != byte-exact hash")


# --- verify_subject_tree canonical-LF admission ------------------------------

def test_crlf_and_lf_subject_admit_identical_canonical_content():
    # The same snap-… seals a CRLF subject and an LF subject (snapshot hashing
    # normalizes to LF). The admitted materialization must be the CANONICAL LF form
    # for BOTH — otherwise the same snap-… could hand the agent different bytes and
    # yield a different trace. verify_subject_tree must return byte-identical LF
    # content from either tree.
    snap = _snapshot()  # sealed over LF CONTENT
    lf_dir = tempfile.mkdtemp(prefix="traaviis-lf-")
    crlf_dir = tempfile.mkdtemp(prefix="traaviis-crlf-")
    try:
        _write_tree(lf_dir, {k: v.encode("utf-8") for k, v in CONTENT.items()})
        _write_tree(crlf_dir, {k: v.replace("\n", "\r\n").encode("utf-8")
                               for k, v in CONTENT.items()})
        lf_content = A.verify_subject_tree(snap, lf_dir)
        crlf_content = A.verify_subject_tree(snap, crlf_dir)
        assert lf_content == crlf_content
        assert all("\r" not in v for v in crlf_content.values())
        assert crlf_content == CONTENT  # exactly the canonical LF subject
    finally:
        shutil.rmtree(lf_dir, ignore_errors=True)
        shutil.rmtree(crlf_dir, ignore_errors=True)


# --- admit_subject (id + binding together) -----------------------------------

def test_admit_subject_returns_snap_id():
    snap = _snapshot()
    got = A.admit_subject(snap, CONTENT)
    assert got == snap["snapshot_id"]


def test_admit_rejects_false_snapshot_id():
    snap = _snapshot()
    snap["snapshot_id"] = "snap-fixture"  # a lie
    try:
        A.admit_subject(snap, CONTENT)
    except A.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a false snapshot_id")


# --- cross-bind: task references the *verified* reward + snapshot ------------

def test_cross_bind_accepts_matching_references():
    task = {"reward_id": "rew-abc", "subject": {"snapshot_id": "snap-xyz"}}
    A.cross_bind_task(task, "rew-abc", "snap-xyz")  # returns None, no raise


def test_cross_bind_rejects_wrong_reward_reference():
    # A task pointing at a *different* (even if internally valid) reward id.
    task = {"reward_id": "rew-000", "subject": {"snapshot_id": "snap-xyz"}}
    try:
        A.cross_bind_task(task, "rew-abc", "snap-xyz")
    except A.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a mis-referenced reward_id")


def test_cross_bind_rejects_wrong_snapshot_reference():
    task = {"reward_id": "rew-abc", "subject": {"snapshot_id": "snap-000"}}
    try:
        A.cross_bind_task(task, "rew-abc", "snap-xyz")
    except A.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for a mis-referenced snapshot_id")


def test_cross_bind_rejects_missing_subject():
    task = {"reward_id": "rew-abc"}
    try:
        A.cross_bind_task(task, "rew-abc", "snap-xyz")
    except A.AdmissionError:
        return
    raise AssertionError("expected AdmissionError for an absent subject reference")


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
