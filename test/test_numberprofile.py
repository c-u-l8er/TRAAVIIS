"""Laws for `JCS_IJSON_CLOSED_NUMBER_PROFILE_V1` — the language-neutral domain.

The profile this replaces is sound and has one property nobody wants: its
admitted set depends on where **ECMAScript switches notation**. `PROFILE`
decides admission by inspecting the emitted token — a literal holding neither
`.` nor `e` is an integer literal and is bounded to the interoperable range,
anything else is admitted because an ordinary parse yields a binary64. ES2019
7.1.12.1 step 6 renders positionally while `n <= 21`, so:

    1e20   token `100000000000000000000`   an integer literal   ->  refused
    1e21   token `1e+21`                   not an integer       ->  admitted

Two integral values one decimal place apart, both far above `2**53`, separated by
a *lexical* rule in one language's renderer. `PROFILE`'s own docstring names the
resulting shape honestly — a hole across `[2**53, 1e21)` that reopens at `1e21` —
which is the right way to ship a defect you have decided not to fix yet, and not
a reason to keep it.

`PROFILE_IJSON` asks about the number instead:

    finite binary64 values only

    mathematically integral:   abs(value) <= 2**53 - 1
    non-integral:              the rendered token must parse back to identical
                               binary64 bits

Both `1e20` and `1e21` are refused. So is `2**54`, as an `int` or as a `float`.

**Nothing declares this profile yet, and that is the subject of N9 below.**
Implementing a profile and cutting over to it are different acts: the cutover
narrows the admitted domain of every future `episode-…`, and the ruling makes
`audit/number-oracle/run_audit.py` a hard gate on it. These laws exist so the
gate has something to be a gate on.

    N1-N5   the rule, and every consequence the ruling names, measured
    N6-N8   the two profiles are one serializer with two admission rules
    N9      the cutover has NOT happened, asserted so it cannot happen by
            accident
    N10     the exact oracle is present and is the gate

Run directly:      python3 test/test_numberprofile.py
Run under pytest:  pytest test/test_numberprofile.py
"""
import ast
import inspect
import math
import os
import struct
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import identity as I  # noqa: E402
from traaviis import jcs as J  # noqa: E402

AUDIT = os.path.join(REPO, "audit", "number-oracle")


class Skip(Exception):
    pass


def _token(value, profile=J.PROFILE_IJSON):
    """The canonical token for one scalar under `profile`, or `None` if refused."""
    try:
        return J.canonical_bytes(value, profile=profile).decode("utf-8")
    except J.JcsError:
        return None


def _reason(value, profile=J.PROFILE_IJSON):
    try:
        J.canonical_bytes(value, profile=profile)
    except J.JcsError as exc:
        return exc.detail.get("reason") or exc.code
    return None


# ===================================================== N1-N5: the rule, measured

def test_n1_an_integral_value_is_bounded_by_the_interoperable_range():
    """`abs(value) <= 2**53 - 1` for anything mathematically whole.

    The bound is `2**53 - 1` and not `2**53`: RFC 7493 §2.2 and RFC 8259 §6 both
    state the interoperable range as `[-(2**53)+1, (2**53)-1]`, so `2**53` itself
    is outside it. Checked at the boundary from both sides, because an
    off-by-one here is the difference between implementing the RFC and
    implementing a memory of it.
    """
    assert _token(J.MAX_SAFE_INTEGER) == "9007199254740991"
    assert _token(J.MIN_SAFE_INTEGER) == "-9007199254740991"
    assert _token(2 ** 53) is None
    assert _token(-(2 ** 53)) is None
    assert _token(float(2 ** 53)) is None

    assert _reason(2 ** 53) == J.REASON_INTEGRAL_OUT_OF_RANGE
    # Ordinary small integers are untouched, in both spellings.
    for value, want in ((0, "0"), (100, "100"), (3.0, "3"), (-1, "-1"),
                        (1000000, "1000000"), (2.0, "2")):
        assert _token(value) == want, value


def test_n2_the_host_type_does_not_change_the_answer():
    """`int(v)` and `float(v)` get one verdict, for every `v` either can hold.

    This is the whole claim in the profile's name. `PROFILE` already had it for
    its own rule; what changes is that the rule being applied is now about the
    number rather than about the token ECMAScript would print for it.

    Note the pairs that used to *disagree between profiles*: `10**21` and `1e21`
    are the same number, `PROFILE` admits both (its token is `1e+21`, not an
    integer literal), and `PROFILE_IJSON` refuses both. Agreement within a
    profile was never the defect; the defect was which set it agreed on.
    """
    for magnitude in (0, 1, 2, 10 ** 3, 2 ** 53 - 1, 2 ** 53, 2 ** 54,
                      10 ** 21, 10 ** 30):
        for value in (magnitude, -magnitude):
            as_float = float(value) if abs(value) < 2 ** 1000 else None
            if as_float is None:
                continue
            assert _token(value) == _token(as_float), value
            assert _reason(value) == _reason(as_float), value


def test_n3_the_lexical_discontinuity_is_gone():
    """`1e20` and `1e21` get the same answer, which they did not before.

    The single measurement this profile exists for. Under `PROFILE` the pair
    straddles the boundary — `1e20` refused, `1e21` admitted — for no reason
    except that ES2019 step 6 stops rendering positionally at `n = 21`. Both
    directions are asserted: the old behaviour is pinned as *still true of the
    old profile*, so this law records a change rather than merely a state.
    """
    # The old profile, unchanged. If this half ever fails, the cutover happened
    # by accident and N9 is the law that should have caught it.
    assert _token(1e20, profile=J.PROFILE) is None
    assert _token(1e21, profile=J.PROFILE) == "1e+21"

    # The new one, with the discontinuity closed.
    assert _token(1e20) is None
    assert _token(1e21) is None
    assert _reason(1e20) == _reason(1e21) == J.REASON_INTEGRAL_OUT_OF_RANGE

    # And it is closed *along the whole range*, not just at the one pair that
    # named it. Every integral power of ten from the bound upward is refused.
    for exponent in range(16, 30):
        assert _token(float("1e%d" % exponent)) is None, exponent


def test_n4_every_consequence_the_ruling_names_is_measured():
    """The ruling's table, transcribed and checked line by line.

        int(2**54)      reject          float(2**54)    reject
        int(10**21)     reject          float(1e21)     reject
        1e20            reject
        NaN / Infinity  reject
        -0              render as 0
    """
    assert _token(int(2 ** 54)) is None
    assert _token(float(2 ** 54)) is None
    assert _token(int(10 ** 21)) is None
    assert _token(float(1e21)) is None
    assert _token(1e20) is None

    assert _token(float("nan")) is None
    assert _token(float("inf")) is None
    assert _token(float("-inf")) is None
    # A non-finite value is refused as non-finite, not as out of range: the two
    # are different facts and RFC 8785 §3.2.2.3 names only the first.
    try:
        J.canonical_bytes(float("nan"), profile=J.PROFILE_IJSON)
    except J.JcsError as exc:
        assert exc.code == J.JCS_NON_FINITE, exc.code
    else:
        raise AssertionError("NaN was canonicalized")

    assert _token(-0.0) == "0"
    assert _token(0.0) == "0"
    # Not merely equal strings: the two must be the same bytes, since the whole
    # point of §3.2.2.3's rule is that a signed zero cannot mint a second id.
    assert (J.canonical_bytes(-0.0, profile=J.PROFILE_IJSON)
            == J.canonical_bytes(0.0, profile=J.PROFILE_IJSON))


def test_n5_a_non_integral_value_is_admitted_exactly_when_its_token_round_trips():
    """The other half of the rule, over a corpus that includes the hard cases.

    Every non-integral finite binary64 does round-trip, because
    `_shortest_digits` searches for the first precision that parses back to the
    identical double — so the clause is enforcement on the path rather than a
    filter that ever fires. The law checks the property directly, on bit
    patterns rather than on decimal equality, so a `0.0` / `-0.0` confusion
    cannot pass it.

    The corpus is the profile's own boundary set (`audit/number-oracle/
    patterns.txt`) rather than a hand-written list, so it includes the subnormal
    floor, the `21` and `-6` rendering thresholds, and RFC 8785's Appendix B
    rows.
    """
    patterns = os.path.join(AUDIT, "patterns.txt")
    if not os.path.isfile(patterns):
        raise Skip("the boundary corpus is not in this tree")

    checked = 0
    with open(patterns, encoding="ascii") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            x = struct.unpack(">d", bytes.fromhex(line))[0]
            if math.isnan(x) or math.isinf(x) or x == int(x):
                continue                      # integral values are N1's subject
            token = _token(x)
            assert token is not None, ("a non-integral double was refused", line)
            assert struct.pack(">d", float(token)) == struct.pack(">d", x), \
                ("token does not round-trip", line, token)
            checked += 1
    assert checked > 50, "the corpus contributed too few non-integral values"


# ============================== N6-N8: one serializer, two admission rules

def test_n6_the_two_profiles_share_everything_except_admission():
    """A document both profiles admit gets byte-identical bytes.

    This is what makes the eventual cutover a *narrowing* rather than a
    re-hashing: nothing that is legal today and legal tomorrow moves, so every
    already-sealed id that survives the narrowing survives it unchanged. If the
    two profiles could disagree on the bytes of a commonly-admitted document,
    item 13 would be a migration instead of a switch.
    """
    documents = [
        {"a": 1, "b": [0.5, -0.0, 2 ** 53 - 1], "c": {"d": "€\U0001F600"}},
        [True, False, None, 1e-7, 1e-6, 0.1],
        {"": "", "é": 3.0, "z": [[[1]]]},
    ]
    for doc in documents:
        assert (J.canonical_bytes(doc, profile=J.PROFILE)
                == J.canonical_bytes(doc, profile=J.PROFILE_IJSON)), doc


def test_n7_the_new_profile_admits_a_strict_subset_of_the_old_one():
    """Narrowing, proved as a subset relation rather than asserted as an intent.

    Every value the new profile admits, the old one admits too — and there is at
    least one the old one admits and the new one does not, or "narrowing" would
    be a word for "identical". The witness is `1e21`, which is the value the
    whole change is named after.
    """
    patterns = os.path.join(AUDIT, "patterns.txt")
    if not os.path.isfile(patterns):
        raise Skip("the boundary corpus is not in this tree")

    only_old = []
    with open(patterns, encoding="ascii") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            x = struct.unpack(">d", bytes.fromhex(line))[0]
            new = _token(x)
            old = _token(x, profile=J.PROFILE)
            if new is not None:
                assert old == new, ("the new profile admits what the old refuses",
                                    line, old, new)
            elif old is not None:
                only_old.append(line)
    assert only_old, \
        "no vector separates the profiles; the corpus proves no narrowing"


def test_n8_an_unknown_profile_is_refused_and_never_silently_defaulted():
    """A typo in a profile name must not canonicalize under whichever is default.

    The failure this forbids is the quiet one: `canonical_bytes(doc,
    profile="JCS_IJSON")` succeeding under `PROFILE` and producing bytes that
    claim a domain they were never checked against.
    """
    assert J.PROFILES == (J.PROFILE, J.PROFILE_IJSON)
    for bogus in ("JCS_IJSON", "RFC8785", "", None, "jcs_ijson_closed_number_profile_v1"):
        try:
            J.canonical_bytes({"a": 1}, profile=bogus)
        except J.JcsError as exc:
            assert exc.code == J.JCS_NOT_JSON, (bogus, exc.code)
        else:
            raise AssertionError("profile %r was silently accepted" % (bogus,))


# ============================================ N9-N10: the cutover has not happened

def test_n9_nothing_declares_the_new_profile_yet():
    """The cutover is item 13 and has not been performed.

    Three ways it could happen by accident, all closed here:

      * `canonical_bytes`'s default changing, so every caller moves without
        anybody writing a line of code that says so;
      * `identity` binding its RFC 8785 scheme name to the new profile, which is
        the string documents actually carry;
      * some module in the package passing the new profile at a call site.

    Read on the parse tree for the third, because a docstring naming the profile
    — this file's subject is the profile, and several modules discuss it — is
    prose, not a call.

    This law is expected to be *deleted* at item 13, not weakened. A law that
    said "the cutover has not happened yet, except where it has" would be the
    thing it exists to prevent.
    """
    signature = inspect.signature(J.canonical_bytes)
    assert signature.parameters["profile"].default == J.PROFILE, \
        "the default profile moved; that IS the cutover"

    assert I.SCHEME_RFC8785 == J.PROFILE, \
        "identity now declares the new profile on the wire"

    package = os.path.join(REPO, "traaviis")
    offenders = []
    for name in sorted(os.listdir(package)):
        if not name.endswith(".py") or name == "jcs.py":
            continue
        with open(os.path.join(package, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "PROFILE_IJSON":
                offenders.append("%s:%d" % (name, node.lineno))
            elif (isinstance(node, ast.Constant)
                  and node.value == J.PROFILE_IJSON):
                offenders.append("%s:%d" % (name, node.lineno))
    assert not offenders, (
        "the new profile is referenced in code outside jcs.py: %s. Either the "
        "cutover happened and this law should be deleted, or it happened by "
        "accident and should not have." % ", ".join(offenders))


def test_n10_the_exact_oracle_is_present_and_is_relocatable():
    """The gate exists, has one entry point, and names no absolute path.

    The ruling's exact words: the shipped audit "supports the historical claim
    but does not discharge the v2 cutover gate" until there is one relocatable
    entry point. This law does not *run* the audit — it needs `node` and `rustc`
    and a battery must not depend on either — it asserts that the entry point is
    there and that it could run somewhere else.

    "Somewhere else" is checked structurally: every string constant in
    `run_audit.py` that looks like a POSIX absolute path is a violation, which
    is exactly the defect the reviewed harness had four times over
    (`/tmp/claude-…/scratchpad`, and one user's Node build).
    """
    entry = os.path.join(AUDIT, "run_audit.py")
    if not os.path.isfile(entry):
        raise Skip("the audit directory is not in this tree")

    with open(entry, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    absolute = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if text.startswith("/") and len(text) > 1 and " " not in text:
                absolute.append("%d: %r" % (node.lineno, text))
    assert not absolute, (
        "run_audit.py holds an absolute path: %s" % "; ".join(absolute))

    functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for required in ("shortest_digits_exact", "render_es2019", "run_v8",
                     "run_rust", "main"):
        assert required in functions, required

    # The historical scripts are kept, and are *not* the entry point. Their
    # absolute paths are the record of what was actually run; the README says
    # which is which. What matters is that they are not what anybody is told to
    # invoke.
    assert os.path.isfile(os.path.join(AUDIT, "README.md")), \
        "the audit directory does not say which file is the entry point"


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
