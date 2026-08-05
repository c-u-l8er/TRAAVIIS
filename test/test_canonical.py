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

**Two divergences were closed, and only because closing them moved nothing.**
Both were cases where the divergence was not a conformance preference but a
wrong answer, and both changed behaviour only for inputs that do not occur.

*The numeric domain.* RFC 8785 §3.2.2.3 requires a compliant implementation to
*terminate* on NaN or Infinity; Python's default emitted the bare tokens `NaN` /
`Infinity`, which are not JSON, so `canonical_bytes` could mint an id over a
preimage no parser will read back. `allow_nan=False` closes it (C15, C18, C27)
and the *costlessness* is itself a checked law (C28-C30).

*The key domain.* Worse, and closed second. RFC 8785 inherits I-JSON's rule that
object member names are strings; Python instead **coerces** `int` / `float` /
`bool` / `None` keys, so `{1: "x"}` and `{"1": "x"}` minted one id between them.
Not an anonymous error like the NaN case — a silent wrong answer, in the one
function whose whole claim is that identity distinguishes content. A key-type
pre-walk closes it (C18, C26, C31-C34) and the costlessness is again measured
(C35-C36), with the vacuity of all of it checked by removing the guard (C37).
The status is worth stating exactly: it was a **reachable latent collision, not
a live one**. No production call path and no corpus member supplies a
non-string key, and `json.loads` cannot produce one; what was true is that the
public boundary accepted them.

Every other divergence changes bytes for inputs that do occur and stays
characterized, not fixed.

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
  costly when it is not, either way on nobody's measurement (C28-C30, C35-C36);
- two documents that every other JSON tool considers identical could carry two
  different ids (C16);
- two documents that *no* JSON tool considers identical could carry the **same**
  id, silently, because the serializer rewrote a key rather than refusing it
  (C26, C31-C34) -- the same failure as C16 with the sign flipped, and the only
  one of these where the wrong answer never announces itself;
- a law in this file could pass without the code it names being present at all
  (C37).

What was actually read to write this: RFC 8785 §3.1 (I-JSON restrictions),
§3.2.2.2 (string escaping, lone surrogates), §3.2.2.3 (numbers, NaN/Infinity),
§3.2.3 (UTF-16 code-unit property sorting), Appendix A (sorting sample) and
Appendix B (number test vectors); RFC 7493 §2.2 and RFC 8259 §6 (the
interoperable integer range `[-(2**53)+1, (2**53)-1]`, which is C49's bound);
and — as of C49-C52 — **ECMA-262 itself**, 10th edition (ES2019) §7.1.12.1,
which is the section and edition RFC 8785 §3.2.2.3 names, plus ES5.1 §9.8.1 and
the current tc39 living draft for comparison.

That last one is a change of standing worth flagging, because the opposite used
to be recorded here. `Number::toString` was previously *not* read as prose —
WebFetch declined to reproduce it — so `_es_number_to_string` below was
differential-tested against V8 (node v25.2.1) over 76,926 double bit patterns
instead, and the exponent thresholds `21` and `-6` were V8-derived rather than
quoted. They are quoted now: ES2019 §7.1.12.1 steps 6, 7 and 8 carry both
constants literally. C51 adds the executable half — 484 boundary vectors from a
**second oracle that is not V8** — and records what that measured, including
where the second oracle and V8 disagreed.

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

    Originally written from the shape of the ECMA-262 algorithm (find k, n, s
    with s the shortest decimal that round-trips, then pick one of five
    renderings by where n falls) and *validated by execution* against V8; the
    branch constants have since been confirmed against ES2019 §7.1.12.1 steps
    6/7/8 — see the module docstring. Deliberately **not** rewritten to follow
    the citation, because this function's job is to be a second reading of the
    same specification; making it a transcription of production's reading is
    exactly what C42 exists to prevent. `repr(float)` supplies the same
    shortest-round-trip digits ES uses, so only the rendering rules had to be
    reimplemented.
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


@contextlib.contextmanager
def without_key_validation():
    """Run the real rungs with the key-type pre-walk removed, and nothing else.

    This is the *serializer as it stood before the key check*, obtained by
    deletion rather than by transcription. `legacy_canonical_bytes` above had to
    be a copy, because `allow_nan=False` is an argument to a call and there is
    no way to reach around it; the key check is a separate statement calling a
    module-global function, so neutering that global removes exactly the check
    under test and leaves every other byte of `canonical_bytes` running.

    That distinction matters for what the laws built on it are worth. A copied
    fixture can drift from the thing it claims to model, and C28-C30 carry a
    standing warning not to tidy it. This one cannot drift: it *is* the shipped
    code, minus one guard. So C35-C36 ("no id moved") and C37 ("the laws are not
    vacuous") are measuring the real serializer both times.
    """
    original = I._find_bad_key
    I._find_bad_key = lambda obj: None
    try:
        yield
    finally:
        I._find_bad_key = original


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


def test_c18_three_domain_checks_are_enforced_and_nothing_else_is():
    """Exactly three domain checks exist. Naming which three is the point.

    The first version of this law asserted *absence* — that `canonical_bytes`
    forwarded straight to `json.dumps` with no validation of any kind — and said
    that if enforcement were ever added it should be rewritten to assert the new
    check instead. It has now been rewritten twice: once for `allow_nan=False`
    (the numeric domain, C27-C30) and once for the key-type pre-walk (the key
    domain, C31-C37).

    It stays deliberately two-sided. The first half pins the checks that exist,
    so removing either goes red. The second half pins that they are the *only*
    ones: no key charset test, no numeric range test, no I-JSON `2**53` bound,
    no lone-surrogate check of our own, no key sorting of our own. Those
    absences are what keep C11, C14 and C17 true, and a later "while I'm here"
    hardening would move ids without anybody deciding to.

    The forbidden-token scan covers the pre-walk as well as `canonical_bytes`,
    because a domain check smuggled into the walk restricts the domain exactly
    as much as one written inline — and the walk is now the more inviting place
    to put one, since it already visits every node.
    """
    import inspect
    src = inspect.getsource(I.canonical_bytes)
    body = "".join(src.split('"""', 2)[-1].split())
    # check 1: the key domain, enforced *before* anything is serialized, so the
    # collision cannot be minted and then regretted
    assert body.startswith("bad_key=_find_bad_key(obj)ifbad_keyisnotNone:"), body
    assert "CANONICAL_KEY_TYPE," in body, "the key guard vanished; ids collide"
    assert body.index("_find_bad_key") < body.index("json.dumps"), \
        "the key check must run before serialization, not after"
    # check 2: the numeric domain, unchanged, still on the same dumps call
    assert ('try:text=json.dumps(obj,sort_keys=True,separators=(",",":"),'
            'ensure_ascii=False,allow_nan=False,)') in body, body
    assert "allow_nan=False" in body, "the NaN guard vanished; ids are at risk"
    # check 3: the encoding domain, on the same encode call that always raised
    assert 'try:returntext.encode("utf-8")exceptUnicodeEncodeErrorasex:' in body, \
        body
    assert "CANONICAL_ENCODING," in body, "the encoding guard vanished"
    # those three are the only domain restrictions -- these would each move ids
    walk = "".join(inspect.getsource(I._find_bad_key).split('"""', 2)[-1].split())
    for token in ("isascii", "unicodedata", "2**53",
                  "9007199254740992", "sort(", "unicode_escape"):
        assert token not in body, "unexpected domain check %r appeared" % token
        assert token not in walk, "unexpected domain check %r in the walk" % token
    # "surrogate" used to be on that list, to catch a hand-rolled surrogate
    # scan. Check 3 makes the word legitimate, so the guard moves to the thing
    # that actually mattered: the encoding domain must be defined by the
    # *encoder*, not by a codepoint range we invented and could get wrong.
    judge = "".join(
        inspect.getsource(I._unencodable_index).split('"""', 2)[-1].split())
    assert '.encode("utf-8")' in judge, judge
    for token in ("0xD800", "0xDFFF", "55296", "57343", "surrogatepass"):
        assert token not in judge, \
            "the encoding domain was hand-rolled as a range check: %r" % token
    # and it still accepts every *finite*, *string-keyed* thing a dict can hold
    assert I.canonical_bytes({chr(0x1F600): 1e308, chr(0xD7FF): -0.0})
    # while both refusals are typed, not bare stdlib leaks
    for doc, code in (({chr(0x1F600): float("inf")}, I.CANONICAL_NON_FINITE),
                      ({0: chr(0x1F600)}, I.CANONICAL_KEY_TYPE)):
        try:
            I.canonical_bytes(doc)
        except I.IdentityError as ex:
            assert ex.code == code, (ex.code, code)
        else:
            raise AssertionError("expected a refusal for %r" % (doc,))


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
    """The one place Python's stricter behaviour already landed on the RFC's side.

    RFC 8785 §3.2.2.2 requires a compliant implementation to terminate on a
    lone surrogate. `canonical_bytes` always did terminate — not by a check, but
    because `.encode("utf-8")` refuses to encode one. The *outcome* was right
    and the *reason* was incidental, which this law recorded, noting that a
    future switch to `errors="surrogatepass"` would silently remove it.

    That incidental protection is now a deliberate one: the encoder still does
    the detecting, but the failure is caught and re-raised as a typed
    `CANONICAL_ENCODING` refusal naming the path (C38). This law keeps the
    conformance statement — both schemes refuse — and C38 holds the shape.
    """
    for doc in ({"k": "\ud800"}, {"\ud800": 1}, {"k": "a\udfffb"}):
        try:
            I.canonical_bytes(doc)
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_ENCODING, ex.code
        else:
            raise AssertionError("expected a refusal for %r" % (doc,))
        try:
            jcs(doc)
        except JCSError:
            pass
        else:
            raise AssertionError("RFC 8785 must refuse %r" % (doc,))


def test_c26_non_string_property_names_are_refused_by_both_schemes():
    """The second place Python's behaviour now lands on the RFC's side.

    JSON object member names are strings -- RFC 8259 §4 gives the grammar as
    `member = string name-separator value` -- and the reference implementation
    above refuses anything else, tagged I-JSON after RFC 8785 §3.1's restriction
    to that profile. `canonical_bytes`
    used to *disagree*: it coerced an all-non-string-keyed dict to string names,
    so `{1: "a"}` and `{"1": "a"}` minted one id between them. That is the
    collision C31-C37 close, and this law records that the divergence closed —
    both schemes now refuse, and this is the second of the eight characterized
    divergences to be repaired rather than merely characterized (C25 was the
    first, and it was already accidental agreement rather than a repair).

    Kept here, next to C25, because the C1-C26 sequence is the *conformance*
    story and should read as a complete ledger of where the two schemes stand.
    The behavioural detail of the refusal lives in C31-C37.
    """
    for doc in ({1: "a"}, {True: "a"}, {None: "a"}, {1.0: "a"}, {1: "a", "b": 2}):
        try:
            I.canonical_bytes(doc)
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_KEY_TYPE, ex.code
        else:
            raise AssertionError("expected a refusal for %r" % (doc,))
        try:
            jcs(doc)
        except JCSError:
            pass
        else:
            raise AssertionError("RFC 8785 must refuse %r" % (doc,))


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
        # a *real* declared version, so the poison is judged after the scheme
        # dispatch rather than instead of it (C47)
        (I.episode_id, {"episode_version": "traaviis.episode.v1",
                        "reward": float("nan")}),
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
# C31-C37  the key-type refusal: the collision, its shape, and its cost       #
#                                                                             #
# C27-C30 closed a hole where a bad input *errored* anonymously. This closes a #
# strictly worse one: distinct documents minting identical bytes, with no      #
# error at all. The status is precise and should not be inflated in either     #
# direction -- it is a **reachable latent collision, not a live one**. No      #
# production call path and no corpus member supplies a non-string key, and     #
# `json.loads` cannot produce one, so nothing that exists is wrong today. What #
# was true is that the public canonicalization boundary accepted them.        #
# --------------------------------------------------------------------------- #

#: The documents that collided: `(non-string-keyed, the string-keyed document
#: it was indistinguishable from)`. Each pair is a *verified* collision -- see
#: C37, which turns the check off and watches every one of them come back.
KEY_COLLISION_PAIRS = (
    ({1: "x"}, {"1": "x"}),
    ({True: "x"}, {"true": "x"}),
    ({None: "x"}, {"null": "x"}),
    ({1.0: "x"}, {"1.0": "x"}),
    ({1: "a", 2: "b"}, {"1": "a", "2": "b"}),        # the homogeneous multi-key
)


def test_c31_every_demonstrated_key_collision_is_refused():
    """Two documents no reader would call equal must not share a content address.

    This is the whole point of the module, stated as the narrowest possible
    failure: `{1: "x"}` and `{"1": "x"}` are different documents, and before
    this check they minted the same id. Not a wrong error -- a *right-looking
    answer* that was wrong, in the one function whose entire claim is that
    identity distinguishes content. Every other failure this battery guards
    against announces itself; this one did not, which is what makes it worse
    than the NaN hole C27-C30 closed even though that one was also unsound.

    The refusal has to be typed and it has to *locate* the problem, for the same
    reason C27 gives: an operator whose next question is "which key, where?"
    must not have to search the document by hand. So `detail` carries the
    containing object's path, the offending key's type name, and a rendering of
    the key itself.

    That rendering is deliberately not a bare `repr` of whatever was handed in.
    A key is an arbitrary Python object, so its `__repr__` is third-party code
    that may raise or may return megabytes; a refusal that crashes while
    explaining itself has replaced one failure with a worse one. The last two
    clauses hold that.
    """
    for bad, coerced in KEY_COLLISION_PAIRS:
        try:
            I.canonical_bytes(bad)
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_KEY_TYPE == "CANONICAL_KEY_TYPE"
            assert isinstance(ex, ValueError), "must stay catchable as ValueError"
            assert ex.detail["path"] == "$", ex.detail
            assert "[CANONICAL_KEY_TYPE]" in str(ex), str(ex)
            assert "non-string" in ex.message, ex.message
        else:
            raise AssertionError(
                "%r minted an id it shares with %r" % (bad, coerced))
        # the document it used to be confusable with is still perfectly legal
        assert I.canonical_bytes(coerced)

    # the type name is reported, and it is the *key's* type, not the value's
    for bad, want in (({1: "x"}, "int"), ({True: "x"}, "bool"),
                      ({None: "x"}, "NoneType"), ({1.0: "x"}, "float"),
                      ({b"k": "x"}, "bytes"), ({(1, 2): "x"}, "tuple")):
        try:
            I.canonical_bytes(bad)
        except I.IdentityError as ex:
            assert ex.detail["key_type"] == want, (ex.detail, want)
            assert ex.detail["key"] == repr(next(iter(bad))), ex.detail
        else:
            raise AssertionError("expected a refusal for %r" % (bad,))

    # every rung, not just `canonical_bytes`: a rung that projected its way
    # around the guard would mint the very id this exists to prevent
    rungs = [
        (I.snapshot_id, {"snapshot_version": "v", "files": {1: "h"}}),
        (I.finding_id, {"finding_version": "v", "claims": [{0: "c"}]}),
        (I.patch_id, {"patch_version": "v", "diff": "x", "meta": {1: 2}}),
        (I.reward_id, {"signals": {"t": {None: 1}}}),
        (I.task_id, {"task_spec_version": "v", "policy": {True: 1}}),
        (I.trace_id, {"trace_version": "v", "events": [{"cwd": {1: "/"}}]}),
        (I.episode_id, {"episode_version": "traaviis.episode.v1",
                        "reward": {2.5: 1}}),
        (I.environment_id, {"environment_version": "v", "splits": {1: ["a"]}}),
        (I.bundle_id, {"bundle_version": "v", "members": [{7: "m"}]}),
    ]
    for fn, doc in rungs:
        try:
            fn(doc)
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_KEY_TYPE, (fn.__name__, ex)
        else:
            raise AssertionError("%s minted an id over a non-string key"
                                 % fn.__name__)

    # a key whose __repr__ is hostile must not turn a refusal into a crash
    class Exploding:
        def __repr__(self):
            raise RuntimeError("this repr is third-party code")

        def __hash__(self):
            return 0

    class Enormous:
        def __repr__(self):
            return "z" * 100000

        def __hash__(self):
            return 0

    try:
        I.canonical_bytes({Exploding(): 1})
    except I.IdentityError as ex:
        assert ex.detail["key"] == "<unrepresentable>", ex.detail
        assert ex.detail["key_type"] == "Exploding", ex.detail
    else:
        raise AssertionError("expected a refusal")
    try:
        I.canonical_bytes({Enormous(): 1})
    except I.IdentityError as ex:
        assert len(ex.detail["key"]) < 200, len(ex.detail["key"])
        assert len(str(ex)) < 2000, len(str(ex))
    else:
        raise AssertionError("expected a refusal")


def test_c32_the_coercing_and_the_mixed_shape_failed_differently_before():
    """One refusal now, but two distinct defects -- and only one was silent.

    Worth pinning separately because a single test would hide that they failed
    differently, and because the difference was got wrong once already. The
    external report said a mixed dict `{1: "a", "1": "b"}` emitted **duplicate
    JSON member names**. It does not, and never did: `sort_keys=True` tries to
    order the keys first and `'<'` is not defined between `str` and `int`, so
    the mixed shape died before a single byte was written. The first half of
    this law measures that rather than repeating it, because a fabricated
    failure mode in a repository whose product is refusals is worse than the bug
    it was invented to describe.

    So the two shapes were:

    * **homogeneous** (`{1: "a", 2: "b"}`) -- serialized happily, to bytes
      identical to the string-keyed document. Silent. This is the actual
      finding, and the actual collision.
    * **mixed** (`{1: "a", "1": "b"}`) -- an untyped `TypeError` about `'<'`
      leaking from `json`'s internals, naming the serializer's problem rather
      than the caller's document. Loud, but wrong-shaped and unlocatable.

    Both now get `CANONICAL_KEY_TYPE`, naming the path and the key.

    The `TypeError` -> `IdentityError` change is a real behaviour change, since
    `IdentityError` is a `ValueError` and `TypeError` is not, so the two are not
    absorbed by the same `except` clause. It was checked before being made: no
    caller in `traaviis/` catches `TypeError` around an identity call (the two
    `except ... TypeError` clauses in `episode_bundle.py` wrap manifest-member
    path handling and JSON loading, neither of which reaches this module), and
    C27's law that a non-JSON *value* is still a `TypeError` is unchanged --
    the pre-walk descends into containers, never into leaves.
    """
    for bad, coerced in KEY_COLLISION_PAIRS:
        with without_key_validation():
            assert I.canonical_bytes(bad) == I.canonical_bytes(coerced), bad
    mixed = [{1: "a", "1": "b"}, {True: "a", "true": "b"},
             {None: "a", "null": "b"}]
    for doc in mixed:
        with without_key_validation():
            try:
                emitted = I.canonical_bytes(doc)
            except TypeError as ex:
                assert "'<'" in str(ex), str(ex)
            else:
                raise AssertionError(
                    "mixed keys were expected to fail the sort, not emit %r"
                    % (emitted,))
    # ...and now every one of them, both shapes, is the same typed refusal
    for doc in [b for b, _ in KEY_COLLISION_PAIRS] + mixed:
        try:
            I.canonical_bytes(doc)
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_KEY_TYPE, ex.code
        else:
            raise AssertionError("expected a refusal for %r" % (doc,))
    # the value domain is untouched: a non-JSON leaf is still a TypeError
    try:
        I.canonical_bytes({"x": object()})
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError for an unserializable value")


def test_c33_a_non_finite_key_is_a_key_error_and_position_decides_the_code():
    """`float("nan")` is both defects at once; the code follows where it sat.

    Before the pre-walk, `{float("nan"): 1}` raised a raw `ValueError` reading
    "Out of range float values are not JSON compliant" -- untyped, uncoded,
    unlocatable -- while `{"a": float("nan")}` raised the typed
    `CANONICAL_NON_FINITE`. The asymmetry was a real gap: `_find_non_finite`
    walks mapping *values* and not mapping *keys*, so the handler that turns
    `json`'s anonymous `ValueError` into a refusal found nothing and re-raised.

    The gap closes, but by the key check rather than by extending that walk, so
    the code is `CANONICAL_KEY_TYPE`. The rule is **position decides, not
    value**, and the argument is that a key is never serialized as a number at
    all -- it would be coerced to the string `"nan"`, which is perfectly good
    JSON, so `CANONICAL_NON_FINITE`'s claim ("the bytes hashed would not be
    JSON") is simply false on that path. Meanwhile `{1.0: "x"}`, a finite float,
    is refused for exactly the same reason `{float("nan"): "x"}` is: it is a key
    that is not a string. The float-ness is the whole story; the NaN-ness is
    incidental.

    The consequence is that `_find_non_finite` not walking keys is now correct
    by construction rather than by omission -- no float key of any value can
    reach it -- which is why that function is deliberately left alone.
    """
    for key in (float("nan"), float("inf"), float("-inf")):
        try:
            I.canonical_bytes({key: "x"})
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_KEY_TYPE, (key, ex.code)
            assert ex.detail["key_type"] == "float", ex.detail
        else:
            raise AssertionError("expected a refusal for key %r" % key)
        # the same value in the value position keeps the other code
        try:
            I.canonical_bytes({"x": key})
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_NON_FINITE, (key, ex.code)
        else:
            raise AssertionError("expected a refusal for value %r" % key)
        # and it was a *raw* ValueError before, which is the gap that closed
        with without_key_validation():
            try:
                I.canonical_bytes({key: "x"})
            except I.IdentityError:
                raise AssertionError(
                    "the key path was already typed; this law measures nothing")
            except ValueError as ex:
                assert "Out of range float" in str(ex), str(ex)

    # a document carrying both defects is refused for its key: the check that
    # runs first is the one that reports, and that ordering is deterministic
    try:
        I.canonical_bytes({1: float("nan")})
    except I.IdentityError as ex:
        assert ex.code == I.CANONICAL_KEY_TYPE, ex.code
    else:
        raise AssertionError("expected a refusal")


def test_c34_the_refusal_names_the_containing_object_not_just_the_document():
    """"Somewhere in this document" is not an answer an operator can act on.

    A path is only worth reporting if it is right at depth, so this walks a bad
    key down through nested objects, through list elements, and through both
    mixed together, and checks the reported path against the one written by
    hand. The list cases are the ones that catch an off-by-one in a walk that
    tracks its position by index rather than by key.

    The reported path is the **containing object's**, not the key's: the key is
    reported separately, and appending it would produce a path that cannot be
    resolved -- it names a member that must not be allowed to exist.
    """
    cases = [
        ({0: "x"}, "$"),
        ({"a": {0: "x"}}, "$.a"),
        ({"a": {"b": {"c": {0: "x"}}}}, "$.a.b.c"),
        ({"a": [{0: "x"}]}, "$.a[0]"),
        ({"a": ["skip", "skip", {0: "x"}]}, "$.a[2]"),
        ({"a": {"b": [{"c": {0: "x"}}]}}, "$.a.b[0].c"),
        ({"a": [[{"b": {0: "x"}}]]}, "$.a[0][0].b"),
        ({"a": [{"b": [{"c": [{"d": {0: "x"}}]}]}]}, "$.a[0].b[0].c[0].d"),
        ([{"a": {0: "x"}}], "$[0].a"),
        ({"a": ("t", {"b": {0: "x"}})}, "$.a[1].b"),   # tuples are arrays too
    ]
    for doc, want in cases:
        try:
            I.canonical_bytes(doc)
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_KEY_TYPE, ex.code
            assert ex.detail["path"] == want, (ex.detail["path"], want)
        else:
            raise AssertionError("expected a refusal for %r" % (doc,))

    # a deep document with no bad key anywhere must still serialize
    deep = {"a": {"b": [{"c": [{"d": {"e": 1}}]}]}}
    assert I.canonical_bytes(deep)

    # depth alone must not cost Python stack: the walk is iterative, and a
    # document deep enough to blow a recursive one still refuses cleanly
    node = {0: "x"}
    for _ in range(4000):
        node = {"n": [node]}
    try:
        I.canonical_bytes(node)
    except I.IdentityError as ex:
        assert ex.code == I.CANONICAL_KEY_TYPE, ex.code
        assert ex.detail["path"].endswith(".n[0]"), ex.detail["path"][-40:]
    else:
        raise AssertionError("expected a refusal")


def test_c35_the_key_check_is_byte_identical_over_the_whole_shipped_corpus():
    """The load-bearing one: the pre-walk changed no bytes that exist.

    Same measurement C28 makes for the numeric domain, over the same corpus and
    with the same guards, because the safety argument is the same one and has to
    be made again for a different check. Every JSON document in the repository
    -- `examples/`, `legacy/`, and the *published* packets inside `dist/*.zip`,
    whose ids other people already hold -- is serialized with the key check on
    and with it off, and compared byte for byte.

    It should be impossible for this to fail, and that is exactly why it is
    written: `json.loads` cannot produce a non-string key, so the claim "no
    corpus member is affected" follows from the parser's type signature. A law
    that measures it anyway is what distinguishes a checked deduction from a
    plausible one, and it is cheap.

    The counted-corpus assertions matter as much as the equality -- a walker
    that silently found nothing would pass while proving nothing. The `dist/`
    clause is conditional for the reason C28 records: `tools/build_packet.py`
    deliberately omits `dist/`, so demanding it unconditionally would assert the
    shape of one checkout rather than the property under test, and would fail
    inside the release packet, which is where it matters most.
    """
    checked = 0
    labels = []
    for label, doc in corpus_documents():
        with without_key_validation():
            before = I.canonical_bytes(doc)
        assert I.canonical_bytes(doc) == before, label
        checked += 1
        labels.append(label)
    if checked == 0:
        raise Skip("no JSON artifacts in this checkout")
    assert checked >= 20, checked
    assert any(l.endswith("receipt.json") for l in labels), "no receipt walked"
    assert any(l.startswith("examples/") for l in labels), labels[:5]
    published = [n for n in os.listdir(os.path.join(REPO, "dist"))
                 if n.endswith(".zip")] if os.path.isdir(
                     os.path.join(REPO, "dist")) else []
    if published:
        assert any(l.startswith("dist/") and ".zip!" in l for l in labels), \
            "this checkout ships %d packet(s) and none was walked" % len(published)
        assert checked >= 70, checked


def test_c36_no_id_in_the_corpus_moved():
    """Bytes are the mechanism; ids are the thing anyone actually holds.

    C35 compares serializer output. This compares the *ids* -- recomputing each
    document's id through the real `identity.*_id` rung twice, once with the key
    check and once without. Going through the real rungs is deliberate, for the
    reason C29 gives: it exercises the projections (`_EPISODE_IDENTITY_KEYS`,
    the trace event filter, the `_drop` calls) rather than a hand-written
    restatement of them.

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
            with without_key_validation():
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
    if os.path.exists(LIVE_RECEIPT):
        with open(LIVE_RECEIPT, encoding="utf-8") as fh:
            receipt = json.load(fh)
        assert I.episode_id(receipt) == receipt["episode_id"] == (
            "episode-42d0bb07e5f83e9e57518bf5cd3717e2a1e3aa45aa5821e36ac19206a3d73299"
        )
        with without_key_validation():
            assert I.episode_id(receipt) == receipt["episode_id"]


def test_c37_the_key_laws_fail_when_the_validation_is_removed():
    """A law that would pass without the code it tests is decoration.

    Every law in this file was checked for vacuity by breaking the thing it
    claims to hold, and this is that check written down for C31-C36 rather than
    performed once and remembered. `without_key_validation` deletes exactly the
    new guard -- it rebinds one module global and leaves the rest of
    `canonical_bytes` running -- so what follows is the old serializer, and each
    assertion below is the old behaviour that C31-C34 now forbid.

    The last clause is the one that makes the vacuity check itself non-vacuous:
    it asserts that C35 and C36 would *not* go red under the same perturbation.
    That is not a weakness in them, it is their entire content -- they claim
    nothing in the corpus is affected, so a corpus that reacted to the guard
    would mean an id had moved. Stating both directions keeps "these two laws
    passed" from being read as "these two laws were checked the same way as the
    others".
    """
    # C31 and C26: the collisions come back, as bytes *and* as ids
    for bad, coerced in KEY_COLLISION_PAIRS:
        with without_key_validation():
            assert I.canonical_bytes(bad) == I.canonical_bytes(coerced), bad
            assert I.reward_id(bad) == I.reward_id(coerced), bad
            assert I.snapshot_id(bad) == I.snapshot_id(coerced), bad
        try:
            I.canonical_bytes(bad)
        except I.IdentityError:
            pass
        else:
            raise AssertionError("the guard is not installed at all")

    # C32: the mixed shape reverts to the untyped TypeError
    with without_key_validation():
        try:
            I.canonical_bytes({1: "a", "1": "b"})
        except TypeError:
            pass
        else:
            raise AssertionError("expected the old TypeError")

    # C33: the non-finite key reverts to a raw, uncoded ValueError
    with without_key_validation():
        try:
            I.canonical_bytes({float("nan"): 1})
        except I.IdentityError:
            raise AssertionError("expected the old untyped failure")
        except ValueError:
            pass

    # C34: no path is reported at all, because there is no refusal to carry one
    with without_key_validation():
        assert I.canonical_bytes({"a": {"b": [{"c": {0: "x"}}]}}) == \
            I.canonical_bytes({"a": {"b": [{"c": {"0": "x"}}]}})

    # C35/C36 are the laws that must *not* react, and that is their content
    sample = 0
    for label, doc in corpus_documents():
        with without_key_validation():
            unguarded = I.canonical_bytes(doc)
        assert I.canonical_bytes(doc) == unguarded, label
        sample += 1
        if sample >= 5:
            break
    if sample == 0:
        raise Skip("no JSON artifacts in this checkout")

    # and the guard is restored afterwards, so the fixture cannot leak
    try:
        I.canonical_bytes({1: "x"})
    except I.IdentityError as ex:
        assert ex.code == I.CANONICAL_KEY_TYPE, ex.code
    else:
        raise AssertionError("without_key_validation leaked past its block")


# --------------------------------------------------------------------------- #
# C38-C39  the encoding refusal, and the enumeration that closes the domain   #
# --------------------------------------------------------------------------- #

@contextlib.contextmanager
def without_encoding_check():
    """Run the real rungs with the encoding refusal removed, and nothing else.

    Same deletion-not-transcription trick as `without_key_validation`: the
    refusal is built by `_find_unencodable`, looked up as a module global, and a
    walker that finds nothing makes `canonical_bytes` re-raise the encoder's own
    `UnicodeEncodeError` -- which is exactly the old behaviour.
    """
    original = I._find_unencodable
    I._find_unencodable = lambda obj, path="$", seen=None: None
    try:
        yield
    finally:
        I._find_unencodable = original


def test_c38_unencodable_strings_are_a_typed_refusal_naming_the_path():
    """The one hole of the three with a *live* call path, not a latent one.

    A Python `str` can hold a lone surrogate; `json.dumps` will put it in the
    output text; only `.encode("utf-8")` refuses, with a raw
    `UnicodeEncodeError` whose `start` is a byte offset into a serialized string
    the caller never sees. So the outcome was right (C25) and useless: untyped,
    uncoded, and unable to say which field.

    Why this one is urgent where the other two are not. `evalone`'s finding
    artifact hashes agent-supplied `citations` behind an `isinstance(list)`
    check, and `finding_id` is inside `_EPISODE_IDENTITY_KEYS`. So the agent
    *under evaluation* can reach the evaluator's hasher and throw an unhandled
    exception out of it. For a product whose seam is "the thing being evaluated
    must not be the thing doing the evaluating", that is the failure mode
    itself. The caller-side validation is someone else's change; this is the
    boundary, and the boundary is where the refusal has to be typed.

    Three things beyond the code. It must cover **keys as well as values** --
    `_find_non_finite` walks only values, and a surrogate hides in a key just as
    well. It must be accurate at depth. And it must be a *safe* conversion: the
    other two refusals changed `TypeError` into `IdentityError`, which are not
    absorbed by the same `except`, but `UnicodeEncodeError` is itself a
    `ValueError` subclass, so this one narrows nothing. That is asserted below
    rather than assumed, because "it is a ValueError" is exactly the kind of
    claim that is obvious and occasionally false.
    """
    assert issubclass(UnicodeEncodeError, ValueError), \
        "the conversion is only free because the old exception was a ValueError"

    cases = [
        ({"citations": ["\ud800"]}, "$.citations[0]", "value", "U+D800"),
        ({"k": "\ud800"}, "$.k", "value", "U+D800"),
        ({"\ud800": 1}, "$", "key", "U+D800"),
        ({"k": "a\udfffb"}, "$.k", "value", "U+DFFF"),
        ({"a": {"b": [{"c": "x\ud800"}]}}, "$.a.b[0].c", "value", "U+D800"),
        ({"a": [{"\udc00": 1}]}, "$.a[0]", "key", "U+DC00"),
        ("\ud800", "$", "value", "U+D800"),
    ]
    for doc, path, position, codepoint in cases:
        try:
            I.canonical_bytes(doc)
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_ENCODING == "CANONICAL_ENCODING"
            assert isinstance(ex, ValueError), "must stay catchable as ValueError"
            assert ex.detail["path"] == path, (ex.detail, path)
            assert ex.detail["position"] == position, ex.detail
            assert ex.detail["codepoint"] == codepoint, ex.detail
            assert "[CANONICAL_ENCODING]" in str(ex), str(ex)
        else:
            raise AssertionError("expected a refusal for %r" % (doc,))

    # every rung, with the surrogate where that rung actually looks
    rungs = [
        (I.snapshot_id, {"snapshot_version": "v", "files": {"a": "\ud800"}}),
        (I.finding_id, {"finding_version": "v", "citations": ["\ud800"]}),
        (I.patch_id, {"patch_version": "v", "diff": "\ud800"}),
        (I.reward_id, {"signals": {"t": {"note": "\ud800"}}}),
        (I.task_id, {"task_spec_version": "v", "instructions": "\ud800"}),
        (I.trace_id, {"trace_version": "v", "events": [{"command": "\ud800"}]}),
        (I.episode_id, {"episode_version": "traaviis.episode.v1",
                        "status": "\ud800"}),
        (I.environment_id, {"environment_version": "v", "splits": {"t": "\ud800"}}),
        (I.bundle_id, {"bundle_version": "v", "members": ["\ud800"]}),
    ]
    for fn, doc in rungs:
        try:
            fn(doc)
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_ENCODING, (fn.__name__, ex)
        else:
            raise AssertionError("%s minted an id over unencodable bytes"
                                 % fn.__name__)

    # perturbation: delete the locator and the raw encoder error comes back
    with without_encoding_check():
        try:
            I.canonical_bytes({"citations": ["\ud800"]})
        except I.IdentityError:
            raise AssertionError("expected the old untyped failure")
        except UnicodeEncodeError as ex:
            assert "surrogates not allowed" in str(ex), str(ex)
    # ...and the guard is back afterwards
    try:
        I.canonical_bytes({"citations": ["\ud800"]})
    except I.IdentityError as ex:
        assert ex.code == I.CANONICAL_ENCODING, ex.code
    else:
        raise AssertionError("without_encoding_check leaked past its block")

    # encodable strings are untouched, including every awkward legal one
    for doc in ({"a": chr(0x1F600)}, {chr(0xFB33): chr(0x20)}, {"": chr(0x7F)},
                {"a": "￾"}, {"a": "é"}):
        assert I.canonical_bytes(doc)


def test_c39_nothing_else_json_accepts_escapes_the_domain():
    """The enumeration, so the next hole is found here and not by a fourth review.

    Three holes were found one at a time, each by someone noticing a case: a
    non-finite value, a non-string key, an unencodable string. They share a
    shape -- `json.dumps` accepts something a conforming JSON reader will not --
    so the useful question is not "was that the last one?" but "what else does
    `json.dumps` accept?". This law asks it exhaustively rather than waiting.

    The property asserted is the one that actually matters, and it is stronger
    than a list of blessed types: **every input `canonical_bytes` accepts must
    produce bytes that parse back as JSON.** A content address over bytes no
    parser will read is the failure all three holes shared, so a probe that is
    accepted and round-trips is not a hole, whatever exotic Python type produced
    it. The corpus below is deliberately adversarial -- subclasses with
    overridden `__repr__`, enums, alternate mapping and sequence types, the
    I-JSON integer boundary, noncharacters, NUL, empty and astral keys.

    Two results are worth stating because they look like holes and are not:

    * `int` and `float` **subclasses cannot hijack the output**. CPython's
      encoder binds `int.__repr__` / `float.__repr__` directly rather than
      dispatching on the instance, so a subclass whose `__repr__` returns
      "PWNED" still serializes as its numeric value. Checked, not assumed.
    * a `tuple` and a `list` of the same content **do** mint the same id. That
      is correct: they are two Python spellings of one JSON array, unlike
      `{1: "x"}` / `{"1": "x"}`, which were two different JSON *documents*.
      A collision between spellings of the same document is what canonicalization
      is *for*.

    And the honest residue -- what is NOT closed, and why:

    * **integers outside I-JSON's 2**53 safe range** serialize exactly and
      round-trip in Python, but lose precision in a JavaScript reader. This is a
      real I-JSON divergence and it is deliberately left open: it is C14's
      characterized divergence, closing it would refuse documents that exist,
      and the identity-scheme decision is being adjudicated separately. Named
      here so it is a known open item rather than a fourth surprise.
    * **`RecursionError` on nesting deeper than the interpreter's limit** is a
      crash, not a wrong id, and it is not `ValueError`, so no handler absorbs
      it wrongly. It is also unreachable from parsed input: `json.loads` raises
      `RecursionError` at the same depth, so no document that *entered* through
      a parser can be deeper than one that can leave through the serializer.
    * **non-JSON value types** (`bytes`, `set`, `complex`, `Decimal`, arbitrary
      objects) raise `TypeError`. Loud, locatable by traceback, and never a
      wrong answer. C27 and C32 pin that deliberately; typing them would be a
      tidy, not a soundness fix.
    """
    import enum
    from collections import OrderedDict, defaultdict
    from decimal import Decimal

    class MyStr(str):
        pass

    class MyInt(int):
        def __repr__(self):
            return "PWNED"

    class MyFloat(float):
        def __repr__(self):
            return "PWNED"

    class IntE(enum.IntEnum):
        A = 3

    class StrE(str, enum.Enum):
        A = "a"

    accepted = [
        {"a": MyStr("x")}, {"a": MyInt(5)}, {"a": MyFloat(1.5)},
        dict({"a": 1}), {"a": [1, 2]}, {"a": (1, 2)},
        {"a": IntE.A}, {"a": StrE.A},
        OrderedDict([("b", 1), ("a", 2)]), defaultdict(int, {"a": 1}),
        {"a": 2 ** 53 + 1}, {"a": 10 ** 60}, {"a": True}, {"a": None},
        {"a": "￾"}, {"a": "x\x00y"}, {"": 1}, {chr(0x1F600): 1},
        {"a": -0.0}, {"a": 5e-324}, {"a": 1.7976931348623157e308},
    ]
    for doc in accepted:
        raw = I.canonical_bytes(doc)
        # the load-bearing property: accepted implies readable back as JSON
        json.loads(raw.decode("utf-8"))
        assert b"PWNED" not in raw, ("a subclass __repr__ reached the output",
                                     raw)

    # two spellings of one JSON array are one id, and that is correct
    assert I.canonical_bytes({"a": (1, 2)}) == I.canonical_bytes({"a": [1, 2]})

    # the three closed holes, each still refused, each with its own code
    for doc, code in ((({"a": float("nan")}), I.CANONICAL_NON_FINITE),
                      (({"a": float("inf")}), I.CANONICAL_NON_FINITE),
                      (({1: "x"}), I.CANONICAL_KEY_TYPE),
                      (({None: "x"}), I.CANONICAL_KEY_TYPE),
                      (({"a": "\ud800"}), I.CANONICAL_ENCODING),
                      (({"\ud800": 1}), I.CANONICAL_ENCODING)):
        try:
            I.canonical_bytes(doc)
        except I.IdentityError as ex:
            assert ex.code == code, (doc, ex.code, code)
        else:
            raise AssertionError("expected a refusal for %r" % (doc,))

    # non-JSON value types stay TypeError: loud, and never a wrong answer
    for value in (b"x", {1, 2}, 1j, Decimal("1.5"), object()):
        try:
            I.canonical_bytes({"a": value})
        except TypeError:
            pass
        else:
            raise AssertionError("expected TypeError for %r" % (value,))

    # the stated residue, asserted so it cannot silently change
    assert I.canonical_bytes({"a": 2 ** 53 + 1}) == b'{"a":9007199254740993}', \
        "the I-JSON integer divergence is characterized, not closed (C14)"
    assert not issubclass(RecursionError, ValueError), \
        "a depth crash must not be absorbed by an except ValueError"


# --------------------------------------------------------------------------- #
# C40-C48  the second scheme: `traaviis.episode.v2` / RFC 8785                 #
#                                                                             #
# B1 landed here. `episode-<64 lowercase hex>` is unchanged and permanent; what#
# a receipt now declares is which *serializer* produced the bytes under the    #
# digest, and `identity.episode_scheme` reads that declaration and refuses an  #
# unknown one. `traaviis/jcs.py` is the production RFC 8785 implementation.    #
#                                                                             #
# Its arrival is the thing this section has to be careful about, and C42 is    #
# where that care is written down. Everything above rests on the reference at  #
# the top of this file being *independent* of what it measures; there are now  #
# two implementations of one specification, and "they agree" is a weaker claim #
# than "each matches the standard". Both claims are made, separately, and C42  #
# says which is which.                                                        #
# --------------------------------------------------------------------------- #

_ISOLATED = [0]


@contextlib.contextmanager
def isolated_package(*edits):
    """Import a private copy of `traaviis/` with literal source edits applied.

    The non-vacuity instrument for this section. `without_key_validation` above
    neuters a module global, which is enough when the guard *is* a call to a
    global; the guards below are inline statements and a dispatch expression, so
    proving they are load-bearing means **deleting them from the source** and
    watching the law go red.

    Each edit is `(relative path, old text, new text)`. The old text must be
    present -- asserted, because an edit that silently fails to apply would
    leave the copy identical to the shipped package, and a "removed the fix"
    proof performed on an unmodified copy proves nothing. (It would still fail
    loudly rather than pass quietly, since the law expects red and would get
    green; the assertion is so the reason is named rather than guessed at.)

    The copy is imported under a fresh package name each time, so it never
    collides with the real `traaviis` in `sys.modules` and the relative imports
    inside it resolve to the copy rather than to the original.
    """
    _ISOLATED[0] += 1
    name = "traaviis_isolated_%d" % _ISOLATED[0]
    root = tempfile.mkdtemp(prefix="trvs-isolated-")
    try:
        shutil.copytree(os.path.join(REPO, "traaviis"), os.path.join(root, name),
                        ignore=shutil.ignore_patterns("__pycache__"))
        for relpath, old, new in edits:
            path = os.path.join(root, name, relpath)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            assert old in text, "edit target absent from %s: %r" % (relpath, old)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace(old, new, 1))
        sys.path.insert(0, root)
        try:
            module = __import__(name + ".identity", fromlist=["identity"])
            yield module
        finally:
            sys.path.remove(root)
            for key in [k for k in sys.modules if k.split(".")[0] == name]:
                del sys.modules[key]
    finally:
        shutil.rmtree(root, ignore_errors=True)


#: The production RFC 8785 serializer, imported here to be *measured*. Note
#: carefully what this import does and does not mean: the reference at the top
#: of this file does not use it and must never use it (C42).
from traaviis import jcs as PROD  # noqa: E402


def _without_docstrings(source):
    """`source` with every triple-quoted block removed, so prose is not code.

    Written with the tokenizer rather than a regex: a regex over `\"\"\"` cannot
    tell a docstring from a triple-quoted value, and cannot see a quote inside a
    comment at all. `tokenize` knows which is which because it is the same
    lexer the interpreter uses, so a check built on this cannot be defeated by
    the shape of a string literal.
    """
    import io
    import tokenize
    lines = source.splitlines(keepends=True)
    spans = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.STRING, tokenize.COMMENT):
            spans.append((token.start, token.end))
    for (srow, scol), (erow, ecol) in reversed(spans):
        if srow == erow:
            line = lines[srow - 1]
            lines[srow - 1] = line[:scol] + line[ecol:]
        else:
            lines[srow - 1] = lines[srow - 1][:scol] + "\n"
            for row in range(srow, erow - 1):
                lines[row] = ""
            lines[erow - 1] = lines[erow - 1][ecol:]
    return "".join(lines)


def _corpus_episodes():
    """Every id-bearing episode receipt in the corpus, on disk and in the zips."""
    for label, doc in corpus_documents():
        if isinstance(doc, dict) and "episode_version" in doc \
                and "episode_id" in doc:
            yield label, doc


def test_c40_the_production_serializer_matches_the_rfc_tables_directly():
    """The production implementation is checked against the RFC, not against us.

    C21 and C22 anchor the *reference* to RFC 8785's own published Appendix A
    sorting sample and Appendix B number vectors. This law runs the identical
    two tables against `traaviis.jcs`, so the production serializer has its own
    direct line to the standard and does not inherit its warrant from agreeing
    with a second implementation in a test file.

    That separation is the whole point. If this law only compared the two
    implementations, then a shared misreading of the RFC would pass silently in
    both; the RFC's tables are the one input to this file that neither
    implementation authored.

    **Why this runs `number_to_string` and not `admit_number`.** Since
    `JCS_CLOSED_NUMBER_PROFILE_V1`, production has two numeric entry points: a
    renderer that is ECMA-262 7.1.12.1 and renders every finite binary64, and an
    admission gate that decides which of them this scheme will seal. The RFC's
    Appendix B table describes *rendering*, and one of its rows — "Max pos int",
    `4340000000000000` → `9007199254740992` — is a value the profile refuses to
    admit, because `2**53` is one above the interoperable maximum. Had the
    profile been pushed down into the renderer, this law would have had to drop
    that row, and the battery's direct line to the standard would have quietly
    shortened to fit a local policy decision. It is asserted below that the row
    is still checked *and* that the profile still refuses the value.
    """
    keys = [chr(c) for c in (0x20AC, 0x0D, 0xFB33, 0x31, 0x1F600, 0x80, 0xF6)]
    want = [chr(c) for c in (0x0D, 0x31, 0x80, 0xF6, 0x20AC, 0x1F600, 0xFB33)]
    assert sorted(keys, key=PROD.utf16_units) == want
    assert sorted(keys) != want, "code-point order must not accidentally match"

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
    for hexbits, expected in rows:
        x = double(hexbits)
        assert PROD.number_to_string(x) == expected, \
            (hexbits, PROD.number_to_string(x), expected)
        if json.dumps(x) != expected:
            disagreeing += 1
    assert disagreeing == 3, disagreeing

    # the RFC's own "Max pos int" row renders here and is refused by the
    # profile: rendering and admission are two questions with two answers
    assert PROD.number_to_string(double("4340000000000000")) == "9007199254740992"
    try:
        PROD.admit_number(double("4340000000000000"))
    except PROD.JcsError as ex:
        assert ex.code == PROD.JCS_INT_RANGE, ex.code
    else:
        raise AssertionError("the profile admitted 2**53")

    # --- how far the RFC's table actually reaches, measured not assumed ------
    # `Number::toString` picks one of five renderings by where `n` falls. The
    # thresholds (`21`, `-6`) *were* the part of this implementation never read
    # from ECMA-262 -- V8-derived rather than quoted -- and that is no longer
    # true: ES2019 7.1.12.1 steps 6/7/8 were read and carry both constants
    # literally. The branch-coverage measurement below is kept anyway, because
    # it answers a question the citation does not: how much of the *rendering*
    # RFC 8785's own vectors witness, independently of any engine and of any
    # reading. That number is what tells a reader how much of this table would
    # still stand if the ECMA citation turned out to be to the wrong edition.
    #
    # It reads as an odd thing to assert until you notice what goes wrong
    # without it. A coverage claim stated in prose decays the moment someone
    # adds a vector, or believes they have. Pinning the covered set means
    # widening the anchor *has* to move this law.
    def branch_of(x):
        if x == 0:
            return "zero"
        digits, n = PROD._shortest_digits(abs(x))
        k = len(digits)
        if k <= n <= 21:
            return "plain-integer"
        if 0 < n <= 21:
            return "integer-and-fraction"
        if -6 < n <= 0:
            return "leading-zeros"
        return "exponent"

    covered = {branch_of(double(hexbits)) for hexbits, _ in rows}
    assert covered == {"zero", "plain-integer", "integer-and-fraction",
                       "exponent"}, sorted(covered)
    assert "leading-zeros" not in covered, \
        "the RFC table now covers the leading-zeros branch -- good news, but " \
        "the coverage disclosure in traaviis/jcs.py and C42 must be " \
        "narrowed to say so rather than left overstating the gap"
    # the uncovered branch is still exercised, just not against the standard
    assert PROD.number_to_string(0.001) == "0.001"
    assert branch_of(0.001) == "leading-zeros"
    # and neither threshold boundary is witnessed by any RFC vector
    assert PROD.number_to_string(1e21) == "1e+21"          # n=22, exponent
    assert PROD.number_to_string(1e20) == "1" + "0" * 20   # n=21, plain
    assert PROD.number_to_string(1e-7) == "1e-7"           # n=-6, exponent
    assert PROD.number_to_string(1e-6) == "0.000001"       # n=-5, leading zeros
    assert not any(branch_of(double(h)) == "leading-zeros" for h, _ in rows)


def test_c41_production_round_trips_and_never_zero_pads_an_exponent():
    """The one bug that would make every v2 id quietly wrong.

    C23's argument applied to the production serializer, run independently of
    it: a shortest-round-trip renderer that does not round-trip mints ids over
    numbers it has silently altered. Checked by parsing the emitted text back
    with Python's own float parser -- which is neither implementation's code --
    rather than by comparing against the reference.
    """
    xs = [0.1, 0.5, 1.0, -1.0, 1e16, 1e21, 1e-7, 5e-324, -0.0,
          1.7976931348623157e308, 333333333.3333333, 1424953923781206.2,
          2.0 ** 53, 0.30000000000000004, 2.220446049250313e-16]
    for e in range(-300, 300, 7):
        xs.append(float("1.234567890123456e%d" % e))
    for x in xs:
        rendered = PROD.number_to_string(x)
        assert float(rendered) == x, (x, rendered)
        assert "e+0" not in rendered and "e-0" not in rendered, \
            "ECMAScript never zero-pads an exponent: %r" % rendered
    # integers below the exact-representation bound render as integers
    for n in (0, 1, -1, 2 ** 53 - 1, -(2 ** 53), 9007199254740992):
        assert PROD.number_to_string(n) == str(n).replace("-0", "0") \
            or PROD.number_to_string(n) == str(n), n


def test_c42_the_two_implementations_are_independent_and_agree():
    """**The yardstick problem, stated and resolved rather than stepped over.**

    Before this change there was one RFC 8785 implementation in the repository
    and it lived here, on purpose, as a yardstick: C21-C24 validated it and
    every divergence law rested on it being independent of `identity`. Promoting
    it to be the production hasher would have made the battery check the
    implementation against itself, silently. It was **not** promoted. There are
    now two implementations, kept apart, and this law is the record of exactly
    what that buys.

    **What is still independently checked, after the change:**

    * *The reference, against the standard.* C21 and C22 are untouched: the
      reference still reproduces RFC 8785's Appendix A sorting sample and all
      eight Appendix B number vectors, and those tables are the RFC's, not ours.
      C23 and C24 are likewise untouched and still measure the reference on its
      own (round-tripping, and the `conforming_float` predicate).
    * *Production, against the same standard, directly.* C40 runs the identical
      two RFC tables against `traaviis.jcs`, and C41 re-derives the round-trip
      property for it against Python's float parser. Neither borrows its warrant
      from the other implementation.
    * *Every divergence law C9-C20.* Those compare `identity.canonical_bytes`
      against the reference, and `canonical_bytes` is untouched by this change.
      The thing they measure and the thing they measure it with are still two
      different pieces of code with no import between them.

    **What is now checking itself, stated plainly:** nothing, but the margin is
    thinner and the reason is worth naming. The *agreement* asserted at the
    bottom of this law -- 100,000+ doubles and the whole shipped corpus -- is
    **not** validation against the standard, and must not be read as if it were.
    Two implementations agreeing is evidence that neither has an isolated
    coding error; it is no evidence at all against a shared misreading of RFC
    8785, because a misreading of the specification is exactly the error two
    readers of the same specification make together. The RFC's own tables are
    the only defence against that, they are small, and they are the same tables
    in C22 and C40.

    **The residual risk, restated after the ECMA-262 citation landed.** The
    RFC's Appendix B is eight vectors and Appendix A is one object. Everything
    beyond those used to rest on the V8 differential run described in the module
    docstring -- a real ECMAScript engine, but not a reading of ECMA-262 -- and
    in particular the exponent thresholds `21` and `-6` were V8-derived. That
    is discharged. ES2019 7.1.12.1 steps 6, 7 and 8 were read and carry both
    constants literally, ES5.1 §9.8.1 agrees, and RFC 8785 §3.2.2.3 names that
    exact section including its "Note 2" enhancement. The thresholds are quoted.

    What is left, stated at its real size:

    * *The RFC's table still reaches only four of the five rendering branches.*
      C40 pins the covered set and pins that `leading-zeros` (`-6 < n <= 0`) is
      **not** among them, and that no vector sits on either threshold boundary.
      Those cases are now standards-anchored through the ECMA citation rather
      than through an engine, which is a different and better warrant, but it is
      a citation this file cannot execute.
    * *So the executable check moved to C51.* A 484-vector boundary table,
      generated from a **second, non-V8 oracle** (Rust's `flt2dec`) with the
      Note 2 tie-break re-derived by exact rounding, covers both thresholds,
      both neighbours of every power of two from `2**49` to `2**70`, the
      subnormals and the largest finite double. Rust and V8 *disagreed* on 6 of
      the 484 before the tie-break was applied, which is the useful part: it
      shows the table is measuring something rather than restating it.
    * *The agreement below is still not conformance.* The two implementations
      differ in how they derive the digits, which is what makes the differential
      run meaningful -- but their branch selection is the same structure. Read
      the 100,000+ patterns below as a check on the digit derivation, C40 as the
      standards-anchored table, and C51 as the independent-oracle table.

    None of this was a defect introduced by this change -- the reference had
    exactly the same standing before `traaviis/jcs.py` existed. What changed is
    the consequence: those thresholds now decide production `episode-` ids under
    `traaviis.episode.v2`, not just the characterization claims in this file.

    **Independence by construction** is asserted, not assumed. The reference's
    five functions must contain no reference to the production module, and the
    production module must contain no reference to this file. And the two must
    derive the hard part -- the shortest round-tripping decimal -- by different
    means, or their agreement is a tautology: the reference adopts `repr`'s
    digits through `Decimal`, production searches `%.*e` and *verifies* each
    candidate by parsing it back.
    """
    import inspect

    # 1. no call from the yardstick into the thing it measures
    for fn in (_es_number_to_string, _jcs_string, _utf16_sortkey,
               _jcs_serialize, jcs):
        src = inspect.getsource(fn)
        for token in ("PROD", "traaviis.jcs", "from traaviis import jcs"):
            assert token not in src, \
                "the reference %s reaches into production via %r" % (
                    fn.__name__, token)

    # 2. no call from production back into the battery. Read as *code*, with
    #    the docstrings stripped: `traaviis/jcs.py` names this file in prose
    #    deliberately -- explaining why it is a second implementation is the
    #    point -- and a text scan that could not tell a citation from an import
    #    would forbid the explanation. Sixth time in this codebase that a raw
    #    text scan made a claim about structure it could not see (cf. O30).
    prod_src = inspect.getsource(PROD)
    prod_code = _without_docstrings(prod_src)
    assert "RFC 8785" not in prod_code, "the docstring strip did not strip"
    for token in ("test_canonical", "import test", "unittest", "pytest",
                  "_es_number_to_string", "_jcs_serialize"):
        assert token not in prod_code, \
            "traaviis/jcs.py reaches into the test battery via %r" % token
    assert [line for line in prod_code.splitlines()
            if line.startswith("import ") or line.startswith("from ")] \
        == ["import math"], "production grew an import; check what it now trusts"

    # 3. two derivations of the shortest decimal, not one shared one.
    #    An *absence* is asserted over the code, for the reason in (2); a
    #    *presence* may be asserted over the whole source, since prose cannot
    #    make a missing implementation appear.
    ref_src = inspect.getsource(_es_number_to_string)
    assert "Decimal(repr(" in ref_src, \
        "the reference stopped adopting repr's digits; C42's premise moved"
    assert "Decimal" not in prod_code, \
        "production adopted the reference's derivation; agreement is now vacuous"
    assert '"%.*e"' in prod_src and "float(text) == x" in prod_code, \
        "production stopped deriving-and-verifying its digits"

    # 4. the number-rendering warrant is written down, and is the *current*
    #    one. This assertion used to demand the V8-derived disclosure be
    #    present. That disclosure has been discharged -- ES2019 7.1.12.1 was
    #    read and the constants are quoted -- so demanding it still be present
    #    would now be demanding a stale limitation. It is replaced, not
    #    dropped: the citation and the second, non-V8 oracle must both be
    #    legible, and the history of the superseded claim must survive, because
    #    a warrant that stops being written down stops being checkable.
    for token in ("ECMA-262", "7.1.12.1", "ES2019", "Note 2", "Rust", "V8",
                  "76,926", "484"):
        assert token in prod_src, \
            "the number-rendering warrant lost %r; see C51 and the module " \
            "docstring for what it is supposed to say" % token

    # 5. and, having established they are two things: they agree.
    #    NOT a conformance check -- see the docstring. An agreement check.
    import random
    rng = random.Random(20260804)
    xs = []
    while len(xs) < 90000:
        f = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if f == f and abs(f) != float("inf"):
            xs.append(f)
    for e in range(-320, 309):
        for m in ("1", "1.5", "9.999", "1.234567890123456", "5"):
            try:
                xs.append(float(m + "e" + str(e)))
            except (ValueError, OverflowError):
                pass
    for i in range(4000):
        xs += [float(i), i / 3.0, i * 1e-7, i * 1e-5, -float(i)]
    xs = [x for x in xs if x == x and abs(x) != float("inf")]
    assert len(xs) >= 76926, len(xs)          # at least the prior V8 run's size
    wrong = [(repr(x), PROD.number_to_string(x), _es_number_to_string(x))
             for x in xs if PROD.number_to_string(x) != _es_number_to_string(x)]
    assert not wrong, wrong[:10]

    # whole documents, over everything this checkout ships
    documents = 0
    for label, doc in corpus_documents():
        documents += 1
        assert PROD.canonical_bytes(doc) == jcs(doc), label
    if documents == 0:
        raise Skip("no JSON corpus in this checkout")
    # The floor is the *packet's* corpus, not the working tree's. A release
    # packet carries the sources and `examples/` but not `dist/`, so it sees 22
    # documents where the working tree sees 73 -- the two published `dist/*.zip`
    # packets and the memo corpus are tree-only. A floor calibrated to the tree
    # passes here and fails inside `tools/accept_packet.py`, which is exactly
    # how this law first failed: G6 reported `test_c42… : 21`. Same lesson C19
    # already learned, which is why its floor is 12 and not 36.
    assert documents >= 20, documents


def test_c43_the_scheme_declaration_is_itself_inside_the_hash():
    """The property B1 rests on, verified rather than trusted.

    A versioned id *prefix* would put a piece of the derivation outside the
    bytes, where nothing verifies it: a producer could stamp any scheme tag on
    any document and the id would still "verify" against a canonicalizer chosen
    by the label. B1 avoids that only if the declaration is inside what the
    digest covers, so that a receipt lying about its scheme computes a different
    id and fails.

    Measured three ways on the live shipped receipt: relabelling it moves the
    id, removing the field moves the id, and adding the second declaration to a
    v1 receipt moves the id too -- which is incidentally why `canonicalization`
    can be *required absent* under v1 without any existing document being
    affected: no v1 receipt can carry it and still be self-consistent.
    """
    if not os.path.exists(LIVE_RECEIPT):
        raise Skip("the example episode is not in this checkout")
    with open(LIVE_RECEIPT, encoding="utf-8") as fh:
        receipt = json.load(fh)
    declared = receipt["episode_id"]
    assert I.episode_id(receipt) == declared, "fixture drifted; law is vacuous"
    assert "episode_version" in I._EPISODE_IDENTITY_KEYS
    assert "canonicalization" in I._EPISODE_IDENTITY_KEYS

    relabelled = dict(receipt)
    relabelled["episode_version"] = "traaviis.episode.v2"
    relabelled["canonicalization"] = I.SCHEME_RFC8785
    assert I.episode_id(relabelled) != declared, \
        "the scheme declaration is outside the hash; B1's premise is false"

    stripped = {k: v for k, v in receipt.items() if k != "episode_version"}
    try:
        I.episode_id(stripped)
    except I.IdentityError as ex:
        assert ex.code == I.EPISODE_SCHEME_UNKNOWN, ex.code
    else:
        raise AssertionError("a receipt with no declared version was canonicalized")

    # and the grammar did not move: 64 lowercase hex under both schemes
    for identifier in (declared, I.episode_id(relabelled)):
        prefix, _, body = identifier.partition("-")
        assert prefix == "episode" and len(body) == 64
        assert all(c in "0123456789abcdef" for c in body), identifier


def test_c44_no_id_in_the_corpus_moved_when_the_dispatch_landed():
    """C29/C36 for the scheme dispatch: the whole corpus, disk and shipped zips.

    Same instrument and same reason as C29 and C36. Every id-bearing document
    in the working tree *and inside both published `dist/*.zip` packets* is
    re-derived through the real rung functions twice: once as the code stands,
    and once in an isolated copy with the dispatch **deleted from the source**
    and `canonicalize_episode` restored to the single unconditional call it was
    before. Every id must be byte-identical, and the count must be non-trivial
    or the law is measuring an empty corpus.

    This is the one law that answers "did anything I already published move?",
    and it answers it over published bytes rather than over the working tree.
    """
    before_source = """    return CANONICALIZERS[episode_scheme(receipt)]({
        k: receipt[k] for k in _EPISODE_IDENTITY_KEYS if k in receipt
    })"""
    after_source = """    return canonical_bytes({
        k: receipt[k] for k in _EPISODE_IDENTITY_KEYS if k in receipt
    })"""
    edits = [
        ("identity.py", before_source, after_source),
        ("identity.py", '"episode_version", "canonicalization",',
         '"episode_version",'),
    ]
    with isolated_package(*edits) as old:
        rungs = [(v, i, getattr(I, f.__name__), getattr(old, f.__name__))
                 for v, i, f in ID_KINDS]
        checked = live = 0
        for label, doc in corpus_documents():
            if not isinstance(doc, dict):
                continue
            for vfield, idfield, now, before in rungs:
                if vfield not in doc or idfield not in doc:
                    continue
                checked += 1
                assert now(doc) == before(doc), \
                    "%s moved under %s: %s -> %s" % (
                        label, idfield, before(doc), now(doc))
                if now(doc) == doc[idfield]:
                    live += 1
    if checked == 0:
        raise Skip("no id-bearing documents in this checkout")
    # C19's floor, for C19's reason: a release packet ships `examples/` but not
    # `dist/`, so it sees 12 id-bearing documents where the working tree sees
    # 36. A tree-calibrated floor is green here and red in the packet gate.
    assert checked >= 12, checked
    assert live == checked, \
        "%d of %d corpus ids do not recompute to their declared value" % (
            checked - live, checked)


def test_c45_a_v2_receipt_is_reproducible_and_the_reference_agrees():
    """The other half of C13: the id the divergence was always going to produce.

    C13 pins that the live shipped receipt *would* move under RFC 8785 and
    records the id it would move to. That id was, until now, a number this file
    computed about a migration nobody had performed. This law performs it: the
    same receipt, relabelled `traaviis.episode.v2`, is minted through the real
    production path, and the result must equal what the **independent reference
    implementation** computes over the same projection.

    So the v2 id is checked by the yardstick, not by the thing that produced it.
    """
    if not os.path.exists(LIVE_RECEIPT):
        raise Skip("the example episode is not in this checkout")
    with open(LIVE_RECEIPT, encoding="utf-8") as fh:
        receipt = json.load(fh)
    v2 = {"episode_version": "traaviis.episode.v2",
          "canonicalization": I.SCHEME_RFC8785}
    v2.update({k: v for k, v in receipt.items()
               if k not in ("episode_version", "episode_id")})
    minted = I.episode_id(v2)

    projected = {k: v2[k] for k in I._EPISODE_IDENTITY_KEYS if k in v2}
    assert minted == "episode-" + hashlib.sha256(jcs(projected)).hexdigest(), \
        "production and the independent reference disagree on a v2 episode id"
    assert I.episode_id(dict(v2)) == minted, "not reproducible"
    assert minted != receipt["episode_id"]
    assert I.episode_scheme(v2) == I.SCHEME_RFC8785
    assert I.episode_scheme(receipt) == I.SCHEME_LEGACY

    # v1 dispatch selects the frozen serializer, byte for byte
    v1_projected = {k: receipt[k] for k in I._EPISODE_IDENTITY_KEYS
                    if k in receipt}
    assert I.canonicalize_episode(receipt) == I.canonical_bytes(v1_projected)


def test_c46_an_unknown_scheme_is_refused_and_the_refusal_is_load_bearing():
    """A reader that stops rather than guesses -- the fourth copy of a pattern.

    `bundle.py:202`, `pack.py:236` and `episode_bundle.py:246,533` all read a
    document's declared `*_version`, compare it to what they support, and stop
    with a typed refusal naming the unsupported value. This is the same reader
    for `episode_version`, and it stops for the same reason: a canonicalizer
    chosen by guesswork computes a confident wrong id, which is a verifier being
    wrong without failing.

    Both halves of the declaration are refused -- an unrecognised version, and a
    `canonicalization` field that disagrees with the version, is missing when
    the version requires it, or is present when the version forbids it. The
    second field is a redundancy check and never an input; if it could be
    *consulted* it would be a second place to be wrong.

    **Non-vacuity by deletion.** An isolated copy has the version refusal
    replaced by a fallback to the legacy scheme -- i.e. by exactly the guess the
    law forbids -- and the declaration check disabled. Under that copy both
    refusals become ids. That is the proof that the refusals are what stops
    them, and not some other property of the document.
    """
    with open(LIVE_RECEIPT, encoding="utf-8") as fh:
        receipt = json.load(fh)
    base = {k: v for k, v in receipt.items() if k != "episode_id"}
    v2 = {"episode_version": "traaviis.episode.v2",
          "canonicalization": I.SCHEME_RFC8785}
    v2.update({k: v for k, v in base.items() if k != "episode_version"})

    cases = [
        ({**base, "episode_version": "traaviis.episode.v3"},
         I.EPISODE_SCHEME_UNKNOWN, "a version from the future"),
        ({**base, "episode_version": "residency.episode.v1"},
         I.EPISODE_SCHEME_UNKNOWN, "a plausible near-miss"),
        ({**base, "episode_version": None},
         I.EPISODE_SCHEME_UNKNOWN, "no version at all"),
        ({**base, "canonicalization": I.SCHEME_RFC8785},
         I.EPISODE_SCHEME_DECLARATION, "v1 claiming RFC 8785"),
        ({k: v for k, v in v2.items() if k != "canonicalization"},
         I.EPISODE_SCHEME_DECLARATION, "v2 with no declaration"),
        ({**v2, "canonicalization": "rfc8785-v2"},
         I.EPISODE_SCHEME_DECLARATION, "v2 declaring a scheme it is not"),
    ]
    for doc, code, why in cases:
        try:
            I.episode_id(doc)
        except I.IdentityError as ex:
            assert ex.code == code, (why, ex.code, code)
            assert "episode_version" in ex.detail, why
            assert isinstance(str(ex), str) and ex.code in str(ex), why
        else:
            raise AssertionError("minted an id for %s" % why)

    # the refusal reaches every entry point that canonicalizes an episode
    for fn in (I.episode_id, I.canonicalize_episode, I.episode_scheme):
        try:
            fn({**base, "episode_version": "traaviis.episode.v9"})
        except I.IdentityError as ex:
            assert ex.code == I.EPISODE_SCHEME_UNKNOWN, (fn.__name__, ex.code)
        else:
            raise AssertionError("%s guessed a scheme" % fn.__name__)

    # --- and the same documents, with the refusals deleted from the source ---
    guessing = (
        "    scheme = EPISODE_SCHEMES.get(declared) "
        "if isinstance(declared, str) else None\n    if scheme is None:",
        "    scheme = EPISODE_SCHEMES.get(declared, SCHEME_LEGACY)\n"
        "    if scheme is None:",
    )
    with isolated_package(
            ("identity.py", guessing[0], guessing[1]),
            ("identity.py", "    if stated != wanted:",
             "    if False and stated != wanted:")) as blind:
        for doc, _code, why in cases:
            minted = blind.episode_id(doc)
            assert minted.startswith("episode-"), why
        # the specific harm: two documents declaring different schemes, one of
        # them unsupported, silently share a canonicalizer under the guess
        assert blind.episode_id({**base, "episode_version": "traaviis.episode.v3"}) \
            != blind.episode_id(base), "the deletion made the copy degenerate"


def test_c47_both_schemes_enforce_the_same_input_domain():
    """The three domain checks apply under v2 as well, and a fourth appears.

    Stated per check, because "RFC 8785 is stricter" is true in general and
    misleading in the particular -- one of the three is enforced by the
    *identical* code under both schemes, one moves earlier, one is unchanged in
    effect, and conformance *introduces* a hazard that legacy did not have.

    * `CANONICAL_KEY_TYPE` -- not redundant. Enforced by the same
      `_find_bad_key` pre-walk in both canonicalizers, so `{1: "x"}` and
      `{"1": "x"}` cannot collide under either. RFC 8785 §3.1 requires string
      names, but a requirement in a specification does not enforce itself in a
      language whose `dict` accepts any hashable key.
    * `CANONICAL_NON_FINITE` -- not redundant, unchanged in effect.
    * `CANONICAL_ENCODING` -- not redundant, and it fires **earlier**: §3.2.2.2
      refuses a lone surrogate during string serialization, before any bytes
      exist, where the legacy path reaches `.encode("utf-8")` first. Same code,
      same detail keys, different step.
    * `CANONICAL_INT_RANGE` -- **new, and v2 only.** This is the tightening
      I-JSON demands and the one place conformance is not a narrowing of an
      existing domain but a fresh hazard: RFC 8785 has only binary64 numbers, so
      `2**53 + 1` and `2**53` would serialize identically and mint one id
      between two documents no reader would call the same. C39 characterizes the
      legacy behaviour (`{"a": 2**53+1}` prints the integer exactly); under v2
      that is a silent collision, so it is refused instead.

    The collision is *demonstrated*, in an isolated copy with the bound deleted,
    rather than asserted -- otherwise the law is a story about what would happen.

    **What moved when `JCS_CLOSED_NUMBER_PROFILE_V1` landed.** This law used to
    assert `canonical_bytes_rfc8785({"a": 2**53}) == b'{"a":9007199254740992}'`
    — v2 accepted `2**53` and refused `2**53 + 1`. Both halves were wrong. The
    accepted half emitted a token that reparses as an integer the same
    canonicalizer then refused, so a sealed document could not be re-read by its
    own verifier; and the bound was `2**53` where RFC 7493 §2.2 and RFC 8259 §6
    both give the interoperable maximum as `2**53 - 1`. The refused half was
    right by accident, for a reason that did not generalise: it tested the
    *Python type*, so `float(2**54)` sailed through while `int(2**54)` did not.
    The domain is now decided by the emitted token, which is C49 and C50; what
    is asserted here is only that the code still reaches this boundary and is
    still load-bearing.
    """
    for canonicalize in (I.canonical_bytes, I.canonical_bytes_rfc8785):
        for doc, code in (
                ({"a": float("inf")}, I.CANONICAL_NON_FINITE),
                ({"a": [1, float("nan")]}, I.CANONICAL_NON_FINITE),
                ({1: "a"}, I.CANONICAL_KEY_TYPE),
                ({"a": {2.5: 1}}, I.CANONICAL_KEY_TYPE),
                ({"a": "\ud800"}, I.CANONICAL_ENCODING),
                ({"\ud800": "a"}, I.CANONICAL_ENCODING)):
            try:
                canonicalize(doc)
            except I.IdentityError as ex:
                assert ex.code == code, (canonicalize.__name__, doc, ex.code)
                assert "path" in ex.detail, (canonicalize.__name__, doc)
            else:
                raise AssertionError(
                    "%s accepted %r" % (canonicalize.__name__, doc))
        # a non-JSON value type stays a TypeError under both: a caller bug,
        # not a law of this system
        for value in (b"x", {1, 2}, object()):
            try:
                canonicalize({"a": value})
            except TypeError:
                pass
            else:
                raise AssertionError("%s accepted %r" % (canonicalize.__name__,
                                                         value))

    # the fourth check, v2 only
    assert I.canonical_bytes({"a": 2 ** 53 + 1}) == b'{"a":9007199254740993}', \
        "legacy still prints a large integer exactly (C39)"
    for value in (2 ** 53 + 1, 2 ** 53, float(2 ** 53), 2 ** 54,
                  float(2 ** 54), 1e20):
        try:
            I.canonical_bytes_rfc8785({"a": value})
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_INT_RANGE, (value, ex.code)
            assert ex.detail["path"] == "$.a", (value, ex.detail)
        else:
            raise AssertionError("v2 accepted %r" % (value,))
    assert I.canonical_bytes_rfc8785({"a": 2 ** 53 - 1}) \
        == b'{"a":9007199254740991}'

    # --- non-vacuity: two guards, deleted one at a time -----------------------
    # They close two different failures and neither substitutes for the other,
    # so proving them together would leave it open which one was doing the work.

    # (a) the exactness test in `_as_double`. Without it, two distinct integers
    #     round onto one double and share a token -- the original collision.
    with isolated_package(
            ("jcs.py", "    if as_double is None or as_double != value:",
             "    if False:")) as blind:
        one = blind.canonical_bytes_rfc8785({"a": 10 ** 21})
        two = blind.canonical_bytes_rfc8785({"a": 10 ** 21 + 1})
        assert one == two == b'{"a":1e+21}', (one, two)
        assert I.canonical_bytes({"a": 10 ** 21}) \
            != I.canonical_bytes({"a": 10 ** 21 + 1}), \
            "legacy never had this hazard"
    try:
        I.canonical_bytes_rfc8785({"a": 10 ** 21 + 1})
    except I.IdentityError as ex:
        assert ex.code == I.CANONICAL_INT_RANGE, ex.code
        assert ex.detail["reason"] == "inexact-as-binary64", ex.detail
    else:
        raise AssertionError("the shipped code did not refuse 10**21 + 1")

    # (b) the safe-integer test in `admit_number`. Deleting it alone would only
    #     make the copy accept more, so the edit here puts the *superseded*
    #     domain back instead: the safe-integer test removed and the host-type
    #     bound (`isinstance(value, int) and abs(value) > 2**53`) reinstated in
    #     its place. That is the defect this step repaired, reproduced from
    #     source rather than described, and the assertions below are the
    #     measurement of it that opened the step.
    with isolated_package(
            ("jcs.py", "    if as_double is None or as_double != value:",
             "    if isinstance(value, int) and abs(value) > 2 ** 53:"),
            ("jcs.py",
             "    if magnitude > MAX_SAFE_INTEGER or magnitude < MIN_SAFE_INTEGER:",
             "    if False:")) as broken:
        # the host type, not the value, decided -- the same number twice
        try:
            broken.canonical_bytes_rfc8785({"a": 2 ** 54})
        except broken.IdentityError as ex:
            assert ex.code == broken.CANONICAL_INT_RANGE, ex.code
        else:
            raise AssertionError("the reinstated bound did not apply")
        emitted = broken.canonical_bytes_rfc8785({"a": float(2 ** 54)})
        assert emitted == b'{"a":18014398509481984}', emitted
        # ...and the canonicalizer refuses its own canonical output
        reparsed = json.loads(emitted.decode())
        assert isinstance(reparsed["a"], int), "an ordinary parse yields an int"
        try:
            broken.canonical_bytes_rfc8785(reparsed)
        except broken.IdentityError as ex:
            assert ex.code == broken.CANONICAL_INT_RANGE, ex.code
        else:
            raise AssertionError(
                "the superseded domain was closed after all; this law is "
                "reproducing a defect that did not exist")
    # the shipped code answers the same question the same way twice
    for value in (2 ** 54, float(2 ** 54)):
        try:
            I.canonical_bytes_rfc8785({"a": value})
        except I.IdentityError as ex:
            assert ex.code == I.CANONICAL_INT_RANGE, (value, ex.code)
        else:
            raise AssertionError("v2 accepted %r" % (value,))


def test_c48_every_shipped_episode_still_declares_the_legacy_scheme():
    """The migration is opt-in and nothing on disk was rewritten.

    Every episode receipt this repository ships -- working tree and both
    published packets -- must still declare `traaviis.episode.v1` and must still
    recompute to its stored id. A change that "conformed" by rewriting shipped
    artifacts would pass C44 (it re-derives both sides the same way) and would
    still have broken every id anybody already holds.

    It also pins the minting default, which is the cutover decision: this build
    mints v1, and the constant carries the named prerequisite for moving it.
    """
    from traaviis import evalone as E
    seen = 0
    for label, doc in _corpus_episodes():
        seen += 1
        assert doc["episode_version"] == "traaviis.episode.v1", label
        assert "canonicalization" not in doc, label
        assert I.episode_scheme(doc) == I.SCHEME_LEGACY, label
        assert I.episode_id(doc) == doc["episode_id"], label
    if seen == 0:
        raise Skip("no episode receipts in this checkout")
    # One in a release packet (the golden episode under `examples/`), three in
    # the working tree (the same receipt again inside both `dist/*.zip`).
    assert seen >= 1, seen
    assert E.EPISODE_VERSION == "traaviis.episode.v1", \
        "the minting default moved; see the constant's note and E-B4"
    assert set(I.EPISODE_SCHEMES) == {"traaviis.episode.v1",
                                      "traaviis.episode.v2"}
    assert set(I.CANONICALIZERS) == {I.SCHEME_LEGACY, I.SCHEME_RFC8785}


#: `(bit pattern, ECMA-262 rendering)` for 484 boundary doubles. See C51 for
#: how these were produced and by which oracles; the short version is Rust's
#: shortest-decimal digits plus ES2019 7.1.12.1's Note 2 tie-break applied by
#: exact rounding, cross-checked against V8, and generated outside this
#: repository on purpose. Not authored here and not derivable from anything
#: here: this is the same standing the RFC's Appendix B table has.
ORACLE_VECTORS = tuple(
    tuple(line.split()) for line in """\
0000000000000000 0
0000000000000001 5e-324
0000000000000002 1e-323
0000000000000003 1.5e-323
000fffffffffffff 2.225073858507201e-308
0010000000000000 2.2250738585072014e-308
3cb0000000000000 2.220446049250313e-16
3d719799812dea11 1e-12
3d719799812dea12 1.0000000000000002e-12
3d85fd7fe1796495 2.5e-12
3d95fd7fe1796495 5e-12
3da5fd7fe1796495 1e-11
3da5fd7fe1796497 1.0000000000000003e-11
3dbb7cdfd9d7bdbb 2.5e-11
3dcb7cdfd9d7bdbb 5e-11
3ddb7cdfd9d7bdba 9.999999999999999e-11
3ddb7cdfd9d7bdbb 1e-10
3ddb7cdfd9d7bdbc 1.0000000000000002e-10
3df12e0be826d695 2.5e-10
3e012e0be826d695 5e-10
3e112e0be826d694 9.999999999999999e-10
3e112e0be826d695 1e-9
3e112e0be826d696 1.0000000000000003e-9
3e25798ee2308c3a 2.5e-9
3e35798ee2308c3a 5e-9
3e45798ee2308c39 9.999999999999999e-9
3e45798ee2308c3a 1e-8
3e45798ee2308c3b 1.0000000000000002e-8
3e5ad7f29abcaf48 2.5e-8
3e6ad7f29abcaf48 5e-8
3e7ad7f29abcaf47 9.999999999999998e-8
3e7ad7f29abcaf48 1e-7
3e7ad7f29abcaf49 1.0000000000000001e-7
3e7ad7f29abcaf4a 1.0000000000000002e-7
3e90c6f7a0b5ed8d 2.5e-7
3ea0c6f7a0b5ed8d 5e-7
3eb0c6f7a0b5ed8c 9.999999999999997e-7
3eb0c6f7a0b5ed8d 0.000001
3eb0c6f7a0b5ed8e 0.0000010000000000000002
3ec4f8b588e368f1 0.0000025
3ed4f8b588e368f1 0.000005
3ee4f8b588e368f0 0.000009999999999999999
3ee4f8b588e368f1 0.00001
3ee4f8b588e368f2 0.000010000000000000003
3efa36e2eb1c432d 0.000025
3f0a36e2eb1c432d 0.00005
3f1a36e2eb1c432c 0.00009999999999999999
3f1a36e2eb1c432d 0.0001
3f1a36e2eb1c432e 0.00010000000000000002
3f30624dd2f1a9fc 0.00025
3f40624dd2f1a9fc 0.0005
3f50624dd2f1a9fb 0.0009999999999999998
3f50624dd2f1a9fc 0.001
3f50624dd2f1a9fd 0.0010000000000000002
3f647ae147ae147b 0.0025
3f747ae147ae147b 0.005
3f847ae147ae147a 0.009999999999999998
3f847ae147ae147b 0.01
3f847ae147ae147c 0.010000000000000002
3f9999999999999a 0.025
3fa999999999999a 0.05
3fb9999999999999 0.09999999999999999
3fb999999999999a 0.1
3fb999999999999b 0.10000000000000002
3fd0000000000000 0.25
3fd3333333333333 0.3
3fd3333333333334 0.30000000000000004
3fd5555555555555 0.3333333333333333
3fe0000000000000 0.5
3fefffffffffffff 0.9999999999999999
3ff0000000000000 1
3ff0000000000001 1.0000000000000002
4004000000000000 2.5
4014000000000000 5
4023ffffffffffff 9.999999999999998
4024000000000000 10
4024000000000001 10.000000000000002
4039000000000000 25
4049000000000000 50
4058ffffffffffff 99.99999999999999
4059000000000000 100
4059000000000001 100.00000000000001
406f400000000000 250
407f400000000000 500
408f3fffffffffff 999.9999999999999
408f400000000000 1000
408f400000000002 1000.0000000000002
40a3880000000000 2500
40b3880000000000 5000
40c387ffffffffff 9999.999999999998
40c3880000000000 10000
40c3880000000001 10000.000000000002
40d86a0000000000 25000
40e86a0000000000 50000
40f869ffffffffff 99999.99999999999
40f86a0000000000 100000
40f86a0000000001 100000.00000000001
410e848000000000 250000
411e848000000000 500000
412e847fffffffff 999999.9999999999
412e848000000000 1000000
412e848000000002 1000000.0000000002
414312d000000000 2500000
415312d000000000 5000000
416312cfffffffff 9999999.999999998
416312d000000000 10000000
416312d000000001 10000000.000000002
4177d78400000000 25000000
4187d78400000000 50000000
4197d783ffffffff 99999999.99999999
4197d78400000000 100000000
4197d78400000001 100000000.00000001
41adcd6500000000 250000000
41b3de4355555555 333333333.3333333
41bdcd6500000000 500000000
41cdcd64ffffffff 999999999.9999999
41cdcd6500000000 1000000000
41cdcd6500000002 1000000000.0000002
41e2a05f20000000 2500000000
41f2a05f20000000 5000000000
4202a05f1fffffff 9999999999.999998
4202a05f20000000 10000000000
4202a05f20000001 10000000000.000002
42174876e8000000 25000000000
42274876e8000000 50000000000
42374876e7ffffff 99999999999.99998
42374876e8000000 100000000000
42374876e8000001 100000000000.00002
424d1a94a2000000 250000000000
425d1a94a2000000 500000000000
426d1a94a1ffffff 999999999999.9999
426d1a94a2000000 1000000000000
426d1a94a2000002 1000000000000.0002
4282309ce5400000 2500000000000
4292309ce5400000 5000000000000
42a2309ce53fffff 9999999999999.998
42a2309ce5400000 10000000000000
42a2309ce5400001 10000000000000.002
42b6bcc41e900000 25000000000000
42c6bcc41e900000 50000000000000
42d6bcc41e8fffff 99999999999999.98
42d6bcc41e900000 100000000000000
42d6bcc41e900001 100000000000000.02
42ec6bf526340000 250000000000000
42fc6bf526340000 500000000000000
42ffffffffffffff 562949953421311.94
4300000000000000 562949953421312
4300000000000001 562949953421312.1
430c6bf52633ffff 999999999999999.9
430c6bf526340000 1000000000000000
430c6bf526340001 1000000000000000.1
430c6bf526340002 1000000000000000.2
430fffffffffffff 1125899906842623.9
4310000000000000 1125899906842624
4310000000000001 1125899906842624.2
43143ff3c1cb0959 1424953923781206.2
431fffffffffffff 2251799813685247.8
4320000000000000 2251799813685248
4320000000000001 2251799813685248.5
4321c37937e08000 2500000000000000
432fffffffffffff 4503599627370495.5
4330000000000000 4503599627370496
4330000000000001 4503599627370497
4331c37937e08000 5000000000000000
433ffffffffffffe 9007199254740990
433fffffffffffff 9007199254740991
4340000000000000 9007199254740992
4340000000000001 9007199254740994
4341c37937e07fff 9999999999999998
4341c37937e08000 10000000000000000
4341c37937e08001 10000000000000002
434fffffffffffff 18014398509481982
4350000000000000 18014398509481984
4350000000000001 18014398509481988
4356345785d8a000 25000000000000000
435fffffffffffff 36028797018963964
4360000000000000 36028797018963970
4360000000000001 36028797018963976
4366345785d8a000 50000000000000000
4376345785d89fff 99999999999999980
4376345785d8a000 100000000000000000
4376345785d8a001 100000000000000020
438bc16d674ec800 250000000000000000
439bc16d674ec800 500000000000000000
43abc16d674ec7ff 999999999999999900
43abc16d674ec800 1000000000000000000
43abc16d674ec802 1000000000000000300
43afffffffffffff 1152921504606846800
43b0000000000000 1152921504606847000
43b0000000000001 1152921504606847200
43c158e460913d00 2500000000000000000
43cfffffffffffff 4611686018427387400
43d0000000000000 4611686018427388000
43d0000000000001 4611686018427389000
43d158e460913d00 5000000000000000000
43dfffffffffffff 9223372036854775000
43e0000000000000 9223372036854776000
43e0000000000001 9223372036854778000
43e158e460913d00 10000000000000000000
43e158e460913d01 10000000000000002000
43efffffffffffff 18446744073709550000
43f0000000000000 18446744073709552000
43f0000000000001 18446744073709556000
43f5af1d78b58c40 25000000000000000000
4405af1d78b58c40 50000000000000000000
4415af1d78b58c3f 99999999999999980000
4415af1d78b58c40 100000000000000000000
4415af1d78b58c41 100000000000000020000
442b1ae4d6e2ef50 250000000000000000000
443b1ae4d6e2ef50 500000000000000000000
444b1ae4d6e2ef4f 999999999999999900000
444b1ae4d6e2ef50 1e+21
444b1ae4d6e2ef51 1.0000000000000001e+21
444b1ae4d6e2ef52 1.0000000000000003e+21
444fffffffffffff 1.1805916207174112e+21
4450000000000000 1.1805916207174113e+21
4450000000000001 1.1805916207174116e+21
4460f0cf064dd592 2.5e+21
4470f0cf064dd592 5e+21
4480f0cf064dd591 9.999999999999998e+21
4480f0cf064dd592 1e+22
4480f0cf064dd593 1.0000000000000002e+22
44952d02c7e14af6 2.5e+22
44a52d02c7e14af6 5e+22
44b52d02c7e14af6 1e+23
44b52d02c7e14af8 1.0000000000000003e+23
44ca784379d99db4 2.5e+23
44da784379d99db4 5e+23
44ea784379d99db3 9.999999999999998e+23
44ea784379d99db4 1e+24
44ea784379d99db6 1.0000000000000003e+24
45008b2a2c280291 2.5e+24
45108b2a2c280291 5e+24
45208b2a2c280290 9.999999999999999e+24
45208b2a2c280291 1e+25
45208b2a2c280292 1.0000000000000003e+25
4534adf4b7320335 2.5e+25
4544adf4b7320335 5e+25
4554adf4b7320334 9.999999999999999e+25
4b80000000000000 4.9039857307708443e+55
7feffffffffffffe 1.7976931348623155e+308
7fefffffffffffff 1.7976931348623157e+308
8000000000000000 0
8000000000000001 -5e-324
8000000000000002 -1e-323
8000000000000003 -1.5e-323
800fffffffffffff -2.225073858507201e-308
8010000000000000 -2.2250738585072014e-308
bcb0000000000000 -2.220446049250313e-16
bd719799812dea11 -1e-12
bd719799812dea12 -1.0000000000000002e-12
bd85fd7fe1796495 -2.5e-12
bd95fd7fe1796495 -5e-12
bda5fd7fe1796495 -1e-11
bda5fd7fe1796497 -1.0000000000000003e-11
bdbb7cdfd9d7bdbb -2.5e-11
bdcb7cdfd9d7bdbb -5e-11
bddb7cdfd9d7bdba -9.999999999999999e-11
bddb7cdfd9d7bdbb -1e-10
bddb7cdfd9d7bdbc -1.0000000000000002e-10
bdf12e0be826d695 -2.5e-10
be012e0be826d695 -5e-10
be112e0be826d694 -9.999999999999999e-10
be112e0be826d695 -1e-9
be112e0be826d696 -1.0000000000000003e-9
be25798ee2308c3a -2.5e-9
be35798ee2308c3a -5e-9
be45798ee2308c39 -9.999999999999999e-9
be45798ee2308c3a -1e-8
be45798ee2308c3b -1.0000000000000002e-8
be5ad7f29abcaf48 -2.5e-8
be6ad7f29abcaf48 -5e-8
be7ad7f29abcaf47 -9.999999999999998e-8
be7ad7f29abcaf48 -1e-7
be7ad7f29abcaf49 -1.0000000000000001e-7
be7ad7f29abcaf4a -1.0000000000000002e-7
be90c6f7a0b5ed8d -2.5e-7
bea0c6f7a0b5ed8d -5e-7
beb0c6f7a0b5ed8c -9.999999999999997e-7
beb0c6f7a0b5ed8d -0.000001
beb0c6f7a0b5ed8e -0.0000010000000000000002
bec4f8b588e368f1 -0.0000025
bed4f8b588e368f1 -0.000005
bee4f8b588e368f0 -0.000009999999999999999
bee4f8b588e368f1 -0.00001
bee4f8b588e368f2 -0.000010000000000000003
befa36e2eb1c432d -0.000025
bf0a36e2eb1c432d -0.00005
bf1a36e2eb1c432c -0.00009999999999999999
bf1a36e2eb1c432d -0.0001
bf1a36e2eb1c432e -0.00010000000000000002
bf30624dd2f1a9fc -0.00025
bf40624dd2f1a9fc -0.0005
bf50624dd2f1a9fb -0.0009999999999999998
bf50624dd2f1a9fc -0.001
bf50624dd2f1a9fd -0.0010000000000000002
bf647ae147ae147b -0.0025
bf747ae147ae147b -0.005
bf847ae147ae147a -0.009999999999999998
bf847ae147ae147b -0.01
bf847ae147ae147c -0.010000000000000002
bf9999999999999a -0.025
bfa999999999999a -0.05
bfb9999999999999 -0.09999999999999999
bfb999999999999a -0.1
bfb999999999999b -0.10000000000000002
bfd0000000000000 -0.25
bfd3333333333333 -0.3
bfd3333333333334 -0.30000000000000004
bfd5555555555555 -0.3333333333333333
bfe0000000000000 -0.5
bfefffffffffffff -0.9999999999999999
bff0000000000000 -1
bff0000000000001 -1.0000000000000002
c004000000000000 -2.5
c014000000000000 -5
c023ffffffffffff -9.999999999999998
c024000000000000 -10
c024000000000001 -10.000000000000002
c039000000000000 -25
c049000000000000 -50
c058ffffffffffff -99.99999999999999
c059000000000000 -100
c059000000000001 -100.00000000000001
c06f400000000000 -250
c07f400000000000 -500
c08f3fffffffffff -999.9999999999999
c08f400000000000 -1000
c08f400000000002 -1000.0000000000002
c0a3880000000000 -2500
c0b3880000000000 -5000
c0c387ffffffffff -9999.999999999998
c0c3880000000000 -10000
c0c3880000000001 -10000.000000000002
c0d86a0000000000 -25000
c0e86a0000000000 -50000
c0f869ffffffffff -99999.99999999999
c0f86a0000000000 -100000
c0f86a0000000001 -100000.00000000001
c10e848000000000 -250000
c11e848000000000 -500000
c12e847fffffffff -999999.9999999999
c12e848000000000 -1000000
c12e848000000002 -1000000.0000000002
c14312d000000000 -2500000
c15312d000000000 -5000000
c16312cfffffffff -9999999.999999998
c16312d000000000 -10000000
c16312d000000001 -10000000.000000002
c177d78400000000 -25000000
c187d78400000000 -50000000
c197d783ffffffff -99999999.99999999
c197d78400000000 -100000000
c197d78400000001 -100000000.00000001
c1adcd6500000000 -250000000
c1b3de4355555555 -333333333.3333333
c1bdcd6500000000 -500000000
c1cdcd64ffffffff -999999999.9999999
c1cdcd6500000000 -1000000000
c1cdcd6500000002 -1000000000.0000002
c1e2a05f20000000 -2500000000
c1f2a05f20000000 -5000000000
c202a05f1fffffff -9999999999.999998
c202a05f20000000 -10000000000
c202a05f20000001 -10000000000.000002
c2174876e8000000 -25000000000
c2274876e8000000 -50000000000
c2374876e7ffffff -99999999999.99998
c2374876e8000000 -100000000000
c2374876e8000001 -100000000000.00002
c24d1a94a2000000 -250000000000
c25d1a94a2000000 -500000000000
c26d1a94a1ffffff -999999999999.9999
c26d1a94a2000000 -1000000000000
c26d1a94a2000002 -1000000000000.0002
c282309ce5400000 -2500000000000
c292309ce5400000 -5000000000000
c2a2309ce53fffff -9999999999999.998
c2a2309ce5400000 -10000000000000
c2a2309ce5400001 -10000000000000.002
c2b6bcc41e900000 -25000000000000
c2c6bcc41e900000 -50000000000000
c2d6bcc41e8fffff -99999999999999.98
c2d6bcc41e900000 -100000000000000
c2d6bcc41e900001 -100000000000000.02
c2ec6bf526340000 -250000000000000
c2fc6bf526340000 -500000000000000
c2ffffffffffffff -562949953421311.94
c300000000000000 -562949953421312
c300000000000001 -562949953421312.1
c30c6bf52633ffff -999999999999999.9
c30c6bf526340000 -1000000000000000
c30c6bf526340001 -1000000000000000.1
c30c6bf526340002 -1000000000000000.2
c30fffffffffffff -1125899906842623.9
c310000000000000 -1125899906842624
c310000000000001 -1125899906842624.2
c3143ff3c1cb0959 -1424953923781206.2
c31fffffffffffff -2251799813685247.8
c320000000000000 -2251799813685248
c320000000000001 -2251799813685248.5
c321c37937e08000 -2500000000000000
c32fffffffffffff -4503599627370495.5
c330000000000000 -4503599627370496
c330000000000001 -4503599627370497
c331c37937e08000 -5000000000000000
c33ffffffffffffe -9007199254740990
c33fffffffffffff -9007199254740991
c340000000000000 -9007199254740992
c340000000000001 -9007199254740994
c341c37937e07fff -9999999999999998
c341c37937e08000 -10000000000000000
c341c37937e08001 -10000000000000002
c34fffffffffffff -18014398509481982
c350000000000000 -18014398509481984
c350000000000001 -18014398509481988
c356345785d8a000 -25000000000000000
c35fffffffffffff -36028797018963964
c360000000000000 -36028797018963970
c360000000000001 -36028797018963976
c366345785d8a000 -50000000000000000
c376345785d89fff -99999999999999980
c376345785d8a000 -100000000000000000
c376345785d8a001 -100000000000000020
c38bc16d674ec800 -250000000000000000
c39bc16d674ec800 -500000000000000000
c3abc16d674ec7ff -999999999999999900
c3abc16d674ec800 -1000000000000000000
c3abc16d674ec802 -1000000000000000300
c3afffffffffffff -1152921504606846800
c3b0000000000000 -1152921504606847000
c3b0000000000001 -1152921504606847200
c3c158e460913d00 -2500000000000000000
c3cfffffffffffff -4611686018427387400
c3d0000000000000 -4611686018427388000
c3d0000000000001 -4611686018427389000
c3d158e460913d00 -5000000000000000000
c3dfffffffffffff -9223372036854775000
c3e0000000000000 -9223372036854776000
c3e0000000000001 -9223372036854778000
c3e158e460913d00 -10000000000000000000
c3e158e460913d01 -10000000000000002000
c3efffffffffffff -18446744073709550000
c3f0000000000000 -18446744073709552000
c3f0000000000001 -18446744073709556000
c3f5af1d78b58c40 -25000000000000000000
c405af1d78b58c40 -50000000000000000000
c415af1d78b58c3f -99999999999999980000
c415af1d78b58c40 -100000000000000000000
c415af1d78b58c41 -100000000000000020000
c42b1ae4d6e2ef50 -250000000000000000000
c43b1ae4d6e2ef50 -500000000000000000000
c44b1ae4d6e2ef4f -999999999999999900000
c44b1ae4d6e2ef50 -1e+21
c44b1ae4d6e2ef51 -1.0000000000000001e+21
c44b1ae4d6e2ef52 -1.0000000000000003e+21
c44fffffffffffff -1.1805916207174112e+21
c450000000000000 -1.1805916207174113e+21
c450000000000001 -1.1805916207174116e+21
c460f0cf064dd592 -2.5e+21
c470f0cf064dd592 -5e+21
c480f0cf064dd591 -9.999999999999998e+21
c480f0cf064dd592 -1e+22
c480f0cf064dd593 -1.0000000000000002e+22
c4952d02c7e14af6 -2.5e+22
c4a52d02c7e14af6 -5e+22
c4b52d02c7e14af6 -1e+23
c4b52d02c7e14af8 -1.0000000000000003e+23
c4ca784379d99db4 -2.5e+23
c4da784379d99db4 -5e+23
c4ea784379d99db3 -9.999999999999998e+23
c4ea784379d99db4 -1e+24
c4ea784379d99db6 -1.0000000000000003e+24
c5008b2a2c280291 -2.5e+24
c5108b2a2c280291 -5e+24
c5208b2a2c280290 -9.999999999999999e+24
c5208b2a2c280291 -1e+25
c5208b2a2c280292 -1.0000000000000003e+25
c534adf4b7320335 -2.5e+25
c544adf4b7320335 -5e+25
c554adf4b7320334 -9.999999999999999e+25
cb80000000000000 -4.9039857307708443e+55
ffeffffffffffffe -1.7976931348623155e+308
ffefffffffffffff -1.7976931348623157e+308
""".strip().splitlines())


# --------------------------------------------------------------------------- #
# C49-C52  `JCS_CLOSED_NUMBER_PROFILE_V1`: the numeric identity domain           #
#                                                                             #
# The domain C40-C48 shipped was incoherent, and the incoherence was measured  #
# rather than argued: `traaviis/jcs.py` emitted `18014398509481984` for        #
# `float(2**54)` and then **refused its own canonical output** after an        #
# ordinary `json.loads`, because the parse yields a Python `int` and the bound #
# was applied to the host type. The same bound accepted `float(2**54)` and     #
# refused `int(2**54)` -- two answers for one number -- and it was `2**53`     #
# where RFC 7493 §2.2 and RFC 8259 §6 both give the interoperable maximum as   #
# `2**53 - 1`.                                                                #
#                                                                             #
# The repair is a named profile rather than an implied "all of RFC 8785", and  #
# the decisive law is closure under the emitted token, not the Python type:    #
#                                                                             #
#     token  = canonical serialization(value)                                 #
#     parsed = ordinary JSON parse(token)                                      #
#     parsed MUST be admitted by the same profile                             #
#     canonicalize(parsed) MUST reproduce token, byte for byte                #
#                                                                             #
# This was identity-moving for v2 and v2 had not cut over, so it was repaired  #
# in place: `EPISODE_VERSION` is still `traaviis.episode.v1` (C48), no v1 id   #
# moved (C44, re-run unchanged), and no digest produced under the broken       #
# domain is preserved anywhere -- the only v2 digest this file ever pinned is  #
# C13's, which is computed by the independent reference over a projection with #
# no scheme field in it and is therefore not a product of the broken domain.   #
# --------------------------------------------------------------------------- #

def test_c49_the_admitted_numeric_domain_is_the_profile_and_not_the_host_type():
    """What `JCS_CLOSED_NUMBER_PROFILE_V1` admits, stated two-sided at every edge.

    The bound is `2**53 - 1` and `2**53` is **outside** it. That is not a
    stylistic choice: RFC 7493 §2.2 says an I-JSON sender "cannot expect a
    receiver to treat an integer whose absolute value is greater than
    9007199254740991 (i.e., that is outside the range [-(2**53)+1, (2**53)-1])
    as an exact value", and RFC 8259 §6 gives the same interval. The previous
    bound was `2**53`, one too far.

    **Host-type independence is the property, and it is asserted as an
    equality rather than as two separate verdicts.** For every value tested
    here, `int` and `float` spellings of the same number must produce the same
    answer -- the same refusal code or the same bytes. The old domain failed
    exactly this: `int(2**54)` was refused and `float(2**54)` was sealed.

    The shape of the admitted set has one feature worth pinning explicitly,
    because it looks like an inconsistency until the closure law is applied:
    integral doubles from `2**53` up to `1e21` are refused, and `1e21` and
    above are admitted. `1e21` renders as `1e+21` under ECMA-262, an ordinary
    parse of that yields a binary64 rather than an integer, and no claim about
    exact integers is being made about it. The hole is a consequence of the
    closure law, not a second rule.
    """
    profile = "JCS_CLOSED_NUMBER_PROFILE_V1"
    assert PROD.PROFILE == profile
    assert PROD.MAX_SAFE_INTEGER == 2 ** 53 - 1 == 9007199254740991
    assert PROD.MIN_SAFE_INTEGER == -(2 ** 53 - 1) == -9007199254740991

    admitted = {
        0: "0", -0.0: "0", 0.0: "0", 1: "1", 1.0: "1", -1: "-1",
        2 ** 53 - 1: "9007199254740991",
        -(2 ** 53 - 1): "-9007199254740991",
        float(2 ** 53 - 1): "9007199254740991",
        10 ** 15: "1000000000000000",
        1e21: "1e+21", 10 ** 21: "1e+21", 1e22: "1e+22",
        5e-324: "5e-324", 1e-6: "0.000001", 1e-7: "1e-7",
        1.7976931348623157e308: "1.7976931348623157e+308",
        1424953923781206.2: "1424953923781206.2",
        333333333.3333333: "333333333.3333333",
    }
    for value, token in admitted.items():
        assert PROD.admit_number(value) == token, (value, token)
        assert PROD.canonical_bytes({"n": value}) \
            == ('{"n":%s}' % token).encode(), value

    refused = [
        (2 ** 53, "outside-safe-integer-range"),
        (-(2 ** 53), "outside-safe-integer-range"),
        (float(2 ** 53), "outside-safe-integer-range"),
        (float(-(2 ** 53)), "outside-safe-integer-range"),
        (2 ** 54, "outside-safe-integer-range"),
        (float(2 ** 54), "outside-safe-integer-range"),
        (float(2 ** 60), "outside-safe-integer-range"),
        (-float(2 ** 60), "outside-safe-integer-range"),
        (1e20, "outside-safe-integer-range"),
        (-1e20, "outside-safe-integer-range"),
        (1e16, "outside-safe-integer-range"),
        (2 ** 53 + 1, "inexact-as-binary64"),
        (10 ** 21 + 1, "inexact-as-binary64"),
        (10 ** 400, "inexact-as-binary64"),
    ]
    for value, reason in refused:
        try:
            PROD.admit_number(value)
        except PROD.JcsError as ex:
            assert ex.code == PROD.JCS_INT_RANGE, (value, ex.code)
            assert ex.detail["reason"] == reason, (value, ex.detail)
        else:
            raise AssertionError("the profile admitted %r" % (value,))

    # NaN and the infinities, and negative zero folded to 0
    for bad in (float("nan"), float("inf"), float("-inf")):
        try:
            PROD.admit_number(bad)
        except PROD.JcsError as ex:
            assert ex.code == PROD.JCS_NON_FINITE, (bad, ex.code)
        else:
            raise AssertionError("the profile admitted %r" % bad)
    assert PROD.admit_number(-0.0) == PROD.admit_number(0.0) == "0"
    assert PROD.canonical_bytes({"n": -0.0}) == PROD.canonical_bytes({"n": 0.0})

    # --- host-type independence, as an equality over every spelling ---------
    def verdict(value):
        try:
            return ("ok", PROD.admit_number(value))
        except PROD.JcsError as ex:
            return ("refused", ex.code)

    for magnitude in (0, 1, 2, 10 ** 15, 2 ** 53 - 2, 2 ** 53 - 1, 2 ** 53,
                      2 ** 53 + 2, 2 ** 54, 2 ** 60, 10 ** 20, 10 ** 21,
                      10 ** 22, 2 ** 70):
        for value in (magnitude, -magnitude):
            try:
                as_float = float(value)
            except OverflowError:
                continue
            if as_float != value:
                continue                    # not one number in two spellings
            assert verdict(value) == verdict(as_float), \
                "%r and %r are the same number and got different answers: " \
                "%r vs %r" % (value, as_float, verdict(value), verdict(as_float))

    # the hole, and where it closes -- pinned so it cannot drift unnoticed
    assert verdict(float(2 ** 53))[0] == "refused"
    assert verdict(1e20)[0] == "refused"
    assert verdict(1e21) == ("ok", "1e+21")
    assert verdict(float(2 ** 70)) == ("ok", "1.1805916207174113e+21")

    # --- non-vacuity, by deletion, one guard at a time -----------------------
    # (a) the bound is `2**53 - 1` and not `2**53`. An isolated copy with the
    #     superseded bound restored admits the one value the RFCs place outside
    #     the interoperable range, which is the whole of what this law's first
    #     paragraph claims.
    with isolated_package(("jcs.py", "MAX_SAFE_INTEGER = 2 ** 53 - 1",
                           "MAX_SAFE_INTEGER = 2 ** 53")) as loose:
        assert loose.canonical_bytes_rfc8785({"n": 2 ** 53}) \
            == b'{"n":9007199254740992}'
    try:
        I.canonical_bytes_rfc8785({"n": 2 ** 53})
    except I.IdentityError as ex:
        assert ex.code == I.CANONICAL_INT_RANGE, ex.code
    else:
        raise AssertionError("the shipped bound is not 2**53 - 1 after all")

    # (b) host-type independence is produced by deciding on the token. An
    #     isolated copy that decides on the Python type instead answers the
    #     same number two ways, which is the defect this step repaired.
    with isolated_package(
            ("jcs.py", "    if as_double is None or as_double != value:",
             "    if isinstance(value, int) and abs(value) > 2 ** 53:"),
            ("jcs.py",
             "    if magnitude > MAX_SAFE_INTEGER or magnitude < MIN_SAFE_INTEGER:",
             "    if False:")) as typed:
        def typed_verdict(value):
            try:
                return ("ok", typed.canonical_bytes_rfc8785({"n": value}))
            except typed.IdentityError as ex:
                return ("refused", ex.code)
        assert typed_verdict(2 ** 54)[0] == "refused"
        assert typed_verdict(float(2 ** 54)) == ("ok", b'{"n":18014398509481984}')
        assert typed_verdict(2 ** 54) != typed_verdict(float(2 ** 54)), \
            "the reinstated host-type bound does not reproduce the split; " \
            "this law's independence claim is measuring nothing"


def test_c50_the_profile_is_closed_under_its_own_output():
    """Canonicalize, parse, canonicalize: byte-identical, for everything admitted.

    **Enforced, not sampled.** `admit_number` computes the exact token a value
    would be sealed under and then tests *that token*, so the property holds for
    every accepted value by construction rather than for the values a law
    happened to try. The mechanism is asserted here, in three parts, because
    "we tested a lot of numbers" and "the code cannot do otherwise" are
    different claims and only the second one survives new inputs:

    1. *Every admitted value passes through the token test.* There is exactly
       one `return` in `admit_number` that is reached without it, and it is
       guarded by the token containing `.` or `e` -- which is precisely the
       condition under which an ordinary JSON parse yields a binary64 rather
       than an integer, so nothing skips the test that needed it. Read off the
       source below rather than asserted about behaviour.
    2. *`_serialize` has no other numeric path.* If a second one existed the
       enforcement would be local to one of them.
    3. *And then it is measured anyway*, over the shipped corpus and a wide
       generated set, because a structural argument that happens to be wrong
       should fail loudly.

    The generated set is deliberately built from the values the ruling names
    plus every boundary in C51, so that "sampled" and "enforced" can be
    compared: the sample is what would have been checked without (1) and (2),
    and it is a rounding error next to the domain.
    """
    import inspect
    import random
    source = inspect.getsource(PROD.admit_number)
    body = _without_docstrings(source)
    returns = [line.strip() for line in body.splitlines()
               if line.strip().startswith("return ")]
    assert returns == ["return token", "return token"], returns
    # the guard, read twice: its *structure* off the stripped code (where a
    # docstring cannot fake it) and its *literals* off the raw source (where
    # the stripper has removed every string, including these two).
    assert "    if  in token or  in token:\n        return token" in body, \
        "the unguarded return is no longer guarded by the token's shape"
    assert 'if "." in token or "e" in token:' in source, \
        "the token-shape guard no longer tests for `.` and `e`"
    assert "magnitude = int(token)" in body and "MAX_SAFE_INTEGER" in body

    serialize = _without_docstrings(inspect.getsource(PROD._serialize))
    assert serialize.count("admit_number") == 1, serialize
    assert "number_to_string" not in serialize, \
        "_serialize grew a numeric path that bypasses the profile"

    # --- and measured: canonicalize -> parse -> canonicalize ----------------
    values = [0, -0.0, 0.0, 1, 1.0, -1, 0.1, 0.5, 1 / 3, 2 ** 53 - 1,
              -(2 ** 53 - 1), 1e21, 1e22, 10 ** 21, 5e-324, 1e-6, 1e-7,
              1.7976931348623157e308, -1.7976931348623157e308,
              1424953923781206.2, 333333333.3333333, 0.30000000000000004]
    for hexbits, _token in ORACLE_VECTORS:
        values.append(double(hexbits))
    for e in range(-320, 309):
        for m in ("1", "1.5", "9.999", "1.234567890123456", "5", "2.5"):
            try:
                values.append(float(m + "e" + str(e)))
            except (ValueError, OverflowError):
                pass
    for i in range(3000):
        values += [float(i), i / 3.0, i * 1e-7, i * 1e-5, -float(i), i]
    rng = random.Random(20260805)
    while len(values) < 40000:
        f = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if f == f and abs(f) != float("inf"):
            values.append(f)

    accepted = 0
    for value in values:
        try:
            token = PROD.admit_number(value)
        except PROD.JcsError:
            continue
        accepted += 1
        reparsed = json.loads(token)
        assert PROD.admit_number(reparsed) == token, (value, token, reparsed)
        # and through the whole serializer, which is what actually seals bytes
        canon = PROD.canonical_bytes({"n": value})
        assert PROD.canonical_bytes(json.loads(canon.decode())) == canon, value
    assert accepted >= 30000, accepted

    # the shipped corpus, whole documents, disk and both published packets
    documents = 0
    for label, doc in corpus_documents():
        documents += 1
        canon = PROD.canonical_bytes(doc)
        assert PROD.canonical_bytes(json.loads(canon.decode())) == canon, label
    if documents == 0:
        raise Skip("no JSON corpus in this checkout")
    # C42's floor, for C42's reason: a release packet ships no `dist/`.
    assert documents >= 20, documents

    # --- non-vacuity: the closure failure, reproduced from source ------------
    with isolated_package(
            ("jcs.py", "    if as_double is None or as_double != value:",
             "    if isinstance(value, int) and abs(value) > 2 ** 53:"),
            ("jcs.py",
             "    if magnitude > MAX_SAFE_INTEGER or magnitude < MIN_SAFE_INTEGER:",
             "    if False:")) as broken:
        canon = broken.canonical_bytes_rfc8785({"n": float(2 ** 54)})
        try:
            broken.canonical_bytes_rfc8785(json.loads(canon.decode()))
        except broken.IdentityError as ex:
            assert ex.code == broken.CANONICAL_INT_RANGE, ex.code
        else:
            raise AssertionError(
                "the superseded domain was closed; this law proves nothing")
    assert I.canonical_bytes_rfc8785(json.loads(
        I.canonical_bytes_rfc8785({"n": 1e21}).decode())) \
        == I.canonical_bytes_rfc8785({"n": 1e21})


def test_c51_the_rendering_matches_a_second_oracle_that_is_not_v8():
    """484 boundary vectors from Rust, not from V8, and not from this codebase.

    C40 anchors the rendering to RFC 8785's eight Appendix B vectors, which is
    the standard but is eight vectors and touches neither exponent threshold.
    Everything past them used to rest on a differential run against V8 -- one
    engine, and the same engine RFC 8785 §3.2.2.3 names first, so agreeing with
    it is close to agreeing with the thing being implemented. §3.2.2.3 names a
    second: "Another compatible number serialization reference implementation is
    Ryu". Ryu is not installed on this machine; **Rust 1.94.1's `core::fmt`
    float formatting is**, and it is an independent implementation (`flt2dec`:
    Grisu3 with an exact Dragon4 fallback) rather than a port of V8's
    double-conversion.

    Stated at its real strength, because "second oracle" invites inflation:
    this is an independent *implementation*, not an independent *algorithm*.
    Grisu3 is Loitsch's and underlies V8's fast-dtoa as well, so a defect in the
    published algorithm would be invisible to both oracles. What the table rules
    out is one implementation's coding error over the boundaries — the same
    thing C42's agreement rules out for the two in-repo implementations, and no
    more. The standards anchor is still C40's RFC tables and the ECMA citation.

    **The generation, stated so the vectors can be re-derived or disbelieved:**

    * 484 bit patterns: every power of two from `2**49` to `2**70` with both
      `nextafter` neighbours, the ECMA-262 `21` and `-6` decimal thresholds and
      their neighbours, `1e-6`/`1e-7`, `1e20`/`1e21`, subnormals from `5e-324`,
      the largest finite binary64, the RFC's own Appendix B rows, and every
      negative counterpart.
    * Rust's `{:e}` gave the shortest-decimal digits and exponent.
    * ES2019 7.1.12.1's Note 2 tie-break was then applied by exact
      `ROUND_HALF_EVEN` over the exact binary64 -- independent of V8, of
      CPython's `repr`, and of the `%.*e` search production uses.
    * Steps 6/7/8/9/10 of that section, read from the standard, chose the
      rendering.
    * V8 (node v25.2.1) rendered the same 484 as a cross-check.

    **The result that makes this worth having.** Rust's own chosen digits
    disagreed with V8 on **6 of the 484** before Note 2 was applied -- among
    them RFC 8785's own Appendix B "Round to even" row `43143ff3c1cb0959`,
    where Rust emits `1424953923781206.3` and the RFC publishes
    `1424953923781206.2`. Both are shortest and both round-trip; only one is
    ECMAScript's. So Rust is a shortest-*length* oracle and is not by itself an
    ECMAScript oracle, Note 2 is a real constraint rather than a formality, and
    the table below is measuring something. After the tie-break the two oracles
    agree on all 484 and production reproduces all 484.

    The generator is deliberately **not** in this repository. The table is
    transcribed foreign output, so it cannot be regenerated into agreement with
    whatever `traaviis/jcs.py` happens to do.
    """
    assert len(ORACLE_VECTORS) == 484, len(ORACLE_VECTORS)
    assert len({h for h, _ in ORACLE_VECTORS}) == 484, "duplicate bit patterns"

    for hexbits, expected in ORACLE_VECTORS:
        x = double(hexbits)
        assert PROD.number_to_string(x) == expected, (hexbits, expected)
        # self-checking: each vector must round-trip through Python's parser,
        # which is neither oracle, so a mistranscribed row fails here
        assert float(expected) == x, ("mistranscribed", hexbits, expected)

    # the coverage claims in the docstring, measured rather than described
    tokens = [t for _, t in ORACLE_VECTORS]
    assert any(t == "9007199254740992" for t in tokens)      # 2**53
    assert any(t == "1424953923781206.2" for t in tokens)    # the Note 2 row
    assert any(t == "5e-324" for t in tokens)                # least subnormal
    assert any(t == "1.7976931348623157e+308" for t in tokens)
    assert any(t.startswith("-") for t in tokens), "no negative counterparts"
    assert any(t == "0.000001" for t in tokens) and any(t == "1e-7" for t in tokens)
    assert sum(1 for t in tokens if "e" in t) >= 100, "no exponent-branch reach"
    assert sum(1 for t in tokens if "e" not in t and "." not in t) >= 20

    # both thresholds are witnessed on both sides, which Appendix B never is
    def branch(x):
        digits, n = PROD._shortest_digits(abs(x))
        k = len(digits)
        if k <= n <= 21:
            return "plain-integer"
        if 0 < n <= 21:
            return "integer-and-fraction"
        if -6 < n <= 0:
            return "leading-zeros"
        return "exponent"
    seen = {branch(double(h)) for h, _ in ORACLE_VECTORS if double(h) != 0}
    assert seen == {"plain-integer", "integer-and-fraction", "leading-zeros",
                    "exponent"}, sorted(seen)

    # and the split C40 depends on: the table is about *rendering*, so it
    # contains vectors the profile refuses to admit
    refused = 0
    for hexbits, _t in ORACLE_VECTORS:
        try:
            PROD.admit_number(double(hexbits))
        except PROD.JcsError:
            refused += 1
    assert refused > 0, \
        "no vector is outside the profile, so this table no longer " \
        "demonstrates that rendering and admission are separate questions"
    assert refused < len(ORACLE_VECTORS)

    # --- non-vacuity: move a threshold and the table has to notice -----------
    # The point of a boundary corpus is that it sits *on* the boundaries. Two
    # isolated copies each shift one ES2019 constant by one, and the table must
    # go red for both -- otherwise these 484 vectors are 484 vectors that would
    # have passed against a wrong implementation, which is the failure mode a
    # large table invites.
    for old, new, which in (
            ("    if k <= n <= 21:", "    if k <= n <= 22:", "step 6's 21"),
            ("    if 0 < n <= 21:", "    if 0 < n <= 22:", "step 7's 21"),
            ("    if -6 < n <= 0:", "    if -7 < n <= 0:", "step 8's -6")):
        with isolated_package(("jcs.py", old, new)) as shifted:
            moved = [h for h, want in ORACLE_VECTORS
                     if shifted._jcs.number_to_string(double(h)) != want]
            assert moved, \
                "shifting %s changed no vector; the boundary corpus does not " \
                "reach that threshold and this law is weaker than it reads" % which


def test_c52_the_declared_scheme_names_the_profile_and_not_the_bare_rfc():
    """A document says what it actually is, and says it inside its own hash.

    `canonicalization` used to read `"rfc8785-v1"`. That overclaimed: it told a
    reader "RFC 8785" while the implementation accepted strictly less than RFC
    8785 accepts, and after `JCS_CLOSED_NUMBER_PROFILE_V1` it would have been a
    label pointing at a domain nobody could look up. The declared name is now
    the profile's, and it is one string in one place -- `jcs.PROFILE` -- so the
    minter, the verifier and the serializer cannot come to hold three beliefs.

    Nothing on disk carried the old name: `EPISODE_VERSION` is still v1, every
    shipped receipt declares v1 (C48), and v1 receipts are forbidden to carry
    the field at all. So this rename cost no id, which is the only reason it
    could be made at all rather than versioned around.
    """
    import inspect
    assert I.SCHEME_RFC8785 == "JCS_CLOSED_NUMBER_PROFILE_V1"
    assert I.SCHEME_RFC8785 is PROD.PROFILE, \
        "the declared name was copied rather than taken from jcs.PROFILE"
    # read as *code*: identity.py names the superseded value in prose, on
    # purpose, so that the rename is explained where it happened. The same
    # distinction C42 draws, for the same reason -- a text scan that could not
    # tell a citation from a live constant would forbid the explanation.
    assert "rfc8785-v1" not in _without_docstrings(inspect.getsource(I)), \
        "the superseded declared name is still reachable in identity.py"

    # producer and verifier read the same table
    assert I.episode_scheme_declaration("traaviis.episode.v2") \
        == I.SCHEME_RFC8785
    assert I.episode_scheme_declaration("traaviis.episode.v1") is None

    if not os.path.exists(LIVE_RECEIPT):
        raise Skip("the example episode is not in this checkout")
    with open(LIVE_RECEIPT, encoding="utf-8") as fh:
        receipt = json.load(fh)
    v2 = {"episode_version": "traaviis.episode.v2",
          "canonicalization": I.SCHEME_RFC8785}
    v2.update({k: v for k, v in receipt.items()
               if k not in ("episode_version", "episode_id")})
    assert I.episode_scheme(v2) == I.SCHEME_RFC8785

    # the name is inside the hash, so a document cannot lie about its domain
    lying = dict(v2, canonicalization="rfc8785-v1")
    try:
        I.episode_id(lying)
    except I.IdentityError as ex:
        assert ex.code == I.EPISODE_SCHEME_DECLARATION, ex.code
    else:
        raise AssertionError("a receipt declaring the superseded name sealed")
    # and the profile name really does move the bytes it is inside
    without = {k: v for k, v in v2.items() if k != "canonicalization"}
    projected_a = {k: v2[k] for k in I._EPISODE_IDENTITY_KEYS if k in v2}
    projected_b = {k: without[k] for k in I._EPISODE_IDENTITY_KEYS
                   if k in without}
    assert jcs(projected_a) != jcs(projected_b)

    # --- non-vacuity: the old name, restored from source, mints a different id
    # This is what makes the rename an identity-moving change rather than a
    # cosmetic one, and therefore what makes "it cost no id because nothing had
    # cut over" a claim with content. It is also why the rename had to happen
    # now: after cutover it would have been a migration.
    with isolated_package(
            ("identity.py", "SCHEME_RFC8785 = _jcs.PROFILE",
             'SCHEME_RFC8785 = "rfc8785-v1"')) as old_name:
        assert old_name.SCHEME_RFC8785 == "rfc8785-v1"
        stale = dict(v2, canonicalization="rfc8785-v1")
        assert old_name.episode_id(stale) != I.episode_id(v2), \
            "the declared scheme name is outside the hash; renaming it was " \
            "free and this law is measuring nothing"
        # ...and neither build will read the other's declaration
        try:
            old_name.episode_id(v2)
        except old_name.IdentityError as ex:
            assert ex.code == old_name.EPISODE_SCHEME_DECLARATION, ex.code
        else:
            raise AssertionError("the old build accepted the new name")


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
