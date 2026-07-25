"""Mutation laws for `trvs init` scaffolding (RFC Artifacts §6).

Written against `traaviis.scaffold` *first*, exactly as the identity spine was:
the laws below are what `init` is allowed to do, and the CLI is a thin wrapper
over them. The pure laws (L1-L4, L7) never touch the filesystem; L5 exercises
the atomic writer; L6 needs the Forge engine and SKIPS without it.

Run directly:      python3 test/test_scaffold.py
Run under pytest:  pytest test/test_scaffold.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import scaffold as S  # noqa: E402


class Skip(Exception):
    pass


def run(*args):
    argv = [sys.executable, "-m", "traaviis.cli", *args]
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _engine_available():
    code, _, err = run("doctor")
    return not (code == 2 and "could not locate" in err)


# --- L1: invents no identity -------------------------------------------------

def test_l1_no_template_invents_identity():
    for name in S.TEMPLATES:
        files = S.scaffold(name)
        bad = S.identity_violations(files)
        assert bad == [], "%s asserts identity it never computed: %r" % (name, bad)


def test_l1_detector_actually_catches_a_planted_id():
    # The law is only worth stating if its detector fires. Plant both forms.
    planted = {
        "env.json": json.dumps({"env_id": "x"}).encode(),
        "notes.md": b"see snap-c66198abf3ef6153d4bd6033fa40a0bd0028df3a76\n",
    }
    reasons = [r for _, r in S.identity_violations(planted)]
    assert any("identity key" in r for r in reasons), reasons
    assert any("id literal" in r for r in reasons), reasons


def test_l1_task_skeleton_references_specs_not_hashes():
    files = S.scaffold("evidence-residency")
    task = json.loads(files["task.json"])
    # It points at the reward and subject by FILE, because the hashes do not
    # exist yet -- `pack` recomputes and binds them.
    assert task["reward_spec"] == "reward.json"
    assert "reward_id" not in task
    assert task["subject"] == {"snapshot_def": "snapshot_def.json"}
    assert "snapshot_id" not in task["subject"]


# --- L2: deterministic -------------------------------------------------------

def test_l2_scaffold_is_byte_identical_across_calls():
    for name in S.TEMPLATES:
        a, b = S.scaffold(name), S.scaffold(name)
        assert a == b, name
        assert sorted(a) == sorted(b), name


def test_l2_materialize_twice_yields_identical_trees():
    for name in S.TEMPLATES:
        tmp = tempfile.mkdtemp(prefix="trvs-init-law-")
        try:
            one, two = os.path.join(tmp, "one"), os.path.join(tmp, "two")
            S.materialize(name, one)
            S.materialize(name, two)
            assert _tree(one) == _tree(two), name
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _tree(root):
    out = {}
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            p = os.path.join(dirpath, n)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root).replace(os.sep, "/")] = fh.read()
    return out


# --- L3: substrate-distinct --------------------------------------------------

def test_l3_templates_scaffold_genuinely_different_subjects():
    a = S.scaffold("golden-spinner")
    b = S.scaffold("evidence-residency")
    pa = json.loads(a["env.json"])["substrate_profile"]
    pb = json.loads(b["env.json"])["substrate_profile"]
    assert pa == "trvm.world.v1" and pb == "residency.repository.v1"
    assert json.loads(a["env.json"])["subject"]["kind"] == "wrl_source"
    assert json.loads(b["env.json"])["subject"]["kind"] == "repository_snapshot"
    # Not a renamed copy of one skeleton: the file sets differ structurally.
    shared = set(a) & set(b)
    assert shared == {"README.md", "env.json"}, shared
    assert a["README.md"] != b["README.md"]


def test_l3_unknown_template_is_refused():
    try:
        S.scaffold("no-such-template")
    except S.ScaffoldError as ex:
        assert "unknown template" in str(ex)
    else:
        raise AssertionError("unknown template was accepted")


# --- L4: every document declares its versions --------------------------------

_VERSION_KEY = {
    "env.json": ("environment_version", S.ENV_MANIFEST_VERSION),
    "snapshot_def.json": ("snapshot_def_version", S.SNAPSHOT_DEF_VERSION),
    "task.json": ("task_spec_version", S.TASK_SPEC_VERSION),
    "reward.json": ("reward_spec_version", S.REWARD_SPEC_VERSION),
}


def test_l4_documents_declare_frozen_versions_and_profile():
    for name, meta in S.TEMPLATES.items():
        files = S.scaffold(name)
        for path, data in files.items():
            if not path.endswith(".json"):
                continue
            doc = json.loads(data)
            key, expected = _VERSION_KEY[path]
            assert doc.get(key) == expected, (name, path, doc.get(key))
            assert doc.get("substrate_profile") == meta["substrate_profile"], \
                (name, path)


# --- L5: fail-closed + atomic ------------------------------------------------

def test_l5_refuses_non_empty_destination_and_writes_nothing():
    tmp = tempfile.mkdtemp(prefix="trvs-init-law-")
    try:
        dest = os.path.join(tmp, "env")
        os.makedirs(dest)
        with open(os.path.join(dest, "keep.txt"), "w") as fh:
            fh.write("mine\n")
        before = _tree(dest)
        try:
            S.materialize("golden-spinner", dest)
        except S.ScaffoldError as ex:
            assert "non-empty" in str(ex)
        else:
            raise AssertionError("scaffolded over a non-empty directory")
        assert _tree(dest) == before, "a refused init still mutated the destination"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_l5_failed_write_leaves_no_partial_tree():
    tmp = tempfile.mkdtemp(prefix="trvs-init-law-")
    try:
        dest = os.path.join(tmp, "env")
        real = S.scaffold

        def exploding(template):
            files = real(template)
            # Corrupt one entry so the writer fails midway through the tree.
            return {**files, "subject/boom": None}

        S.scaffold = exploding
        try:
            S.materialize("evidence-residency", dest)
        except Exception:
            pass
        else:
            raise AssertionError("expected the write to fail")
        finally:
            S.scaffold = real
        assert not os.path.exists(dest), "a failed init left a partial tree"
        leftovers = [n for n in os.listdir(tmp) if n.startswith(".trvs-init-")]
        assert leftovers == [], leftovers
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_l5_accepts_an_existing_empty_directory():
    tmp = tempfile.mkdtemp(prefix="trvs-init-law-")
    try:
        dest = os.path.join(tmp, "env")
        os.makedirs(dest)
        S.materialize("golden-spinner", dest)
        assert os.path.isfile(os.path.join(dest, "world.wrl"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- L6: the seeded subject is admissible input ------------------------------

def test_l6_residency_snapshot_definition_matches_the_seeded_tree():
    files = S.scaffold("evidence-residency")
    definition = json.loads(files["snapshot_def.json"])
    root = definition["root"]
    declared = set(definition["include"])
    seeded = {p[len(root) + 1:] for p in files if p.startswith(root + "/")}
    assert declared == seeded, (declared ^ seeded)
    assert set(definition["file_modes"]) == declared


def test_l6_seeded_world_lowers_to_a_real_identity():
    if not _engine_available():
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")
    tmp = tempfile.mkdtemp(prefix="trvs-init-law-")
    try:
        dest = os.path.join(tmp, "env")
        S.materialize("golden-spinner", dest)
        code, out, err = run("id", os.path.join(dest, "world.wrl"))
        assert code == 0, err
        assert "sem-" in out, out
        # The seeded frozen world of the other template must lower too: the
        # identity policy pins it, so a bogus seed would make the task unrunnable.
        res = os.path.join(tmp, "res")
        S.materialize("evidence-residency", res)
        code, out, err = run("id", os.path.join(res, "subject", "world", "frozen.wrl"))
        assert code == 0, err
        assert "sem-" in out, out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- L7: portable ------------------------------------------------------------

def test_l7_no_absolute_or_host_paths_in_the_bytes():
    home = os.path.expanduser("~")
    for name in S.TEMPLATES:
        for path, data in S.scaffold(name).items():
            text = data.decode("utf-8")
            assert home not in text, (name, path)
            assert REPO not in text, (name, path)
            for line in text.splitlines():
                assert " /home/" not in line and " C:\\" not in line, (name, path, line)


# --- CLI contract ------------------------------------------------------------

def test_cli_init_writes_the_tree_and_lists_templates():
    tmp = tempfile.mkdtemp(prefix="trvs-init-law-")
    try:
        dest = os.path.join(tmp, "env")
        code, out, err = run("init", "--template", "evidence-residency", dest)
        assert code == 0, err
        assert "env.json" in out and "task.json" in out, out
        assert os.path.isfile(os.path.join(dest, "subject", "src", "mod.py"))

        code, out, _ = run("init", "--list")
        assert code == 0
        assert "golden-spinner" in out and "evidence-residency" in out

        # Refusing to overwrite is exit 2, and says why.
        code, _, err = run("init", "--template", "golden-spinner", dest)
        assert code == 2 and "non-empty" in err, err

        code, _, err = run("init", "--template", "nope", os.path.join(tmp, "x"))
        assert code == 2 and "unknown template" in err, err
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_init_json_is_machine_readable():
    tmp = tempfile.mkdtemp(prefix="trvs-init-law-")
    try:
        dest = os.path.join(tmp, "env")
        code, out, err = run("init", "--template", "golden-spinner", dest, "--json")
        assert code == 0, err
        payload = json.loads(out)
        assert payload["template"] == "golden-spinner"
        assert payload["substrate_profile"] == "trvm.world.v1"
        assert "world.wrl" in payload["files"]
        assert payload["identity"] == "unresolved"
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
