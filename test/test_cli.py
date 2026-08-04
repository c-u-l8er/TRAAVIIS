"""CLI contract tests for `trvs` -- output, JSON, and exit codes.

Runs the real CLI as a subprocess against `worlds/alley.wrl` over the actual
Forge engine (no mocks). If the engine cannot be located (no TRVM checkout / no
$TRVS_FORGE_DIR), every test SKIPS rather than failing -- these assert the CLI
contract, not the presence of the engine on a given machine.

This file is slow on purpose and that is not a hang: `trvs verify` replays the
world three ways and takes ~45s, and the whole file runs for about eight minutes
with the engine present. Both runners take that long -- there is no runner in
which it is fast.

Fixtures go in a fresh `tempfile.mkdtemp`, never in the repository. Two of these
tests used to write fixed paths under `test/`, which is fine for one run and
wrong for two: two sessions running this file at once race on the same name and
one `finally: os.remove` deletes the other's fixture mid-test. That produces a
red battery that says nothing about the code.

Run directly:      python3 test/test_cli.py
Run under pytest:  pytest test/test_cli.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORLD = os.path.join(REPO, "worlds", "alley.wrl")

# Ceiling on a single `trvs` invocation. These are slow on purpose -- `verify`
# replays the world three ways and takes ~45s on an idle machine, which is why
# the whole file runs for about eight minutes -- so this is generous. It is not
# here to bound slowness, it is here so that a command which never returns is a
# failed test rather than a wedged battery: without it `subprocess.run` waits
# forever, and the runner above it waits forever too.
CLI_TIMEOUT = float(os.environ.get("TRVS_TEST_CLI_TIMEOUT", "300"))


class Skip(Exception):
    pass


def run(*args, world=WORLD, timeout=CLI_TIMEOUT):
    argv = [sys.executable, "-m", "traaviis.cli", *args]
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def _world_id(world=WORLD):
    """The semantic id `trvs id` prints for `world`.

    The failure modes are stated as assertions rather than left to raise. Reading
    the id as `out.strip().splitlines()[0]` raises IndexError the moment the CLI
    prints nothing -- which it does whenever the engine is present but unusable,
    since `_engine_available()` below only skips on an engine it cannot *locate*.
    An IndexError is not a failed assertion, so it used to take the entire file
    down at the fourth test and the remaining eight never ran.
    """
    code, out, err = run("id", world)
    assert code == 0, "trvs id exited %d: %s" % (code, err.strip() or out.strip())
    lines = out.strip().splitlines()
    assert lines, "trvs id printed no id (stderr: %r)" % err
    return lines[0]


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
    sem = _world_id()
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
    sem = _world_id()
    code, out, _ = run("replay", WORLD)
    assert code == 0 and "epoch" in out
    assert "final film" in out
    # pinning the real id reproduces (exit 0, marks yes)
    code, out, _ = run("replay", WORLD, "--expect", sem)
    assert code == 0 and "reproduced" in out
    # a bogus id fails the replay contract (exit 1)
    code, out, _ = run("replay", WORLD, "--expect", "sem-deadbeef")
    assert code == 1, out


def test_diff_identical_and_divergent():
    _require_engine()
    code, out, _ = run("diff", WORLD, WORLD)
    assert code == 0 and "identical" in out
    src = open(WORLD).read().replace("n=8", "n=4")
    tmp = tempfile.mkdtemp(prefix="trvs-cli-")
    path = os.path.join(tmp, "variant.wrl")
    with open(path, "w") as fh:
        fh.write(src)
    try:
        code, out, _ = run("diff", WORLD, path)
        assert code == 1, out
        assert "first divergence" in out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    tmp = tempfile.mkdtemp(prefix="trvs-cli-")
    bad = os.path.join(tmp, "broken.wrl")
    with open(bad, "w") as fh:
        fh.write("profile forge.world.core.v1\n[spinner:sp](rotor=nope){sig_in}\n")
    try:
        code, _, err = run("verify", bad)
        assert code == 2, code
        assert "WRL_" in err
        code, _, _ = run("id", bad)
        assert code == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
        except Exception as e:  # noqa: BLE001 -- deliberate, see below
            # Anything that is not an assertion: an IndexError on empty CLI
            # output, a TimeoutExpired from `run`, an OSError writing a fixture.
            # These used to propagate out of main() and kill the process, so
            # every test after the first one lost never ran and the battery's
            # total for this file was short rather than red -- a quieter, worse
            # failure than a red one. Record it as a failure of *this* test and
            # let the rest of the file run. BaseException (KeyboardInterrupt,
            # SystemExit) is deliberately still allowed through.
            failed += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
    print("\n%d passed, %d skipped, %d failed" % (passed, skipped, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
