"""Laws for TRAAVIIS canonical JSON measured against RFC 8785 (JCS).

`traaviis.identity.canonical_bytes` is the frozen serializer under every
`snap-` / `finding-` / `patch-` / `trace-` / `rew-` / `task-` / `episode-` /
`env-` / `bundle-` id. It is

    json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

which is *close to* RFC 8785 but is not RFC 8785. This battery is the record of
exactly where the two agree and exactly where they part, so that the question
"can another language reimplement our ids from a one-line spec?" has a checkable
answer instead of an assumed one.

**These laws do not ask for the hasher to change.** Ids are frozen keys: moving
`canonical_bytes` moves every id in existence, which is the most expensive
change this repo can make. So the divergence laws below are *characterization*
laws — they assert that the divergence is present, with a concrete reproducing
value. If someone conforms the hasher, they will go red, and that is the point:
the decision has to be taken deliberately and the ids migrated, not slid in.

**One divergence was closed, and only because closing it moved nothing.**
RFC 8785 §3.2.2.3 requires a compliant implementation to *terminate* on NaN or
Infinity; Python's default emitted the bare tokens `NaN` / `Infinity`, which are
not JSON, so `canonical_bytes` could mint an id over a preimage no parser will
read back. That is not a conformance preference, it is a content address over
unparseable bytes, and it is the one divergence whose repair costs no id: the
inputs it changes behaviour for do not occur. `allow_nan=False` now closes it
(C15, C18, C27) and the *costlessness* is itself a checked law rather than a
claim (C28-C30). Every other divergence changes bytes for inputs that do occur
and stays characterized, not fixed.

The failure modes they exist to close:

- we could publish "our ids are SHA-256 over RFC 8785 canonical JSON" and be
  wrong, so a second implementation computes different ids and every cross-
  language admission check fails for reasons nobody can find (C1-C4, C20);
- we could believe the divergence is theoretical when a live shipped receipt
  already sits on the wrong side of it (C13, C19);
- we could believe the reachable domain is constrained when nothing anywhere
  enforces a constraint — no schema, no key charset check, no numeric domain
  check (C17, C18);
- the reference implementation used to judge all of this could itself be wrong,
  which would make every verdict in this file confidently false (C21-C23);
- `canonical_bytes` could emit bytes that are not JSON at all and hash them
  anyway, minting an id for a document no parser will read back (C15, C27 —
  now closed);
- the repair for that could be believed costless when it is not, or believed
  costly when it is not, either way on nobody's measurement (C28-C30);
- two documents that every other JSON tool considers identical could carry two
  different ids (C16).

What was actually read to write this: RFC 8785 §3.1 (I-JSON restrictions),
§3.2.2.2 (string escaping, lone surrogates), §3.2.2.3 (numbers, NaN/Infinity),
§3.2.3 (UTF-16 code-unit property sorting), Appendix A (sorting sample) and
Appendix B (number test vectors). ECMA-262's `Number::toString` was *not* read
as prose — WebFetch declined to reproduce it — so `_es_number_to_string` below
was instead differential-tested against V8 (node v25.2.1) over 76,926 double
bit patterns with zero mismatches, and against the eight RFC 8785 Appendix B
vectors reproduced in C22. That is a stronger warrant than a quotation, but the
provenance is stated here rather than implied.

Runs with pytest, or standalone: `python3 test/test_canonical.py`.
"""

import contextlib
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import zipfile
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traaviis import identity as I  # noqa: E402
from traaviis import snapshot as S  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# A minimal RFC 8785 reference implementation (stdlib only, no dependency)     #
#                                                                             #
# This exists so the battery can *measure* conformance rather than assert it.  #
# It is deliberately not exported and nothing in traaviis/ imports it: it is a #
# yardstick, not a second hasher.                                             #
# --------------------------------------------------------------------------- #

class JCSError(ValueError):
    """Input outside the RFC 8785 domain; the RFC says MUST terminate."""


def _es_number_to_string(x):
    """ECMAScript `Number::toString(x, 10)`, which RFC 8785 §3.2.2.3 mandates.

    Written from the shape of the ECMA-262 algorithm (find k, n, s with s the
    shortest decimal that round-trips, then pick one of five renderings by where
    n falls) and then *validated by execution* against V8 — see the module
    docstring. `repr(float)` supplies the same shortest-round-trip digits ES
    uses, so only the rendering rules had to be reimplemented.
    """
    if isinstance(x, bool):
        raise JCSError("bool is not a number")
    if isinstance(x, int):
        x = float(x)
    if math.isnan(x) or math.isinf(x):
        raise JCSError("NaN/Infinity not permitted (RFC 8785 §3.2.2.3)")
    if x == 0:
        return "0"                                    # also covers -0.0
    if x < 0:
        return "-" + _es_number_to_string(-x)
    _sign, digits, exp = Decimal(repr(x)).as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:         # shortest s
        digits.pop()
        exp += 1
    k = len(digits)
    n = exp + k
    ds = "".join(str(d) for d in digits)
    if k <= n <= 21:
        return ds + "0" * (n - k)                      # plain integer
    if 0 < n <= 21:
        return ds[:n] + "." + ds[n:]                   # integer part + fraction
    if -6 < n <= 0:
        return "0." + "0" * (-n) + ds                  # leading zeros
    e = n - 1                                          # exponent notation
    mant = ds if k == 1 else ds[0] + "." + ds[1:]
    return mant + "e" + ("+" if e >= 0 else "-") + str(abs(e))


_JCS_SHORT_ESCAPES = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n",
                      0x0C: "\\f", 0x0D: "\\r"}


def _jcs_string(s):
    """RFC 8785 §3.2.2.2 string serialization."""
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            raise JCSError("lone surrogate U+%04X (RFC 8785 §3.2.2.2)" % cp)
        if cp in _JCS_SHORT_ESCAPES:
            out.append(_JCS_SHORT_ESCAPES[cp])
        elif cp < 0x20:
            out.append("\\u%04x" % cp)                 # lowercase hex, per §3.2.2.2
        elif ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        else:
            out.append(ch)                             # everything else "as is"
    out.append('"')
    return "".join(out)


def _utf16_sortkey(s):
    """RFC 8785 §3.2.3 sorts property names as arrays of UTF-16 code units.

    Big-endian UTF-16 bytes compare, as unsigned bytes, exactly as the code
    units compare as unsigned integers — and a prefix sorts first, which is the
    RFC's length tiebreaker. So the encoding *is* the sort key.
    """
    return s.encode("utf-16-be", "surrogatepass")


def _jcs_serialize(obj):
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, str):
        return _jcs_string(obj)
    if isinstance(obj, (int, float)):
        return _es_number_to_string(obj)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_jcs_serialize(v) for v in obj) + "]"
    if isinstance(obj, dict):
        for k in obj:
            if not isinstance(k, str):
                raise JCSError("non-string property name %r (I-JSON)" % (k,))
        items = sorted(obj.items(), key=lambda kv: _utf16_sortkey(kv[0]))
        return "{" + ",".join(
            _jcs_string(k) + ":" + _jcs_serialize(v) for k, v in items) + "}"
    raise JCSError("not JSON data: %r" % (obj,))


def jcs(obj):
    """RFC 8785 canonical bytes for `obj`."""
    return _jcs_serialize(obj).encode("utf-8")


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def agree(obj):
    """True iff `canonical_bytes(obj)` is byte-identical to RFC 8785."""
    return I.canonical_bytes(obj) == jcs(obj)


def both(obj):
    """`(traaviis bytes, rfc8785 bytes)` for a value, as a diffable pair."""
    return I.canonical_bytes(obj), jcs(obj)


def double(hexbits):
    """The IEEE-754 binary64 named by a 16-hex-digit big-endian bit pattern."""
    return struct.unpack(">d", bytes.fromhex(hexbits))[0]


def _skn(x):
    """`(k, n)`: shortest-decimal digit count, and the decimal exponent."""
    _sign, digits, exp = Decimal(repr(abs(x))).as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exp += 1
    return len(digits), exp + len(digits)


def conforming_float(x):
    """The measured predicate for "this float serializes identically both ways".

    Established by scanning 502,555 finite doubles (uniform random bit patterns
    plus structured corpora across every decimal exponent): zero
    mispredictions. C24 re-runs a sample of that scan so the predicate stays
    honest rather than becoming folklore.
    """
    if x == 0:
        return False                        # 0.0 / -0.0 vs "0"
    k, n = _skn(x)
    if k <= n <= 21:
        return False                        # integral double < 1e21: Python adds ".0"
    if 0 < n <= 21:
        return True                         # integer part + fraction
    if -6 < n <= 0:
        return n >= -3                      # Python leaves plain form at 1e-4
    return abs(n - 1) >= 10                 # Python zero-pads a 1-digit exponent


# --------------------------------------------------------------------------- #
# The pre-`allow_nan=False` serializer, and the corpus it must agree with      #
#                                                                             #
# C28-C30 are the laws that make the NaN repair safe to land: they measure the #
# repair against the serializer it replaced, over the artifacts that actually  #
# exist. That needs the old serializer to still be executable, so it is kept   #
# here — verbatim, as a fixture, deliberately not imported from anywhere.      #
# --------------------------------------------------------------------------- #

def legacy_canonical_bytes(obj):
    """`identity.canonical_bytes` exactly as it stood before `allow_nan=False`.

    Byte-for-byte the old body. Nothing may be "tidied" in it: its whole job is
    to be the thing the current serializer is compared against, so any edit to
    it silently weakens C28-C30 into a comparison with itself.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


@contextlib.contextmanager
def under_legacy_serializer():
    """Run the real `canonicalize_*` / `*_id` functions on the old serializer.

    `identity`'s canonicalizers call `canonical_bytes` through the module
    global, so rebinding it here reroutes every rung — which is what lets C29
    recompute a *whole id* the old way rather than re-deriving each projection
    by hand and testing the re-derivation instead of the code.
    """
    original = I.canonical_bytes
    I.canonical_bytes = legacy_canonical_bytes
    try:
        yield
    finally:
        I.canonical_bytes = original


#: `(version field, id field, id function)` for every rung that mints an id.
#: A document is typed by its own `*_version`, so a receipt's *reference* to a
#: `reward_id` is never mistaken for a reward document.
ID_KINDS = (
    ("snapshot_version", "snapshot_id", I.snapshot_id),
    ("reward_spec_version", "reward_id", I.reward_id),
    ("task_spec_version", "task_id", I.task_id),
    ("finding_version", "finding_id", I.finding_id),
    ("patch_version", "patch_id", I.patch_id),
    ("trace_version", "trace_id", I.trace_id),
    ("episode_version", "episode_id", I.episode_id),
    ("environment_version", "env_id", I.environment_id),
    ("bundle_version", "bundle_id", I.bundle_id),
)


def corpus_documents():
    """Every JSON document in the repository, on disk and inside shipped zips.

    Yields `(label, parsed)`. Deliberately wider than `examples/`: the packets
    under `dist/` are *published* artifacts whose ids other people already hold,
    so a repair that moved one of those would be exactly as expensive as one
    that moved a working copy. Unparseable files are skipped and counted by the
    caller — `legacy/node-harness/test/fixtures/parse-error.json` is malformed
    on purpose and is not an artifact.
    """
    for dirpath, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in sorted(names):
            path = os.path.join(dirpath, name)
            label = os.path.relpath(path, REPO)
            if name.endswith(".json"):
                try:
                    with open(path, encoding="utf-8") as fh:
                        yield label, json.load(fh)
                except (ValueError, OSError):
                    continue
            elif name.endswith(".zip"):
                try:
                    zf = zipfile.ZipFile(path)
                except (zipfile.BadZipFile, OSError):
                    continue
                with zf:
                    for member in sorted(zf.namelist()):
                        if not member.endswith(".json"):
                            continue
                        try:
                            yield (label + "!" + member,
                                   json.loads(zf.read(member).decode("utf-8")))
                        except (ValueError, OSError, KeyError):
                            continue


#: A real receipt shipped in this repo, whose `episode-` is a live frozen id.
LIVE_RECEIPT = os.path.join(
    REPO, "examples", "eval-one", "episodes",
    "episode-42d0bb07e5f83e9e57518bf5cd3717e2a1e3aa45aa5821e36ac19206a3d73299",
    "receipt.json")


class Skip(Exception):
    """A law whose fixture is not present in this checkout."""


# --------------------------------------------------------------------- laws #
# C1-C8   where the two schemes already agree (the conforming core)          #
# --------------------------------------------------------------------------- #

def test_c1_ascii_keys_and_small_integers_are_already_jcs():
    """The common case conforms, which is why the divergence stayed invisible.

    Nearly every document TRAAVIIS hashes is ASCII keys, short strings, small
    integers and booleans. Over that shape the two schemes are byte-identical,
    so no amount of ordinary use would ever surface a difference. This law
    states the agreement explicitly so the later divergence laws read as
    *narrow* rather than as "the hasher is wrong".
    """
    doc = {
        "episode_version": "residency.episode.v1",
        "status": "ok",
        "exit_code": 0,
        "attempts": 3,
        "timed_out": False,
        "trace_id": None,
        "verifier_versions": {"tests": "v1", "patch": "v1"},
        "outputs": ["finding.json", "candidate.patch"],
    }
    assert agree(doc), both(doc)


def test_c2_string_escaping_conforms_exactly():
    """Escaping is the one place we could have diverged and did not.

    RFC 8785 §3.2.2.2 requires `\\b \\t \\n \\f \\r` for those five, lowercase
    `\\u00hh` for the rest of U+0000-U+001F, and everything else emitted as is —
    including U+007F, `/`, and all non-ASCII. `ensure_ascii=False` gives Python
    exactly that. If this law ever breaks, the one-sentence spec is unsalvageable,
    because escaping differences touch *every* string in *every* document.
    """
    probe = "".join(chr(c) for c in (
        0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1F, 0x20, 0x22, 0x2F,
        0x5C, 0x7F, 0xE9, 0x20AC, 0x1F600))
    assert agree({"s": probe}), both({"s": probe})
    emitted = json.dumps(probe, ensure_ascii=False)
    assert "\\b\\t\\n\\u000b\\f\\r\\u001f" in emitted, emitted
    assert "\\u000B" not in emitted, "hex escapes must be lowercase (§3.2.2.2)"
    assert "\\/" not in emitted, "solidus must not be escaped"


def test_c3_non_ascii_string_values_conform():
    """A non-ASCII *value* is safe; only a non-ASCII *key* can reorder anything.

    Worth separating from C2 because it is the reassuring half: an environment
    description in Hebrew, a finding quoting Greek source, an emoji in an
    instruction — none of those can move an id relative to RFC 8785.
    """
    for cp in (0xE9, 0x3B1, 0x5D0, 0x20AC, 0xFB33, 0x1F600, 0x10FFFF):
        doc = {"instructions": "x" + chr(cp) + "y"}
        assert agree(doc), (hex(cp), both(doc))


def test_c4_bmp_key_ordering_conforms():
    """Below U+10000, code-point order and UTF-16 code-unit order coincide.

    So the divergence in C11 is precisely and only about astral-plane keys —
    not about non-ASCII keys in general. Stating that here is what makes the
    "constrain the domain" option in the report a small ask rather than
    "ban Unicode from keys".
    """
    doc = {chr(cp): 1 for cp in
           (0x0D, 0x31, 0x41, 0x7A, 0x80, 0xF6, 0x3B1, 0x5D0, 0x20AC,
            0xD7FF, 0xE000, 0xFB33, 0xFFFF)}
    assert agree(doc), both(doc)


def test_c5_typical_reward_weights_conform():
    """The weights actually shipped in this repo are on the conforming side.

    `examples/eval-one/*/reward.json` uses 0.25 / 0.20 / 0.30 / 0.15 / 0.10.
    Every one of those has an integer part of 0 and a fraction, so both schemes
    render it identically and every shipped `rew-` id is already RFC 8785. The
    divergence is not in the weights — it is in what the weights *sum to*
    (C13).
    """
    for w in (0.25, 0.2, 0.3, 0.15, 0.1, 0.05, 0.125, 0.4, 0.75, 0.999):
        assert conforming_float(w), w
        assert agree({"weight": w}), both({"weight": w})


def test_c6_nested_lists_preserve_order_in_both_schemes():
    """Neither scheme sorts arrays, so the "producer orders lists" rule holds.

    `canonical_bytes`'s docstring promises maps are order-independent and lists
    are not. RFC 8785 makes the same promise, so adopting it would not silently
    change what a reordered list means.
    """
    doc = {"events": [{"b": 1, "a": 2}, {"a": 3}], "paths": ["z", "a", "m"]}
    assert agree(doc), both(doc)
    assert b'"paths":["z","a","m"]' in I.canonical_bytes(doc)


def test_c7_the_empty_key_and_the_empty_document_conform():
    """Boundary shapes, checked because a sort key of length zero is a classic
    place for a "shorter string precedes" rule to be implemented differently."""
    for doc in ({}, {"": 1}, {"": 1, "a": 2}, {"a": {}}, {"a": []}):
        assert agree(doc), both(doc)


def test_c8_a_real_shipped_reward_and_task_document_conforms():
    """Measured, not assumed: an actual on-disk artifact, byte for byte.

    Twelve id-bearing documents ship in `examples/` and eleven of them are
    already RFC 8785 canonical (C19 walks all of them). Naming one here keeps
    the verdict proportionate: the scheme is not broadly wrong.
    """
    path = os.path.join(REPO, "examples", "eval-one", "residency-demo",
                        "reward.json")
    if not os.path.exists(path):
        raise Skip("examples/eval-one/residency-demo/reward.json absent")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    projected = {k: v for k, v in doc.items() if k != "reward_id"}
    assert I.canonicalize_reward(doc) == jcs(projected), both(projected)


# --------------------------------------------------------------------------- #
# C9-C16  where the two schemes part                                          #
# --------------------------------------------------------------------------- #

def test_c9_integral_valued_floats_diverge():
    """`1.0` is `1.0` to Python and `1` to RFC 8785. This is the live one.

    Not a corner case: `reward.score()` initialises `reward = 0.0` and adds
    `float(weight)`, so **every** episode receipt carries a float in the field
    `reward`, which is inside `_EPISODE_IDENTITY_KEYS`. A fully-passing episode
    against a spec whose weights sum to 1 has `reward` exactly `1.0`; a tampered
    one has exactly `0.0`. Both are on the diverging side, and both are the
    modal outcomes rather than exotic ones.
    """
    assert I.canonical_bytes(1.0) == b"1.0"
    assert jcs(1.0) == b"1"
    assert I.canonical_bytes(0.0) == b"0.0"
    assert jcs(0.0) == b"0"
    for x in (1.0, 0.0, 3.0, 100.0, 2.0 ** 53, 1e16, 1e20):
        assert not agree({"reward": x}), x
    # and the integer 1 is unaffected -- it is only the *float* that differs
    assert agree({"reward": 1})


def test_c10_negative_zero_diverges():
    """RFC 8785 folds -0.0 to "0"; Python keeps the sign.

    Reachable the moment any weight or cap is negative, or any arithmetic
    produces a negative zero. Appendix B lists `8000000000000000 -> 0`
    explicitly, so this is the RFC's own test vector disagreeing with us.
    """
    assert I.canonical_bytes(-0.0) == b"-0.0"
    assert jcs(-0.0) == b"0"
    assert jcs(-0.0) == jcs(0.0), "RFC 8785 makes -0.0 and 0.0 one value"
    assert I.canonical_bytes(-0.0) != I.canonical_bytes(0.0), \
        "TRAAVIIS makes them two different ids"


def test_c11_astral_plane_keys_sort_differently():
    """The UTF-16 rule: a key at U+10000+ sorts *before* U+E000-U+FFFF.

    Because an astral code point is a surrogate pair whose lead unit is
    D800-DBFF, RFC 8785 §3.2.3 places it below U+E000 — while Python's
    `sort_keys=True` compares code points and places it above U+FFFF. Two
    non-ASCII keys are needed to see it, one astral and one in U+E000-U+FFFF;
    an astral key next to ASCII keys sorts the same in both schemes.
    """
    doc = {chr(0x1F600): 1, chr(0xFB33): 2}
    assert not agree(doc), both(doc)
    assert sorted(doc) == [chr(0xFB33), chr(0x1F600)], "Python: code point order"
    order = sorted(doc, key=_utf16_sortkey)
    assert order == [chr(0x1F600), chr(0xFB33)], "RFC 8785: UTF-16 code unit order"
    # An astral key with only ASCII neighbours does *not* diverge.
    assert agree({chr(0x1F600): 1, "z": 2})


def test_c12_exponent_notation_thresholds_diverge():
    """Python and ECMAScript switch to exponent form at different magnitudes.

    ECMAScript uses plain form for `n <= 21` and down to `n > -6`; Python's
    `repr` switches at `1e16` and at `1e-5`. So the whole band `[1e16, 1e21)`
    and the band `[1e-6, 1e-4)` render differently. Also, Python zero-pads a
    one-digit exponent (`1e-07`), ECMAScript does not (`1e-7`).
    """
    cases = [
        (1e16, b"1e+16", b"10000000000000000"),
        (1e20, b"1e+20", b"100000000000000000000"),
        (1e-5, b"1e-05", b"0.00001"),
        (1e-6, b"1e-06", b"0.000001"),
        (1e-7, b"1e-07", b"1e-7"),
        (1.5e-8, b"1.5e-08", b"1.5e-8"),
    ]
    for x, py, want in cases:
        assert I.canonical_bytes(x) == py, (x, I.canonical_bytes(x))
        assert jcs(x) == want, (x, jcs(x))
    # ...and the thresholds where they DO agree, so the band is pinned on both sides
    for x in (1e21, 1e22, 1e23, 1e-4, 1e-10, 1e-11, 5e-324,
              1.7976931348623157e308):
        assert agree({"x": x}), (x, both({"x": x}))


def test_c13_a_live_shipped_episode_id_would_move():
    """The divergence is not hypothetical; it is already in the repository.

    `examples/eval-one/episodes/episode-42d0.../receipt.json` carries
    `"reward": 1.0`. Its declared `episode-` id recomputes exactly under
    `canonical_bytes` (so it is a live id, not a stale one) and would be a
    *different* id under RFC 8785. This single assertion is the whole cost
    argument for option (a) in the audit report.
    """
    if not os.path.exists(LIVE_RECEIPT):
        raise Skip("the example episode is not in this checkout")
    with open(LIVE_RECEIPT, encoding="utf-8") as fh:
        receipt = json.load(fh)
    assert receipt["reward"] == 1.0 and isinstance(receipt["reward"], float)
    declared = receipt["episode_id"]
    assert I.episode_id(receipt) == declared, "fixture drifted; law is vacuous"
    projected = {k: receipt[k] for k in I._EPISODE_IDENTITY_KEYS if k in receipt}
    under_rfc = "episode-" + hashlib.sha256(jcs(projected)).hexdigest()
    assert under_rfc != declared
    assert under_rfc == (
        "episode-0207bbfcb1a425a6e65d830ce2701d801834b9071bf8b907165a5ef2707253e4"
    ), under_rfc


def test_c14_large_integers_diverge_two_different_ways():
    """Python ints are exact and unbounded; RFC 8785 numbers are binary64.

    Two separate failures. Above 2**53 the value itself is not expressible as a
    double, so RFC 8785 §3.1's I-JSON rule puts it outside the domain entirely.
    And at or above 1e21 even an exactly-representable integer renders in
    exponent form under ECMAScript while Python writes all the digits.
    Reachable only through a byte count or a nanosecond timestamp, neither of
    which is in an identity allowlist today — but nothing forbids one.
    """
    assert I.canonical_bytes(10 ** 22) == b"10000000000000000000000"
    assert jcs(10 ** 22) == b"1e+22"
    assert I.canonical_bytes(2 ** 63) == b"9223372036854775808"
    assert jcs(2 ** 63) == b"9223372036854776000", "binary64 cannot hold 2**63 + 1"
    # exactly-representable and below 1e21: still agrees
    assert agree({"n": 10 ** 20})
    assert agree({"n": 2 ** 53})


def test_c15_nan_and_infinity_are_refused_instead_of_hashed():
    """No id is minted over bytes that are not JSON. This is the closed one.

    RFC 8785 §3.2.2.3 says NaN and Infinity MUST terminate a compliant
    implementation. Python's `json.dumps` defaults to `allow_nan=True` and emits
    the bare tokens `NaN` / `Infinity`, which no strict JSON parser will read
    back — so a reward spec with a NaN weight used to produce a well-formed-
    looking `rew-...` over a document that cannot survive a round trip. That is
    a robustness hole independent of the conformance question: a content address
    whose preimage is unreadable addresses nothing.

    It is the one divergence closed rather than characterized, because it is the
    one whose repair moves no id (C28, C29). The law here is the *behaviour*:
    refuse, do not hash. `legacy_canonical_bytes` is used to state precisely what
    changed — the old serializer really did emit those tokens, so this law would
    be vacuous without it.
    """
    # what the old serializer did -- the thing being removed, still executable
    assert legacy_canonical_bytes(float("nan")) == b"NaN"
    assert legacy_canonical_bytes(float("inf")) == b"Infinity"
    assert legacy_canonical_bytes(float("-inf")) == b"-Infinity"
    # ...and what it now does instead
    for bad in (float("nan"), float("inf"), float("-inf")):
        try:
            I.canonical_bytes(bad)
        except I.IdentityError:
            pass
        else:
            raise AssertionError("expected a refusal for %r" % bad)
        try:
            jcs(bad)
        except JCSError:
            pass
        else:
            raise AssertionError("RFC 8785 must refuse %r" % bad)
    # the specific hazard: no `rew-` can be minted over a NaN weight any more
    spec = {"signals": {"t": {"weight": float("nan")}}}
    try:
        I.reward_id(spec)
    except I.IdentityError:
        pass
    else:
        raise AssertionError("a rew- was minted over unparseable bytes")
    # and the reason it mattered: those bytes were never JSON
    try:
        json.loads(legacy_canonical_bytes({"w": float("nan")}).decode(),
                   parse_constant=_reject)
    except ValueError:
        pass
    else:
        raise AssertionError("expected strict JSON to reject the emitted bytes")


def _reject(_tok):
    raise ValueError("not JSON")


def test_c16_int_and_float_weights_are_two_ids_here_and_one_id_under_rfc():
    """`"weight": 1` and `"weight": 1.0` are the same JSON number everywhere else.

    RFC 8785 has no integer type — every number is a binary64 — so the two
    documents canonicalize identically. TRAAVIIS distinguishes them, so a
    reward spec hand-edited from `1.0` to `1` silently becomes a different
    environment. That is a divergence that hurts *today*, with no RFC adoption
    involved: it is an authoring hazard in the current scheme.
    """
    as_int = {"signals": {"tests": {"verifier": "v", "weight": 1}}}
    as_float = {"signals": {"tests": {"verifier": "v", "weight": 1.0}}}
    assert I.reward_id(as_int) != I.reward_id(as_float)
    assert jcs(as_int) == jcs(as_float)


# --------------------------------------------------------------------------- #
# C17-C20  what constrains the reachable domain (answer: nothing)             #
# --------------------------------------------------------------------------- #

def test_c17_snapshot_file_keys_are_arbitrary_unicode_from_the_filesystem():
    """The astral key divergence is reachable through the real `build_snapshot`.

    `files` and `file_modes` are keyed by relative POSIX paths taken straight
    from `os.path.relpath` over a real repository. `paths.safe_relposix` rejects
    absolute paths, drive letters and `..` segments — it says nothing about
    character sets. So a subject repository containing one file named with an
    emoji and one named with a U+E000-U+FFFF character produces a `snap-` that
    RFC 8785 would compute differently, with no error anywhere on the path.
    """
    tmp = tempfile.mkdtemp(prefix="trvs-canonical-",
                           dir=os.environ.get("TMPDIR") or None)
    try:
        for name in (chr(0x1F600) + ".md", chr(0xFB33) + ".md", "README.md"):
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                fh.write("x\n")
        snap = S.build_snapshot(tmp)
        projected = {k: v for k, v in snap.items() if k != "snapshot_id"}
        assert I.canonicalize_snapshot(snap) != jcs(projected), \
            "expected the astral filename to reorder the files map"
        assert I.snapshot_id(snap) != "snap-" + hashlib.sha256(
            jcs(projected)).hexdigest()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_c18_the_numeric_domain_is_enforced_and_nothing_else_is():
    """Exactly one domain check exists. Naming which one is the whole point.

    The predecessor of this law asserted *absence* — that `canonical_bytes`
    forwarded straight to `json.dumps` with no validation of any kind — and said
    that if enforcement were ever added it should be rewritten to assert the new
    check instead. `allow_nan=False` is that enforcement, so this is the
    rewrite.

    It is deliberately two-sided. The first half pins the check that now exists,
    so removing it goes red. The second half pins that it is the *only* one: no
    key charset test, no numeric range test, no I-JSON `2**53` bound, no
    lone-surrogate check of our own. Those absences are what keep C11, C14 and
    C17 true, and a later "while I'm here" hardening would move ids without
    anybody deciding to.
    """
    import inspect
    src = inspect.getsource(I.canonical_bytes)
    body = "".join(src.split('"""', 2)[-1].split())
    assert body.startswith(
        'try:text=json.dumps(obj,sort_keys=True,separators=(",",":"),'
        'ensure_ascii=False,allow_nan=False,)'
    ), body
    assert body.endswith('returntext.encode("utf-8")'), body
    assert "allow_nan=False" in body, "the NaN guard vanished; ids are at risk"
    # the guard is the only domain restriction -- these would each move ids
    for token in ("isascii", "unicodedata", "surrogate", "2**53",
                  "9007199254740992", "sort(", "unicode_escape"):
        assert token not in body, "unexpected domain check %r appeared" % token
    # and it still accepts every *finite* thing a Python dict can hold
    assert I.canonical_bytes({chr(0x1F600): 1e308, chr(0xD7FF): -0.0})
    # while the one refusal is typed, not a bare stdlib leak
    try:
        I.canonical_bytes({chr(0x1F600): float("inf")})
    except I.IdentityError as ex:
        assert ex.code == I.CANONICAL_NON_FINITE, ex.code
    else:
        raise AssertionError("expected a refusal")


def test_c19_exactly_one_shipped_document_diverges():
    """Quantifies the migration cost from the artifacts that actually exist.

    Walking every id-bearing JSON document under `examples/` and typing it by
    its own `*_version` field: twelve documents carry an id, and exactly one --
    the episode receipt of C13, on its `reward: 1.0` -- is on the diverging
    side. So "conform and migrate" costs one shipped id today, not a corpus.

    The law asserts the diverging *set*, not a count, so that adding a
    conforming example never fails it while adding a diverging one always does.
    """
    def drop(field):
        return lambda d: {k: v for k, v in d.items() if k != field}

    # Typed by the document's own `*_version` field, so a receipt's *reference*
    # to a `reward_id` is never mistaken for a reward document.
    versions = {
        "snapshot_version": (I.canonicalize_snapshot, "snapshot_id",
                             drop("snapshot_id")),
        "reward_spec_version": (I.canonicalize_reward, "reward_id",
                                drop("reward_id")),
        "task_spec_version": (I.canonicalize_task, "task_id", drop("task_id")),
        "finding_version": (I.canonicalize_finding, "finding_id",
                            drop("finding_id")),
        "trace_version": (I.canonicalize_trace, "trace_id", lambda d: {
            "trace_version": d.get("trace_version"),
            "events": [{k: e[k] for k in I._TRACE_EVENT_KEYS if k in e}
                       for e in d.get("events", [])]}),
        "episode_version": (I.canonicalize_episode, "episode_id",
                            lambda d: {k: d[k] for k in I._EPISODE_IDENTITY_KEYS
                                       if k in d}),
        "environment_version": (I.canonicalize_environment, "env_id",
                                lambda d: {k: d[k]
                                           for k in I._ENVIRONMENT_IDENTITY_KEYS
                                           if k in d}),
        "bundle_version": (I.canonicalize_bundle, "bundle_id",
                           drop("bundle_id")),
    }
    found = 0
    diverging = set()
    for dirpath, _dirs, names in os.walk(os.path.join(REPO, "examples")):
        for name in sorted(names):
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (ValueError, OSError):
                continue
            if not isinstance(doc, dict):
                continue
            for vfield, (canon, idfield, project) in versions.items():
                if vfield not in doc or idfield not in doc:
                    continue
                found += 1
                if canon(doc) != jcs(project(doc)):
                    diverging.add((os.path.relpath(path, REPO), idfield))
    if found == 0:
        raise Skip("no example artifacts in this checkout")
    assert found >= 12, found
    assert diverging == {(os.path.relpath(LIVE_RECEIPT, REPO), "episode_id")}, \
        sorted(diverging)


def test_c20_the_conforming_subset_is_a_real_and_statable_subset():
    """A document restricted to the stated domain hashes identically both ways.

    This is the law that makes option (b) and option (c) in the report
    coherent: there *is* a subset over which "SHA-256 over RFC 8785 canonical
    JSON" is a true sentence. The subset is: string keys with no code point at
    or above U+10000; numbers that are either Python ints of magnitude below
    2**53, or floats satisfying `conforming_float`; no NaN or Infinity; strings
    with no lone surrogates.
    """
    doc = {
        "episode_version": "residency.episode.v1",
        "instructions": "cite " + chr(0x5D0) + " and " + chr(0x20AC),
        "signals": {"tests": {"weight": 0.3}, "patch": {"weight": 0.25}},
        "exit_code": 0,
        "attempts": 12,
        "ratio": 0.6666666666666666,
        "tiny": 1e-11,
        "huge": 1e23,
        "flags": [True, False, None],
    }
    assert agree(doc), both(doc)


# --------------------------------------------------------------------------- #
# C21-C24  the yardstick itself                                               #
# --------------------------------------------------------------------------- #

def test_c21_reference_matches_the_rfc_8785_appendix_a_sorting_sample():
    """Without this, every divergence law above is an unfounded assertion.

    Appendix A's sample object uses exactly the keys that discriminate the two
    orderings: U+20AC, U+000D, U+FB33, "1", U+1F600, U+0080, U+00F6. The RFC's
    stated canonical order puts U+1F600 *before* U+FB33.
    """
    keys = [chr(c) for c in (0x20AC, 0x0D, 0xFB33, 0x31, 0x1F600, 0x80, 0xF6)]
    want = [chr(c) for c in (0x0D, 0x31, 0x80, 0xF6, 0x20AC, 0x1F600, 0xFB33)]
    assert sorted(keys, key=_utf16_sortkey) == want
    assert sorted(keys) != want, "code-point order must not accidentally match"


def test_c22_reference_matches_the_rfc_8785_appendix_b_number_vectors():
    """The RFC's own number table, by IEEE-754 bit pattern.

    Three of these eight vectors are ones Python gets differently, which is a
    neat statement of the problem: the RFC ships a conformance table and we
    fail three rows of it.
    """
    rows = [
        ("0000000000000000", "0"),
        ("8000000000000000", "0"),
        ("0000000000000001", "5e-324"),
        ("7fefffffffffffff", "1.7976931348623157e+308"),
        ("4340000000000000", "9007199254740992"),
        ("44b52d02c7e14af6", "1e+23"),
        ("41b3de4355555555", "333333333.3333333"),
        ("43143ff3c1cb0959", "1424953923781206.2"),
    ]
    disagreeing = 0
    for hexbits, want in rows:
        x = double(hexbits)
        assert _es_number_to_string(x) == want, (hexbits, _es_number_to_string(x))
        if json.dumps(x) != want:
            disagreeing += 1
    assert disagreeing == 3, disagreeing


def test_c23_reference_number_rendering_is_self_consistent_and_round_trips():
    """Every rendering the reference emits must parse back to the same double.

    A shortest-round-trip serializer that does not round-trip is the one bug
    that would make the whole battery silently wrong, because it would report
    divergences that are really its own errors.
    """
    xs = [0.1, 0.5, 1.0, -1.0, 1e16, 1e21, 1e-7, 5e-324,
          1.7976931348623157e308, 333333333.3333333, 1424953923781206.2,
          2.0 ** 53, 0.30000000000000004, 2.220446049250313e-16]
    for e in range(-300, 300, 7):
        xs.append(float("1.234567890123456e%d" % e))
    for x in xs:
        rendered = _es_number_to_string(x)
        assert float(rendered) == x, (x, rendered)
        assert "e+0" not in rendered and "e-0" not in rendered, \
            "ECMAScript never zero-pads an exponent: %r" % rendered


def test_c24_the_conforming_float_predicate_is_exact_on_a_sampled_scan():
    """`conforming_float` must be a measurement, not a story about the code.

    The report's domain statement rests on this predicate, so it is re-derived
    here against the real serializers over random bit patterns plus a sweep of
    every decimal exponent. A mispredicton in either direction fails the law.
    """
    import random
    rng = random.Random(20260804)
    xs = []
    while len(xs) < 20000:
        f = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if f == f and abs(f) != float("inf"):
            xs.append(f)
    for e in range(-320, 305):
        for m in ("1", "1.5", "9.999", "1.234567890123456"):
            try:
                xs.append(float(m + "e" + str(e)))
            except (ValueError, OverflowError):
                pass
    for i in range(4000):
        xs += [float(i), i / 3.0, i * 1e-7, i * 1e-5]
    xs = [x for x in xs if x == x and abs(x) != float("inf")]
    wrong = [x for x in xs
             if conforming_float(x) != (json.dumps(x) == _es_number_to_string(x))]
    assert not wrong, [(repr(x), json.dumps(x), _es_number_to_string(x))
                       for x in wrong[:10]]
    # and the scan must actually contain both outcomes, or it proves nothing
    outcomes = {conforming_float(x) for x in xs}
    assert outcomes == {True, False}


def test_c25_lone_surrogates_are_refused_by_both_schemes():
    """The one place Python's stricter behaviour lands on the RFC's side.

    RFC 8785 §3.2.2.2 requires a compliant implementation to terminate on a
    lone surrogate. `canonical_bytes` does terminate — not by a check, but
    because `.encode("utf-8")` refuses to encode one. The outcome is right; the
    reason is incidental, which is worth recording since a future switch to
    `errors="surrogatepass"` would silently remove the protection.
    """
    for doc in ({"k": "\ud800"}, {"\ud800": 1}, {"k": "a\udfffb"}):
        try:
            I.canonical_bytes(doc)
        except UnicodeEncodeError:
            pass
        else:
            raise AssertionError("expected a refusal for %r" % (doc,))
        try:
            jcs(doc)
        except JCSError:
            pass
        else:
            raise AssertionError("RFC 8785 must refuse %r" % (doc,))


def test_c26_non_string_property_names_cannot_reach_a_mixed_sort():
    """A dict with a non-string key is either coerced or refused, never ordered.

    Python coerces an all-int-keyed dict to string names (so `{1: x}` and
    `{"1": x}` collide), and raises `TypeError` as soon as key types are mixed
    under `sort_keys=True`. Neither outcome is a silent reordering, so this is
    not an identity hazard — but the coercion means an int key is *not* an
    error, which the report notes as the one remaining unguarded shape.
    """
    assert I.canonical_bytes({1: "a"}) == I.canonical_bytes({"1": "a"})
    try:
        I.canonical_bytes({1: "a", "b": 2})
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError for mixed key types")
    try:
        jcs({1: "a"})
    except JCSError:
        pass
    else:
        raise AssertionError("RFC 8785 must refuse a non-string property name")


# --------------------------------------------------------------------------- #
# C27-C30  the NaN refusal: its shape, and the proof it cost no id            #
# --------------------------------------------------------------------------- #

def test_c27_the_refusal_is_typed_and_reaches_every_rung():
    """A refusal a caller cannot tell apart from a crash is not a refusal.

    Three things have to hold. It must be *typed* — `IdentityError` carrying the
    stable code `CANONICAL_NON_FINITE`, in the `code`/`message`/`detail` shape
    every typed refusal in this codebase uses, so "your document contained a
    non-finite number" is distinguishable from every other failure. (The class
    is `identity`'s own rather than one borrowed from a module above it:
    `identity` is the leaf of the import graph, and a layering law in
    `test_kernel.py` holds it there by name.) It must **still be a
    `ValueError`**, because that is what `json.dumps` raised for this input
    class and narrowing it would change which failures existing `except` clauses
    absorb. And it must hold at *every* rung, not just at `canonical_bytes`: a
    rung that projected its way around the guard would mint the very id this
    exists to prevent.

    The `detail["path"]` assertion is not decoration. The message has to name
    *which* field was refused, or the operator's next question ("where?") has no
    answer but a full-document search.
    """
    ex = None
    try:
        I.canonical_bytes({"signals": {"t": {"weight": float("nan")}}})
    except I.IdentityError as caught:
        ex = caught
    assert ex is not None, "expected a refusal"
    assert ex.code == I.CANONICAL_NON_FINITE == "CANONICAL_NON_FINITE"
    assert isinstance(ex, ValueError), "must stay catchable as ValueError"
    assert ex.detail["path"] == "$.signals.t.weight", ex.detail
    assert "[CANONICAL_NON_FINITE]" in str(ex), str(ex)
    assert "non-finite" in ex.message, ex.message

    # every rung, with the poison placed where that rung actually looks
    rungs = [
        (I.snapshot_id, {"snapshot_version": "v", "files": {"a": float("inf")}}),
        (I.finding_id, {"finding_version": "v", "claims": [float("nan")]}),
        (I.patch_id, {"patch_version": "v", "diff": "x", "n": float("inf")}),
        (I.reward_id, {"signals": {"t": {"weight": float("nan")}}}),
        (I.task_id, {"task_spec_version": "v", "budget": float("inf")}),
        (I.trace_id, {"trace_version": "v",
                      "events": [{"exit_code": float("nan")}]}),
        (I.episode_id, {"episode_version": "v", "reward": float("nan")}),
        (I.environment_id, {"environment_version": "v",
                            "splits": {"train": float("inf")}}),
        (I.bundle_id, {"bundle_version": "v", "members": [float("-inf")]}),
    ]
    for fn, doc in rungs:
        try:
            fn(doc)
        except I.IdentityError as caught:
            assert caught.code == I.CANONICAL_NON_FINITE, (fn.__name__, caught)
        else:
            raise AssertionError("%s minted an id over a non-finite number"
                                 % fn.__name__)

    # a *different* ValueError from json.dumps must not be relabelled as this one
    cycle = {}
    cycle["self"] = cycle
    try:
        I.canonical_bytes(cycle)
    except I.IdentityError:
        raise AssertionError("a circular reference was mislabelled as non-finite")
    except ValueError as caught:
        assert "ircular" in str(caught), str(caught)
    # and a non-JSON *type* is still a TypeError, as it always was
    try:
        I.canonical_bytes({"x": object()})
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError for an unserializable value")


def test_c28_the_refusal_is_byte_identical_over_the_whole_shipped_corpus():
    """The load-bearing one: `allow_nan=False` changed no bytes that exist.

    This is the entire safety argument for landing the change, so it is written
    as a measurement over real artifacts rather than as a restatement of the
    diff. Every JSON document in the repository — `examples/`, `legacy/`, and
    the *published* packets inside `dist/*.zip`, whose ids other people already
    hold — is re-serialized both ways and compared byte for byte.

    The counted-corpus assertions matter as much as the equality. A walker that
    silently found nothing would pass this law while proving nothing at all, so
    the document count and the presence of the live receipt are asserted too.

    Those guards were first written against the development tree, and demanding
    `dist/` unconditionally made this law fail inside the release packet — which
    is where it matters most. `tools/build_packet.py` deliberately omits `dist/`
    (a packet does not carry older packets), so the strong form asserted the
    shape of one checkout rather than the property under test. The property is
    "every document that exists *here* is unchanged", so the corpus floor is now
    what a standalone packet actually ships, and the `dist/` clause applies only
    when this checkout has published packets to walk. In the source tree that is
    still the full 73-document corpus; in an extracted packet it is the ~21 that
    ship, and both are honest about what they measured.
    """
    checked = 0
    labels = []
    for label, doc in corpus_documents():
        assert I.canonical_bytes(doc) == legacy_canonical_bytes(doc), label
        checked += 1
        labels.append(label)
    if checked == 0:
        raise Skip("no JSON artifacts in this checkout")
    assert checked >= 20, checked
    assert any(l.endswith("receipt.json") for l in labels), "no receipt walked"
    assert any(l.startswith("examples/") for l in labels), labels[:5]

    # Published packets hold ids other people already have, so when this
    # checkout has any, walking them is not optional.
    published = [n for n in os.listdir(os.path.join(REPO, "dist"))
                 if n.endswith(".zip")] if os.path.isdir(
                     os.path.join(REPO, "dist")) else []
    if published:
        assert any(l.startswith("dist/") and ".zip!" in l for l in labels), \
            "this checkout ships %d packet(s) and none was walked" % len(published)
        assert checked >= 70, checked


def test_c29_no_id_in_the_corpus_moved():
    """Bytes are the mechanism; ids are the thing anyone actually holds.

    C28 compares serializer output. This compares the *ids* — recomputing each
    document's id through the real `identity.*_id` rung twice, once on the
    current serializer and once with `canonical_bytes` rebound to the old one.
    Going through the real rungs is deliberate: it exercises the projections
    (`_EPISODE_IDENTITY_KEYS`, the trace event filter, the `_drop` calls) rather
    than a hand-written restatement of them, so a rung that somehow depended on
    the serializer's NaN behaviour would show up here.

    It also re-verifies every *declared* id against its recomputation, and pins
    the live `episode-42d0...` explicitly. Without that, a corpus of documents
    whose ids never verified in the first place would satisfy "nothing moved"
    trivially.
    """
    minted = verified = 0
    for label, doc in corpus_documents():
        if not isinstance(doc, dict):
            continue
        for vfield, idfield, idfn in ID_KINDS:
            if vfield not in doc:
                continue
            now = idfn(doc)
            with under_legacy_serializer():
                before = idfn(doc)
            assert now == before, (label, idfield, now, before)
            minted += 1
            declared = doc.get(idfield)
            if declared is not None:
                assert declared == now, (label, idfield, declared, now)
                verified += 1
    if minted == 0:
        raise Skip("no id-bearing artifacts in this checkout")
    assert minted >= 12, minted
    assert verified >= 12, verified
    # the live one, named, so the law cannot pass on a corpus of strangers
    if os.path.exists(LIVE_RECEIPT):
        with open(LIVE_RECEIPT, encoding="utf-8") as fh:
            receipt = json.load(fh)
        assert I.episode_id(receipt) == receipt["episode_id"] == (
            "episode-42d0bb07e5f83e9e57518bf5cd3717e2a1e3aa45aa5821e36ac19206a3d73299"
        )
        with under_legacy_serializer():
            assert I.episode_id(receipt) == receipt["episode_id"]


def test_c30_the_refusal_is_byte_identical_over_every_finite_input():
    """The corpus is what exists; this is what *could* exist and still is safe.

    C28 proves nothing moved today. It cannot prove nothing moves tomorrow,
    because tomorrow's documents are not in the corpus. `allow_nan=False`
    changes `json.dumps` for exactly three input values and no others, so the
    general statement is available and worth having: over every finite float,
    including the extremes and the whole diverging band of C12, and over
    documents mixing ints, strings, nulls, astral keys and deep nesting, the two
    serializers agree byte for byte.

    Stated the other way: the *only* documents whose treatment changed are the
    ones that were never JSON. Which is why this repair, alone among the eight
    divergences, needed no id migration.
    """
    import random
    rng = random.Random(20260804)
    finite = [0.0, -0.0, 1.0, -1.0, 0.1, 0.25, 1e16, 1e21, 1e-7, 5e-324,
              1.7976931348623157e308, -1.7976931348623157e308,
              333333333.3333333, 2.0 ** 53, 0.30000000000000004]
    while len(finite) < 5000:
        f = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if f == f and abs(f) != float("inf"):
            finite.append(f)
    for x in finite:
        assert I.canonical_bytes(x) == legacy_canonical_bytes(x), repr(x)
        assert I.canonical_bytes({"v": [x]}) == \
            legacy_canonical_bytes({"v": [x]}), repr(x)
    # ...and over shapes, not just scalars
    shapes = [
        {}, [], {"": 1}, {"a": {}}, {"a": [[[{"b": None}]]]},
        {"n": 10 ** 22, "i": 0, "neg": -7, "t": True, "f": False},
        {chr(0x1F600): "e", chr(0xFB33): 1, "z": [1, "2", None]},
        {"deep": {"a": {"b": {"c": {"d": [1.5, {"e": 2e-9}]}}}}},
        {"s": "".join(chr(c) for c in (0x08, 0x0A, 0x1F, 0x22, 0x5C, 0x7F))},
    ]
    for doc in shapes:
        assert I.canonical_bytes(doc) == legacy_canonical_bytes(doc), doc
    # the sole difference, restated as the exhaustive list it is
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert legacy_canonical_bytes(bad) in (b"NaN", b"Infinity", b"-Infinity")
        try:
            I.canonical_bytes(bad)
        except I.IdentityError:
            pass
        else:
            raise AssertionError("expected a refusal for %r" % bad)


# --------------------------------------------------------------------------- #
# standalone runner (zero deps)                                               #
# --------------------------------------------------------------------------- #

def _main():
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    passed = skipped = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print("PASS  %s" % name)
        except Skip as exc:
            skipped += 1
            print("SKIP  %s (%s)" % (name, exc))
        except AssertionError as exc:
            failed += 1
            print("FAIL  %s: %s" % (name, exc))
    print("\n%d passed, %d skipped, %d failed" % (passed, skipped, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
