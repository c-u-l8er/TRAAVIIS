"""One bounded JSON boundary: parse under declared bounds, refuse in one shape.

Why this module exists
======================
Three separate rounds of the same defect happened in this tree, in three
different files, for the same reason::

    except (ValueError, UnicodeDecodeError):

does not catch ``RecursionError``. A `RecursionError` is a `RuntimeError`, so a
*legal* JSON document nested past the decoder's stack -- 200 000 nested arrays
is 400 kB of ordinary bytes anybody can write -- walked straight through every
one of those clauses and killed the process. A crashed evaluator persists no
receipt, and `batch`/`compare` refuse a pair they cannot read, so a candidate
who crashes the reader has *erased* a bad score rather than earned a good one.
The refusal being untyped is the whole payoff.

Two sites were fixed by widening the clause. Then five more were found. Then
five more were named and left unswept, three of which caught only `ValueError`,
which is narrower still. Sweeping one handler at a time loses this race: the
defect is not in any handler, it is in there being N handlers at all, each an
independent chance to enumerate the decoder's failure modes slightly wrong.

So the closure is not a wider clause. It is **one boundary that every reader
goes through**, whose refusals are a single type, and whose bounds are facts
about the bytes rather than facts about the host.

There was also a path with no exception at all. ``episode_bundle`` wrote members
with ``json.dump(obj, fh, indent=2)`` -- pretty-printing is O(depth^2) in output
size, because every level of nesting adds its own indent to every line below it.
Measured: a depth-5 000 / 10 kB input produced **50 080 218 bytes** and exited 0.
Projected at depth 45 000 / 90 kB: **4 050 180 003 bytes**, which never finished,
so nothing persisted -- the same grade-erasure payoff as the crash, reached
without raising anything at all. Note what a wider `except` would have done
about that: nothing. It is not an exception, it is a resource.

The bounds
==========
Frozen, and deliberately *declared* rather than discovered::

    MAX_INPUT_BYTES        8 MiB    encoded input
    MAX_DEPTH              128      container nesting
    MAX_NODES              100 000  JSON values
    MAX_STRING_BYTES       8 MiB    total UTF-8 bytes of all strings + keys
    MAX_OUTPUT_BYTES       8 MiB    serialized output
    MAX_DIAGNOSTIC_BYTES   64 KiB   per verifier diagnostic

A bound is worth having only if it is the same number on every machine.
`runner.run_agent` says this in its own words while explaining why it does *not*
catch `MemoryError`: whether a document exhausts memory is a fact about the
host, so catching it would score the same submission `fail` on one machine and
let it through on another, and *"the real closure for size is a declared byte
bound on the result file (a fact about the bytes, identical on every host)"*.
This module is that closure. `MemoryError` is still not caught here, for exactly
the reason that precedent gives.

Depth is checked **lexically, on the raw text, before `json.loads` is called**.
That is not a re-derivation of the decoder's domain -- the objection
`runner.run_agent` raises against a pre-parse depth check, and rightly, since a
check that tries to predict *what nesting this host's decoder can take* is a
second copy of a host-dependent fact and will drift. 128 is not that. It is a
declared limit far below what any host's decoder can take, so the scan's answer
is the same everywhere and `json.loads` is never handed a document that could
recurse past its stack in the first place. `RecursionError` is still caught,
because a guard that relies on nothing is worth more than a guard that relies on
its own arithmetic being right.

What refusals look like
=======================
`BoundedJsonError` subclasses `ValueError` **deliberately** -- the same choice,
for the same reason, that `identity.CanonicalizationError` makes. Every existing
reader in this tree already catches `ValueError` at minimum. So the instant a
site routes through here, its refusal is typed *even if that site's own handler
is one of the narrow ones that has not been swept yet*. The subclassing is what
makes the sweep safe to do incrementally rather than all-or-nothing, and it
means a site added later by somebody who never read this docstring still cannot
produce an untyped crash.

Each refusal carries a machine-readable `reason` from `REASONS`, so a caller can
map every way this boundary can say no onto its own single refusal code without
enumerating exception classes again -- which is the mistake this module exists
to stop repeating.

What this module does NOT do
============================
It does not write. `dump_json_bounded` returns bytes and the caller writes them.
That is the entire mechanism behind "no partial output is written after a
refusal": there is no file handle open at the moment a bound is tested, because
the bytes do not exist yet. The previous shape -- open the file, then
``json.dump`` into it -- cannot make that promise, since the serializer is
writing as it walks and any failure mid-walk leaves a truncated member on disk.
A half-written bundle member is worse than a crash: a crash is visibly a crash,
whereas a truncated member is a document that will be read back, fail to parse,
and be attributed to whoever submitted it.

It also does not change any existing bytes. Callers that pretty-print keep
pretty-printing, with the identical `indent` / `sort_keys` / `ensure_ascii`
arguments they always passed; the only change is that the bounds are proven
*first* and the serialization happens in memory. `pack._doc`'s output in
particular is inside a content digest (its bytes are sha256'd into the bundle
manifest and therefore into `bundle-`), so compacting it would move published
ids. Nothing here compacts anything.
"""

import json

__all__ = [
    "BoundedJsonError",
    "MAX_INPUT_BYTES",
    "MAX_DEPTH",
    "MAX_NODES",
    "MAX_STRING_BYTES",
    "MAX_OUTPUT_BYTES",
    "MAX_DIAGNOSTIC_BYTES",
    "REASONS",
    "load_json_bounded",
    "validate_json_ast",
    "dump_json_bounded",
    "bound_diagnostic",
]


# --------------------------------------------------------------- the bounds
#: Maximum encoded input accepted by `load_json_bounded`.
MAX_INPUT_BYTES = 8 * 1024 * 1024

#: Maximum container nesting. A scalar is depth 0; ``[]`` is depth 1.
MAX_DEPTH = 128

#: Maximum JSON *values*. Object member keys are not values and are not counted
#: here -- they are counted toward `MAX_STRING_BYTES`. So ``[1, 2]`` is 3 nodes
#: (the array and its two numbers) and ``{"a": 1}`` is 2 (the object and the 1).
MAX_NODES = 100000

#: Maximum total UTF-8 bytes across every string in the document, keys included.
MAX_STRING_BYTES = 8 * 1024 * 1024

#: Maximum serialized output from `dump_json_bounded`.
MAX_OUTPUT_BYTES = 8 * 1024 * 1024

#: Maximum diagnostic text a single verifier may attach to its evidence.
#: Exposed here so the bound is declared in one place; the verifier modules
#: apply it (this module owns the number, not the wiring).
MAX_DIAGNOSTIC_BYTES = 64 * 1024


#: Every way this boundary can refuse, as stable machine-readable tokens.
#:
#: Callers map these onto their own refusal codes. The point of the token is
#: that a caller never has to name an exception class to know what happened --
#: enumerating exception classes at each site is precisely the failure this
#: module closes.
REASONS = (
    "input_too_large",    # encoded input exceeded MAX_INPUT_BYTES
    "not_utf8",           # the bytes are not valid UTF-8
    "malformed",          # not valid JSON (syntax, or the CPython int-digit cap)
    "too_deep",           # container nesting exceeded MAX_DEPTH
    "too_many_nodes",     # value count exceeded MAX_NODES
    "strings_too_large",  # total string bytes exceeded MAX_STRING_BYTES
    "output_too_large",   # serialized output would exceed MAX_OUTPUT_BYTES
    "not_serializable",   # the object is not JSON (a type, or a cycle)
)


class BoundedJsonError(ValueError):
    """A document was refused at the bounded JSON boundary.

    Subclasses `ValueError` on purpose; see the module docstring. `reason` is
    one of `REASONS`; `detail` carries the numbers involved, so a refusal can
    say *how far* over a bound a document was without the caller re-measuring.
    """

    def __init__(self, reason, message, detail=None):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.detail = dict(detail or {})

    def __str__(self):
        return self.message


# ------------------------------------------------------------ lexical depth
def _scan_depth(text):
    """Maximum container nesting in `text`, by lexical scan. No recursion.

    Runs *before* `json.loads` so an over-deep document is refused rather than
    handed to a recursive-descent decoder. It only has to be correct about
    bracket nesting, which needs exactly one piece of JSON knowledge: brackets
    inside string literals are not brackets. Hence the string/escape state --
    without it, ``{"a": "[[[["}`` would read as depth 5.

    Deliberately not a validator. It does not care whether the brackets balance
    or whether anything else about the document is well-formed; `json.loads` is
    the authority on that and this scan must not become a second, drifting
    opinion on what valid JSON is. Its only claim is a lower bound on nesting
    that is exact for well-formed input -- which is all that is needed, because
    a document this refuses never reaches the decoder, and a document it admits
    is shallow enough that the decoder cannot overflow on it.
    """
    depth = 0
    deepest = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{" or ch == "[":
            depth += 1
            if depth > deepest:
                deepest = depth
                if deepest > MAX_DEPTH:
                    # Stop at the first proof. A 4 GB document of open brackets
                    # must not be scanned to the end to learn what its second
                    # bracket already established.
                    return deepest
        elif ch == "}" or ch == "]":
            if depth > 0:
                depth -= 1
    return deepest


# ------------------------------------------------------------------ the API
def validate_json_ast(obj,
                      max_depth=MAX_DEPTH,
                      max_nodes=MAX_NODES,
                      max_string_bytes=MAX_STRING_BYTES):
    """Refuse `obj` if it breaks a structural bound. Returns a measurement dict.

    Walks **iteratively**, over an explicit stack. A recursive walk would be a
    third place in this codebase where a deep document turns into a
    `RecursionError`, and it would raise it from the very code whose job is to
    prevent that -- a guard that fails the way the thing it guards fails is not
    a guard.

    Returns ``{"depth", "nodes", "string_bytes"}`` for the accepted document, so
    a caller that wants to record what it admitted does not have to walk again.

    The walk's domain is the JSON value types. Anything else is refused as
    `not_serializable` here rather than being left for `json.dumps` to hit
    halfway through writing -- which is the difference between a refusal and a
    truncated file.
    """
    depth = 0
    nodes = 0
    string_bytes = 0

    # (value, depth-of-that-value). Containers push their children.
    stack = [(obj, 1)]
    while stack:
        value, level = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise BoundedJsonError(
                "too_many_nodes",
                "document has more than %d JSON values" % max_nodes,
                {"max_nodes": max_nodes})

        if isinstance(value, dict):
            if level > depth:
                depth = level
            if level > max_depth:
                raise BoundedJsonError(
                    "too_deep",
                    "document nests deeper than %d containers" % max_depth,
                    {"max_depth": max_depth, "depth": level})
            for key, sub in value.items():
                if not isinstance(key, str):
                    # `json.dumps` would silently *coerce* an int key to a
                    # string. Refusing is the same call `identity` makes: a
                    # coerced key is a document nobody wrote.
                    raise BoundedJsonError(
                        "not_serializable",
                        "object key is %s, not a string" % type(key).__name__,
                        {"key_type": type(key).__name__})
                string_bytes += len(key.encode("utf-8", "surrogatepass"))
                if string_bytes > max_string_bytes:
                    raise BoundedJsonError(
                        "strings_too_large",
                        "total string bytes exceed %d" % max_string_bytes,
                        {"max_string_bytes": max_string_bytes})
                stack.append((sub, level + 1))
        elif isinstance(value, (list, tuple)):
            if level > depth:
                depth = level
            if level > max_depth:
                raise BoundedJsonError(
                    "too_deep",
                    "document nests deeper than %d containers" % max_depth,
                    {"max_depth": max_depth, "depth": level})
            for sub in value:
                stack.append((sub, level + 1))
        elif isinstance(value, str):
            string_bytes += len(value.encode("utf-8", "surrogatepass"))
            if string_bytes > max_string_bytes:
                raise BoundedJsonError(
                    "strings_too_large",
                    "total string bytes exceed %d" % max_string_bytes,
                    {"max_string_bytes": max_string_bytes})
        elif value is None or isinstance(value, (bool, int, float)):
            pass
        else:
            raise BoundedJsonError(
                "not_serializable",
                "value of type %s is not JSON" % type(value).__name__,
                {"value_type": type(value).__name__})

    # `depth` is recorded only when a *container* is visited, so it is
    # container nesting -- the thing `MAX_DEPTH` actually bounds -- and a
    # document of scalars reports 0. Counting every value's level instead
    # inflated the answer by one for any document whose deepest node is a
    # scalar: `{"a": 1}` came back as depth 2 when it nests one container.
    # Enforcement was never wrong (only containers are ever compared against
    # the bound); the number handed back to callers was, and a measurement that
    # disagrees with the bound it is reported alongside is a trap for whoever
    # reads it next.
    return {"depth": depth, "nodes": nodes, "string_bytes": string_bytes}


def load_json_bounded(data,
                      max_input_bytes=MAX_INPUT_BYTES,
                      max_depth=MAX_DEPTH,
                      max_nodes=MAX_NODES,
                      max_string_bytes=MAX_STRING_BYTES):
    """Parse `data` (bytes or str) under the declared bounds.

    Raises `BoundedJsonError` -- and only `BoundedJsonError` -- for every way
    these bytes can fail to be an in-bounds JSON document. That totality is the
    contract: *the same input receives the same typed refusal regardless of
    which command read it.*

    Order matters and is not arbitrary. Size, then UTF-8, then lexical depth,
    then parse, then structure. Each step is cheaper than the one after it and
    each one makes the next safe: the size check bounds the decode, the decode
    bounds the scan, and the scan is what guarantees `json.loads` is never
    handed something it could recurse to death on.

    `MemoryError` is deliberately **not** caught, matching `runner.run_agent`
    and `batch.load_candidate_set`: whether a document exhausts memory is a fact
    about the host, and a host-dependent refusal is worse than a visible crash
    because it scores the same submission differently on two machines.
    """
    if isinstance(data, (bytes, bytearray, memoryview)):
        raw = bytes(data)
        size = len(raw)
        if size > max_input_bytes:
            raise BoundedJsonError(
                "input_too_large",
                "input is %d bytes; the bound is %d" % (size, max_input_bytes),
                {"size": size, "max_input_bytes": max_input_bytes})
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BoundedJsonError(
                "not_utf8", "input is not valid UTF-8: %s" % exc)
    elif isinstance(data, str):
        # Measured in *encoded* bytes, because the bound is on the encoded
        # input and a str of N characters can be up to 4N bytes. Character
        # count is checked first so an oversized str is refused without paying
        # for an encode that would itself be the memory problem.
        if len(data) > max_input_bytes:
            raise BoundedJsonError(
                "input_too_large",
                "input is over %d characters; the bound is %d encoded bytes"
                % (max_input_bytes, max_input_bytes),
                {"max_input_bytes": max_input_bytes})
        size = len(data.encode("utf-8", "surrogatepass"))
        if size > max_input_bytes:
            raise BoundedJsonError(
                "input_too_large",
                "input is %d bytes; the bound is %d" % (size, max_input_bytes),
                {"size": size, "max_input_bytes": max_input_bytes})
        text = data
    else:
        # Not routed through `BoundedJsonError`. `json.loads` raises `TypeError`
        # here too, and every site in this tree that reaches this line does so
        # only through a bug in itself -- the argument is always the result of a
        # read. `runner`, `batch` and `evalsplit` all say the same thing in
        # their own comments: a bug in the reader must not be reported as
        # somebody's malformed document.
        raise TypeError("load_json_bounded wants bytes or str, got %s"
                        % type(data).__name__)

    deepest = _scan_depth(text)
    if deepest > max_depth:
        raise BoundedJsonError(
            "too_deep",
            "document nests deeper than %d containers" % max_depth,
            {"max_depth": max_depth, "depth": deepest})

    try:
        obj = json.loads(text)
    except RecursionError:
        # Unreachable if `_scan_depth` is right, and caught anyway. The scan
        # is arithmetic and arithmetic can be wrong; the promise that a
        # `RecursionError` never escapes this boundary must not rest on it.
        raise BoundedJsonError(
            "too_deep",
            "document nests deeper than the decoder can descend",
            {"max_depth": max_depth})
    except UnicodeDecodeError as exc:
        # A `ValueError` subclass, so the clause below would take it; named
        # separately because it comes from a different call than the rest and
        # deserves its own reason token.
        raise BoundedJsonError("not_utf8", "input is not valid UTF-8: %s" % exc)
    except ValueError as exc:
        # `json.JSONDecodeError` (syntax), and the >4300-digit integer refusal
        # (CVE-2020-10735), which is a `ValueError` but not a decode error.
        raise BoundedJsonError("malformed", "not valid JSON: %s" % exc)

    validate_json_ast(obj, max_depth=max_depth, max_nodes=max_nodes,
                      max_string_bytes=max_string_bytes)
    return obj


def dump_json_bounded(obj,
                      indent=None,
                      sort_keys=True,
                      ensure_ascii=False,
                      trailing_newline=False,
                      max_output_bytes=MAX_OUTPUT_BYTES,
                      max_depth=MAX_DEPTH,
                      max_nodes=MAX_NODES,
                      max_string_bytes=MAX_STRING_BYTES):
    """Serialize `obj` to bytes, or refuse before producing anything.

    **Returns bytes; writes nothing.** The caller opens the file. That is what
    makes "no partial output after a refusal" structural rather than careful:
    every bound is tested while the output is still a value in memory, and a
    refusal happens with no file descriptor open anywhere.

    Bounds are proven on the *structure* first and the *size* second, in that
    order, because the structural bound is what makes pretty-printing safe at
    all. Pretty output is O(depth^2): the measured case was a depth-5 000
    document, 10 kB in, 50 080 218 bytes out, exit 0. `MAX_DEPTH` refuses that
    document at depth 128, before a single byte of indent is generated -- so the
    expansion never happens rather than happening and then being measured.
    Checking the output size alone would not close it, since measuring an
    oversized document requires first producing it.

    `indent` is passed through unchanged. This function does not compact
    anything: several callers' bytes are inside content digests (`pack._doc`
    feeds the bundle manifest's per-member sha256, and therefore `bundle-`), and
    a bounds check is not a licence to move a published id.
    """
    validate_json_ast(obj, max_depth=max_depth, max_nodes=max_nodes,
                      max_string_bytes=max_string_bytes)

    try:
        text = json.dumps(obj, indent=indent, sort_keys=sort_keys,
                          ensure_ascii=ensure_ascii)
    except RecursionError:
        # `validate_json_ast` walks iteratively and bounds depth at 128, so the
        # serializer cannot run out of stack on anything that got here. Caught
        # for the same reason as in `load_json_bounded`: the guarantee must not
        # depend on the guard's own arithmetic.
        raise BoundedJsonError(
            "too_deep", "document nests deeper than the encoder can descend",
            {"max_depth": max_depth})
    except (TypeError, ValueError) as exc:
        # A value `json.dumps` cannot serialize, or a circular structure (which
        # `json.dumps` reports as a `ValueError`).
        #
        # In practice `validate_json_ast` has already refused both: an
        # unserializable type as `not_serializable`, and a *cycle* as
        # `too_deep`. The cycle case is worth stating, because the obvious
        # worry about an iterative walk is that it would follow a cycle
        # forever -- it does not. Each hop down a cycle increments the level, so
        # a self-referential list is refused after 128 hops, in microseconds,
        # and `MAX_NODES` would stop it 100 000 hops later even if depth
        # somehow did not. The walk needs no seen-set.
        #
        # This clause stays anyway: it costs nothing, and the promise that this
        # function raises only `BoundedJsonError` must not rest on the walk's
        # domain being a perfect match for the serializer's.
        raise BoundedJsonError(
            "not_serializable", "object is not serializable as JSON: %s" % exc)

    if trailing_newline:
        text += "\n"
    out = text.encode("utf-8", "surrogatepass")
    if len(out) > max_output_bytes:
        raise BoundedJsonError(
            "output_too_large",
            "serialized output is %d bytes; the bound is %d"
            % (len(out), max_output_bytes),
            {"size": len(out), "max_output_bytes": max_output_bytes})
    return out


def bound_diagnostic(text, max_bytes=MAX_DIAGNOSTIC_BYTES):
    """Truncate one verifier diagnostic to `max_bytes` UTF-8 bytes.

    Truncation, not refusal, on purpose: a diagnostic is an explanation attached
    to a verdict that has already been reached, so discarding the verdict
    because its explanation ran long would let a candidate suppress its own bad
    score by emitting a very talkative failure. The truncation is marked so a
    reader never mistakes a cut-off diagnostic for a complete one.

    The cut is made in the encoded bytes and then repaired, rather than guessed
    at in characters: slicing UTF-8 at an arbitrary offset can land inside a
    multi-byte sequence, and a diagnostic that is not valid UTF-8 is a
    diagnostic that will fail to serialize later, at a point far away from here.
    `errors="ignore"` on the decode drops exactly the split trailing sequence
    and nothing else, so the result is always both valid and within budget.

    Budgeted so that the marker fits *inside* `max_bytes`. A truncation notice
    that pushed the result over the bound it was announcing would be its own
    small joke.
    """
    if not isinstance(text, str):
        text = str(text)
    encoded = text.encode("utf-8", "surrogatepass")
    if len(encoded) <= max_bytes:
        return text
    marker = "\n[diagnostic truncated at %d bytes]" % max_bytes
    budget = max_bytes - len(marker.encode("utf-8"))
    if budget <= 0:
        return marker.strip()
    return encoded[:budget].decode("utf-8", "ignore") + marker
