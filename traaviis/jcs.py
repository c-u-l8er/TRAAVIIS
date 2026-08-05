"""`JCS_CLOSED_NUMBER_PROFILE_V1`: RFC 8785 over a named number domain.

This is the canonicalizer behind `traaviis.episode.v2`. It is a *second*
serializer, sitting beside the frozen `identity.canonical_bytes`; neither
replaces the other, because the scheme a document is canonicalized under is
declared by the document and legacy documents keep declaring the old one.

**The scheme is named for a profile, not for the RFC.** A document under this
scheme declares `"canonicalization": "JCS_CLOSED_NUMBER_PROFILE_V1"`, and the name
is the honest one: what is implemented is RFC 8785 *serialization* restricted to
an explicitly frozen numeric input domain. Declaring the bare RFC would claim
that any input RFC 8785 accepts is accepted here, which is false and was false
before the profile existed too -- the difference is that the overclaim is now
gone from the wire rather than only from the prose.

**The profile, frozen.**

* A value whose canonical token is a JSON **integer literal** is admitted iff
  that literal lies in `[-(2**53 - 1), 2**53 - 1]` -- the interoperable integer
  range RFC 7493 §2.2 and RFC 8259 §6 both state as `[-(2**53)+1, (2**53)-1]`.
  That covers every Python `int` in the range and every `float` that renders as
  one, which is the point: the rule is about the JSON integer domain, not about
  which Python type the producer happened to hold. `2**53` *itself* is refused;
  the normative bound is `2**53 - 1`, not `2**53`.
* A Python `int` must additionally be *exactly* a binary64, so that two distinct
  integers can never round onto one token.
* `NaN`, `+Infinity` and `-Infinity` are refused, as §3.2.2.3 requires.
* Negative zero canonicalizes to `0`, as §3.2.2.3 requires.
* Everything else is decided by **closure under the emitted token**, below.

Stated as "integer inputs must lie in `[-(2**53 - 1), 2**53 - 1]`" the first
rule would be shorter and would be wrong in one direction that matters:
`int(10**21)` would be refused while `float(1e21)` -- the same number -- was
admitted, which is the host-type dependence this profile exists to remove. The
rule is about the token, and `10**21`'s token is `1e+21`, not an integer
literal.

**Closure under the emitted token is the decisive law, and it is what the
previous domain got wrong.** The bound used to be applied to the *Python type*:
an `int` above `2**53` was refused while a `float` of the identical value was
accepted, so the admitted domain was a property of the producer's language
rather than of JSON. Worse, it was not closed: this serializer emitted
`18014398509481984` for `float(2**54)` and then **refused its own output** after
an ordinary `json.loads`, because the parse yields a Python `int`. For a
canonicalization scheme that is fatal -- a verifier re-reading a sealed document
would refuse the document it had just been asked to check.

So admission is defined over the token, not over the host type:

    token  = number_to_string(value)          # ECMA-262 7.1.12.1
    parsed = ordinary JSON parse of token
    parsed must be admitted by this same profile
    canonicalizing parsed must reproduce token, byte for byte

`admit_number` *enforces* that rather than testing for it, and does so without a
JSON parser, because the token's own shape decides what a parser would return: a
JSON number literal holding neither `.` nor `e` is an integer literal and every
conforming parser yields an integer for it; anything else yields a binary64.
So the only way a token can reparse outside the profile is for it to be an
integer literal of magnitude above `2**53 - 1`, and that is the one case
`admit_number` has to test. Closure is then a property of the code path, which
is why C50 can prove it by deleting the test rather than by sampling values.

What that admits and refuses, measured rather than asserted:

* `±(2**53 - 1)` is admitted; `±2**53` is refused, `int` or `float` alike.
* `float(2**54)` is refused -- its token `18014398509481984` reparses as an
  out-of-profile integer. So is `float(2**60)`, whose token is
  `1152921504606847000`, and so is `1e20` and `1e16`.
* `1e21` and above are **admitted**, because ECMA-262 renders them in exponent
  form (`1e+21`), an ordinary parse of that yields a binary64 rather than an
  integer, and no claim about exact integers is being made. The admitted domain
  therefore has a hole across the integral doubles in `[2**53, 1e21)` and
  reopens at `1e21`. That shape is a consequence of the closure law rather than
  a separate decision, and it is stated here so nobody has to rediscover it.
* An `int` that is not *exactly* a binary64 is refused before rendering, so
  `10**21` (exact, admitted) and `10**21 + 1` (inexact, refused) cannot collide
  on the token `1e+21`. Refusing large integers outright would have been simpler
  and would have reintroduced the host-type dependence this profile removes.

Applications needing genuinely large exact integers must carry them as strings.
That is RFC 7493 §2.2's own recommendation ("it is RECOMMENDED to encode them in
JSON string values") and RFC 8785 §3.1 inherits it verbatim.

**Correcting a false statement this module used to carry.** The old refusal
message said binary64 "represents no integer of magnitude above 2**53 exactly".
That is wrong: `2**54` and `2**60` are both exactly representable, as is every
power of two up to `2**1023`. What binary64 cannot do is represent every
*consecutive* integer above `2**53` -- above `2**53` the representable integers
thin to every second one, above `2**54` to every fourth, and so on. The hazard
the bound closes is therefore not "the value is inexpressible" but "distinct
integers round to one double and mint one id", which is why the exactness test
above is `float(value) == value` and not a magnitude comparison.

**Layering.** This module imports nothing from `traaviis`. It sits *below*
`identity`, which imports it, so `identity` keeps the property its own docstring
claims — it names nothing above itself — while gaining a second scheme. Refusals
are raised as `JcsError`, a local type carrying the same `code` / `message` /
`detail` shape every typed refusal in this codebase takes; `identity` translates
them into `IdentityError` at the boundary so a caller sees one refusal type
whichever scheme it asked for.

**Why this is written out rather than imported from the test battery.**
`test/test_canonical.py` has carried an RFC 8785 reference implementation since
before this module existed, deliberately, as an *independent yardstick*: laws
C21-C24 validate that reference against the RFC's own published tables, and
every divergence claim in the battery rests on the yardstick being independent
of the thing it measures. Promoting that reference to be the production hasher
would have made the battery check the implementation against itself.

So there are two implementations and they are kept apart on purpose. This one
must never import the battery's, and the battery's must never import this one;
C42 asserts both directions. They also derive the hardest part -- the shortest
round-tripping decimal -- by **different means**, so that their agreement is
evidence rather than tautology:

* the battery's reference trusts `repr(float)` to already *be* the shortest
  round-tripping decimal, and re-reads its digits with `Decimal.as_tuple`;
* this one does not trust any single rendering. It searches `%.{p}e` for
  increasing precision and stops at the first that parses back to the identical
  double, so the digit string is *derived and then verified* rather than
  adopted. If CPython's `repr` ever stopped being shortest-round-trip, the two
  would disagree instead of being wrong together.

What that buys and what it does not is stated exactly in C40-C43: agreement
between two implementations is not the same as validation against the standard,
and the anchor to the standard remains the RFC's Appendix A sorting sample and
Appendix B number vectors, which *both* implementations are checked against
directly.

**The V8-derived disclosure is now discharged, and this records what replaced
it.** RFC 8785 §3.2.2.3 mandates ECMAScript's `Number::toString`, naming
"Section 7.1.12.1 of [ECMA-262], including the 'Note 2' enhancement", where its
`[ECMA-262]` reference is the ECMAScript 2019 edition. Earlier work in this
repository could not obtain that text and reconstructed the five rendering
branches from the algorithm's shape, validating them against V8 (node v25.2.1)
over 76,926 double bit patterns; the exponent thresholds `21` and `-6` were
therefore V8-derived rather than read from the standard, and that limitation was
recorded here rather than left implicit.

It was obtained this time. ECMA-262 10th edition (ES2019) §7.1.12.1 was read at
`https://262.ecma-international.org/10.0/`, and steps 6, 7 and 8 carry the
constants literally -- "If k <= n <= 21", "If 0 < n <= 21", "If -6 < n <= 0" --
with steps 9 and 10 the exponential forms. Step 5 defines the `s`, `k`, `n`
triple `_shortest_digits` computes, and the Note 2 enhancement adds the
tie-break: among equally short candidates take the closest to `m`, and on a tie
take the even one. The same section was read in ES5.1 §9.8.1, where the
constants are identical, and in the current tc39 living draft, where they
survive but restructured (`n` in the inclusive interval from -5 to 21, which is
`-6 < n <= 21` rearranged). **The branch constants below are read from the
standard.** What remains inferred is nothing about them.

**Two oracles, and the second one is not V8.** RFC 8785 §3.2.2.3 names Ryu as a
compatible reference implementation alongside V8. Ryu is not available on this
machine; Rust 1.94.1's `core::fmt` float formatting is, and it is an independent
implementation (`flt2dec`: Grisu3 with an exact Dragon4 fallback) rather than a
port of V8's double-conversion. Stated at its real strength: independent
*implementation*, not independent *algorithm* -- Grisu3 is Loitsch's and
underlies V8's fast-dtoa too, so a defect in the published algorithm would be
invisible to both. What this rules out is one implementation's coding error,
which is the same thing the two in-repo implementations rule out for each other
and no more. A boundary corpus of 484 bit patterns -- powers of two
from `2**49` to `2**70` with both `nextafter` neighbours of each, the `21` and
`-6` decimal thresholds and their neighbours, `1e-6`/`1e-7`, `1e20`/`1e21`,
subnormals from `5e-324` up, the largest finite binary64, the RFC's own
Appendix B rows, and every negative counterpart -- was rendered by Rust's `{:e}`
to obtain `k` and the digits, and by V8's `String(x)`.

That produced a result worth keeping rather than a rubber stamp: **Rust and V8
disagreed on 6 of the 484**, one of them RFC 8785's own Appendix B "Round to
even" row `43143ff3c1cb0959`, where Rust emits `1424953923781206.3` and the RFC
publishes `1424953923781206.2`. Rust picks *a* shortest round-tripping decimal;
ES2019's Note 2 picks *the* one closest to the value, ties to even. So Rust is a
shortest-*length* oracle and is not by itself an ECMAScript oracle. Taking `k`
from Rust and re-deriving `s` by exact `ROUND_HALF_EVEN` over the exact binary64
-- independent of V8, of CPython's `repr`, and of the `%.*e` search this module
uses -- the two oracles agree on all 484, and this module reproduces all 484.
C51 carries the resulting vector table. The generator itself is deliberately
*not* in the repository, so the table is transcribed foreign output rather than
something this codebase can quietly regenerate into agreement with itself.
"""

import math

__all__ = [
    "JcsError", "JCS_KEY_TYPE", "JCS_NON_FINITE", "JCS_ENCODING",
    "JCS_INT_RANGE", "JCS_NOT_JSON", "PROFILE",
    "MAX_SAFE_INTEGER", "MIN_SAFE_INTEGER",
    "canonical_bytes", "admit_number", "number_to_string", "utf16_units",
]


#: The name this scheme declares on the wire, and the whole of what it claims.
#: `identity.SCHEME_RFC8785` is this string; the two are kept equal by C52 so
#: that renaming the profile cannot leave a document declaring the old name.
PROFILE = "JCS_CLOSED_NUMBER_PROFILE_V1"


#: A mapping key was not exactly a `str`. RFC 8785 §3.1 inherits I-JSON's rule
#: that object member names are strings.
JCS_KEY_TYPE = "JCS_KEY_TYPE"

#: NaN or an infinity reached the serializer. §3.2.2.3 requires termination.
JCS_NON_FINITE = "JCS_NON_FINITE"

#: A string held a lone surrogate. §3.2.2.2 requires termination.
JCS_ENCODING = "JCS_ENCODING"

#: A number outside `JCS_CLOSED_NUMBER_PROFILE_V1`'s integer domain. Raised for
#: two distinct reasons, separated by `detail["reason"]`: an integer that is not
#: exactly a binary64 (`"inexact-as-binary64"`), and a value whose canonical
#: token is an integer literal outside the interoperable range
#: (`"outside-safe-integer-range"`). See `admit_number`.
JCS_INT_RANGE = "JCS_INT_RANGE"

#: A value of a type JSON has no representation for.
JCS_NOT_JSON = "JCS_NOT_JSON"


#: The interoperable safe-integer bounds. RFC 7493 §2.2 and RFC 8259 §6 both
#: state the range as `[-(2**53)+1, (2**53)-1]`; `2**53` itself is *outside* it.
#: These are the profile's integer domain, and the only numeric constants in
#: this module that are not read from ECMA-262 7.1.12.1.
MAX_SAFE_INTEGER = 2 ** 53 - 1
MIN_SAFE_INTEGER = -(2 ** 53 - 1)


class JcsError(ValueError):
    """A typed refusal from the RFC 8785 serializer.

    Subclasses `ValueError` for exactly the reason `identity.IdentityError`
    does: `json.dumps` is what raises on this input class and it raises
    `ValueError`, so narrowing the base would quietly change which failures an
    existing `except` clause absorbs.
    """

    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def __str__(self):
        return "[%s] %s" % (self.code, self.message)


def _shortest_digits(x):
    """`(digits, n)` for a positive finite float: the RFC's `s` and `n`.

    ES2019 7.1.12.1 step 5 defines `s`, `k`, `n` as integers with `k >= 1`,
    `10**(k-1) <= s < 10**k`, the Number value for `s * 10**(n-k)` equal to `x`,
    and `k` as small as possible. Its Note 2 enhancement -- which RFC 8785
    §3.2.2.3 explicitly requires -- resolves the remaining freedom: among
    equally short candidates take the one closest in value to `x`, and if two
    are equally close take the even one. Everything else in `number_to_string`
    is a choice of rendering; this is where the number actually comes from, so
    this is the part that must not be taken on trust.

    It is derived by search rather than adopted from `repr`. `"%.*e"` at
    precision `p - 1` emits exactly `p` significant digits with correct
    round-to-nearest-even, which is Note 2's rule; the first `p` whose output
    parses back to the identical double is by construction the shortest
    round-tripping decimal. Seventeen significant digits always suffice for a
    binary64, so the loop is bounded and cannot fall through.

    That Note 2 is a real constraint and not a formality is measured, not
    assumed: Rust's shortest-decimal formatter picks a *different* `s` on 6 of
    the 484 boundary vectors in C51, one of them RFC 8785's own Appendix B
    "Round to even" row. Both choices are shortest and both round-trip; only one
    is ECMAScript's.

    The `n` returned is the position of the decimal point relative to the digit
    string -- `x == 0.<digits> * 10**n` -- which is one more than the exponent
    `"%e"` prints, because `"%e"` normalizes to a single leading digit.
    """
    for precision in range(1, 18):
        text = "%.*e" % (precision - 1, x)
        if float(text) == x:
            break
    else:                                   # unreachable for a binary64
        raise JcsError(JCS_NON_FINITE,
                       "no decimal of 17 significant digits round-trips %r" % x,
                       {"value": repr(x)})
    mantissa, _, exponent = text.partition("e")
    digits = mantissa.replace(".", "")
    n = int(exponent) + 1
    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]                # `s` carries no trailing zero
    return digits, n


def _as_double(value, where):
    """`value` as the binary64 it denotes, or a refusal if it denotes no binary64.

    Only integers can fail here, and the test is exactness rather than
    magnitude: `10**21` is an integer far above the safe range and *is* exactly
    a binary64, while `10**21 + 1` is not and rounds onto it. Admitting the
    first and refusing the second is what keeps `int` and `float` of equal value
    interchangeable without letting two distinct integers share a token. A
    magnitude bound here would do neither.
    """
    if not isinstance(value, int):
        return value
    try:
        as_double = float(value)
    except OverflowError:
        as_double = None
    if as_double is None or as_double != value:
        raise JcsError(
            JCS_INT_RANGE,
            "refusing to canonicalize the integer %d at %s: it is not exactly "
            "an IEEE-754 binary64, so it and at least one other integer would "
            "round to one double, serialize to identical bytes and mint one id "
            "between two distinct documents. Carry it as a string instead "
            "(RFC 7493 §2.2, inherited by RFC 8785 §3.1)." % (value, where),
            {"path": where, "value": str(value),
             "reason": "inexact-as-binary64"})
    return as_double


def admit_number(value, where="$"):
    """The canonical token for `value` under the profile, or a typed refusal.

    The profile's whole enforcement, in one place, so that "what this scheme
    accepts" has a single answer and `_serialize` cannot grow a second one.

    The last test is the closure law from the module docstring, applied rather
    than sampled. `number_to_string` has already produced the exact bytes this
    value would be sealed under; a JSON number literal holding neither `.` nor
    `e` is an integer literal, which every conforming parser reads back as an
    integer, so that token and only that token can reparse outside the profile.
    Testing it here means no accepted value can fail to survive a parse -- not
    because a law checked a list of examples, but because the check is on the
    path every accepted value takes.
    """
    if value is True or value is False:
        raise JcsError(JCS_NOT_JSON, "a bool is not a number", {"path": where})
    token = number_to_string(_as_double(value, where))
    if "." in token or "e" in token:
        return token                        # reparses as a binary64; closed
    magnitude = int(token)
    if magnitude > MAX_SAFE_INTEGER or magnitude < MIN_SAFE_INTEGER:
        raise JcsError(
            JCS_INT_RANGE,
            "refusing to canonicalize %r at %s: its canonical form is the "
            "integer literal %s, which an ordinary JSON parse reads back as an "
            "integer outside the interoperable range [%d, %d] that RFC 7493 "
            "§2.2 and RFC 8259 §6 both name. Sealing it would produce a "
            "document this same canonicalizer refuses to re-read, so the id "
            "could never be checked. Carry it as a string instead."
            % (value, where, token, MIN_SAFE_INTEGER, MAX_SAFE_INTEGER),
            {"path": where, "value": repr(value), "token": token,
             "limit": str(MAX_SAFE_INTEGER),
             "reason": "outside-safe-integer-range"})
    return token


def number_to_string(value):
    """ECMAScript `Number::toString(value, 10)`, which RFC 8785 §3.2.2.3 mandates.

    **Rendering only.** This is ECMA-262 (ES2019) 7.1.12.1 and nothing else: it
    renders every finite binary64, including ones the profile does not admit,
    because that is what the standard says the function does. The profile is a
    constraint on *input admission* (§3.1's I-JSON adaptation), not on
    rendering, and keeping the two apart is what lets C40 keep checking this
    function against RFC 8785's Appendix B table -- whose "Max pos int" row is
    `2**53`, a value the profile refuses. A renderer narrowed to the profile
    would have quietly cost the battery its direct line to the standard.

    Callers that are canonicalizing a document want `admit_number`. This is
    exported for the laws that measure it against the RFC and against the
    two-oracle boundary table, and for nothing else.
    """
    if value is True or value is False:
        raise JcsError(JCS_NOT_JSON, "a bool is not a number", {})
    if isinstance(value, int):
        try:
            value = float(value)
        except OverflowError:
            raise JcsError(
                JCS_INT_RANGE,
                "refusing to render the integer %d: it exceeds every finite "
                "IEEE-754 binary64, which is the only number type this "
                "serialization has." % value,
                {"value": str(value), "reason": "inexact-as-binary64"}) from None
    if math.isnan(value) or math.isinf(value):
        raise JcsError(
            JCS_NON_FINITE,
            "refusing to canonicalize the non-finite number %r: JSON has no "
            "NaN or Infinity and RFC 8785 §3.2.2.3 requires termination."
            % value, {"value": repr(value)})
    if value == 0:
        return "0"                          # covers -0.0, per §3.2.2.3
    if value < 0:
        return "-" + number_to_string(-value)
    digits, n = _shortest_digits(value)
    k = len(digits)
    # The four branches and both constants are ES2019 7.1.12.1 steps 6-10, read
    # from the standard rather than reconstructed. See the module docstring.
    if k <= n <= 21:
        return digits + "0" * (n - k)                    # step 6:  100
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]             # step 7:  1.5
    if -6 < n <= 0:
        return "0." + "0" * (-n) + digits                # step 8:  0.001
    exponent = n - 1                                     # steps 9 and 10
    mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
    return "%se%s%d" % (mantissa, "+" if exponent >= 0 else "-", abs(exponent))


_SHORT_ESCAPES = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n",
                  0x0C: "\\f", 0x0D: "\\r"}


def _string(text, where):
    """RFC 8785 §3.2.2.2 string serialization.

    The surrogate check is a codepoint range test, and that is a deliberate
    departure from `identity._unencodable_index`, which asks the UTF-8 encoder
    instead and is pinned by C18 to keep asking it. The two are answering
    different questions. Under the legacy scheme the domain is "bytes that can
    exist", so the encoder is the only honest judge. Under this one the RFC
    *names* the range and requires termination inside string serialization,
    before any encoding happens, so the range is the specification rather than a
    proxy for it. Both refusals carry the same code once `identity` has
    translated them, because from a caller's side the fact is the same: the
    document holds a character the canonical form has no bytes for.
    """
    out = ['"']
    for index, char in enumerate(text):
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise JcsError(
                JCS_ENCODING,
                "refusing to canonicalize a string holding the lone surrogate "
                "U+%04X at offset %d (%s): RFC 8785 §3.2.2.2 requires a "
                "compliant implementation to terminate."
                % (code, index, where),
                {"path": where, "codepoint": "U+%04X" % code, "offset": index})
        if code in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[code])
        elif code < 0x20:
            out.append("\\u%04x" % code)                  # lowercase, per §3.2.2.2
        elif char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        else:
            out.append(char)                             # everything else as is
    out.append('"')
    return "".join(out)


def utf16_units(text):
    """`text` as the tuple of UTF-16 code units RFC 8785 §3.2.3 sorts on.

    Computed arithmetically -- astral code points are split into their
    surrogate pair by hand -- rather than by encoding to `utf-16-be` and
    comparing bytes, which is how the battery's reference derives the same
    order. Two derivations of one order is the point; see the module docstring.
    """
    units = []
    for char in text:
        code = ord(char)
        if code > 0xFFFF:
            code -= 0x10000
            units.append(0xD800 + (code >> 10))
            units.append(0xDC00 + (code & 0x3FF))
        else:
            units.append(code)
    return tuple(units)


def _serialize(value, where):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _string(value, where)
    if isinstance(value, (int, float)):
        return admit_number(value, where)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(
            _serialize(item, "%s[%d]" % (where, position))
            for position, item in enumerate(value)) + "]"
    if isinstance(value, dict):
        for key in value:
            if type(key) is not str:
                raise JcsError(
                    JCS_KEY_TYPE,
                    "refusing to canonicalize a non-string object key at %s: "
                    "JSON object names are strings (RFC 8259 §4) and RFC 8785 "
                    "§3.1 inherits I-JSON's requirement." % where,
                    {"path": where, "key_type": type(key).__name__})
        items = sorted(value.items(), key=lambda kv: utf16_units(kv[0]))
        return "{" + ",".join(
            _string(key, where) + ":" + _serialize(item, "%s.%s" % (where, key))
            for key, item in items) + "}"
    raise JcsError(JCS_NOT_JSON,
                   "not JSON data at %s: a value of type %s"
                   % (where, type(value).__name__),
                   {"path": where, "type": type(value).__name__})


def canonical_bytes(value):
    """RFC 8785 canonical UTF-8 bytes for `value`.

    The encode cannot fail: `_string` has already refused every code point
    UTF-8 has no encoding for, which is the whole of what it could have failed
    on. That ordering is the RFC's, not a convenience -- §3.2.2.2 terminates
    during serialization, so under this scheme a lone surrogate is refused
    strictly earlier than under the legacy one, where `.encode` is what raises.
    """
    return _serialize(value, "$").encode("utf-8")
