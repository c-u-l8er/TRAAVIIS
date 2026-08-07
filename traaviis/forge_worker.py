"""The Forge lowering worker — one WRL lower, in a process that may be killed.

``python3 -m traaviis.forge_worker`` reads one JSON request from stdin, lowers
the source through the same public ``forge_api.lower_source`` boundary the
in-process adapter used, writes one JSON response to stdout, and exits. It holds
no state, is never reused, and is not a server.

**Why this process exists.** ``evalone.resolve_signal`` contains a verifier that
*stops* — it classifies a raise and a malformed return as ``error`` and keeps the
episode. It cannot contain a verifier that never stops. An infinite loop, a
native deadlock, an ``os._exit``, a segfault or a runaway allocation reaches no
``except`` clause; the evaluating process simply does not come back, nothing is
persisted, and a failing grade is erased exactly as thoroughly as an uncaught
exception erased it. The ``tests`` verifier has never had this problem, because
it has always run its commands as subprocesses under a timeout. The ``identity``
verifier did: it called ``adapter.lower_source(patched[rel])`` in-process on a
WRL source the *candidate* wrote. That asymmetry is what this module removes.

**The wire is bounded in both directions** (§9D). One request object of at most
``execlimits.MAX_IPC_REQUEST_BYTES``, carrying at most
``MAX_IPC_SOURCE_BYTES`` of WRL; one response object of at most
``MAX_IPC_RESPONSE_BYTES``, carrying at most ``MAX_IPC_DIAGNOSTIC_BYTES`` of
engine diagnostic. Both ends parse through ``boundedjson``. The earlier reading
— that these are internal envelopes and therefore trusted parses — was wrong in
the half that matters: the envelope is written by ``forge_adapter``, but its
payload is *candidate-modified WRL* on the way in and *engine-emitted
diagnostic* on the way back. Neither is this repository's own bytes.

**The request/response contract** (``PROTOCOL_VERSION``), one JSON object each
way, stdin to stdout::

    request   {"source": "<WRL text>"}

    response  {"status": "ok", "ok": true,  "semantic_artifact_id": "sem-…"}
              {"status": "ok", "ok": false, "error": "<compile diagnostic>"}
              {"status": "unavailable", "error": "<why the engine could not answer>"}

``status: "ok"`` means the engine gave a verdict, and ``ok`` says whether that
verdict was a successful lowering. Ordinary invalid WRL is ``status: "ok"`` with
``ok: false`` — a *data* outcome, exactly as ``LowerResultV1`` frames it — while
an engine that cannot be located, imported, or called at all is
``status: "unavailable"``. The parent maps the second to ``ForgeUnavailable``,
preserving the meaning that ``forge_adapter`` already had.

**Three failures have no response at all**, and the parent must treat all three
alike: a non-zero exit, output that is not one JSON object, and no output
because the process was killed on its timeout. Each says the engine did not
answer, which is ``ForgeUnavailable`` (or its ``ForgeTimeout`` refinement) —
never a ``pass`` and never a ``fail``. A worker that dies is never evidence
against the candidate.

**stdout is claimed.** The response channel is a duplicate of fd 1 taken before
the engine is imported, and ``sys.stdout`` is then pointed at stderr for the rest
of the run. An engine that prints — a warning, a progress line, a stray
``print`` in a lowering pass — would otherwise land in the middle of the response
document and turn a good answer into an unparseable one. The engine keeps its
voice; it just does not share the wire.
"""

import os
import sys

from . import boundedjson as _bjson, execlimits

__all__ = ["PROTOCOL_VERSION", "STATUS_OK", "STATUS_UNAVAILABLE", "lower", "main",
           "read_request", "encode_response"]

#: The request/response contract above. Bumping it is a coordinated change of
#: both sides; it is deliberately **not** part of any identity, because the wire
#: format between a process and its own worker says nothing about the world being
#: lowered. What *does* enter ``verifier_versions.identity`` is the engine
#: boundary version computed by ``forge_adapter.real_adapter`` — unchanged by the
#: existence of this module, which is what keeps every sealed ``episode-…`` where
#: it is.
PROTOCOL_VERSION = "traaviis.forge-worker.v1"

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"


def lower(source):
    """Lower one source; return the response object described in the module doc.

    Total by construction: every failure mode becomes a response rather than a
    traceback, because a traceback on stderr and a non-zero exit is a strictly
    worse report than a structured ``unavailable``. (The parent handles that case
    too — it has to, for a segfault — but it can say less about it.)
    """
    from . import engine

    forge_api = engine.try_load()
    if forge_api is None:
        return {"status": STATUS_UNAVAILABLE,
                "error": "could not locate a compatible Forge engine "
                         "(set TRVS_FORGE_DIR)"}
    fn = getattr(forge_api, "lower_source", None)
    if not callable(fn):
        return {"status": STATUS_UNAVAILABLE,
                "error": "forge_api has no callable lower_source"}
    try:
        payload = fn(source)
    except Exception as exc:  # noqa: BLE001 -- an engine that raises is unavailable
        return {"status": STATUS_UNAVAILABLE,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        if not payload.get("ok"):
            return {"status": STATUS_OK, "ok": False,
                    "error": payload.get("error")}
        return {"status": STATUS_OK, "ok": True,
                "semantic_artifact_id": payload["semantic_artifact_id"]}
    except Exception as exc:  # noqa: BLE001 -- a LowerResultV1 that is not one
        # The engine answered with something the frozen contract does not
        # describe. That is the engine failing to answer, not the candidate
        # failing to compile, so it is `unavailable` and never `ok: false`.
        return {"status": STATUS_UNAVAILABLE,
                "error": "malformed LowerResultV1: %s: %s"
                         % (type(exc).__name__, exc)}


def read_request(stream):
    """The WRL source of one request, read under the IPC bounds.

    **The envelope is internally generated; the payload is not.** This module's
    exemption from the bounded-JSON boundary used to be argued on the first half
    of that sentence — an internal IPC message written by ``forge_adapter``, not
    by a candidate — and the ruling that opened 9D was right that it encodes the
    wrong trust judgment. ``source`` is *candidate-modified WRL*, read out of the
    patched tree. An internally generated envelope around candidate-authored
    bytes is a candidate-influenced parse site, and it is now registered as one.

    Three bounds, and each closes a different unbounded step:

    * ``sys.stdin.read()`` read until EOF, so a parent (or anything else
      holding this pipe) could hand the worker an arbitrarily large message.
      The read is now ``MAX_IPC_REQUEST_BYTES + 1`` — one byte past the bound is
      exactly enough to prove it was exceeded, and no more is ever allocated.
    * the parse is `boundedjson.load_json_bounded` under the IPC profile, so
      depth, node count and string volume are bounded too, and every refusal is
      one type.
    * ``source`` is bounded independently of the envelope, because a request can
      be small and still carry more WRL than any lowering has business seeing.

    Returns the source string. Raises `execlimits.ResourceLimitError` or
    `boundedjson.BoundedJsonError` — both `ValueError` — for a request outside
    the profile, and `TypeError`/`KeyError` for one that is inside it and still
    not a request.
    """
    raw = stream.read(execlimits.MAX_IPC_REQUEST_BYTES + 1)
    if len(raw) > execlimits.MAX_IPC_REQUEST_BYTES:
        raise execlimits.ResourceLimitError(
            "ipc_request_too_large",
            "refusing the lowering request: it is over the bound of %d bytes"
            % execlimits.MAX_IPC_REQUEST_BYTES,
            {"max_bytes": execlimits.MAX_IPC_REQUEST_BYTES})
    request = _bjson.load_json_bounded(
        raw, max_input_bytes=execlimits.MAX_IPC_REQUEST_BYTES)
    source = request["source"]
    if not isinstance(source, str):
        raise TypeError("source must be a string")
    encoded = len(source.encode("utf-8", "surrogatepass"))
    if encoded > execlimits.MAX_IPC_SOURCE_BYTES:
        raise execlimits.ResourceLimitError(
            "ipc_source_too_large",
            "refusing to lower a source of %d bytes; the bound is %d"
            % (encoded, execlimits.MAX_IPC_SOURCE_BYTES),
            {"size": encoded, "max_bytes": execlimits.MAX_IPC_SOURCE_BYTES})
    return source


def encode_response(response):
    """One response as bounded bytes — never more than `MAX_IPC_RESPONSE_BYTES`.

    The engine's ``error`` is *engine-influenced*: a compile diagnostic can be
    any length a third-party lowering pass feels like emitting, and it used to be
    serialized straight onto the wire with no cap at either end. It is truncated
    to the declared diagnostic bound first, with the truncation marked, so a
    reader never mistakes a cut-off diagnostic for a complete one.

    If the bounded response *still* will not encode — a semantic id of absurd
    length, or a value the engine returned that is not JSON — the fallback is a
    minimal ``unavailable``, because a worker that cannot say what happened has
    still said the engine did not answer, and that is the whole contract. The
    one thing it must never do is write a partial document: the parent would
    read a truncated object, fail to parse it, and report a lowering that never
    took place.
    """
    bounded = dict(response)
    if isinstance(bounded.get("error"), str):
        bounded["error"] = _bjson.bound_diagnostic(
            bounded["error"], execlimits.MAX_IPC_DIAGNOSTIC_BYTES)
    try:
        return _bjson.dump_json_bounded(
            bounded, sort_keys=True,
            max_output_bytes=execlimits.MAX_IPC_RESPONSE_BYTES)
    except _bjson.BoundedJsonError as exc:
        return _bjson.dump_json_bounded(
            {"status": STATUS_UNAVAILABLE,
             "error": "worker response exceeded the IPC bound (%s)" % exc.reason},
            sort_keys=True,
            max_output_bytes=execlimits.MAX_IPC_RESPONSE_BYTES)


def main(argv=None):
    """Read one request, write one response. Always exits 0 when it responded."""
    # Claim the response channel before anything can import the engine and print
    # on it. See the module docstring.
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    sys.stdout = sys.stderr

    try:
        source = read_request(sys.stdin.buffer)
    except Exception as exc:  # noqa: BLE001 -- a refused request is still a response
        # Covers the bounded refusals (`ResourceLimitError`,
        # `BoundedJsonError`) and the shapes that are in bounds and still not a
        # request. All three mean the same thing to the parent: no lowering
        # happened. A traceback and a non-zero exit would mean the same thing
        # too, and say less.
        response = {"status": STATUS_UNAVAILABLE,
                    "error": "malformed worker request: %s: %s"
                             % (type(exc).__name__, exc)}
    else:
        response = lower(source)

    channel.write(encode_response(response))
    channel.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
