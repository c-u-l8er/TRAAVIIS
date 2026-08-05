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
import re
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


# --- L1a-L1g: the detector fails CLOSED, and says where it does not ----------
#
# A guard that returns nothing on an input it does not understand reports
# "clean" for "unreadable". But a guard that reports *everything* is switched
# off by the next person to trip it, which is fail-open by a slower route. These
# seven laws pin both edges and the line between them:
#
#   L1a  an unparseable rung-prefixed token IS a violation
#   L1b  ordinary hyphenated prose is NOT
#   L1c  the rung list cannot drift away from what `identity.py` mints
#   L1d  a digest under an invented rung IS a violation
#   L1e  the prose exemption of L1b is bounded
#   L1f  a well-formed id is reported wherever it sits, including inside a
#        longer hyphenated or dotted name
#   L1g  what the guard deliberately does not report, and what bounds it
#
# Every one of them is measured against the two guards this one replaced, both
# reproduced below. A law that only shows the current implementation agreeing
# with itself proves nothing about what the implementation bought.

#: The guard that predates the fail-closed rewrite: a rung, a hyphen, lowercase
#: hex. It matches a well-formed digest and *nothing else*, so every unparseable
#: token was silence. `\b` lets it start after a `-` or a `.`.
_LEGACY_ID_LITERAL = re.compile(
    r"\b(?:env|bundle|snap|task|rew|episode|trace|patch|finding|sem|scen|replay)"
    r"-[0-9a-f]{8,}\b")

#: The first fail-closed rewrite. It fixed the silence and introduced two
#: defects of its own: a lookbehind that refused to start a token after `-` or
#: `.` (so it lost ids the legacy regex caught), and a `malformed` verdict with
#: no prose exemption (so it flagged `env-vars`). Kept here so the laws can
#: measure against it rather than assert improvement.
_LOOKBEHIND_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_]*)-([0-9A-Za-z][0-9A-Za-z_-]*)")


def _lookbehind_tokens(text):
    out = []
    for m in _LOOKBEHIND_TOKEN.finditer(text):
        prefix, remainder = m.group(1), m.group(2)
        digest = re.match(r"[0-9a-f]{8,}\Z", remainder) is not None
        if prefix in S.ID_RUNGS:
            out.append((prefix, remainder, "id" if digest else "malformed"))
        elif digest and len(remainder) >= 16:
            out.append((prefix, remainder, "unknown"))
    return out


def test_l1a_an_unparseable_rung_prefixed_token_is_a_violation():
    """The fail-open case, named exactly.

    Each of these carries a rung prefix -- so it is making an identity claim --
    and a remainder that is not a hex digest, and not readable as English
    either: every one bears a digit or an uppercase letter, which no word does
    and no mangled sha256 loses. Under the legacy regex every one of them
    produced no match at all, and `identity_violations` therefore reported a
    clean scaffold. A typo'd id, a truncated one, and an id under any future
    grammar all landed in that same silence.

    `episode-nonsense` used to be the first entry here and has moved to L1f: it
    is eight lowercase letters, and so is `task-oriented`. See that law.
    """
    unparseable = {
        "trailing.md": b"see snap-abcdef12ZZZ\n",             # digest + garbage
        "regrammared.md": b"see episode-jcs1-"                # a changed grammar
                          b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
        "upper.md": b"see rew-ABCDEF1234567890\n",            # not lowercase hex
    }
    for path, data in sorted(unparseable.items()):
        found = S.identity_violations({path: data})
        assert found, "%s passed the guard unread" % path
        assert all("malformed id literal" in reason for _p, reason in found), found

        # Non-vacuity: the legacy regex was blind to every one of these. The law
        # is red here and green there, which is what makes it worth stating.
        assert _LEGACY_ID_LITERAL.findall(data.decode()) == [], \
            "%s was already caught by the regex this replaced" % path


def test_l1b_ordinary_prose_is_not_an_identity_claim():
    """A template may contain English. Two shapes of it, both previously flagged.

    This is the other half of failing closed: a guard aggressive enough to read
    every hyphenated token as an id flags the templates' own prose, and a guard
    that cries wolf is turned off -- which puts the scaffold back to fail-open by
    a different route.

    The first block is the elision form (`snap-...`), which was always exempt.
    The second is the defect: under the lookbehind guard *every* hyphenated
    phrase whose first word happened to be a rung name was a "malformed id
    literal", and each line below is asserted to have been one. The earlier
    version of this law used only `one-shot` and `cpython-3.11`, whose prefixes
    are not rungs at all, so it dodged the failure it was supposed to pin.
    """
    elision = (b"`pack` derives env-..., snap-... and rew-... from the bytes;\n"
               b"this is a one-shot cpython-3.11 run of a well-formed task.\n")
    assert S.identity_violations({"README.md": elision}) == [], \
        S.identity_violations({"README.md": elision})

    sentences = [
        b"set the env-vars first\n",
        b"a task-oriented agent\n",
        b"run patch-apply now\n",
        b"sem-ver 2.0 is not a digest\n",
        b"the bundle-path is relative\n",
        b"a finding-completeness signal\n",
        b"a trace-driven replay-only rerun\n",
    ]
    for line in sentences:
        assert S.identity_violations({"README.md": line}) == [], \
            "the guard read %r as an identity claim: %r" \
            % (line, S.identity_violations({"README.md": line}))
        # Non-vacuity, the other way round: each of these WAS a violation under
        # the guard being repaired. Without this the law reads as a tautology.
        assert [t for t in _lookbehind_tokens(line.decode()) if t[2] == "malformed"], \
            "%r was not a false positive before, so this line pins nothing" % line


def test_l1c_the_rung_list_covers_every_id_identity_mints():
    """`ID_RUNGS` is written once; this is what keeps it honest.

    The rung list used to be spelled out inside three separate regexes, so a
    tenth rung added to `identity.py` would have escaped all three without a
    word. It is now one tuple -- and this law derives the truth from the other
    side, by *minting* an id through every public `*_id` function the identity
    spine exports and refusing any prefix `ID_RUNGS` has not heard of. A new
    rung fails here, in the file that owns the list, rather than silently
    widening a guard somewhere else.
    """
    from traaviis import identity as I

    # The smallest document each rung will actually seal. Empty was enough for
    # every rung until `episode-` gained a declared canonicalization scheme
    # (B1): `identity.episode_scheme` refuses a receipt that declares no
    # version rather than guessing one, so `{}` is no longer a document it can
    # mint over. The law is unchanged in what it asserts -- it still derives the
    # rung list by minting through every public `*_id` the spine exports -- and
    # a rung that needs a non-empty document says so here, once.
    seed = {"episode_id": {"episode_version": "traaviis.episode.v1"}}

    minters = [getattr(I, name) for name in I.__all__ if name.endswith("_id")]
    assert len(minters) >= 9, [f.__name__ for f in minters]
    for mint in minters:
        rung = mint(seed.get(mint.__name__, {})).split("-", 1)[0]
        assert rung in S.ID_RUNGS, \
            "identity.%s mints the %r rung, which ID_RUNGS does not name" \
            % (mint.__name__, rung)


def test_l1d_an_invented_rung_is_a_violation():
    """A digest under a prefix that is not a rung is an id nobody can re-derive.

    (Numbered L1d because it was written as a second `test_l1a_…`. Two laws
    wore the same label and nothing noticed, which is what
    `test_the_law_labels_are_unique_and_match_the_documented_series` now
    catches.)
    """
    found = S.identity_violations(
        {"index.md": b"evaluation-" + b"a" * 32 + b"\n"})
    assert found, "an invented rung passed the guard"
    assert "unknown rung" in found[0][1], found


def test_l1e_the_prose_exemption_does_not_swallow_a_real_id():
    """The exemption L1b buys is narrow, and here is its boundary.

    Each line is the `env-vars` prose shape with exactly one thing changed -- a
    digit, an uppercase letter, a run too long to be a word, or an actual
    digest. Every one is still reported. An exemption without a law like this is
    an unbounded hole; with it, widening the exemption has to move a test.
    """
    still_violations = [
        b"set the env-vars0000 first\n",             # a digit
        b"set the env-varsVARS first\n",             # an uppercase letter
        b"set the env-" + b"v" * 16 + b" first\n",   # too long to be a word
        b"set the env-abcdef12 first\n",             # an actual (short) digest
    ]
    for line in still_violations:
        found = S.identity_violations({"README.md": line})
        assert found, "the prose exemption swallowed %r" % line


def test_l1f_a_well_formed_id_is_reported_wherever_it_sits():
    """An id inside a longer name is still an id. This coverage was traded away.

    The lookbehind in the first fail-closed rewrite refused to start a token
    after `-` or `.`, on the reasoning that "an id is written standalone". It is
    not: `prev-episode-<hex>` and `run.episode-<hex>` are how an id appears
    inside a key, a path, or a diff, and the **legacy regex reported both**. The
    rewrite returned `[]` for them -- a real, well-formed id going completely
    unreported -- while its comment claimed no id-shaped token was left
    unreported.

    Measured three ways, because "the new guard works" is not the claim; the
    claim is that the guard it replaced was blind where the one *it* replaced
    was not:

        legacy regex        reports it
        lookbehind rewrite  reports NOTHING          <- the regression
        this guard          reports it, as `"id"`

    The discrimination that lets position be dropped is the remainder's shape,
    not the token's position: `traaviis.finding-completeness-impl.v1` -- the
    frozen verifier version in every `ComparisonV1` this repository produces,
    and the thing the lookbehind was really standing in for -- has a remainder
    that is not a digest, and is exempt for that reason instead.
    """
    digest = "42d0bb07e5f83e9e"
    embedded = ["prev-episode-" + digest,
                "run.episode-" + digest,
                "/tmp/x/.tmp-episode-%s/receipt.json" % digest]
    for text in embedded:
        assert _LEGACY_ID_LITERAL.findall(text) == ["episode-" + digest], text
        assert _lookbehind_tokens(text) == [], \
            "%r was NOT lost by the lookbehind guard, so this law pins nothing" % text
        assert ("episode", digest, "id") in S.id_tokens(text), \
            "a well-formed id inside %r is still unreported" % text

    # And the tension the lookbehind existed to resolve is resolved without it:
    # the version string that failed C20 is not a token under either rule.
    version = "traaviis.finding-completeness-impl.v1"
    assert S.id_tokens(version) == [], S.id_tokens(version)
    assert [t for t in _lookbehind_tokens("finding-completeness-impl")
            if t[2] == "malformed"], \
        "the C20 false positive is not reproduced, so nothing is being traded"

    # Planted into a REAL scaffolded template. Two plants, each blinding a
    # different one of the two guards this replaced -- which is the point: the
    # fix has to be red where BOTH of them were green, not merely red.
    files = S.scaffold("evidence-residency")
    assert S.identity_violations(files) == []
    plants = {
        # the re-grammared id: invisible to the legacy regex (`j` is not hex)
        "episode-jcs1-" + "b" * 64: "legacy",
        # the same id one hyphen deeper: invisible to the lookbehind rewrite
        "prev-episode-" + "a" * 64: "lookbehind",
    }
    for planted, blind in plants.items():
        doctored = dict(files)
        doctored["README.md"] = files["README.md"] + planted.encode() + b"\n"
        assert S.identity_violations(doctored), \
            "%r planted in a real template went unreported" % planted
        if blind == "legacy":
            assert _LEGACY_ID_LITERAL.findall(planted) == [], planted
        else:
            assert _LEGACY_ID_LITERAL.findall(planted) != [], planted
            assert _lookbehind_tokens(planted) == [], \
                "%r was already caught before, so this plant proves nothing" % planted


def test_l1g_the_guard_states_what_it_does_not_report():
    """The trade, written down. A stated limitation is acceptable; a silent one
    is not, and the sentence this law replaces ("together they leave no token
    that is both id-shaped and unreported") was the silent kind.

    **Not reported, deliberately.** `episode-nonsense` and `task-oriented` are
    both eight lowercase letters. One is stipulated to be a typo'd id and the
    other is ordinary English, and *nothing in the two strings differs*: same
    length, same character class, same hex-letter density (`e` three times in
    each). No lexical rule separates them, so the guard reports neither. The
    first version of this battery reported both, which is how `env-vars` became
    a violation.

    **What bounds the silence**, and this is the half that makes the trade
    survivable -- each of these is one edit away from the exempt shape and each
    is still reported (L1e), because a mangled sha256 keeps its digits and its
    length while an English word has neither.
    """
    # The silence itself, pinned so that widening or narrowing it moves a law.
    unreported = [
        b"see episode-nonsense\n",        # a rung + a lowercase word: ambiguous
        b"a task-oriented agent\n",       # the same shape, meant as English
        b"traaviis.finding-completeness-impl.v1\n",   # a rung inside a version
        b"/tmp/traaviis-snap-01uu24ob/x\n",           # a rung inside a tempdir
    ]
    for line in unreported:
        assert S.identity_violations({"notes.md": line}) == [], \
            "%r is documented as unreported but the guard reported it" % line

    # Non-vacuity: the first two were violations under the lookbehind guard and
    # the last two were not, so this law records a real change of verdict in one
    # direction and a deliberate agreement in the other.
    assert [t for t in _lookbehind_tokens("episode-nonsense") if t[2] == "malformed"]
    assert [t for t in _lookbehind_tokens("task-oriented") if t[2] == "malformed"]
    assert _lookbehind_tokens("/tmp/traaviis-snap-01uu24ob/x") == []

    # The boundary: the same four with an identity-shaped remainder are reported.
    for line in (b"see episode-nonsense0\n",
                 b"a task-orientedORIENTED agent\n",
                 b"traaviis.finding-" + b"c" * 32 + b".v1\n",
                 b"/tmp/traaviis-snap-" + b"0" * 16 + b"/x\n"):
        assert S.identity_violations({"notes.md": line}), \
            "the silence is not bounded: %r went unreported too" % line


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


# --- the battery itself ------------------------------------------------------
#
# Should the L-series carry a range pin like `test_mcp`'s M30, which asserts
# `numbers == list(range(1, 40))`? Yes, but not that check, because the two
# series are not the same shape and M30's form would be both weaker and wrong
# here.
#
# M30 works because the M-series is FLAT: one integer per law, one test per
# integer, so contiguity simultaneously proves no duplicate, no gap and no
# undocumented member. The L-series is TWO-LEVEL -- seven numbered laws, each
# with lettered sub-laws -- and legitimately has several tests per number (L1
# has three statements about L1 itself) plus tests that are not laws at all
# (`test_cli_init_*`). Asserting `range(1, 8)` over the numbers would be
# satisfied by the exact defect that occurred: two functions both labelled L1a.
#
# So the pin is placed on the axis that actually broke. A lettered label is a
# sub-law and names exactly one test; letters run contiguously from `a`, so a
# retired L1b cannot leave a hole that reads as "there was never one"; and the
# set of numbers is tied to the law table in `scaffold`'s own module docstring,
# so a law cannot exist in the battery without being documented, or be
# documented without being tested. That is strictly more than contiguity on the
# axis that failed, and it makes no claim about a shape this series does not
# have.

_LAW_NAME = re.compile(r"^test_l(\d+)([a-z]*)_")
_DOCUMENTED_LAW = re.compile(r"^\s{4}L(\d+)\s{2,}", re.MULTILINE)


def _label_defects(names, documented):
    """Every way the law labels in `names` are malformed. Pure, so it is testable.

    `names` is a list of test function names; `documented` is the set of law
    numbers the module under test documents.
    """
    defects = []
    seen = {}
    for name in names:
        m = _LAW_NAME.match(name)
        if m:
            seen.setdefault((int(m.group(1)), m.group(2)), []).append(name)
    for (number, letters), owners in sorted(seen.items()):
        if letters and len(owners) > 1:
            defects.append("L%d%s labels %d tests: %s"
                           % (number, letters, len(owners), sorted(owners)))
    numbers = {n for n, _l in seen}
    for number in sorted(numbers):
        letters = sorted(l for n, l in seen if n == number and l)
        expected = [chr(ord("a") + i) for i in range(len(letters))]
        if letters != expected:
            defects.append("L%d sub-laws are %s, not contiguous from 'a'"
                           % (number, letters))
    for number in sorted(numbers - documented):
        defects.append("L%d is tested but not documented" % number)
    for number in sorted(documented - numbers):
        defects.append("L%d is documented but not tested" % number)
    return defects


def test_the_law_labels_are_unique_and_match_the_documented_series():
    """Two functions were both named `test_l1a_…` and nothing noticed."""
    # The literal set is asserted as well as parsed: a docstring that lost its
    # law table would otherwise make every check below vacuously true, which is
    # the same failure mode in a different place.
    documented = {int(n) for n in _DOCUMENTED_LAW.findall(S.__doc__)}
    assert documented == {1, 2, 3, 4, 5, 6, 7}, sorted(documented)

    names = [k for k in globals() if k.startswith("test_")]
    assert _label_defects(names, documented) == [], \
        _label_defects(names, documented)

    # Non-vacuity, one planted defect per branch -- including the real one: the
    # battery as it stood before this revision, with the invented-rung law still
    # wearing L1a's label.
    before = [("test_l1a_an_invented_rung_is_a_violation"
               if n == "test_l1d_an_invented_rung_is_a_violation" else n)
              for n in names]
    assert any("labels 2 tests" in d for d in _label_defects(before, documented)), \
        _label_defects(before, documented)

    planted = {
        "duplicate": (["test_l1a_x", "test_l1a_y", "test_l2_z"], "labels 2 tests"),
        "gap": (["test_l1a_x", "test_l1c_y", "test_l2_z"], "not contiguous"),
        "undocumented": (["test_l9a_x"], "tested but not documented"),
        "untested": ([], "documented but not tested"),
    }
    for what, (case, expected) in sorted(planted.items()):
        got = _label_defects(case, documented)
        assert any(expected in d for d in got), (what, got)


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
