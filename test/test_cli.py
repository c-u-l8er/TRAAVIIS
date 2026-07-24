"""CLI contract tests for `trvs` -- output, JSON, and exit codes.

Runs the real CLI as a subprocess against `worlds/alley.wrl` over the actual
Forge engine (no mocks). If the engine cannot be located (no TRVM checkout / no
$TRVS_FORGE_DIR), every test SKIPS rather than failing -- these assert the CLI
contract, not the presence of the engine on a given machine.

Run directly:      python3 test/test_cli.py
Run under pytest:  pytest test/test_cli.py
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORLD = os.path.join(REPO, "worlds", "alley.wrl")


class Skip(Exception):
    pass


def run(*args, world=WORLD):
    argv = [sys.executable, "-m", "traaviis.cli", *args]
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _engine_available():
    code, out, err = run("doctor")
    if code == 2 and "could not locate" in err:
        return False
    return True


def _require_engine():
    if not _engine_available():
        raise Skip("Forge engine not locatable; set TRVS_FORGE_DIR")


def test_doctor_ok():
    _require_engine()
    code, out, _ = run("doctor")
    assert code == 0, out
    assert "engine API" in out and "status" in out


def test_id_matches_across_commands():
    _require_engine()
    _, id_out, _ = run("id", WORLD)
    sem = id_out.strip().splitlines()[0]
    assert sem.startswith("sem-")
    _, run_out, _ = run("run", WORLD)
    _, ver_out, _ = run("verify", WORLD)
    assert sem in run_out and sem in ver_out


def test_run_epochs():
    _require_engine()
    code, out, _ = run("run", WORLD)
    assert code == 0
    assert "epochs" in out and "film" in out


def test_verify_strict_3of3():
    _require_engine()
    code, out, _ = run("verify", WORLD)
    assert code == 0, out
    assert "agreement    3/3" in out.replace("\t", " ") or "3/3" in out


def test_verify_no_oracle_2of2():
    _require_engine()
    code, out, _ = run("verify", WORLD, "--no-oracle")
    assert code == 0
    assert "2/2" in out


def test_verify_reference_only_1of1():
    _require_engine()
    code, out, _ = run("verify", WORLD, "--reference-only")
    assert code == 0
    assert "1/1" in out


def test_inspect_default_and_selectors():
    _require_engine()
    code, out, _ = run("inspect", WORLD)
    assert code == 0 and "actors" in out and "graph" in out
    code, g, _ = run("inspect", WORLD, "--graph")
    assert code == 0 and "-->" in g and "actors" not in g.split("graph", 1)[0]


def test_replay_prints_strip_and_reproduces():
    _require_engine()
    _, id_out, _ = run("id", WORLD)
    sem = id_out.strip().splitlines()[0]
    code, out, _ = run("replay", WORLD)
    assert code == 0 and "epoch" in out
    assert "final film" in out
    # pinning the real id reproduces (exit 0, marks yes)
    code, out, _ = run("replay", WORLD, "--expect", sem)
    assert code == 0 and "reproduced" in out
    # a bogus id fails the replay contract (exit 1)
    code, out, _ = run("replay", WORLD, "--expect", "sem-deadbeef")
    assert code == 1, out


def test_diff_identical_and_divergent(tmp_variant="test/_variant.wrl"):
    _require_engine()
    code, out, _ = run("diff", WORLD, WORLD)
    assert code == 0 and "identical" in out
    src = open(WORLD).read().replace("n=8", "n=4")
    path = os.path.join(REPO, tmp_variant)
    with open(path, "w") as fh:
        fh.write(src)
    try:
        code, out, _ = run("diff", WORLD, path)
        assert code == 1, out
        assert "first divergence" in out
    finally:
        os.remove(path)


def test_json_payloads_parse():
    _require_engine()
    for args in (("id", WORLD, "--json"), ("run", WORLD, "--json"),
                 ("verify", WORLD, "--json"), ("inspect", WORLD, "--json"),
                 ("replay", WORLD, "--json"), ("doctor", "--json")):
        code, out, _ = run(*args)
        assert code == 0, args
        json.loads(out)  # must be valid JSON


def test_bad_world_exits_2():
    _require_engine()
    bad = os.path.join(REPO, "test", "_broken.wrl")
    with open(bad, "w") as fh:
        fh.write("profile forge.world.core.v1\n[spinner:sp](rotor=nope){sig_in}\n")
    try:
        code, _, err = run("verify", bad)
        assert code == 2, code
        assert "WRL_" in err
        code, _, _ = run("id", bad)
        assert code == 2
    finally:
        os.remove(bad)


def test_missing_file_exits_2():
    _require_engine()
    code, _, err = run("id", os.path.join(REPO, "worlds", "does_not_exist.wrl"))
    assert code == 2 and "no such world" in err


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
