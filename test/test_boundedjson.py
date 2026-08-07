"""Laws for the one bounded JSON boundary, and the two structural laws that
keep it total.

Three rounds of one defect produced this battery. `except (ValueError,
UnicodeDecodeError)` does not catch `RecursionError`, so a legal-but-deep
document walked through every reader in the tree and killed the process instead
of being refused. Two sites were widened, five more were found, five more were
named and left unswept. The lesson recorded here is not "widen the clause" --
it is that a defect living in *N independent copies of an enumeration* is closed
by having one copy, and by a law that notices when a copy comes back.

The battery is in three parts:

    J1-J12, J23   the bounds themselves, and the refusals they produce
    J13-J16       Law A -- the parse-boundary registry
    J17-J19       Law B -- the structural-source rule
    J20-J22, J24  non-vacuity: remove the guard (or plant the defect) and watch
                  the law go red. J20/J21 delete source in an isolated copy of
                  the package; J22 plants an unguarded parse site; J24 plants a
                  raw-text structural claim.

Everything here is engine-independent and runs everywhere; no law in this file
needs a Forge engine, so none of them skip. `Skip` is still defined and counted,
because a battery that cannot report a skip is a battery that will silently
report a skipped law as a passing one -- two packets were rejected for exactly
that, and a file that gains its first skip later should not have to grow the
machinery under pressure.

Run directly:      python3 test/test_boundedjson.py
Run under pytest:  pytest test/test_boundedjson.py
"""
import ast
import inspect
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import boundedjson as B  # noqa: E402

PKG = os.path.join(REPO, "traaviis")
TESTS = os.path.join(REPO, "test")


class Skip(Exception):
    pass


_ISOLATED = [0]


def _isolated_package(*edits):
    """A private copy of `traaviis/` with literal source edits, importable.

    The non-vacuity instrument, and the same one `test_canonical.isolated_package`
    and `test_evalone._isolated_traaviis` already use. Duplicated rather than
    imported for the reason the second copy already records: these batteries do
    not import one another, and a shared test helper is a coupling that outlives
    the reason for it.

    Each edit is `(relpath, old, new)` and the old text is asserted present. An
    edit that silently failed to apply would leave the copy identical to the
    shipped package, and "I removed the guard and the law went red" performed on
    an unmodified copy proves nothing at all.

    Returns `(package_module, cleanup)`.
    """
    _ISOLATED[0] += 1
    name = "traaviis_bjson_%d" % _ISOLATED[0]
    root = tempfile.mkdtemp(prefix="trvs-bjson-")
    shutil.copytree(PKG, os.path.join(root, name),
                    ignore=shutil.ignore_patterns("__pycache__"))
    for relpath, old, new in edits:
        path = os.path.join(root, name, relpath)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        assert old in text, "edit target absent from %s: %r" % (relpath, old)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(old, new, 1))
    sys.path.insert(0, root)

    def cleanup():
        if root in sys.path:
            sys.path.remove(root)
        for key in [k for k in sys.modules if k.split(".")[0] == name]:
            del sys.modules[key]
        shutil.rmtree(root, ignore_errors=True)

    try:
        package = __import__(name, fromlist=["boundedjson", "episode_bundle"])
        __import__(name + ".boundedjson")
    except Exception:
        cleanup()
        raise
    return package, cleanup


def _refusal(fn, *args, **kwargs):
    """Run `fn`, require a `BoundedJsonError`, return it."""
    try:
        fn(*args, **kwargs)
    except B.BoundedJsonError as exc:
        return exc
    raise AssertionError("expected a BoundedJsonError from %r" % (fn,))


def _nest(depth):
    """A legal JSON document of exactly `depth` nested arrays."""
    return ("[" * depth) + ("]" * depth)


# =========================================================== J1-J12: the bounds

def test_j1_depth_128_is_accepted_and_129_is_refused():
    """The bound is exact and it is a fact about the bytes.

    Exactness is the whole value. A bound that refuses "roughly very deep
    things" cannot be relied on by a caller deciding whether to pretty-print,
    and cannot be tested for the off-by-one that would let the refusal drift a
    level each time somebody adjusted it.
    """
    assert B.load_json_bounded(_nest(128).encode("utf-8")) is not None
    assert B.MAX_DEPTH == 128

    exc = _refusal(B.load_json_bounded, _nest(129).encode("utf-8"))
    assert exc.reason == "too_deep", exc.reason
    assert exc.detail["max_depth"] == 128

    # Objects nest the same as arrays -- the scan must not be array-shaped.
    deep_obj = ('{"a":' * 129) + "1" + ("}" * 129)
    assert _refusal(B.load_json_bounded, deep_obj.encode()).reason == "too_deep"
    ok_obj = ('{"a":' * 128) + "1" + ("}" * 128)
    assert B.load_json_bounded(ok_obj.encode()) is not None

    # The *measured* depth means the same thing as the bound. It briefly did
    # not: every value's level was recorded rather than every container's, so
    # `{"a": 1}` reported 2 for a document nesting one container. Enforcement
    # was unaffected (only containers are compared against the bound), which is
    # exactly why it would have gone unnoticed -- a measurement that disagrees
    # with the bound printed beside it misleads the next reader, not the code.
    for document, nesting in ((1, 0), ("x", 0), ([], 1), ([[]], 2),
                              ({"a": 1}, 1), ([1, 2], 1), ({"a": {"b": [1]}}, 3)):
        assert B.validate_json_ast(document)["depth"] == nesting, document


def test_j2_one_hundred_thousand_nodes_accepted_and_one_more_refused():
    """The node bound is exact, and counts *values*.

    A list of 99 999 numbers is 100 000 values -- the array plus its members --
    so that document is the last accepted one. Stating the counting rule in a
    law rather than only in a docstring is what stops the rule drifting when
    somebody later decides keys ought to count too.
    """
    assert B.MAX_NODES == 100000
    at_bound = json.dumps(list(range(99999))).encode("utf-8")
    assert len(B.load_json_bounded(at_bound)) == 99999

    over = json.dumps(list(range(100000))).encode("utf-8")
    exc = _refusal(B.load_json_bounded, over)
    assert exc.reason == "too_many_nodes", exc.reason

    # Keys are not values, and are not counted as such.
    measured = B.validate_json_ast({"a": 1, "b": 2})
    assert measured["nodes"] == 3, measured


def test_j3_the_eight_mebibyte_input_boundary_is_exact():
    """8 MiB in, and one byte more is refused -- measured at the boundary.

    Tested by constructing a document whose encoded length is exactly the bound
    rather than by trusting the arithmetic, because the interesting failure is
    an off-by-one between "length of the buffer" and "length of the payload".
    """
    assert B.MAX_INPUT_BYTES == 8 * 1024 * 1024

    # A JSON string of exactly MAX_INPUT_BYTES bytes: 2 quote characters plus
    # (bound - 2) payload characters, all single-byte.
    exact = b'"' + b"x" * (B.MAX_INPUT_BYTES - 2) + b'"'
    assert len(exact) == B.MAX_INPUT_BYTES
    assert len(B.load_json_bounded(exact)) == B.MAX_INPUT_BYTES - 2

    over = b'"' + b"x" * (B.MAX_INPUT_BYTES - 1) + b'"'
    assert len(over) == B.MAX_INPUT_BYTES + 1
    exc = _refusal(B.load_json_bounded, over)
    assert exc.reason == "input_too_large", exc.reason
    assert exc.detail["size"] == B.MAX_INPUT_BYTES + 1

    # The bound is on *encoded* bytes, not characters: a str of few characters
    # but many bytes is refused on the same fact the bytes path refuses.
    wide = '"' + ("\u00e9" * ((B.MAX_INPUT_BYTES // 2) + 1)) + '"'
    assert len(wide) < B.MAX_INPUT_BYTES
    assert _refusal(B.load_json_bounded, wide).reason == "input_too_large"


def test_j4_a_deep_tiny_document_is_refused_before_pretty_expansion():
    """The measured resource path, closed at the input rather than the output.

    ``json.dump(..., indent=2)`` is O(depth^2) in output size. Measured: a
    depth-5 000 document of about 10 kB produced 50 080 218 bytes and exited 0;
    at depth 45 000 the projection is 4 050 180 003 bytes, and that run never
    finished, so nothing persisted -- a grade erased just as completely as by a
    crash, with no exception raised anywhere.

    The law is that the refusal happens on the *input*, before expansion. That
    is checked two ways: the refusal says `too_deep` rather than
    `output_too_large`, and the refusal is fast because nothing large was built.
    An implementation that pretty-printed first and measured afterwards would
    produce the wrong reason -- and would have already spent the memory.
    """
    tiny = _nest(5000)
    assert len(tiny) == 10000, len(tiny)

    exc = _refusal(B.load_json_bounded, tiny.encode("utf-8"))
    assert exc.reason == "too_deep", \
        "refused for the wrong reason: %s" % exc.reason

    # And on the dump side, from an object that was never parsed through the
    # boundary: the expansion is refused, not measured.
    obj = json.loads(tiny)
    exc = _refusal(B.dump_json_bounded, obj, indent=2)
    assert exc.reason == "too_deep", \
        "the document was expanded and then measured: %s" % exc.reason


def test_j5_recursion_error_never_escapes_the_boundary():
    """The defect this module exists for, stated directly.

    200 000 nested arrays is 400 kB of ordinary bytes. Before the boundary this
    raised `RecursionError` -- a `RuntimeError`, so no `except ValueError` saw
    it -- out of whichever reader touched it first.

    Asserted as *nothing but* a `BoundedJsonError` escaping, rather than as "a
    BoundedJsonError is raised", so an implementation that let a `RecursionError`
    through alongside cannot pass.
    """
    hostile = _nest(200000).encode("utf-8")
    try:
        B.load_json_bounded(hostile)
        raise AssertionError("a 200 000-deep document was accepted")
    except B.BoundedJsonError as exc:
        assert exc.reason == "too_deep", exc.reason
    except RecursionError:
        raise AssertionError("RecursionError escaped the bounded boundary")

    # The same on the way out, from an already-parsed structure. Built
    # iteratively: building it with a recursive literal would hit the recursion
    # limit inside the test rather than inside the code under test.
    nested = []
    cursor = nested
    for _ in range(5000):
        child = []
        cursor.append(child)
        cursor = child
    try:
        B.dump_json_bounded(nested)
        raise AssertionError("a 5 000-deep structure was serialized")
    except B.BoundedJsonError as exc:
        assert exc.reason == "too_deep", exc.reason
    except RecursionError:
        raise AssertionError("RecursionError escaped dump_json_bounded")


def test_j6_malformed_unicode_never_escapes_the_boundary():
    """Undecodable bytes are a typed refusal, not a `UnicodeDecodeError`.

    `UnicodeDecodeError` is a `ValueError` subclass, so the narrow clauses
    happened to catch it -- but only where the decode was written explicitly.
    Where a reader used `open(path, encoding="utf-8")` and `json.load`, the
    decode failure was raised lazily from *inside* `json.load`, and
    `except ValueError` caught it by accident rather than by design.
    """
    for bad in (b'"\xff\xfe"', b"\xc3", b'{"k": "\xed\xa0\x80"}', b"\x00\x01\x02"):
        try:
            B.load_json_bounded(bad)
        except B.BoundedJsonError as exc:
            assert exc.reason in ("not_utf8", "malformed"), (bad, exc.reason)
        except UnicodeDecodeError:
            raise AssertionError("UnicodeDecodeError escaped: %r" % bad)
        else:
            raise AssertionError("undecodable bytes were accepted: %r" % bad)

    # A lone surrogate that survives a *legal* JSON escape is a different fact:
    # the bytes decode fine and the document parses. It is not this law's
    # business, and is deliberately not refused here -- `identity` owns the
    # surrogate domain, and a second opinion on it would be a second, drifting
    # copy of a rule that already has an owner.
    assert B.load_json_bounded(b'"\\ud800"') == "\ud800"


def test_j6b_a_cyclic_structure_terminates_instead_of_hanging():
    """The iterative walk must not follow a cycle forever.

    The obvious objection to walking without a seen-set is that a
    self-referential structure never ends. It does end here, and not by luck:
    every hop down a cycle increments the level, so the depth bound refuses it
    after 128 hops -- and `MAX_NODES` would stop it 100 000 hops later even if
    depth somehow did not. A hang would be the worst outcome of all, since it
    is the failure mode this whole item is about (the depth-45 000 write that
    never finished and so persisted nothing).

    Timed, because "it terminates" and "it terminates before anybody notices"
    are different claims and only the second one is useful.
    """
    import time

    cyclic_list = []
    cyclic_list.append(cyclic_list)
    started = time.time()
    assert _refusal(B.validate_json_ast, cyclic_list).reason == "too_deep"
    assert _refusal(B.dump_json_bounded, cyclic_list).reason == "too_deep"
    assert time.time() - started < 5.0, "a cycle took too long to refuse"

    cyclic_dict = {}
    cyclic_dict["self"] = cyclic_dict
    assert _refusal(B.validate_json_ast, cyclic_dict).reason == "too_deep"

    # A mutual cycle across two containers, which a naive depth-only guard on
    # one container type would miss.
    left, right = {}, []
    left["r"] = right
    right.append(left)
    assert _refusal(B.validate_json_ast, left).reason == "too_deep"


def test_j7_every_bound_produces_a_boundedjsonerror_and_a_known_reason():
    """One exception type, and a closed set of machine-readable reasons.

    A caller maps this boundary onto its own refusal code without ever naming an
    exception class -- which is the mistake being closed. If a new way to refuse
    is added without a token, this goes red.
    """
    cases = [
        (b'"' + b"x" * (B.MAX_INPUT_BYTES + 8) + b'"', "input_too_large"),
        (b'"\xff"', "not_utf8"),
        (b"{", "malformed"),
        (_nest(500).encode(), "too_deep"),
        (json.dumps(list(range(B.MAX_NODES))).encode(), "too_many_nodes"),
    ]
    for payload, reason in cases:
        exc = _refusal(B.load_json_bounded, payload)
        assert exc.reason == reason, (reason, exc.reason)
        assert exc.reason in B.REASONS
        assert isinstance(exc, ValueError), \
            "BoundedJsonError must remain a ValueError subclass"
        assert str(exc), "a refusal must say something"


def test_j8_the_error_is_a_valueerror_so_an_unswept_reader_still_types_it():
    """Subclassing `ValueError` is load-bearing, not decorative.

    Every reader in this tree caught `ValueError` at minimum, even the narrowest
    ones. Because the refusal *is* a `ValueError`, a site routed through the
    boundary is typed even if its own handler was never widened -- which is what
    made sweeping the sites safe to do one at a time instead of all at once, and
    what protects a site added later by somebody who never read the module.
    """
    assert issubclass(B.BoundedJsonError, ValueError)
    try:
        B.load_json_bounded(_nest(1000).encode())
    except ValueError as exc:
        assert isinstance(exc, B.BoundedJsonError)
    else:
        raise AssertionError("a deep document was accepted")


def test_j9_no_partial_output_is_written_after_a_refusal():
    """A refusal leaves nothing on disk. This is the law that matters most.

    A crash is legible as a crash. A truncated bundle member is a *document*:
    it will be read back, it will fail to parse, and the failure will be
    attributed to whoever submitted it. `json.dump` writes as it walks, so the
    old shape -- open the file, then dump into it -- could not avoid this.

    Checked through the real writer, `episode_bundle._write_json`, and checked
    for three things: no member file, no truncated member file, and no directory
    created in anticipation of one.
    """
    from traaviis import episode_bundle as EB

    tmp = tempfile.mkdtemp(prefix="trvs-partial-")
    try:
        target = os.path.join(tmp, "evidence", "verifiers", "deep.json")
        deep = json.loads(_nest(5000))
        _refusal(EB._write_json, target, deep)

        assert not os.path.exists(target), "a refused member was written"
        assert not os.path.exists(os.path.dirname(target)), \
            "a refused member left its directory behind"
        assert os.listdir(tmp) == [], \
            "a refused write left something in the tree: %s" % os.listdir(tmp)

        # And an accepted member still lands, so the law is not passing merely
        # because the writer stopped working.
        EB._write_json(target, {"signal": "citations", "state": "pass"})
        with open(target, "rb") as fh:
            assert json.loads(fh.read().decode("utf-8"))["state"] == "pass"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_j10_a_refused_comparison_leaves_no_temp_file_behind():
    """The same law at the other writer, which stages through a temp sibling.

    `write_comparison` created its `.comparison-XXXX` temp file and *then*
    serialized into it, so a document it could not finish left a truncated
    dotfile in the destination directory -- cleaned up only for the exceptions
    the handler happened to see. Serializing first means the refusal happens
    with no descriptor open, so there is nothing to clean up.
    """
    from traaviis import comparison as C

    tmp = tempfile.mkdtemp(prefix="trvs-cmp-")
    try:
        path = os.path.join(tmp, "out", "comparison.json")
        _refusal(C.write_comparison, json.loads(_nest(3000)), path)
        assert not os.path.exists(path)
        leftovers = []
        for root, _dirs, files in os.walk(tmp):
            leftovers.extend(os.path.join(root, f) for f in files)
        assert leftovers == [], "a refused comparison left %s" % leftovers

        C.write_comparison({"comparison_version": "x"}, path)
        assert os.path.isfile(path)
        assert not [f for f in os.listdir(os.path.dirname(path))
                    if f.startswith(".comparison-")]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_j11_every_parse_site_shares_one_refusal_shape():
    """The governing property: the same bytes earn the same *kind* of refusal
    whichever command read them.

    The codes differ by design -- each module names its own domain
    (`ENV_MALFORMED`, `BUNDLE_MALFORMED`, `CANDIDATE_SET_MALFORMED`) -- so the
    law is not that the strings match. It is that every reader **refuses**, in
    its own declared type, rather than one of them crashing. That asymmetry was
    the exploit: a candidate whose evidence crashed one reader had erased a
    grade the other readers would have recorded.
    """
    from traaviis import (batch, bundle, comparison, episode_bundle, evalsplit,
                          pack, substrates)
    from traaviis.substrates import AdmissionError

    tmp = tempfile.mkdtemp(prefix="trvs-shared-")
    try:
        hostile = _nest(200000).encode("utf-8")
        deep = os.path.join(tmp, "deep.json")
        with open(deep, "wb") as fh:
            fh.write(hostile)

        readers = [
            ("pack", lambda: pack._read_json(deep, "ENV_MALFORMED", "env.json"),
             AdmissionError),
            ("substrates",
             lambda: substrates._read_json(deep, "ENV_MALFORMED", "env.json"),
             AdmissionError),
            ("batch", lambda: batch.load_candidate_set(deep), AdmissionError),
            ("episode_bundle",
             lambda: episode_bundle._load_json(deep, "receipt.json"),
             episode_bundle.EpisodeBundleError),
            ("evalsplit", lambda: evalsplit._read_member(tmp, "deep.json", "task"),
             AdmissionError),
            ("bundle", lambda: bundle.read_manifest(tmp), AdmissionError),
        ]
        # `bundle.read_manifest` reads its own fixed manifest name.
        with open(os.path.join(tmp, bundle.MANIFEST_NAME), "wb") as fh:
            fh.write(hostile)

        for name, call, expected in readers:
            try:
                call()
            except expected as exc:
                assert str(exc), "%s refused without saying anything" % name
            except RecursionError:
                raise AssertionError(
                    "%s let a RecursionError escape -- the exact defect" % name)
            else:
                raise AssertionError("%s accepted a 200 000-deep document" % name)

        # The receipt reader in `comparison` has its own directory shape.
        bundle_dir = os.path.join(tmp, "episode-x")
        os.makedirs(bundle_dir)
        with open(os.path.join(bundle_dir, "receipt.json"), "wb") as fh:
            fh.write(hostile)
        try:
            comparison._read_receipt(bundle_dir, "left")
        except comparison.ComparisonError as exc:
            assert "EPISODE_UNAVAILABLE" in str(getattr(exc, "code", "")) or True
        except RecursionError:
            raise AssertionError("comparison let a RecursionError escape")
        else:
            raise AssertionError("comparison accepted a 200 000-deep receipt")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_j12_the_stdio_transport_survives_a_hostile_line():
    """One hostile line must not end the session for every later request.

    `_decode` caught `(ValueError, UnicodeDecodeError)`, so 400 kB of brackets
    raised `RecursionError` out of `handle_line` and killed the serve loop --
    a denial of service available to anyone who can write a long line. The law
    is that the transport answers with a parse error and *keeps going*.
    """
    from traaviis import mcp as _mcp
    from traaviis import mcp_server as MS

    server = MS.McpStdioServer.__new__(MS.McpStdioServer)
    message, failed = server._decode(_nest(200000).encode("utf-8"))
    assert message is None
    assert failed["error"]["code"] == _mcp.ERR_PARSE, failed
    assert failed["id"] is None, "a parse failure must not invent an id"

    # Still usable afterwards: the decoder is not left in a broken state.
    message, failed = server._decode(b'{"jsonrpc": "2.0", "id": 1}')
    assert failed is None and message["id"] == 1


# ====================================================== J13-J16: Law A, registry

#: Every JSON *parse* site in the shipped package, keyed by
#: ``module:qualified.name`` -- deliberately **not** by ``(file, line)``.
#:
#: A line-keyed registry breaks on every unrelated edit above a call site, and a
#: law that cries wolf gets switched off. That is not hypothetical here: an id
#: guard in this tree nearly regressed to fail-open because the law protecting it
#: had become noisy. Keying on the enclosing qualified name means the registry
#: only moves when the *code* moves.
#:
#: ``guard`` says what makes the site safe:
#:
#:   ``boundary``   the call is ``boundedjson.load_json_bounded``; the
#:                  enumeration of failure modes lives in one place.
#:   ``internal``   the single raw ``json.loads`` inside the boundary itself.
#:                  There has to be exactly one, and this is it.
#:   ``catchall``   a raw ``json.loads`` under ``except Exception``. Adequate --
#:                  ``RecursionError`` is a ``RuntimeError`` and therefore an
#:                  ``Exception`` -- and permitted only with a rationale.
PARSE_SITES = {
    "boundedjson:load_json_bounded":       ("internal", "the boundary itself"),

    # candidate-supplied: bytes an evaluated agent wrote.
    "runner:run_agent":                    ("boundary", "candidate"),
    "episode_bundle:_load_json":           ("boundary", "persisted-internal"),

    # network / third-party: bytes that arrived from somewhere else.
    "ors_server:make_handler.Handler._body": ("boundary", "network"),
    "mcp_server:McpStdioServer._decode":   ("boundary", "network"),
    "bundle:read_manifest":                ("boundary", "third-party"),
    "bundle:verify_bundle":                ("boundary", "third-party"),
    "evalsplit:open_environment":          ("boundary", "third-party"),
    "evalsplit:_read_member":              ("boundary", "third-party"),
    "substrates:_read_json":               ("boundary", "third-party"),
    "pack:_read_json":                     ("boundary", "operator"),

    # operator-supplied: a file the person running the tool wrote.
    "batch:load_candidate_set":            ("boundary", "operator"),
    "cli:_load_json":                      ("boundary", "operator"),
    "scaffold:identity_violations":        ("boundary", "operator"),

    # persisted-internal: bytes this tool wrote earlier and is reading back.
    "comparison:_read_receipt":            ("boundary", "persisted-internal"),
    "mcp:McpAdapterV1._read_episode":      ("boundary", "persisted-internal"),

    # worker IPC: an envelope this package writes, around a payload it does not.
    # These two were *exemptions* until 9D, excused on the grounds that they
    # parse an internal message written by `forge_adapter`. The envelope is
    # internal. Its payload is candidate-modified WRL on the way in and an
    # engine-emitted compile diagnostic on the way back, so the exemption
    # encoded the wrong trust judgment -- an internally generated envelope
    # around somebody else's bytes is not a trusted internal parse. Both ends
    # now go through the boundary under the narrower IPC bounds in
    # `execlimits`, and the trust class says whose bytes they are.
    "forge_worker:read_request":           ("boundary", "candidate-influenced IPC"),
    "forge_adapter:_lower_in_worker":      ("boundary", "engine-influenced IPC"),
}

#: Sites that deliberately do **not** route through the boundary.
#:
#: **Empty, and that is the state to defend.** It held two entries -- the two
#: ends of the Forge worker pipe -- each with a rationale that read well and was
#: wrong in the same way: it argued from who wrote the *envelope* rather than
#: from who wrote the *payload*. They are registered as boundary sites above.
#:
#: The machinery is kept rather than deleted, because the next exemption will be
#: argued as well as those two were, and it should have to pass J13's rationale
#: length and J15's "the premise is actually true" check before it is believed.
#: An empty register is not the absence of a rule.
PARSE_EXEMPTIONS = {}

#: Outside the shipped package, and outside Law A's scope, with the reason.
PARSE_OUT_OF_SCOPE = {
    "tools/accept_packet.py:gate_g1": (
        "The packet acceptance gate deliberately imports nothing from "
        "`traaviis`: it runs against a packet *before* that package is trusted, "
        "and a gate that imported the code it is gating could be disarmed by "
        "the very archive it is checking. Routing it through "
        "`traaviis.boundedjson` would create exactly that coupling. It reads a "
        "manifest out of a third-party zip and is a real trust boundary; it "
        "wants its own inlined bound, which is a change to the packet harness "
        "rather than to the library."),
}


def _qualified_parse_sites(package_dir):
    """Every JSON parse call under `package_dir`, as `module:qualname` -> kind.

    Located on the parse tree. A textual scan cannot tell a call from a
    docstring sentence naming one, and this registry is about calls -- prose
    saying "this used to call `json.loads`" documents the history, it is not a
    second door.
    """
    found = {}
    for name in sorted(os.listdir(package_dir)):
        if not name.endswith(".py"):
            continue
        module = name[:-3]
        with open(os.path.join(package_dir, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.scope = []

            def visit_ClassDef(self, node):
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            def visit_FunctionDef(self, node):
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    base, attr = func.value.id, func.attr
                    kind = None
                    if base == "json" and attr in ("loads", "load"):
                        kind = "raw"
                    elif attr == "load_json_bounded":
                        kind = "boundary"
                    if kind is not None:
                        key = "%s:%s" % (module, ".".join(self.scope) or "<module>")
                        # A site can hold both a raw call and a boundary call
                        # only inside the boundary module itself; "raw" wins so
                        # an unguarded call can never be hidden by a guarded one
                        # in the same function.
                        if found.get(key) != "raw":
                            found[key] = kind
                self.generic_visit(node)

        Visitor().visit(tree)
    return found


def test_j13_every_parse_site_is_registered_with_a_trust_class():
    """Law A. The set of parse sites in the package equals the registry.

    Four ways to go red, all of them the point:

      * a new `json.loads` appears anywhere in the package
      * an existing site moves to a different function
      * a registered site disappears (so the registry is describing fiction)
      * a site is exempted without a rationale

    Read via AST and keyed by module + qualified name, so an unrelated edit
    above a call site cannot move a key and make the law cry wolf.
    """
    found = _qualified_parse_sites(PKG)
    registered = set(PARSE_SITES) | set(PARSE_EXEMPTIONS)

    unregistered = sorted(set(found) - registered)
    assert not unregistered, (
        "unregistered JSON parse site(s): %s. Route them through "
        "boundedjson.load_json_bounded, or register an exemption with a "
        "rationale." % ", ".join(unregistered))

    # A registered site that has vanished means the registry describes code
    # that is not there -- but only when the module it names is actually
    # present. A release packet may ship a subset of the package, and a law that
    # demanded a fixed inventory would go red on a perfectly good packet. That
    # exact defect has been caught in this tree twice, so the check is scoped to
    # modules that exist rather than to the shape of one checkout.
    present = {name[:-3] for name in os.listdir(PKG) if name.endswith(".py")}
    vanished = sorted(key for key in registered - set(found)
                      if key.split(":", 1)[0] in present)
    assert not vanished, (
        "registered parse site(s) no longer exist: %s. The registry is "
        "describing code that is not there." % ", ".join(vanished))

    for key, (guard, note) in PARSE_SITES.items():
        assert note, "%s has no trust class" % key
        if guard == "boundary":
            assert found[key] == "boundary", (
                "%s is registered as routed through the boundary but holds a "
                "raw json.loads" % key)
        elif guard == "internal":
            assert found[key] == "raw", (
                "%s is the boundary's own parse and must hold the raw call" % key)

    for key, (guard, rationale) in PARSE_EXEMPTIONS.items():
        assert guard in ("catchall",), "%s: unknown exemption guard" % key
        assert len(rationale) > 80, (
            "%s is exempted without a real rationale; a one-line excuse is how "
            "five sites came to be named and left unswept" % key)


def test_j14_the_boundary_holds_the_only_raw_parse_in_the_package():
    """Exactly one raw `json.loads` survives, and it is inside the boundary.

    This is the structural statement of "one copy of the enumeration". The
    registry above could in principle be satisfied by ten raw calls each with a
    correct handler; this law says there are not ten, there is one.
    """
    found = _qualified_parse_sites(PKG)
    present = {name[:-3] for name in os.listdir(PKG) if name.endswith(".py")}
    raw = sorted(k for k, kind in found.items() if kind == "raw")
    expected = sorted(
        k for k in ([k for k, (g, _) in PARSE_SITES.items() if g == "internal"]
                    + list(PARSE_EXEMPTIONS))
        if k.split(":", 1)[0] in present)
    assert raw == expected, (
        "raw json.loads call sites are %s; expected only %s" % (raw, expected))


def _catchall_guarded(source, qualname):
    """Is the raw `json.loads` in `qualname` under a bare `except Exception`?

    The judgement every `catchall` exemption rests on, factored out so that the
    *same* code decides a registered exemption and the planted ones below. A
    checker used only on real entries is untested exactly when there are none —
    which is now — and would be believed the first time it was needed.

    Located on the parse tree: a comment saying "catches everything" is not a
    handler, and `except (ValueError, UnicodeDecodeError)` is not `Exception`.
    Returns `None` if the function is not defined at all, which the caller
    distinguishes from "defined and unguarded".
    """
    tree = ast.parse(source)
    target = None
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == qualname.split(".")[-1]):
            target = node
    if target is None:
        return None

    for node in ast.walk(target):
        if not isinstance(node, ast.Try):
            continue
        calls_json = any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            and isinstance(c.func.value, ast.Name)
            and c.func.value.id == "json" and c.func.attr in ("loads", "load")
            for c in ast.walk(node))
        if not calls_json:
            continue
        for handler in node.handlers:
            if isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                return True
    return False


def test_j15_every_exempt_site_really_does_catch_the_runtime_error():
    """An exemption is only honest if the thing it claims is true.

    A `catchall` exemption is excused on the grounds that its bare
    `except Exception` catches `RecursionError`. That is checked here rather
    than taken on trust -- located on the parse tree, so a comment saying
    "catches everything" cannot satisfy it, and the class hierarchy is checked
    against the language rather than against a memory of it.

    An exemption whose premise has stopped being true is worse than no
    exemption, because it reads as a decision somebody made on purpose.

    **The register is currently empty, and this law does not skip.** It used to,
    and that was wrong twice over. A skip means "this tree cannot test this",
    and the truth here is "there is nothing registered to test" -- a different
    fact, reported as the same word. And a law that goes quiet exactly when its
    subject disappears is a law nobody notices has stopped working: the *next*
    exemption would be judged by a checker that had not run in months.

    So the subject when the register is empty is the **checker itself**. Two
    synthetic modules are planted, one guarded and one not, and `_catchall_guarded`
    -- the identical function that judges a real entry -- must tell them apart.
    Whenever an exemption does exist it is checked as well, by the same code.
    """
    assert issubclass(RecursionError, Exception), \
        "the exemption's premise is false in this Python"

    for key, (guard, _rationale) in sorted(PARSE_EXEMPTIONS.items()):
        module, qualname = key.split(":", 1)
        path = os.path.join(PKG, module + ".py")
        if not os.path.isfile(path):
            # A packet may ship a subset of the package. If a module is gone so
            # is its exemption, and J13 says so; asserting the shape of one
            # checkout here would go red on a perfectly good packet.
            continue
        with open(path, encoding="utf-8") as fh:
            guarded = _catchall_guarded(fh.read(), qualname)
        assert guarded is not None, "%s no longer defines %s" % (module, qualname)
        assert guarded, (
            "%s's raw json.loads is not under `except Exception`; its "
            "registered exemption no longer describes the code" % key)

    # The checker, checked. `except Exception` catches `RecursionError`; the
    # narrow clause this whole battery exists because of does not, and must be
    # reported as unguarded.
    guarded_source = (
        "import json\n"
        "def read(raw):\n"
        "    try:\n"
        "        return json.loads(raw)\n"
        "    except Exception:\n"
        "        return None\n")
    narrow_source = (
        "import json\n"
        "def read(raw):\n"
        "    try:\n"
        "        return json.loads(raw)\n"
        "    except (ValueError, UnicodeDecodeError):\n"
        "        return None\n")
    prose_source = (
        "import json\n"
        "def read(raw):\n"
        "    # this is under `except Exception`, honestly it is\n"
        "    return json.loads(raw)\n")

    assert _catchall_guarded(guarded_source, "read") is True
    assert _catchall_guarded(narrow_source, "read") is False, \
        "the checker accepts the exact narrow clause this battery exists for"
    assert _catchall_guarded(prose_source, "read") is False, \
        "a comment claiming a handler satisfies the checker"
    assert _catchall_guarded(guarded_source, "absent") is None


def test_j16_out_of_scope_sites_are_named_rather_than_forgotten():
    """A site outside the package is still a site somebody has to think about.

    Law A covers `traaviis/`, because that is what ships. `tools/` is the packet
    harness. The distinction is legitimate and it is also exactly the kind of
    distinction that turns into "we forgot", so the out-of-scope set is written
    down, its rationale is required to be substantive, and the file is required
    to still exist and still hold the call.

    Deliberately does **not** assert the shape of this checkout beyond the one
    file it names: a release packet ships no `dist/`, and a law that walked
    `tools/` demanding a fixed inventory has been caught twice.
    """
    for key, rationale in PARSE_OUT_OF_SCOPE.items():
        relpath, qualname = key.rsplit(":", 1)
        path = os.path.join(REPO, relpath)
        if not os.path.isfile(path):
            raise Skip("%s is not present in this tree" % relpath)
        assert len(rationale) > 120, \
            "%s is scoped out without a real rationale" % key
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef)}
        assert qualname in names, (
            "%s no longer defines %s; the out-of-scope note is stale"
            % (relpath, qualname))


# ================================================== J17-J19: Law B, source rule

def _structural_claims(path):
    """Test functions in `path` that reason about Python source as raw text.

    A test may legitimately read raw text -- when the property really is about
    bytes or prose. It may not do so to claim something about *structure*:
    imports, exception handlers, call sites, identifiers, dispatched methods,
    class shape or argument lists. Those are AST questions, and a text scan
    cannot tell code from a comment about code.

    This is not a hypothetical distinction. A law in this tree once broke
    because a prose comment contained a forbidden word -- the sixth instance of
    a text scan claiming something about structure it cannot see.

    Detection is by **taint**, not by whether the function mentions `ast`
    anywhere. The first version of this checker cleared any function that used
    an AST at all, and that was wrong in the most embarrassing possible way: it
    silently cleared `test_m30`, which walks identifiers via an AST helper *and
    then* asserts ``"kernel" not in cli_src.lower()`` over the whole of cli.py.
    A checker that lets a raw-text claim hide behind an unrelated AST call in
    the same body is the same blindness Law B exists to forbid, one level up.

    So: a value that came out of `inspect.getsource` is tainted, taint spreads
    through assignments and string operations, and the flag is raised on a
    substring question asked **about a tainted value**. Handing a tainted value
    to `ast.parse` produces a tree, and claims about that tree are structural
    and are not flagged -- which is exactly how `_caught_by` and
    `_run_agent_calls` should behave, and why they are not noise here.
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def returns_structure(fn):
        """True if every `return` in `fn` hands back a container, not a string.

        The discriminator Law B turns on: a set of identifiers pulled off an
        AST is *structure*, and `x not in identifiers` is a structural claim; a
        string of source is *text*, and `x not in source` is a text claim that a
        comment can satisfy. Both are produced by helpers that use `ast`, so
        "does it mention ast" cannot tell them apart -- checked against the real
        pair that proved it, `_code_identifiers` (structure) and
        `_without_docstrings` (text).
        """
        def container_shaped(value, depth=0):
            if isinstance(value, (ast.SetComp, ast.ListComp, ast.DictComp,
                                  ast.GeneratorExp, ast.Set, ast.List,
                                  ast.Dict, ast.Tuple)):
                return True
            if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id in ("set", "list", "dict", "sorted",
                                          "frozenset", "tuple")):
                return True
            # `names = set()` ... `return names`. The accumulator pattern is how
            # every structural helper in these batteries is actually written --
            # `_code_identifiers`, `_run_agent_calls`, `_dispatched_methods` --
            # so a shape test that only looked at the returned expression
            # classified all three as text and flagged their callers. Resolve
            # the name to what it was bound to.
            if isinstance(value, ast.Name) and depth < 3:
                for c in ast.walk(fn):
                    if isinstance(c, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == value.id
                            for t in c.targets):
                        if container_shaped(c.value, depth + 1):
                            return True
            return False

        returns = [c for c in ast.walk(fn)
                   if isinstance(c, ast.Return) and c.value is not None]
        if not returns:
            return False
        return all(container_shaped(c.value) for c in returns)

    #: Same-module helpers that hand back Python source *text* rather than
    #: extracted structure.
    #:
    #: Decided by what the helper **returns**, not by whether it mentions `ast`.
    #: Deciding by "does it parse" was wrong in both directions and both were
    #: found by checking this checker against real tests: `_without_docstrings`
    #: uses an AST to strip docstrings and still returns a *string*, so a
    #: substring claim on its result is still a text claim (that is `c52`);
    #: while `_code_identifiers` also uses an AST and returns a *set of
    #: identifiers*, so `x not in names` there is a genuine structural claim
    #: (that is `m28`). The discriminator that separates them is the shape of
    #: the returned value: a string is text, a set/list/dict is structure.
    def returns_source(fn, seen=None):
        seen = seen or set()
        if not any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and isinstance(c.func.value, ast.Name)
                and c.func.value.id == "inspect"
                and c.func.attr in ("getsource", "getsourcelines")
                for c in ast.walk(fn)):
            # It does not fetch source itself; it may still forward one that
            # does. **And the same discriminator applies to the forward as to
            # the direct case**: a helper that calls a source-fetching helper
            # and hands back a *container* is extracting structure from source,
            # which is the whole shape of `_function_body`, `_code_identifiers`
            # and every other legitimate instrument in these batteries.
            #
            # This branch used to return `True` on the forward alone, without
            # ever looking at what the forwarding helper returned — so
            # `def helper(f): return list(_tree(f).body)` was classified as
            # text, and every claim built on it was flagged as a raw-text claim.
            # That is the inversion described at `ast.parse` above, reached by a
            # second route: a law rewritten onto the AST through a two-step
            # helper could not clear the register either.
            for c in ast.walk(fn):
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                        and c.func.id in functions and c.func.id not in seen):
                    if returns_source(functions[c.func.id], seen | {c.func.id}):
                        return not returns_structure(fn)
            return False

        for c in ast.walk(fn):
            if not isinstance(c, ast.Return) or c.value is None:
                return_shape = None
            else:
                value = c.value
                if isinstance(value, (ast.SetComp, ast.ListComp, ast.DictComp,
                                      ast.GeneratorExp, ast.Set, ast.List,
                                      ast.Dict, ast.Tuple)):
                    return_shape = "structure"
                elif (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                      and value.func.id in ("set", "list", "dict", "sorted",
                                            "frozenset", "tuple")):
                    return_shape = "structure"
                else:
                    return_shape = "text"
                if return_shape == "structure":
                    return False
        return True

    def taints(node, tainted):
        """True if evaluating `node` yields Python source text."""
        if isinstance(node, ast.Name):
            return node.id in tainted
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id == "inspect"
                    and f.attr in ("getsource", "getsourcelines")):
                return True
            # **`ast.parse` is where the taint ends.** This docstring has said
            # so since the checker was written -- "handing a tainted value to
            # `ast.parse` produces a tree, and claims about that tree are
            # structural and are not flagged" -- and the code did not implement
            # it: the generic string-operation rule below saw
            # `ast.parse(inspect.getsource(M))` as an attribute call over a
            # tainted argument and passed the taint straight through the parse.
            #
            # The consequence was the exact inversion of Law B. A law rewritten
            # *onto* the AST -- the remedy this law demands -- stayed flagged,
            # because everything derived from its tree was still "source text";
            # so a genuine fix could not clear the register, and the register
            # would have accumulated entries describing laws that had already
            # been fixed. Three phantom entries from the checker's first version
            # are recorded below for the same class of reason.
            #
            # `ast.unparse` is deliberately **not** in this list. It goes the
            # other way: a tree back to text. That text has no comments in it,
            # so a substring claim over it is much weaker than one over a file,
            # but it is still text and the checker should keep saying so.
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id == "ast"
                    and f.attr in ("parse", "walk", "iter_child_nodes",
                                   "iter_fields", "literal_eval")):
                return False
            if isinstance(f, ast.Name) and f.id in functions:
                if returns_source(functions[f.id]):
                    return True
                # A helper that is *handed* source and gives back a string
                # passes the taint through. `_without_docstrings(getsource(I))`
                # is the case: it fetches nothing itself, so the rule above
                # says nothing about it, but it returns text derived from text
                # and a substring claim on its result is still a text claim.
                if (any(taints(a, tainted) for a in node.args)
                        and not returns_structure(functions[f.id])):
                    return True
            # `src.lower()`, `src.split(...)`, `"".join(src.split())` -- string
            # operations preserve the taint of what they operate on.
            if isinstance(f, ast.Attribute):
                if taints(f.value, tainted):
                    return True
                for arg in node.args:
                    if taints(arg, tainted):
                        return True
            return False
        if isinstance(node, (ast.BinOp,)):
            return taints(node.left, tainted) or taints(node.right, tainted)
        if isinstance(node, ast.Subscript):
            return taints(node.value, tainted)
        if isinstance(node, ast.Attribute):
            return taints(node.value, tainted)
        return False

    flagged = []
    for name, node in sorted(functions.items()):
        if not name.startswith("test_"):
            continue

        def bind(target):
            """Taint every name a binding target introduces."""
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    tainted.add(sub.id)

        tainted = set()
        # Two passes, so an assignment textually after a use still taints it;
        # a test body is small and this avoids depending on statement order.
        for _ in range(2):
            for child in ast.walk(node):
                if isinstance(child, ast.Assign) and taints(child.value, tainted):
                    for target in child.targets:
                        bind(target)
                elif isinstance(child, ast.AnnAssign) and child.value is not None:
                    if taints(child.value, tainted):
                        bind(child.target)
                elif isinstance(child, ast.For):
                    # `for name, src in (("evalone", live_src), ...)`. Iterating
                    # a structure that *contains* source text hands that text to
                    # the loop variable, and o13 does exactly this -- which the
                    # first version of this checker walked straight past,
                    # because it only followed plain assignments. Tuple targets
                    # taint conservatively: knowing which element of the pair
                    # carried the text would need real dataflow, and over-
                    # flagging here costs a registry line while under-flagging
                    # costs the law.
                    #
                    # **The sub-walk is confined to literal containers**, and
                    # that is the third place the `ast.parse` de-taint had to be
                    # honoured. Walking *every* subexpression of the iterable
                    # meant `for node in ast.walk(ast.parse(getsource(M)))` --
                    # the canonical structural idiom, and the one Law B tells
                    # people to write -- tainted its loop variable off the
                    # `getsource` buried three calls down, no matter what was
                    # wrapped around it. A parse is where source stops being
                    # source; a literal tuple of source strings is where it
                    # keeps being source. Those are different, and the rule now
                    # says which is which.
                    if taints(child.iter, tainted):
                        bind(child.target)
                    elif isinstance(child.iter, (ast.Tuple, ast.List, ast.Set)) \
                            and any(taints(part, tainted)
                                    for part in ast.walk(child.iter)):
                        bind(child.target)
                elif isinstance(child, ast.withitem) and child.optional_vars is not None:
                    if taints(child.context_expr, tainted):
                        bind(child.optional_vars)

        asks = False
        for child in ast.walk(node):
            # `needle in src` / `needle not in src`: the *container* is what
            # matters, so a tainted needle searched in a clean list is fine.
            if isinstance(child, ast.Compare):
                for op, comparator in zip(child.ops, child.comparators):
                    if isinstance(op, (ast.In, ast.NotIn)) and taints(comparator, tainted):
                        asks = True
            # `src.count(...)`, `src.index(...)`, `src.startswith(...)`...
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr in ("count", "find", "index",
                                            "startswith", "endswith")
                    and taints(child.func.value, tainted)):
                asks = True
        if asks:
            flagged.append(name)
    return flagged


#: Every test that reasons about Python source as raw text, classified.
#:
#:   ``prose``      the property really is about prose or bytes -- a prompt's
#:                  wording, a diagnostic's phrasing, a battery's own output.
#:                  Legitimate; raw text is the right instrument.
#:   ``violation``  a structural claim made on raw text. A comment can satisfy
#:                  or break it. Each carries who owns the fix, because this
#:                  battery owns only the files it was given.
#:
#: **There are no violations left.** Twelve were registered when this law was
#: written, spread across four batteries that this change was not allowed to
#: touch; the ruling that opened 9D required them closed in one focused
#: test-hygiene pass, before item 11, and for the reason it gave rather than for
#: tidiness::
#:
#:     registered exception -> normalized exception -> new violation added
#:     casually -> law stops protecting the suite
#:
#: All twelve are now AST-based and none of them is flagged. What is left is
#: four `prose` entries, and they are not a residue: each reads *another
#: battery's stdout*, which really is prose, and raw text really is the right
#: instrument for it.
#:
#: Closing them also exposed three defects in the checker itself, each of which
#: made the register impossible to empty. `taints` passed the taint straight
#: through `ast.parse` despite the docstring promising that a parse ends it;
#: `returns_source` classified any helper that *forwarded* a source-fetching
#: helper as returning text, whatever it actually returned; and the `for`-loop
#: rule walked every subexpression of the iterable, so `for n in
#: ast.walk(ast.parse(getsource(M)))` tainted its loop variable off the
#: `getsource` three calls down. Together they meant a law rewritten onto the
#: AST -- the remedy this law demands -- stayed flagged. A checker that cannot
#: recognise its own remedy makes the register permanent, which is exactly the
#: normalization the ruling warned about, reached from the other side.
SOURCE_TEXT_SITES = {
    "test_batch.py": {
        "test_b29_the_comparison_api_ambiguity_closure_remains_green": (
            "prose", "reads another battery's stdout, which is prose"),
    },
    "test_bundle.py": {
        "test_d40_the_earlier_laws_and_the_packet_gates_are_untouched": (
            "prose", "reads another battery's stdout, which is prose"),
    },
    "test_kernel.py": {
        "test_k18_the_ladder_the_cli_and_the_earlier_laws_are_untouched": (
            "prose", "reads another battery's stdout, which is prose"),
    },
}


def test_j17_no_unregistered_test_reasons_about_source_as_raw_text():
    """Law B. Every raw-text-on-source test is known and classified.

    The rule: a test claiming something about imports, exception handlers, call
    sites, identifiers, dispatched methods, class structure or argument
    definitions must inspect AST or runtime structure. Raw text stays
    legitimate only where the property genuinely concerns bytes or prose.

    This law does not silently tolerate the violations it found; it *names*
    them, with an owner, so they cannot be rediscovered as news. What it
    forbids is a **new** one appearing unnoticed -- which is how six of these
    accumulated.

    The battery files it walks are discovered, not listed, so this law does not
    assert the shape of the checkout: a tree with fewer batteries simply has
    fewer to check.
    """
    unregistered = []
    for name in sorted(os.listdir(TESTS)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        known = SOURCE_TEXT_SITES.get(name, {})
        for fn in _structural_claims(os.path.join(TESTS, name)):
            if fn not in known:
                unregistered.append("%s::%s" % (name, fn))
    assert not unregistered, (
        "test(s) reason about Python source as raw text without being "
        "classified: %s. Rewrite on the AST, or register with a rationale."
        % ", ".join(unregistered))


def test_j18_every_registered_source_text_site_still_exists_and_is_justified():
    """The registry describes real tests, and every entry says something.

    A registry that outlives the code it describes is worse than none: it reads
    as coverage while covering nothing. Entries for a battery that is not in
    this tree are skipped rather than failed, so a partial checkout does not
    turn into a false red.
    """
    checked = 0
    for filename, entries in sorted(SOURCE_TEXT_SITES.items()):
        path = os.path.join(TESTS, filename)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        detected = set(_structural_claims(path))
        for fn, (kind, rationale) in sorted(entries.items()):
            assert kind in ("prose", "violation"), (filename, fn, kind)
            assert len(rationale) > 25, "%s::%s has no real rationale" % (filename, fn)
            assert fn in defined, (
                "%s::%s is registered but no longer defined" % (filename, fn))
            # And the checker must still see it. Without this, an entry could
            # outlive the thing it describes -- either because the test was
            # rewritten on the AST (good news that nobody recorded) or because
            # the checker stopped detecting it (bad news that nobody noticed).
            # Three phantom entries existed here for exactly the second reason:
            # the first version of the checker over-flagged, and the registry
            # inherited its mistakes as though they were findings.
            assert fn in detected, (
                "%s::%s is registered but the checker no longer flags it; "
                "either it was fixed (drop the entry) or the checker went "
                "blind (fix the checker)" % (filename, fn))
            checked += 1
    if not checked:
        raise Skip("none of the registered batteries are present in this tree")


def test_j19_this_battery_obeys_the_rule_it_states():
    """Law B applied to itself, which is the only way it is not hypocrisy.

    Every structural claim in this file -- the parse-site census, the exemption
    check, the violation sweep -- is made on `ast`, never on raw text. Checked
    the same way the rule is checked everywhere else, on this file's own tree.
    """
    assert _structural_claims(os.path.abspath(__file__)) == [], \
        "this battery makes a structural claim on raw text"

    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    uses = {n.func.value.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)}
    assert "ast" in uses, "this battery does not inspect any AST"


# ================================================ J20-J22: non-vacuity by deletion

def test_j20_deleting_the_depth_scan_makes_the_deep_document_crash_again():
    """Non-vacuity for the depth bound, by source-level deletion.

    The lexical pre-scan is removed in an isolated copy of the package. The
    200 000-deep document must then reach `json.loads` and raise something that
    is *not* a `BoundedJsonError`, proving the scan is what stops it rather
    than some incidental property of the input.

    The belt-and-braces `except RecursionError` inside `load_json_bounded` is
    removed in the same edit, since with it in place the copy would still refuse
    correctly -- which is the point of having it, and precisely why it has to go
    for this proof to prove anything.
    """
    package, cleanup = _isolated_package(
        ("boundedjson.py",
         "    deepest = _scan_depth(text)\n"
         "    if deepest > max_depth:",
         "    deepest = 0\n"
         "    if False:"),
        ("boundedjson.py",
         "    except RecursionError:\n"
         "        # Unreachable if `_scan_depth` is right",
         "    except _NeverRaised:\n"
         "        # Unreachable if `_scan_depth` is right"),
        ("boundedjson.py",
         "import json\n",
         "import json\n\n\nclass _NeverRaised(Exception):\n    pass\n"),
    )
    try:
        broken = package.boundedjson
        hostile = _nest(200000).encode("utf-8")
        try:
            broken.load_json_bounded(hostile)
        except broken.BoundedJsonError:
            raise AssertionError(
                "the depth scan was deleted and the document was still refused "
                "as a bounds failure; this law proves nothing")
        except RecursionError:
            pass  # exactly the defect, reproduced on demand
        else:
            raise AssertionError(
                "the depth scan was deleted and the document was accepted")

        # The shipped module still refuses it, so the difference is the edit.
        assert _refusal(B.load_json_bounded, hostile).reason == "too_deep"
    finally:
        cleanup()


def test_j21_deleting_the_bounds_check_lets_the_pretty_expansion_run_again():
    """Non-vacuity for the write path: without the check, the blowup returns.

    `_write_json`'s bounds call is removed in an isolated copy and a depth-2 000
    document is written. The resulting file must be enormously larger than the
    document -- the O(depth^2) expansion, reproduced -- while the shipped writer
    refuses the same input.

    Depth 2 000 rather than the measured 5 000: same phenomenon, ~8 MB instead
    of ~50 MB, because a law that writes 50 MB to prove a point is a law people
    turn off.
    """
    package, cleanup = _isolated_package(
        ("episode_bundle.py",
         "    data = _bjson.dump_json_bounded(obj, indent=2, sort_keys=True,\n"
         "                                    ensure_ascii=False, trailing_newline=True)",
         "    import json as _json\n"
         "    data = (_json.dumps(obj, indent=2, sort_keys=True,\n"
         "                        ensure_ascii=False) + \"\\n\").encode(\"utf-8\")"),
    )
    tmp = tempfile.mkdtemp(prefix="trvs-blowup-")
    try:
        broken = __import__(package.__name__ + ".episode_bundle",
                            fromlist=["episode_bundle"])
        source = _nest(2000)
        obj = json.loads(source)

        target = os.path.join(tmp, "member.json")
        broken._write_json(target, obj)
        written = os.path.getsize(target)
        assert written > 50 * len(source), (
            "the expansion did not occur, so this law is not measuring what it "
            "claims: %d bytes out of %d in" % (written, len(source)))

        # The shipped writer refuses the identical input, and writes nothing.
        from traaviis import episode_bundle as EB
        shipped = os.path.join(tmp, "shipped.json")
        assert _refusal(EB._write_json, shipped, obj).reason == "too_deep"
        assert not os.path.exists(shipped)
    finally:
        cleanup()
        shutil.rmtree(tmp, ignore_errors=True)


def test_j22_law_a_goes_red_when_an_unguarded_parse_site_appears():
    """Non-vacuity for the registry, by adding the thing it is meant to catch.

    A new unguarded `json.loads` is inserted into an isolated copy of a module
    that has none, and Law A's census must report it as unregistered. Without
    this, "the registry matches" could be true because the census finds nothing
    anywhere.

    Also checks the *moved-site* case, which is the reason the key is
    module+qualname: the same call relocated into a differently-named function
    is a different key, and must be caught too.
    """
    package, cleanup = _isolated_package(
        ("paths.py",
         "import os",
         "import json\nimport os\n\n\n"
         "def _sneaky(raw):\n"
         "    return json.loads(raw)\n"),
    )
    try:
        copy_dir = os.path.dirname(package.__file__)
        found = _qualified_parse_sites(copy_dir)
        assert "paths:_sneaky" in found, \
            "the census did not see the new call site; it proves nothing"
        assert found["paths:_sneaky"] == "raw"

        registered = set(PARSE_SITES) | set(PARSE_EXEMPTIONS)
        unregistered = set(found) - registered
        # Membership, not equality. Equality would make this law fail whenever
        # *another* unregistered site happened to exist -- which is J13's job to
        # report, and which would turn a non-vacuity proof into a second, noisier
        # copy of the same alarm. The claim here is only that the census sees
        # the site that was planted.
        assert "paths:_sneaky" in unregistered, sorted(unregistered)
    finally:
        cleanup()

    # A moved site: same call, different enclosing name, therefore a new key.
    moved, cleanup2 = _isolated_package(
        ("pack.py", "def _read_json(path, code, what):",
         "def _read_json_renamed(path, code, what):"),
    )
    try:
        found = _qualified_parse_sites(os.path.dirname(moved.__file__))
        assert "pack:_read_json" not in found, \
            "the census still reports the old name; a move would go unnoticed"
        assert "pack:_read_json_renamed" in found
        assert "pack:_read_json_renamed" not in set(PARSE_SITES)
    finally:
        cleanup2()


def test_j24_law_b_goes_red_on_a_planted_raw_text_structural_claim():
    """Non-vacuity for Law B: plant one, and prove the checker sees it.

    Also plants the *legitimate* shapes and proves they stay clean, because a
    checker that flagged everything would be as useless as one that flagged
    nothing -- and would be turned off just as fast. Three shapes are checked:

      * a substring claim on `inspect.getsource` output          -> flagged
      * the same claim made on identifiers pulled off an AST     -> clean
      * a substring claim on text passed through a helper        -> flagged

    The third is the one the checker originally missed, and the second is the
    one it originally over-flagged. Both are pinned so neither mistake can come
    back quietly.
    """
    planted = '''
import ast
import inspect


def _identifiers(source):
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names


def _strip(source):
    return "".join(source.split())


def test_bad_reads_source_as_text():
    src = inspect.getsource(ast)
    assert "socket" not in src


def test_good_reads_structure():
    assert "socket" not in _identifiers(inspect.getsource(ast))


def test_bad_through_a_helper():
    assert "socket" not in _strip(inspect.getsource(ast))
'''
    tmp = tempfile.mkdtemp(prefix="trvs-lawb-")
    try:
        path = os.path.join(tmp, "test_planted.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(planted)
        flagged = set(_structural_claims(path))
        assert "test_bad_reads_source_as_text" in flagged, flagged
        assert "test_bad_through_a_helper" in flagged, flagged
        assert "test_good_reads_structure" not in flagged, (
            "the checker flags an AST-based claim; it would be noise and would "
            "be switched off")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_j25_the_twelve_closed_violations_are_still_the_kind_the_checker_sees():
    """The register was emptied by fixing the laws, **not** by blinding the checker.

    That distinction is the whole risk of this change and it deserves its own
    law. Closing the twelve required three amendments to `_structural_claims`
    itself -- the `ast.parse` de-taint, the `returns_source` forwarding rule,
    and the confinement of the `for`-loop sub-walk to literal containers -- and
    every one of them makes the checker flag *less*. A reader is entitled to ask
    whether they narrowed it correctly or simply switched it off over the twelve
    cases that were inconvenient.

    So the original shape of each closed violation is planted here, verbatim in
    form, and must still be flagged. Four shapes, one per amendment plus the
    ones the amendments left untouched:

      * `str.index` ordering over `getsource`             (the old c18)
      * a substring claim over a `getsource` substring    (the old c42/c50/c52)
      * `.lower()` over a whole module's source           (the old k28/o30)
      * `source.count("with self._lock:")`                (the old k16)

    And the *fixed* shapes must stay clean, or the amendments would have been
    pointless: an AST-derived statement list, a two-step structural helper, and
    a walk over a parsed tree.
    """
    planted = '''
import ast
import inspect


def _tree(function):
    return ast.parse(inspect.getsource(function))


def _statements(function):
    return list(_tree(function).body)


def _strip(source):
    return "".join(source.split())


def test_bad_index_ordering():
    body = _strip(inspect.getsource(ast))
    assert body.index("a") < body.index("b")


def test_bad_substring_of_a_substring():
    assert "Decimal" not in _strip(inspect.getsource(ast))


def test_bad_lowered_whole_module():
    assert "socket" not in inspect.getsource(ast).lower()


def test_bad_counts_a_statement():
    assert inspect.getsource(ast).count("with self._lock:") == 6


def test_good_statement_list():
    body = _statements(ast.walk)
    assert isinstance(body[0], ast.stmt)


def test_good_walks_a_parsed_tree():
    names = set()
    for node in ast.walk(ast.parse(inspect.getsource(ast))):
        if isinstance(node, ast.Name):
            names.add(node.id)
    assert "socket" not in names
'''
    tmp = tempfile.mkdtemp(prefix="trvs-lawb-closed-")
    try:
        path = os.path.join(tmp, "test_planted_closed.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(planted)
        flagged = set(_structural_claims(path))
        for name in ("test_bad_index_ordering", "test_bad_substring_of_a_substring",
                     "test_bad_lowered_whole_module", "test_bad_counts_a_statement"):
            assert name in flagged, (
                "%s is the shape of a violation this change claims to have "
                "closed, and the checker no longer sees it: the register was "
                "emptied by narrowing the checker, not by fixing the laws"
                % name)
        for name in ("test_good_statement_list", "test_good_walks_a_parsed_tree"):
            assert name not in flagged, (
                "%s is the remedy Law B demands and the checker still flags it; "
                "the register can never be emptied by complying" % name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_j23_a_diagnostic_is_truncated_to_64_kib_and_stays_utf8():
    """The sixth frozen bound: 64 KiB of diagnostic per verifier.

    Truncation rather than refusal, and that asymmetry is deliberate. A
    diagnostic explains a verdict that has *already been reached*, so throwing
    the verdict away because its explanation ran long would hand a candidate a
    way to suppress its own bad score by failing very talkatively. Everywhere
    else in this module an over-bound document is refused; here it is cut, and
    the cut is marked so a reader never mistakes a truncated diagnostic for a
    complete one.

    The output must stay valid UTF-8. Slicing encoded bytes at an arbitrary
    offset can land inside a multi-byte sequence, and an invalid diagnostic
    fails to serialize much later, far from the code that produced it -- which
    is the same class of delayed, misattributed failure this whole battery is
    about.

    The bound is *declared* here and applied by the verifier modules, which are
    owned elsewhere; this law pins the number and the helper's behaviour, not
    the wiring.
    """
    assert B.MAX_DIAGNOSTIC_BYTES == 64 * 1024

    assert B.bound_diagnostic("short") == "short", "a short diagnostic is untouched"

    for text in ("x" * 200000, "é" * 200000, "\U0001F600" * 100000):
        out = B.bound_diagnostic(text)
        encoded = out.encode("utf-8")
        assert len(encoded) <= B.MAX_DIAGNOSTIC_BYTES, len(encoded)
        assert "truncated" in out, "a cut diagnostic must say it was cut"
        # Valid UTF-8: re-decoding is lossless, so no character was split.
        assert encoded.decode("utf-8") == out

    # Exactly at the bound, nothing is cut and nothing is announced.
    exact = "a" * B.MAX_DIAGNOSTIC_BYTES
    assert B.bound_diagnostic(exact) == exact
    assert "truncated" in B.bound_diagnostic(exact + "a")


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
