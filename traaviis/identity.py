"""Content-addressed identity for TRAAVIIS evaluation artifacts.

Pure canonicalization + hashing. **No I/O, no subprocess, no verifier
execution, no workspace mutation.** This module is the frozen identity spine
that `trvs eval-one` builds on; it is implemented and tested against the
mutation laws *before* any runner exists, exactly as the WRL identity spine was.

Each artifact id is

    <prefix>-<sha256(canonical_bytes)>

where `canonical_bytes` is a deterministic UTF-8 JSON serialization (sorted
object keys, minimal separators) of the artifact with its own id field removed
and any volatile / non-identity metadata excluded, per the frozen contracts in
`RFC_TRAAVIIS_ARTIFACTS.md` (§1–§4) and `RFC_EVIDENCE_RESIDENCY.md` (§4–§9).

**The `episode-` rung is the one rung with two canonicalizers**, selected by the
receipt's own declared `episode_version`. See `episode_scheme` for the whole of
that mechanism and for why the id *grammar* — `episode-<64 lowercase hex>` — is
identical under both and is not versioned. Every other rung has exactly one.

Canonicalization rule of thumb: **maps are order-independent** (object keys are
sorted, so reordering an unordered map never moves an id); **lists preserve
their given order** (the producer is responsible for canonical list ordering).

    | function                | id prefix   | self-field dropped | other exclusions                        |
    | ----------------------- | ----------- | ------------------ | --------------------------------------- |
    | snapshot_id             | `snap-`     | snapshot_id        | —                                       |
    | finding_id              | `finding-`  | finding_id         | —                                       |
    | patch_id                | `patch-`    | patch_id           | line endings normalized to LF           |
    | trace_id                | `trace-`    | trace_id           | volatile events + timed logs (§5a)      |
    | reward_id               | `rew-`      | reward_id          | —                                       |
    | task_id                 | `task-`     | task_id            | —                                       |
    | episode_id              | `episode-`  | episode_id         | everything outside the identity allowlist (volatile timing/PID/path) |
    | environment_id          | `env-`      | env_id             | presentation (name/description/docs) — those move `bundle-` only (§5) |
    | bundle_id               | `bundle-`   | bundle_id          | nothing — the whole manifest is the package (§5b) |
"""

import hashlib
import json
import math
from typing import Any, Mapping

from . import jcs as _jcs

__all__ = [
    "IdentityError", "CANONICAL_NON_FINITE", "CANONICAL_KEY_TYPE",
    "CANONICAL_ENCODING", "CANONICAL_INT_RANGE",
    "EPISODE_SCHEME_UNKNOWN", "EPISODE_SCHEME_DECLARATION",
    "SCHEME_LEGACY", "SCHEME_RFC8785", "EPISODE_SCHEMES", "CANONICALIZERS",
    "DECLARES_CANONICALIZATION", "episode_scheme",
    "episode_scheme_declaration",
    "canonical_bytes", "canonical_bytes_rfc8785",
    "canonicalize_snapshot", "snapshot_id",
    "canonicalize_finding", "finding_id",
    "canonicalize_patch", "patch_id",
    "canonicalize_trace", "trace_id",
    "canonicalize_reward", "reward_id",
    "canonicalize_task", "task_id",
    "canonicalize_episode", "episode_id",
    "canonicalize_environment", "environment_id",
    "canonicalize_bundle", "bundle_id",
]


#: A non-finite number reached the hasher as a *value*.
CANONICAL_NON_FINITE = "CANONICAL_NON_FINITE"

#: A mapping *key* was not exactly a `str`. The two codes are separated by the
#: key's *position*, never by its value: a float value is judged for whether it
#: is finite, a float key is judged for not being a string, and `float("nan")`
#: used as a key is `CANONICAL_KEY_TYPE` for the same reason `1.0` is. See
#: `canonical_bytes`.
CANONICAL_KEY_TYPE = "CANONICAL_KEY_TYPE"

#: A string in the document cannot be encoded as UTF-8 — in practice, it holds
#: a lone surrogate. Named for the *encoding* step rather than for surrogates
#: because that is the property actually tested (`str.encode` is the judge), and
#: because it is the step that fails: the document is well-formed JSON right up
#: until it has to become bytes, and a content address is over bytes.
CANONICAL_ENCODING = "CANONICAL_ENCODING"

#: A number outside the declared scheme's integer domain. Reachable only under
#: `SCHEME_RFC8785`, whose numbers are IEEE-754 binary64 constrained to
#: `jcs.PROFILE`'s interoperable safe-integer range. The legacy scheme prints an
#: `int` exactly at any magnitude, so there is nothing there to refuse; this is
#: the one domain check the two schemes do not share, and it exists because
#: conformance *introduces* the hazard rather than inheriting it.
#:
#: Note what the hazard is and is not. It is **not** that binary64 cannot hold
#: large integers exactly — it holds `2**54` and `2**60` exactly, and every
#: power of two up to `2**1023`. It is that above `2**53` the representable
#: integers thin out, so distinct integers round onto one double and would mint
#: one id between two documents no reader would call the same; and that a token
#: like `18014398509481984` reparses as an integer this same scheme then
#: refuses, which would leave a sealed document its own verifier could not
#: re-read. See `jcs.admit_number` for how both are closed at once.
CANONICAL_INT_RANGE = "CANONICAL_INT_RANGE"

#: A document declared a schema version this build has no canonicalizer for.
#: Refused, never guessed: see `episode_scheme`.
EPISODE_SCHEME_UNKNOWN = "EPISODE_SCHEME_UNKNOWN"

#: A document's `canonicalization` self-declaration is missing, forbidden, or
#: disagrees with the one its `episode_version` implies. See `episode_scheme`.
EPISODE_SCHEME_DECLARATION = "EPISODE_SCHEME_DECLARATION"


class IdentityError(ValueError):
    """A typed refusal from the identity spine; ``code`` names the law broken.

    Carries a stable string ``code`` plus ``message`` and ``detail``, which is
    the shape every typed refusal in this codebase takes. It is declared *here*,
    and not shared with the modules above, because this module is the leaf of
    the import graph: everything that raises a typed refusal imports identity,
    so identity can import none of them. This module names nothing above itself.

    It subclasses ``ValueError`` deliberately. ``json.dumps`` is what raises on
    this input class and it raises ``ValueError``; narrowing to a bare
    ``Exception`` would quietly change which failures an existing ``except``
    clause absorbs, which is a behaviour change wearing a type annotation.

    That inheritance is now **load-bearing, not incidental**:
    ``evalone._finding_artifact`` relies on it to absorb *every* typed refusal,
    present and future, so a candidate can never crash its own evaluation by
    handing in bytes the spine cannot seal. Changing the base class re-opens that.
    Note what it does *not* mean: ``code`` names the law broken, never who broke
    it — ``CANONICAL_ENCODING`` arrives both from a candidate's lone surrogate
    and from the evaluator's own workspace rescan — so no caller can use
    ``code`` to attribute blame.
    """

    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def __str__(self):
        return "[%s] %s" % (self.code, self.message)


def _find_non_finite(obj, path="$", seen=None):
    """``(json-ish path, value)`` of the first NaN/Infinity in ``obj``, or None.

    Runs only on the refusal path, so it costs nothing in the normal case. It
    exists so the refusal can *name* what was refused rather than repeat
    ``json``'s anonymous "out of range float". Container identities are tracked
    so a circular structure — the other ``ValueError`` ``json.dumps`` raises —
    terminates here instead of looping.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return path, obj
        return None
    if isinstance(obj, (dict, list, tuple)):
        if seen is None:
            seen = set()
        if id(obj) in seen:
            return None
        seen.add(id(obj))
        items = (obj.items() if isinstance(obj, dict)
                 else enumerate(obj))
        for key, value in items:
            sub = "%s.%s" % (path, key) if isinstance(obj, dict) \
                else "%s[%d]" % (path, key)
            hit = _find_non_finite(value, sub, seen)
            if hit is not None:
                return hit
    return None


def _unencodable_index(text):
    """Index of the first character UTF-8 cannot encode, or None.

    ``str.encode`` is the judge rather than a surrogate range test of our own.
    Lone surrogates are the only thing a Python ``str`` can hold that UTF-8
    rejects, so the two agree today — but the property that matters is "these
    bytes cannot be produced", and asking the encoder tests exactly that
    instead of a proxy for it that could drift.
    """
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as ex:
        return ex.start
    return None


def _find_unencodable(obj, path="$", seen=None):
    """``(path, "key"|"value", string)`` of the first unencodable string.

    Runs only on the refusal path — ``text.encode("utf-8")`` has already failed
    by the time this is called — so like ``_find_non_finite`` it costs the
    common case nothing. It exists so the refusal can *name* what was refused
    rather than repeat the encoder's byte offset into a JSON string nobody has,
    which is what ``UnicodeEncodeError.start`` is: a position in the serialized
    text, not a path into the caller's document.

    Unlike ``_find_non_finite`` this walks mapping **keys as well as values**.
    A key is a string like any other and encodes like any other, so a surrogate
    hides there just as well; the position is reported so the two cases stay
    distinguishable. (``_find_non_finite`` does not need the same treatment —
    see ``canonical_bytes`` on why no non-string key can reach it.)
    """
    if isinstance(obj, str):
        return (path, "value", obj) if _unencodable_index(obj) is not None \
            else None
    if isinstance(obj, (dict, list, tuple)):
        if seen is None:
            seen = set()
        if id(obj) in seen:
            return None
        seen.add(id(obj))
        if isinstance(obj, dict):
            for key in obj:
                if isinstance(key, str) and _unencodable_index(key) is not None:
                    return path, "key", key
        items = (obj.items() if isinstance(obj, dict) else enumerate(obj))
        for key, value in items:
            sub = "%s.%s" % (path, key) if isinstance(obj, dict) \
                else "%s[%d]" % (path, key)
            hit = _find_unencodable(value, sub, seen)
            if hit is not None:
                return hit
    return None


def _safe_repr(key, limit=80):
    """A bounded, exception-proof rendering of an arbitrary object.

    The offending key can be *any* Python object, so ``repr`` on it is a call
    into code this module does not control: it may raise, and it may return
    megabytes. A refusal that crashes while explaining itself — or that pastes
    an unbounded blob into every log that records it — is a worse outcome than
    the one it was reporting. Hence total: never raises, never unbounded.
    """
    try:
        text = repr(key)
    except Exception:                       # deliberately total; see docstring
        return "<unrepresentable>"
    if not isinstance(text, str):           # a __repr__ returning a non-str
        return "<unrepresentable>"
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


#: The container types ``json.dumps`` descends into, and the scalar types it
#: does not. Disjoint by construction, which is what lets ``_find_bad_key`` skip
#: the ``isinstance`` when a value's type is *exactly* one of the scalars.
_JSON_CONTAINERS = (dict, list, tuple)
_JSON_SCALARS = frozenset((str, int, float, bool, type(None)))


def _find_bad_key(obj):
    """``(path of the *containing* object, key)`` of the first non-``str`` key.

    Unlike ``_find_non_finite``, this cannot run only on a refusal path: there
    is no refusal to run on. ``json.dumps`` does not report a coerced key — it
    silently succeeds — so nothing downstream ever raises for the case this
    exists to catch, and the walk has to happen *before* serialization. That
    makes it the one part of ``canonical_bytes`` the common case pays for, so it
    is written to keep the price to the minimum the job allows:

    * it is iterative, so a deep document costs no Python stack and no call per
      node;
    * it descends only into containers. A document's leaves are nearly all of
      it, and a leaf whose type is *exactly* a JSON scalar cannot contain a key,
      so it is dismissed by one ``frozenset`` probe and never becomes a frame.
      The two sets are disjoint, so the skip is not an approximation: anything
      in ``_JSON_SCALARS`` would have failed the ``isinstance`` anyway;
    * it builds **no strings**. Each frame keeps a parent index and the segment
      that reached it, and ``_frame_path`` assembles a path only once a bad key
      has actually been found. On the happy path no path is ever formatted.

    Together those are worth about 2x over the obvious recursive-with-eager-path
    implementation, measured over this repository's own JSON corpus and over a
    283 KiB synthetic document. What they do not buy is *free*: the walk lands
    at 0.5x-1.1x the cost of the ``json.dumps`` it guards depending on document
    shape, leaving ``canonical_bytes`` at roughly 1.7x-2.2x its former cost.
    That is stated rather than hidden because it is the honest shape of the
    trade — there is no hook in ``json`` to observe a coerced key, so a pre-walk
    is not an implementation choice that could be optimized away, and the only
    alternative to paying it is not checking. It is affordable here because
    ``canonical_bytes`` mints a handful of ids per episode alongside subprocess
    verifier runs: 8 µs on a typical artifact from this corpus, and nothing in
    this repository calls it in a loop. No law asserts these figures — they are
    a measurement taken on one machine, not a property of the code.

    ``dict`` / ``list`` / ``tuple`` are matched rather than ``Mapping`` /
    ``Sequence`` on purpose: those are exactly the container types
    ``json.dumps`` itself descends into, so the walk's domain and the
    serializer's domain cannot drift apart. Container identities are tracked so
    a circular structure terminates here and is left to ``json.dumps`` to report
    as the circular reference it is, rather than being relabelled.
    """
    frames = [(obj, -1, None)]              # (node, parent frame index, segment)
    seen = set()
    index = 0
    while index < len(frames):
        node = frames[index][0]
        here = index
        index += 1
        if isinstance(node, dict):
            if id(node) in seen:
                continue
            seen.add(id(node))
            for key, value in node.items():
                if type(key) is not str:
                    return _frame_path(frames, here), key
                if type(value) not in _JSON_SCALARS \
                        and isinstance(value, _JSON_CONTAINERS):
                    frames.append((value, here, key))
        elif isinstance(node, _JSON_CONTAINERS):
            if id(node) in seen:
                continue
            seen.add(id(node))
            for position, value in enumerate(node):
                if type(value) not in _JSON_SCALARS \
                        and isinstance(value, _JSON_CONTAINERS):
                    frames.append((value, here, position))
    return None


def _frame_path(frames, index):
    """The json-ish path of ``frames[index]``, walked back up the parent chain.

    Called only from the refusal path, which is why ``_find_bad_key`` can afford
    to record parent links instead of formatting paths as it goes.
    """
    segments = []
    while index > 0:                        # frame 0 is the root: no segment
        _node, parent, segment = frames[index]
        segments.append(segment)
        index = parent
    path = "$"
    for segment in reversed(segments):
        path += ("[%d]" % segment if isinstance(segment, int)
                 else ".%s" % segment)
    return path


def canonical_bytes(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON: sorted object keys, no insignificant whitespace.

    Three domain checks are applied, and only three. Each closes a case where
    ``json.dumps`` accepts something that is not I-JSON: one silently, two
    loudly but anonymously. C39 enumerates what is left and argues it is empty.

    **Object keys must be exactly ``str``.** Python's JSON encoder *coerces*
    ``int`` / ``float`` / ``bool`` / ``None`` keys to strings instead of
    refusing them, so ``{1: "x"}`` and ``{"1": "x"}`` — two documents no reader
    would call the same — serialize to the same bytes and therefore mint the
    same id. That is a silent wrong answer in the one place this module exists
    to give a right one, so the key domain is enforced here, ahead of
    ``json.dumps``, and the key is **rejected, never coerced**. (Mixed key types
    behave differently again: ``sort_keys=True`` cannot order ``str`` against
    ``int`` and raises a bare ``TypeError`` about ``<``, which is a correct
    outcome reached for an unrelated reason and reported in terms of the
    serializer's internals rather than the caller's document. Both shapes now
    get one typed refusal.)

    The status this closes is precise and worth not overstating: no production
    call path and no corpus member has been found supplying a non-string key —
    parsed JSON never produces one — so this is a **reachable latent collision,
    not a live one**. What was true before this check is that the public
    canonicalization boundary accepted them.

    ``type(key) is str``, not ``isinstance(key, str)``, and the difference is
    not fastidiousness. The intended canonical domain is exactly *parsed JSON*,
    whose object keys are exactly ``str``; nothing in this repository produces a
    ``str`` subclass as a key, so admitting them buys nothing. What it costs is
    concrete: ``sort_keys=True`` orders keys with ``<``, and a subclass may
    override ``__lt__``. Measured — a subclass whose ``__lt__`` is inverted
    serializes ``{"a": 1, "b": 2}`` as ``{"b":2,"a":1}``, i.e. an object whose
    member order, and therefore whose id, is chosen by the caller's own code.
    ``isinstance`` would admit that. ``type(...) is str`` cannot.

    **Numbers must be finite.** ``allow_nan=False``. Python's default emits the
    bare tokens ``NaN`` / ``Infinity`` / ``-Infinity``, which are **not JSON** —
    no conforming parser in any language reads them back. So the default would
    let this function mint a content-addressed id whose preimage is unparseable,
    which is the exact failure a content address exists to prevent. RFC 8785
    §3.2.2.3 requires a compliant implementation to terminate instead, and now
    this one does.

    The two codes are separated by **position, not by value**: a non-finite
    *value* is ``CANONICAL_NON_FINITE``, and a non-finite *key* is
    ``CANONICAL_KEY_TYPE``. A key is never serialized as a number — it would be
    coerced to the string ``"nan"``, which is perfectly good JSON — so the
    non-finite code would be naming a hazard that does not exist on that path,
    while ``{1.0: "x"}``, a finite float, is refused for precisely the same
    reason ``{float("nan"): "x"}`` is: it is a key that is not a string. The
    float-ness is the whole story; the NaN-ness is incidental. This also means
    ``_find_non_finite``'s not walking mapping keys is now correct by
    construction rather than by omission: no key of any float value can reach
    it.

    **Strings must be encodable as UTF-8.** In practice that means: no lone
    surrogates. A Python ``str`` may hold one, ``json.dumps`` will happily put
    it in the output text, and only ``.encode("utf-8")`` refuses — with a raw
    ``UnicodeEncodeError`` naming a byte offset into a serialized string the
    caller never sees. RFC 8785 §3.2.2.2 requires termination here, and the
    outcome was already right; what was missing was that it be *typed* and say
    *where*.

    It is the only one of the three with **live** call paths today, and there is
    more than one of them. This docstring previously said "the one caller that
    happens to be reachable now"; that was wrong, and the sentence concealed a
    hole for as long as it stood. The known callers that can reach this refusal
    with input from outside the evaluator are:

    * ``evalone._finding_artifact`` — agent-supplied ``summary`` /
      ``citations``. ``json.loads`` turns a ``"\\ud800"`` escape into a lone
      surrogate, and ``finding_id`` is inside ``_EPISODE_IDENTITY_KEYS``.
    * ``runner._scan`` → the trace's ``files_*`` digests — a **filename**. A
      POSIX name is bytes, so an agent that creates ``b"evil\\xff.txt"`` gets it
      back from ``os.walk`` surrogate-escaped, into a map key, into
      ``trace_id``, into ``episode_id``. This one needs no crafted JSON at all
      and is the easiest of the lot; it is closed in ``runner._evidence_name``.
    * ``snapshot.build_snapshot`` → ``$.files`` — the same filename problem over
      an *operator's* subject tree, which is a different input class and is
      answered differently (``snapshot._check_encodable`` refuses; the runner
      escapes).

    That an unhandled refusal out of this function inverts the seam the whole
    product rests on is exactly why it is typed here at the boundary: the list
    above is what has been *found*, not a proof of what exists, and a boundary
    that refuses in one typed way is what lets each caller be fixed as it is
    found without the spine having to know them all.

    Unlike the key check this costs the common case nothing: the encoder already
    raises, so ``_find_unencodable`` runs only to *locate* what failed, exactly
    as ``_find_non_finite`` does. And unlike the key check it is a safe
    conversion by inspection — ``UnicodeEncodeError`` is itself a ``ValueError``
    subclass, so every existing ``except ValueError`` still absorbs it and no
    handler's reach changes.

    None of the three refusals moves an id: over the entire existing corpus the
    emitted bytes are unchanged (see ``test/test_canonical.py`` C27-C30 for the
    numeric domain, C31-C37 for the key domain, and C38 for encoding).

    What must **not** be read into that is a claim that no producer in this
    repository can emit such a document. This docstring used to say exactly
    that, and it was false at the time it was written: ``runner._scan`` emitted
    an unencodable key for any workspace containing a non-UTF-8 filename, and
    the refusal escaped ``run_agent`` uncaught. The honest statement is
    narrower and is the one that can actually be checked: **no artifact in the
    corpus contains a non-finite number, a non-string key, or an unencodable
    string**, so no id in it moves. Whether a *producer* can construct one is a
    property of that producer, is settled at that producer, and is not something
    this function can assert on anyone's behalf.
    """
    bad_key = _find_bad_key(obj)
    if bad_key is not None:
        where, key = bad_key
        raise IdentityError(
            CANONICAL_KEY_TYPE,
            "refusing to mint an id over a non-string object key: %s has the "
            "key %s of type %s. Python coerces int/float/bool/None keys to "
            "strings rather than refusing them, so that document and one "
            "written with the coerced string key would mint identical bytes "
            "and therefore the same id -- two distinct documents sharing one "
            "content address. JSON object names are strings (RFC 8259 §4), so "
            "the key is rejected here rather than silently rewritten."
            % (where, _safe_repr(key), type(key).__name__),
            {"path": where, "key_type": type(key).__name__,
             "key": _safe_repr(key)},
        )
    try:
        text = json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as ex:
        found = _find_non_finite(obj)
        if found is None:
            raise            # a different ValueError (e.g. a circular reference)
        where, value = found
        raise IdentityError(
            CANONICAL_NON_FINITE,
            "refusing to mint an id over a non-finite number: %s is %r. JSON "
            "has no NaN or Infinity, so the bytes hashed would not be JSON and "
            "no conforming parser could read the preimage back "
            "(RFC 8785 §3.2.2.3)." % (where, value),
            {"path": where, "value": repr(value)},
        ) from ex
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as ex:
        found = _find_unencodable(obj)
        if found is None:
            raise            # unencodable, but not from any string we can name
        where, position, text_value = found
        index = _unencodable_index(text_value)
        raise IdentityError(
            CANONICAL_ENCODING,
            "refusing to mint an id over a string UTF-8 cannot encode: the %s "
            "at %s contains U+%04X at offset %d. A content address is over "
            "bytes, and these bytes do not exist -- RFC 8785 §3.2.2.2 requires "
            "a compliant implementation to terminate on a lone surrogate, and "
            "no conforming parser could read the preimage back."
            % (position, where, ord(text_value[index]), index),
            {"path": where, "position": position,
             "codepoint": "U+%04X" % ord(text_value[index]),
             "offset": index, "string": _safe_repr(text_value)},
        ) from ex


def canonical_bytes_rfc8785(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON per RFC 8785, the `traaviis.episode.v2` scheme.

    The second canonicalizer. It exists so that "SHA-256 over RFC 8785 canonical
    JSON" is a true sentence about newly minted episodes, with an off-the-shelf
    implementation in Go, Rust, JavaScript and Java — a property `canonical_bytes`
    provably does not have and is not being changed to have, because changing it
    would move every id in existence.

    **The same three domain checks apply here as there, and one more.** That is
    worth stating precisely, because "JCS is stricter" is true in general and
    misleading in the particular:

    * `CANONICAL_KEY_TYPE` — **not redundant, and enforced by the identical code
      path.** `_find_bad_key` runs first here exactly as it does in
      `canonical_bytes`, so the collision `{1: "x"}` / `{"1": "x"}` is closed
      once, in one walk, for both schemes. RFC 8785 §3.1 requires it too, but a
      requirement in a specification does not enforce itself in Python, where
      the hazard is that a `dict` can hold a key JSON cannot.
    * `CANONICAL_NON_FINITE` — not redundant. §3.2.2.3 requires termination and
      this implementation does terminate, but the refusal has to be *typed* and
      has to *name the path*, which is a property of this boundary rather than
      of the serializer under it.
    * `CANONICAL_ENCODING` — not redundant, but it **moves earlier**. Under the
      legacy scheme a lone surrogate survives serialization and is caught by
      `.encode("utf-8")`; under this one §3.2.2.2 refuses it during string
      serialization, before any bytes exist. Same code, same detail keys,
      strictly earlier detection. The legacy path's "let the encoder be the
      judge" rationale is scheme-specific and does not carry over: here the RFC
      names the range, so the range *is* the specification.
    * `CANONICAL_INT_RANGE` — **new, and only here.** This is the one place
      conformance is not a narrowing of an existing domain but a new hazard.
      RFC 8785 has only binary64 numbers, so `2**53 + 1` and `2**53` would
      canonicalize to the same bytes and mint one id between two documents no
      reader would call the same. That is precisely the silent-collision failure
      the key-type check exists to close, arriving through the front door with
      the conformance work; it is refused rather than accepted. Note what this
      makes true: the v2 number domain is *narrower* than v1's, which prints any
      `int` exactly. Conformance costs a little reach and buys portability.

      The bound is `jcs.PROFILE`'s and is stated there, not here, because it is
      a property of the serialization rather than of this boundary. Two things
      about it are worth knowing at this level. It is `2**53 - 1`, not `2**53`
      — the interoperable maximum RFC 7493 §2.2 and RFC 8259 §6 both state. And
      it is decided by the *token* a value would be sealed under, not by whether
      the caller handed in a Python `int` or a `float`, so `2**54` and
      `float(2**54)` get the same answer and a sealed document always survives
      being parsed and re-canonicalized.

    Nothing else is checked, for the same reason `canonical_bytes` checks
    nothing else: every further restriction would move ids without anyone
    deciding to.
    """
    bad_key = _find_bad_key(obj)
    if bad_key is not None:
        where, key = bad_key
        raise IdentityError(
            CANONICAL_KEY_TYPE,
            "refusing to mint an id over a non-string object key: %s has the "
            "key %s of type %s. JSON object names are strings (RFC 8259 §4) "
            "and RFC 8785 §3.1 inherits that from I-JSON, so the key is "
            "rejected here rather than silently rewritten."
            % (where, _safe_repr(key), type(key).__name__),
            {"path": where, "key_type": type(key).__name__,
             "key": _safe_repr(key)},
        )
    try:
        return _jcs.canonical_bytes(obj)
    except _jcs.JcsError as ex:
        raise _translate_jcs(ex, obj) from ex


#: `JcsError.code` → the `IdentityError` code it is reported as. One refusal
#: vocabulary is presented to callers whichever scheme they asked for, so an
#: `except IdentityError` written against v1 keeps its reach over v2.
_JCS_CODES = {
    _jcs.JCS_KEY_TYPE: CANONICAL_KEY_TYPE,
    _jcs.JCS_NON_FINITE: CANONICAL_NON_FINITE,
    _jcs.JCS_ENCODING: CANONICAL_ENCODING,
    _jcs.JCS_INT_RANGE: CANONICAL_INT_RANGE,
}


def _translate_jcs(ex, obj):
    """A `JcsError` as the `IdentityError` this boundary promises.

    `JCS_NOT_JSON` is deliberately **not** in `_JCS_CODES` and is re-raised as a
    `TypeError`. That is what `json.dumps` does for a value JSON has no
    representation for, so the two schemes agree on it, and a law that pins "a
    non-JSON leaf is a `TypeError`, not a typed identity refusal" keeps holding
    under both. A typed refusal names a *law of this system*; "you passed a
    `set`" is a caller bug and belongs to Python.

    The path in the detail is the serializer's, which walks the document it was
    given. `obj` is accepted so the non-finite case can be located by the same
    helper the legacy path uses and the two refusals read alike.
    """
    if ex.code == _jcs.JCS_NOT_JSON:
        return TypeError(ex.message)
    detail = dict(ex.detail)
    if ex.code == _jcs.JCS_NON_FINITE:
        found = _find_non_finite(obj)
        if found is not None:
            where, value = found
            return IdentityError(
                CANONICAL_NON_FINITE,
                "refusing to mint an id over a non-finite number: %s is %r. "
                "JSON has no NaN or Infinity, so the bytes hashed would not be "
                "JSON and no conforming parser could read the preimage back "
                "(RFC 8785 §3.2.2.3)." % (where, value),
                {"path": where, "value": repr(value)})
    if ex.code == _jcs.JCS_ENCODING:
        found = _find_unencodable(obj)
        if found is not None:
            where, position, text_value = found
            detail.setdefault("position", position)
            detail["path"] = where
            detail["string"] = _safe_repr(text_value)
    return IdentityError(_JCS_CODES[ex.code], ex.message, detail)


def _id(prefix: str, canon: bytes) -> str:
    return prefix + "-" + hashlib.sha256(canon).hexdigest()


def _drop(d: Mapping[str, Any], *keys: str) -> dict:
    return {k: v for k, v in d.items() if k not in keys}


# --- SnapshotV1 → snap- (RFC Evidence Residency §4) --------------------------
# `snap-` seals only the subject: repository file content-hashes, normalized
# relative paths, file modes, exclusions, base revision, task-visible config.
# `files` is a map, so file order never moves the snapshot; a byte change lands
# in a file's content-hash and does move it.

def canonicalize_snapshot(snapshot: Mapping[str, Any]) -> bytes:
    return canonical_bytes(_drop(snapshot, "snapshot_id"))


def snapshot_id(snapshot: Mapping[str, Any]) -> str:
    return _id("snap", canonicalize_snapshot(snapshot))


# --- FindingV1 → finding- (RFC Evidence Residency §5) ------------------------
# Structured claims + citations. Citation object *keys* are sorted (key order is
# cosmetic); a changed span or quote is a semantic change and moves finding-.

def canonicalize_finding(finding: Mapping[str, Any]) -> bytes:
    return canonical_bytes(_drop(finding, "finding_id"))


def finding_id(finding: Mapping[str, Any]) -> str:
    return _id("finding", canonicalize_finding(finding))


# --- PatchV1 → patch- (RFC Evidence Residency §5a) ---------------------------
# A unified diff. The frozen canonical rule normalizes line endings to LF so a
# CRLF/LF difference in the same diff is not a semantic change; the diff text
# itself is otherwise byte-significant.

def _normalize_diff(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonicalize_patch(patch: Mapping[str, Any]) -> bytes:
    p = _drop(patch, "patch_id")
    if isinstance(p.get("diff"), str):
        p["diff"] = _normalize_diff(p["diff"])
    return canonical_bytes(p)


def patch_id(patch: Mapping[str, Any]) -> str:
    return _id("patch", canonicalize_patch(patch))


# --- TraceV1 → trace- (RFC Evidence Residency §5a) ---------------------------
# The canonical trace records deterministic events only. Volatile execution
# metadata (wall-clock timestamps, host paths, human-readable timed logs) is
# projected out and never enters trace-.
#
# ``execution_limits_version`` is the one **optional** member, and the
# projection below already handles it without any special case: keys are copied
# ``if k in e``, so an event that does not carry it canonicalizes to exactly the
# bytes it did before this key existed, and every ``trace-…`` ever sealed stays
# where it is. It is present only on a run that hit a declared resource bound —
# a run that, before ``execlimits``, produced no trace at all because it hung or
# exhausted the host. So the profile name is sealed exactly where a replayer
# needs to know which bounds refused, and it moves nothing anywhere else.
_TRACE_EVENT_KEYS = (
    "command", "cwd", "environment_keys", "exit_code",
    "stdout_digest", "stderr_digest",
    "files_created_digest", "files_modified_digest", "files_deleted_digest",
    "file_modes_changed_digest", "result_file_digest", "policy_violations_digest",
    "execution_limits_version",
)


def canonicalize_trace(trace: Mapping[str, Any]) -> bytes:
    events = [
        {k: e[k] for k in _TRACE_EVENT_KEYS if k in e}
        for e in trace.get("events", [])
    ]
    return canonical_bytes({
        "trace_version": trace.get("trace_version"),
        "events": events,
    })


def trace_id(trace: Mapping[str, Any]) -> str:
    return _id("trace", canonicalize_trace(trace))


# --- RewardSpecV1 → rew- (RFC Artifacts §2) ----------------------------------
# Signals are a keyed map, so signal-map order never moves rew-; renaming,
# rebinding, adding, removing, or reweighting a signal does move it.

def canonicalize_reward(reward: Mapping[str, Any]) -> bytes:
    return canonical_bytes(_drop(reward, "reward_id"))


def reward_id(reward: Mapping[str, Any]) -> str:
    return _id("rew", canonicalize_reward(reward))


# --- TaskSpecV1 → task- (RFC Artifacts §3) -----------------------------------
# Identity includes the instructions and the agent run policy; changing either
# moves task-. The referenced reward_id is part of the task, so rebinding the
# reward also moves task-.

def canonicalize_task(task: Mapping[str, Any]) -> bytes:
    return canonical_bytes(_drop(task, "task_id"))


def task_id(task: Mapping[str, Any]) -> str:
    return _id("task", canonicalize_task(task))


# --- EpisodeReceiptV1 → episode- (RFC Artifacts §4) --------------------------
# The whole receipt is hashed *except* its own id and all volatile metadata.
# We use an explicit identity allowlist: any field outside it (wall-clock
# timestamps, absolute paths, transient PIDs, display formatting, host-specific
# log locations) is excluded by construction. Canonical execution_facts
# (resolved toolchain versions, normalized platform, exit codes, timeout state,
# output-truncation state) and verifier_versions ARE inside the allowlist, so a
# toolchain / platform / exit-code / verifier-version change moves episode-.
_EPISODE_IDENTITY_KEYS = (
    "episode_version", "canonicalization", "substrate_profile", "task_id",
    "reward_id", "subject",
    "trace_id", "outputs", "verification", "verification_evidence",
    "verifier_versions", "reward", "status", "validity", "replayability",
    "execution_facts",
)

#: The frozen serializer every rung has always used and every rung but this one
#: still uses: `json.dumps(sort_keys, minimal separators, ensure_ascii=False,
#: allow_nan=False)` plus the three domain checks. Named so that a document can
#: say which serializer it was sealed under instead of leaving it to be inferred
#: from the release that produced it.
SCHEME_LEGACY = "traaviis.canonical-json.v1"

#: RFC 8785 serialization over a named numeric input domain, as implemented in
#: `traaviis.jcs`. **This declared name was `"rfc8785-v1"` and was changed
#: before any document carrying it shipped**, because it overclaimed: it said
#: "RFC 8785" to a reader while accepting strictly less than RFC 8785 accepts.
#: What is implemented is the RFC's serialization plus `jcs.PROFILE`'s frozen
#: integer domain, so that is what the wire says. The value is taken from
#: `jcs.PROFILE` rather than repeated here — one string, one place, so a future
#: profile revision cannot leave the declared name pointing at the old domain.
SCHEME_RFC8785 = _jcs.PROFILE

#: Declared `episode_version` → the canonicalization it selects. **A build
#: knows exactly the schemes in this table and refuses every other**, which is
#: the fourth instance in this codebase of a reader that stops rather than
#: guesses (`bundle.py`, `pack.py`, `episode_bundle.py` are the other three).
#: Adding a row here is how a scheme ships; there is no fallback row, no
#: `.get(..., default)`, and no "looks like a v1" heuristic anywhere.
EPISODE_SCHEMES = {
    "traaviis.episode.v1": SCHEME_LEGACY,
    "traaviis.episode.v2": SCHEME_RFC8785,
}

#: Canonicalization name → the function that performs it.
CANONICALIZERS = {
    SCHEME_LEGACY: canonical_bytes,
    SCHEME_RFC8785: canonical_bytes_rfc8785,
}

#: Schemes whose receipts must carry a `canonicalization` field naming them.
#: `traaviis.episode.v1` is absent on purpose — see `episode_scheme`.
DECLARES_CANONICALIZATION = frozenset((SCHEME_RFC8785,))


def episode_scheme_declaration(episode_version: Any) -> Any:
    """The `canonicalization` value a receipt of this version must carry, or None.

    The *producer's* half of `episode_scheme`'s check, so that the rule "which
    versions carry the field" is written down once. A builder asks this what to
    stamp; `episode_scheme` asks the same table what to require. Two call sites,
    one table, no way for the minter and the verifier to hold different beliefs
    about which shape is correct — which is the failure a redundant field is
    otherwise an invitation to.

    An unknown version yields None rather than raising. Refusing is
    `episode_scheme`'s job and it happens a moment later, when the id is sealed;
    raising here as well would give one mistake two refusals that could drift.
    """
    scheme = EPISODE_SCHEMES.get(episode_version) \
        if isinstance(episode_version, str) else None
    return scheme if scheme in DECLARES_CANONICALIZATION else None


def episode_scheme(receipt: Mapping[str, Any]) -> str:
    """The canonicalization `receipt` declares, or a typed refusal.

    **The grammar is public; this field is the authority for interpreting it.**
    An `episode-` id is `episode-<64 lowercase hex>` under every scheme, now and
    permanently: the id is a lexical compatibility promise, so nothing about it
    changes, and the 21 `startswith("episode-")` sites, the path components, the
    URI builders and every literal already published stay exactly as they are.
    What varies is *how the bytes under the digest were produced*, and that is
    read off the receipt.

    Two properties make this safe, and only the first is obvious.

    **The declaration is inside the hash.** `episode_version` is the first entry
    of `_EPISODE_IDENTITY_KEYS`, so it is part of what the digest covers. A
    receipt that lies about its scheme therefore computes a *different* id and
    fails; the scheme cannot be edited after the fact, cannot be stripped, and
    cannot be relabelled. This is the property a versioned id *prefix* would not
    have — a tag outside the digest is verified by nothing, and a producer could
    stamp any label on any bytes.

    **An unknown scheme is refused, not guessed.** There is no default. A build
    that meets `traaviis.episode.v3` says so and stops, rather than reaching for
    the serializer it happens to have and computing a confident wrong answer. A
    verifier that guesses is a verifier that can be wrong without failing, which
    is the failure class this whole module is arranged against.

    **On the second field.** A receipt under a non-legacy scheme also carries
    `canonicalization`, so it is self-describing to a reader who has the bytes
    but not this table. That field is deliberately **not** an independent axis:
    `episode_version` alone selects the canonicalizer, and `canonicalization` is
    required to *agree* with what it implies. A second field that could be
    consulted would be a second place to be wrong; a second field that must
    agree is a redundancy check, and disagreement is refused here rather than
    resolved. `traaviis.episode.v1` requires the field to be **absent**, because
    every v1 receipt that exists was sealed without it and admitting it
    optionally would create two v1 shapes — one of which no v1 document has.

    **Mixed-scheme episodes are the normal case, and are coherent.** A v2
    receipt names a `task_id`, a `reward_id`, a `subject.snapshot_id`, a
    `trace_id` and `outputs.finding_id` / `patch_id` that are all still minted
    under the legacy scheme, because those rungs are not versioned by this
    change and their ids are frozen keys. That is not an inconsistency, because
    an id inside a receipt is an opaque *string* to the receipt: `episode-`
    hashes the characters `task-52bd…`, never the task document. What the
    receipt asserts is "these are the artifacts", and it asserts it by name. The
    scheme declared here governs exactly one thing — the serialization of *this*
    document — and claims nothing about how the documents it names were sealed.
    Each rung's own `*_version` continues to answer that for itself.

    The corollary is the reason only `episode-` moved: the rungs form a
    dependency graph — `task-` contains `reward_id` and `snapshot_id`, `env-`
    contains `task_id`s and `reward_id`s, `bundle-` contains `env_id` — and
    `episode-` is the only rung *nothing else contains*. Versioning any other
    rung would restamp it, which would change the id string that a task or an
    environment carries, which would move those too. `episode-` is the leaf, and
    versioning a leaf moves one rung.
    """
    declared = receipt.get("episode_version")
    scheme = EPISODE_SCHEMES.get(declared) if isinstance(declared, str) else None
    if scheme is None:
        raise IdentityError(
            EPISODE_SCHEME_UNKNOWN,
            "refusing to canonicalize a receipt declaring episode_version %r: "
            "this build knows %s and no other. The declared version selects the "
            "canonicalization, so serializing it under a guessed scheme would "
            "compute a confident wrong id rather than fail."
            % (declared, ", ".join(sorted(EPISODE_SCHEMES))),
            {"episode_version": _safe_repr(declared),
             "known": sorted(EPISODE_SCHEMES)},
        )
    stated = receipt.get("canonicalization")
    wanted = episode_scheme_declaration(declared)
    if stated != wanted:
        raise IdentityError(
            EPISODE_SCHEME_DECLARATION,
            "refusing to canonicalize a receipt whose canonicalization "
            "declaration is %r when episode_version %r implies %r. The version "
            "is the authority and the field is a redundancy check, so the two "
            "disagreeing is a broken document, not a choice to be resolved."
            % (stated, declared, wanted),
            {"episode_version": declared, "declared": _safe_repr(stated),
             "implied": wanted},
        )
    return scheme


def canonicalize_episode(receipt: Mapping[str, Any]) -> bytes:
    return CANONICALIZERS[episode_scheme(receipt)]({
        k: receipt[k] for k in _EPISODE_IDENTITY_KEYS if k in receipt
    })


def episode_id(receipt: Mapping[str, Any]) -> str:
    return _id("episode", canonicalize_episode(receipt))


# --- EnvironmentV1 → env- (RFC Artifacts §5) ---------------------------------
# D3: `env-` seals the *manifest* — substrate profile, subject, the task and
# reward sets, the substrate profiles, and split membership. It does NOT seal
# presentation: renaming an environment or rewriting its description moves
# `bundle-` (the distributed package), never `env-`. An explicit allowlist is
# what makes that law hold by construction rather than by convention.
_ENVIRONMENT_IDENTITY_KEYS = (
    "environment_version", "substrate_profile", "subject", "tasks", "rewards",
    "profiles", "splits",
)


def canonicalize_environment(env: Mapping[str, Any]) -> bytes:
    return canonical_bytes({
        k: env[k] for k in _ENVIRONMENT_IDENTITY_KEYS if k in env
    })


def environment_id(env: Mapping[str, Any]) -> str:
    return _id("env", canonicalize_environment(env))


# --- BundleManifestV1 → bundle- (RFC Artifacts §5b) --------------------------
# `bundle-` is the content address of the **canonical logical file tree** that
# `trvs pack` emits: `env-` plus every shipped presentation, documentation and
# screenshot member, identified by normalized relative POSIX path, file bytes
# and canonical mode. It is deliberately NOT the hash of an archive's bytes —
# a package serialized as ZIP and as tar must keep one identity, so ZIP
# compression, ordering and timestamps are outside it. The archive's own
# SHA-256 is a transport checksum, a different claim (see `bundle.py`).
#
# Unlike every other rung, nothing is projected out but the id field itself:
# the manifest *is* the package, so an added field is a changed package. The
# self-exclusion is the whole subtlety — `TRAAVIIS_BUNDLE.json` also excludes
# itself from `members`, so there is no member whose hash would have to contain
# its own hash.

def canonicalize_bundle(manifest: Mapping[str, Any]) -> bytes:
    return canonical_bytes(_drop(manifest, "bundle_id"))


def bundle_id(manifest: Mapping[str, Any]) -> str:
    return _id("bundle", canonicalize_bundle(manifest))
